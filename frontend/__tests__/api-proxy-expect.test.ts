/**
 * @jest-environment node
 */

/**
 * DEV-PROXY-UPLOAD — el proxy `/api/*` no reenvía `Expect`.
 *
 * Un cliente que envía más de 1 MiB puede anunciar `Expect: 100-continue`
 * (curl lo hace). El proxy reenviaba la cabecera tal cual y el `fetch` de Node
 * la rechaza antes de conectar: «fetch failed», y de ahí un 502 «Backend no
 * disponible» para una subida que el backend habría aceptado. `Expect` es un
 * acuerdo entre el cliente y ESTE servidor; no se propaga al siguiente salto.
 */

import { NextRequest } from "next/server";

type Handler = (req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) => Promise<Response>;
let POST: Handler;
let sent: Headers[];

beforeAll(async () => {
  jest.resetModules();
  ({ POST } = (await import("@/app/api/[...path]/route")) as unknown as { POST: Handler });
});

beforeEach(() => {
  sent = [];
  jest.spyOn(global, "fetch").mockImplementation(async (_input: RequestInfo | URL, init?: RequestInit) => {
    const headers = new Headers(init?.headers);
    sent.push(headers);
    // Lo que hace el `fetch` de Node con esa cabecera.
    if (headers.has("expect")) throw new TypeError("fetch failed");
    return new Response("{}", { status: 201, headers: { "content-type": "application/json" } });
  });
});

afterEach(() => jest.restoreAllMocks());

it("una subida que anuncia Expect: 100-continue llega al backend", async () => {
  const request = new NextRequest("http://localhost:3000/api/admin/storefront/images", {
    method: "POST",
    body: new Uint8Array(2048),
    headers: { "content-type": "application/octet-stream", expect: "100-continue", "x-csrftoken": "t" },
  });

  const response = await POST(request, { params: Promise.resolve({ path: ["admin", "storefront", "images"] }) });

  expect(response.status).toBe(201);
  expect(sent).toHaveLength(1);
  expect(sent[0].has("expect")).toBe(false);
  // Lo demás sigue viajando.
  expect(sent[0].get("x-csrftoken")).toBe("t");
  expect(sent[0].get("content-type")).toBe("application/octet-stream");
});
