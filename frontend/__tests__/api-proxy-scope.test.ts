/**
 * @jest-environment node
 */

/**
 * FE-AUTH-01 — el proxy `/api/*` no puede salirse de `/api/`.
 *
 * QUÉ PRUEBA ESTO QUE NADA MÁS PRUEBA
 * -----------------------------------
 * `app/api/[...path]/route.ts` es la ÚNICA ruta del origen público hacia el
 * backend, y su contrato dice `/api/*`. El matcher de Next decodifica cada
 * segmento por separado (`route-matcher`: `match.split('/').map(decode)`), así
 * que un `%2f` del navegador llega al handler ya convertido en una barra DENTRO
 * de un segmento y `%2e%2e` llega como `..`. Al unirlos con `join("/")` y pasar
 * el resultado a `fetch`, la normalización de WHATWG resolvía los `..` y el
 * destino se salía del prefijo: `/api/..%2fadmin/login/` servía el login del
 * admin de Django —con las cookies del visitante reenviadas y su `csrftoken`
 * fijado en el origen de la tienda—, y `/api/..%2fstatic/…` sus estáticos.
 *
 * El backend puede ser alcanzable SOLO a través de este proxy (por eso este
 * handler quita las cabeceras de identidad de red), de modo que el escape
 * publicaba en el origen público rutas que nadie publicó.
 *
 * Se comprueba la propiedad, no la lista: ningún destino puede quedar fuera del
 * prefijo, y un segmento con separadores no se reenvía en absoluto.
 */

import type { NextRequest } from "next/server";

import { DELETE, GET, PATCH, POST, PUT } from "@/app/api/[...path]/route";

const BACKEND = "http://127.0.0.1:8000/api";

/** Una petición del navegador, reducida a lo que el handler realmente usa. */
function request(method = "GET", search = "", headers: Record<string, string> = {}) {
  return {
    method,
    headers: new Headers({ cookie: "blackdog_access=token-de-la-victima", ...headers }),
    nextUrl: { search },
    arrayBuffer: async () => new ArrayBuffer(0),
  } as unknown as NextRequest;
}

/** El contexto que Next entrega: los segmentos YA decodificados uno a uno. */
function context(segments: string[]) {
  return { params: Promise.resolve({ path: segments }) };
}

let fetched: string[];

beforeEach(() => {
  fetched = [];
  jest.spyOn(global, "fetch").mockImplementation(async (input: RequestInfo | URL) => {
    fetched.push(String(input));
    return new Response("{}", { status: 200, headers: { "content-type": "application/json" } });
  });
});

afterEach(() => jest.restoreAllMocks());

/**
 * Lo que un navegador conseguía pidiendo estas rutas. Cada entrada es el
 * resultado REAL del matcher de Next para la URL del comentario, comprobado
 * contra el servidor de desarrollo.
 */
const ESCAPES: Array<[string, string[]]> = [
  // GET /api/..%2fadmin/login/
  ["barra codificada simple", ["../admin", "login"]],
  // GET /api/%2e%2e%2fadmin/login/
  ["punto y barra codificados", ["../admin", "login"]],
  // GET /api/x%2f..%2f..%2fadmin/login/
  ["subida desde un segmento válido", ["x/../../admin", "login"]],
  // GET /api/..%5cadmin/login/  (WHATWG convierte \ en /)
  ["barra invertida codificada", ["..\\admin", "login"]],
  // GET /api/..%2fstatic/admin/css/base.css
  ["estáticos del backend", ["../static", "admin", "css", "base.css"]],
  // Segmentos "." y ".." puros, por si el matcher los entrega separados.
  ["segmentos de subida sueltos", ["..", "..", "admin", "login"]],
  ["segmento punto", [".", "admin", "login"]],
];

describe("el proxy no sale del prefijo /api/", () => {
  for (const [label, segments] of ESCAPES) {
    it(`rechaza ${label} — ${JSON.stringify(segments)} — y no llama al backend`, async () => {
      const res = await GET(request(), context(segments));

      expect(fetched).toEqual([]);
      expect(res.status).toBe(400);
    });
  }

  it("nunca construye un destino fuera del prefijo, cualquiera que sea el método", async () => {
    for (const handler of [GET, POST, PUT, PATCH, DELETE]) {
      fetched = [];
      await handler(request(handler === GET ? "GET" : "POST"), context(["../admin", "login"]));
      for (const url of fetched) {
        expect(new URL(url).pathname).toMatch(/^\/api\//);
      }
    }
  });
});

describe("el proxy sigue sirviendo lo que sí es suyo", () => {
  it("reenvía una ruta normal con su query y sus cookies", async () => {
    const res = await GET(request("GET", "?page_size=1"), context(["products"]));

    expect(fetched).toEqual([`${BACKEND}/products/?page_size=1`]);
    expect(res.status).toBe(200);
    const [, init] = (global.fetch as jest.Mock).mock.calls[0];
    expect((init.headers as Headers).get("cookie")).toContain("blackdog_access=");
  });

  it("reenvía rutas anidadas y las que llevan identificadores", async () => {
    await GET(request(), context(["v1", "internal", "black-dog-store", "sales", "pos", "context"]));
    await GET(request(), context(["admin", "orders", "42", "fiscal-document"]));

    expect(fetched).toEqual([
      `${BACKEND}/v1/internal/black-dog-store/sales/pos/context/`,
      `${BACKEND}/admin/orders/42/fiscal-document/`,
    ]);
  });

  it("admite un segmento con caracteres que no son separadores", async () => {
    await GET(request(), context(["products", "iphone-15-pro", "barcodes"]));

    expect(fetched).toEqual([`${BACKEND}/products/iphone-15-pro/barcodes/`]);
  });
});
