/**
 * @jest-environment node
 */

/**
 * FE-AUTH-06 — el proxy no persigue redirecciones con las cabeceras del visitante.
 *
 * Seguía cualquier redirección del backend en el servidor (`redirect: "follow"`)
 * reenviando las cabeceras de la petición. Hay una redirección real que sale del
 * sitio: con las evidencias en un almacenamiento externo, el backend responde
 * 302 hacia una URL firmada de ese proveedor. El proxy la descargaba él mismo y
 * le enviaba el `X-CSRFToken` del visitante y el resto de sus cabeceras.
 *
 * Ahora una redirección hacia fuera se devuelve al navegador, que va solo y sin
 * nada nuestro. Dentro del backend se sigue, pero sólo bajo `/api/`.
 */

import { NextRequest } from "next/server";

import { GET, POST } from "@/app/api/[...path]/route";

const BACKEND = "http://127.0.0.1:8000";
const SIGNED = "https://almacen.example/bucket/evidencia.jpg?X-Amz-Signature=abc";

type Hit = { url: string; method: string; headers: Headers; body: unknown };
let hits: Hit[];

const context = (...path: string[]) => ({ params: Promise.resolve({ path }) });

function redirect(status: number, location: string) {
  return new Response(null, { status, headers: { location } });
}

function upstream(replies: Array<Response | (() => Response)>) {
  let i = 0;
  jest.spyOn(global, "fetch").mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    hits.push({
      url: String(input),
      method: String(init?.method),
      headers: new Headers(init?.headers),
      body: init?.body,
    });
    const next = replies[Math.min(i, replies.length - 1)];
    i += 1;
    return typeof next === "function" ? next() : next;
  });
}

function request(method = "GET", body?: string) {
  return new NextRequest("http://localhost:3000/api/x", {
    method,
    body,
    headers: { cookie: "blackdog_access=token", "x-csrftoken": "csrf-del-visitante" },
  });
}

beforeEach(() => {
  hits = [];
});
afterEach(() => jest.restoreAllMocks());

describe("redirección hacia fuera del backend", () => {
  it("no la descarga: se la devuelve al navegador", async () => {
    upstream([redirect(302, SIGNED)]);

    const res = await GET(request(), context("v1", "internal", "t", "service", "orders", "9", "evidence", "4", "content"));

    expect(hits).toHaveLength(1);
    expect(hits[0].url.startsWith(`${BACKEND}/api/`)).toBe(true);
    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toBe(SIGNED);
  });

  it("conserva las cabeceras de no guardar en caché del backend", async () => {
    upstream([
      new Response(null, { status: 302, headers: { location: SIGNED, "cache-control": "private, max-age=0, no-store" } }),
    ]);

    const res = await GET(request(), context("x"));
    expect(res.headers.get("cache-control")).toContain("no-store");
  });
});

describe("redirección dentro del backend", () => {
  it("se sigue cuando sigue bajo /api/, con la sesión", async () => {
    upstream([
      redirect(301, `${BACKEND}/api/products/`),
      new Response("[]", { status: 200, headers: { "content-type": "application/json" } }),
    ]);

    const res = await GET(request(), context("products"));

    expect(res.status).toBe(200);
    expect(hits.map((h) => h.url)).toEqual([`${BACKEND}/api/products/`, `${BACKEND}/api/products/`]);
    expect(hits[1].headers.get("cookie")).toContain("blackdog_access=");
  });

  it("acepta una ubicación relativa", async () => {
    upstream([redirect(302, "/api/otra/"), new Response("{}", { status: 200 })]);

    const res = await GET(request(), context("x"));

    expect(res.status).toBe(200);
    expect(hits[1].url).toBe(`${BACKEND}/api/otra/`);
  });

  it("no sale de /api/: una redirección al admin del backend no se sigue", async () => {
    upstream([redirect(302, `${BACKEND}/admin/login/`)]);

    const res = await GET(request(), context("x"));

    expect(hits).toHaveLength(1);
    expect(res.status).toBe(502);
  });

  it("307 y 308 conservan método y cuerpo; 302 tras un POST pasa a GET sin cuerpo", async () => {
    upstream([redirect(307, `${BACKEND}/api/a/`), new Response("{}", { status: 200 })]);
    await POST(request("POST", '{"a":1}'), context("x"));
    expect(hits[1].method).toBe("POST");
    expect(hits[1].body).toBeDefined();

    hits = [];
    jest.restoreAllMocks();
    upstream([redirect(302, `${BACKEND}/api/b/`), new Response("{}", { status: 200 })]);
    await POST(request("POST", '{"a":1}'), context("x"));
    expect(hits[1].method).toBe("GET");
    expect(hits[1].body).toBeUndefined();
    expect(hits[1].headers.get("content-type")).toBeNull();
  });

  it("no gira para siempre", async () => {
    upstream([() => redirect(302, `${BACKEND}/api/x/`)]);

    const res = await GET(request(), context("x"));

    expect(res.status).toBe(502);
    expect(hits.length).toBeLessThanOrEqual(6);
  });
});

describe("lo que el proxy pide al backend", () => {
  it("pide las redirecciones sin seguirlas él solo", async () => {
    const spy = jest.spyOn(global, "fetch").mockResolvedValue(new Response("{}", { status: 200 }));

    await GET(request(), context("x"));

    expect((spy.mock.calls[0][1] as RequestInit).redirect).toBe("manual");
  });
});
