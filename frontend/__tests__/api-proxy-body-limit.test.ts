/**
 * @jest-environment node
 */

/**
 * FE-AUTH-05 — el proxy `/api/*` no guarda en memoria un cuerpo sin tope.
 *
 * El handler leía el cuerpo entero (`req.arrayBuffer()`) antes de reenviarlo.
 * Cualquiera, sin sesión, podía enviar un cuerpo de cualquier tamaño a
 * cualquier ruta bajo `/api/` y el proceso de Next lo retenía completo: el
 * backend tiene sus topes, pero se aplican después, cuando la memoria ya está
 * gastada aquí.
 *
 * Se comprueba el comportamiento, no la constante: lo que pasa del tope no
 * llega al backend, y el proxy deja de leer en cuanto lo sabe.
 */

import { NextRequest } from "next/server";

const LIMIT = 1024;
const URL_ = "http://localhost:3000/api/admin/products";

type Handler = (req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) => Promise<Response>;
let POST: Handler;
let GET: Handler;
let forwarded: Array<{ url: string; body: ArrayBuffer | undefined }>;

const context = () => ({ params: Promise.resolve({ path: ["admin", "products"] }) });

/** Un cuerpo que llega a trozos y cuenta cuántos se le pidieron. */
function chunked(chunkBytes: number, chunks: number) {
  const state = { pulled: 0 };
  const stream = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (state.pulled >= chunks) return controller.close();
      state.pulled += 1;
      controller.enqueue(new Uint8Array(chunkBytes).fill(97));
    },
  });
  return { stream, state };
}

function post(body: BodyInit, headers: Record<string, string> = {}) {
  // `duplex` es obligatorio para enviar un cuerpo en streaming y no está en los tipos.
  const init = { method: "POST", body, headers, duplex: "half" };
  return new NextRequest(URL_, init as ConstructorParameters<typeof NextRequest>[1]);
}

beforeAll(async () => {
  process.env.API_PROXY_MAX_BODY_BYTES = String(LIMIT);
  jest.resetModules();
  ({ POST, GET } = (await import("@/app/api/[...path]/route")) as unknown as { POST: Handler; GET: Handler });
});

afterAll(() => {
  delete process.env.API_PROXY_MAX_BODY_BYTES;
});

beforeEach(() => {
  forwarded = [];
  jest.spyOn(global, "fetch").mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    forwarded.push({ url: String(input), body: init?.body as ArrayBuffer | undefined });
    return new Response("{}", { status: 200, headers: { "content-type": "application/json" } });
  });
});

afterEach(() => jest.restoreAllMocks());

describe("el proxy pone tope al cuerpo que acepta", () => {
  it("rechaza con 413 un cuerpo que declara más de lo permitido, sin leerlo ni llamar al backend", async () => {
    const { stream, state } = chunked(256, 1000);
    const res = await POST(post(stream, { "content-length": String(LIMIT + 1) }), context());

    expect(res.status).toBe(413);
    expect(forwarded).toEqual([]);
    expect(state.pulled).toBeLessThanOrEqual(1);
  });

  it("rechaza un cuerpo sin tamaño declarado en cuanto pasa del tope, sin leerlo entero", async () => {
    const { stream, state } = chunked(256, 1000);
    const res = await POST(post(stream), context());

    expect(res.status).toBe(413);
    expect(forwarded).toEqual([]);
    // 1024 bytes caben en cuatro trozos; hace falta el quinto para saberlo.
    expect(state.pulled).toBeLessThan(10);
  });

  it("no se fía de un tamaño declarado menor que el real", async () => {
    const { stream } = chunked(256, 1000);
    const res = await POST(post(stream, { "content-length": "10" }), context());

    expect(res.status).toBe(413);
    expect(forwarded).toEqual([]);
  });

  it("explica el rechazo en JSON", async () => {
    const res = await POST(post("x".repeat(LIMIT + 1)), context());

    expect(res.status).toBe(413);
    expect(res.headers.get("content-type")).toContain("application/json");
    expect(await res.json()).toEqual({ detail: expect.any(String) });
  });
});

describe("el proxy sigue reenviando lo que cabe", () => {
  it("reenvía intacto un cuerpo que mide justo el tope", async () => {
    const payload = "b".repeat(LIMIT);
    const res = await POST(post(payload, { "content-type": "text/plain" }), context());

    expect(res.status).toBe(200);
    expect(forwarded).toHaveLength(1);
    expect(new TextDecoder().decode(forwarded[0].body)).toBe(payload);
  });

  it("reenvía intacto un cuerpo que llega a trozos", async () => {
    const { stream } = chunked(100, 5);
    const res = await POST(post(stream), context());

    expect(res.status).toBe(200);
    expect(new TextDecoder().decode(forwarded[0].body)).toBe("a".repeat(500));
  });

  it("una lectura no envía cuerpo", async () => {
    const res = await GET(new NextRequest(URL_), context());

    expect(res.status).toBe(200);
    expect(forwarded[0].body).toBeUndefined();
  });
});
