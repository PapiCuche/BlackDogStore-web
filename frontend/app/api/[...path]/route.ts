/**
 * Catch-all proxy Route Handler: /api/* → Django API
 *
 * Why a Route Handler instead of next.config rewrites:
 * - rewrites in Turbopack dev mode can conflict with trailing-slash normalization
 * - Route Handlers are explicit code, not config, so they behave consistently
 *
 * Key behaviours:
 * - Always appends trailing slash to the Django path (avoids APPEND_SLASH redirect/error)
 * - Follows the backend's own redirects server-side; a redirect to another
 *   origin is returned to the browser instead (FE-AUTH-06)
 * - Forwards request headers including cookies (auth) and X-CSRFToken (CSRF),
 *   but STRIPS network-identity headers the browser must not be able to set
 * - Returns all response headers including Set-Cookie (JWT cookie flow)
 * - Handles GET, POST, PUT, PATCH, DELETE
 */

import { NextRequest, NextResponse } from "next/server";

const BACKEND_API = (
  process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000/api"
).replace(/\/$/, "");

// Headers that must not be forwarded across a proxy boundary
const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailers",
  "transfer-encoding",
  "upgrade",
  // `Expect: 100-continue` es un acuerdo entre el cliente y ESTE servidor. El
  // `fetch` de Node no lo admite y falla antes de conectar: reenviarlo
  // convertía una subida de más de 1 MiB hecha con curl en un 502.
  "expect",
]);

/**
 * Network-identity headers — Phase 0.3 / P0-B.
 *
 * These claim to say who the caller is or how they connected. Arriving here
 * they came from a BROWSER, so they say whatever the browser wanted them to
 * say. This handler used to forward every header it received except hop-by-hop
 * and `host`, which meant a page could send
 *
 *     X-Forwarded-For: 1.2.3.4
 *
 * and Django would take it as the client's address — a different value on each
 * request produced a fresh rate-limit bucket each time, and the audit log
 * recorded whatever address the caller chose.
 *
 * WHAT THIS DOES NOT DO
 * ---------------------
 * It does not replace them with the real client IP, because this process does
 * not know it: `NextRequest` exposes no connection address (the `ip` property
 * was removed), so the only IPs available here are the ones in these very
 * headers. Inventing a value from an untrusted header would move the problem
 * rather than fix it.
 *
 * So the headers are simply removed, and Django falls back to REMOTE_ADDR —
 * the peer that actually opened the socket. Django is the authority
 * (`store/client_ip.py`); this is defence in depth on the browser path, and it
 * has to be, because the backend is reachable without passing through here.
 */
const CLIENT_IDENTITY_HEADERS = new Set([
  "forwarded",
  "x-forwarded-for",
  "x-forwarded-host",
  "x-forwarded-proto",
  "x-forwarded-port",
  "x-real-ip",
  "x-client-ip",
  "x-cluster-client-ip",
  "true-client-ip",
  "cf-connecting-ip",
  "fastly-client-ip",
  "fly-client-ip",
]);

/**
 * A BODY HAS A CEILING — FE-AUTH-05.
 *
 * This handler read the whole request body into memory before forwarding it,
 * with no limit: anyone, signed in or not, could post a body of any size to any
 * path under /api/ and this process held all of it. The backend has its own
 * limits, but they apply after the memory is already spent here.
 *
 * The ceiling sits above the largest body the backend accepts — a service
 * evidence photo, 25 MB by default (`SERVICE_EVIDENCE_MAX_UPLOAD_BYTES`), plus
 * its multipart envelope. A deployment that raises that limit raises this one
 * with `API_PROXY_MAX_BODY_BYTES`.
 */
const DEFAULT_MAX_BODY_BYTES = 32 * 1024 * 1024;

const MAX_BODY_BYTES = (() => {
  const configured = Number(process.env.API_PROXY_MAX_BODY_BYTES);
  return Number.isFinite(configured) && configured > 0 ? Math.floor(configured) : DEFAULT_MAX_BODY_BYTES;
})();

function bodyTooLarge(): NextResponse {
  return new NextResponse(
    JSON.stringify({ detail: "El contenido enviado es demasiado grande." }),
    { status: 413, headers: { "content-type": "application/json" } },
  );
}

/**
 * Reads the body up to the ceiling and returns `null` when it goes past it.
 *
 * `Content-Length` is only a shortcut to refuse early: it is the sender's
 * claim, and a chunked body carries none. The count of bytes actually read is
 * what decides, and reading stops at the first chunk that crosses the line.
 */
async function readBody(req: NextRequest): Promise<ArrayBuffer | null> {
  const declared = Number(req.headers.get("content-length"));
  if (Number.isFinite(declared) && declared > MAX_BODY_BYTES) return null;

  if (!req.body) {
    const whole = await req.arrayBuffer();
    return whole.byteLength > MAX_BODY_BYTES ? null : whole;
  }

  const reader = req.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > MAX_BODY_BYTES) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }

  const whole = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    whole.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return whole.buffer;
}

/**
 * REDIRECTS ARE FOLLOWED BY HAND — FE-AUTH-06.
 *
 * This handler used `redirect: "follow"`, so whatever the backend redirected to
 * was fetched from here with the visitor's forwarded headers. One redirect does
 * leave the site: with evidence photos in external storage the backend answers
 * 302 to a signed URL at that provider, and the proxy downloaded it itself,
 * handing the provider the visitor's `X-CSRFToken` along the way.
 *
 * Now:
 *   - a redirect to ANOTHER origin is not fetched. It goes back to the browser,
 *     which follows it alone and carries nothing of ours;
 *   - a redirect inside the backend is followed, with the session, only while
 *     it stays under the API prefix. Anything else under the backend origin is
 *     a route this proxy does not serve (see FE-AUTH-01) and is refused;
 *   - a few hops at most.
 */
const MAX_REDIRECTS = 3;
const BACKEND_ORIGIN = new URL(BACKEND_API).origin;
const BACKEND_PREFIX = `${new URL(BACKEND_API).pathname.replace(/\/$/, "")}/`;

class RedirectRefused extends Error {}

async function fetchFollowingOwnRedirects(
  url: string,
  method: string,
  headers: Headers,
  body: ArrayBuffer | undefined,
): Promise<Response> {
  let currentUrl = url;
  let currentMethod = method;
  let currentBody = body;

  for (let hop = 0; hop <= MAX_REDIRECTS; hop += 1) {
    const res = await fetch(currentUrl, {
      method: currentMethod,
      headers,
      body: currentBody,
      redirect: "manual",
    });

    const location = res.headers.get("location");
    if (res.status < 300 || res.status >= 400 || !location) return res;

    const next = new URL(location, currentUrl);
    // Off-site: hand it to the browser untouched.
    if (next.origin !== BACKEND_ORIGIN) return res;
    if (!next.pathname.startsWith(BACKEND_PREFIX)) {
      throw new RedirectRefused(`outside the API prefix: ${next.pathname}`);
    }

    // What a browser does: 307/308 repeat the request, the rest become a GET.
    if (res.status !== 307 && res.status !== 308) {
      currentMethod = "GET";
      currentBody = undefined;
      // The body is gone; the headers that described it go with it.
      headers.delete("content-length");
      headers.delete("content-type");
    }
    currentUrl = next.toString();
  }

  throw new RedirectRefused("too many redirects");
}

type RouteContext = { params: Promise<{ path: string[] }> };

/**
 * A SEGMENT IS ONE SEGMENT — FE-AUTH-01.
 *
 * Next hands this handler the catch-all already SPLIT and then DECODED piece by
 * piece (`route-matcher`: `match.split('/').map(decode)`). So a `%2f` the
 * browser sent survives as a real slash INSIDE one segment, and `%2e%2e`
 * arrives as `..`. Joining those back together and handing the string to
 * `fetch` let WHATWG resolve the dot segments, and the destination left the
 * prefix this file exists to serve:
 *
 *     GET /api/..%2fadmin/login/   →  http://127.0.0.1:8000/admin/login/
 *     GET /api/..%2fstatic/…       →  the backend's static files
 *
 * both answered 200 through the storefront origin, with the visitor's cookies
 * forwarded and Django's csrftoken set on it. The backend may be reachable only
 * through here — that is why this handler strips network-identity headers at
 * all — so the escape published routes nobody published.
 *
 * The check is on the DECODED segment, which is the only place the separator is
 * visible, and it refuses rather than sanitising: no Django route takes a
 * `<path:>` converter, so a segment containing a separator (or a dot segment)
 * never addresses anything legitimate. Re-encoding instead would send Django a
 * literal `%2f` it would answer 404 for, hiding the defect instead of naming it.
 */
const SEPARATOR_IN_SEGMENT = /[/\\]/;

function escapesPrefix(segments: string[]): boolean {
  return segments.some(
    (segment) => segment === "." || segment === ".." || SEPARATOR_IN_SEGMENT.test(segment),
  );
}

async function proxy(req: NextRequest, ctx: RouteContext): Promise<NextResponse> {
  const { path } = await ctx.params;
  const segments = path ?? [];

  if (escapesPrefix(segments)) {
    return new NextResponse(
      JSON.stringify({ detail: "Ruta no válida." }),
      { status: 400, headers: { "content-type": "application/json" } },
    );
  }

  // Always add trailing slash — Django's APPEND_SLASH=True expects it, and DRF
  // raises a RuntimeError in DEBUG mode when a POST arrives without trailing slash.
  const djangoPath = segments.join("/") + "/";
  const targetUrl = `${BACKEND_API}/${djangoPath}${req.nextUrl.search}`;

  // Forward functional headers (cookies, CSRF, content-type, accept, …) and
  // drop the ones that assert network identity.
  const forwardHeaders = new Headers();
  req.headers.forEach((value, key) => {
    const lower = key.toLowerCase();
    if (HOP_BY_HOP.has(lower)) return;
    if (lower === "host") return;
    if (CLIENT_IDENTITY_HEADERS.has(lower)) return;
    forwardHeaders.set(lower, value);
  });

  let body: ArrayBuffer | undefined;
  if (!["GET", "HEAD"].includes(req.method.toUpperCase())) {
    const read = await readBody(req);
    if (read === null) return bodyTooLarge();
    body = read;
  }

  let upstream: Response;
  try {
    upstream = await fetchFollowingOwnRedirects(targetUrl, req.method, forwardHeaders, body);
  } catch (err) {
    if (err instanceof RedirectRefused) {
      console.error("[proxy] Redirect refused", { targetUrl, reason: err.message });
      return new NextResponse(
        JSON.stringify({ detail: "Backend no disponible." }),
        { status: 502, headers: { "content-type": "application/json" } },
      );
    }
    // This runs server-side — console.error here does NOT trigger the browser overlay
    console.error("[proxy] Upstream unreachable", { targetUrl, err: String(err) });
    return new NextResponse(
      JSON.stringify({ detail: "Backend no disponible." }),
      { status: 502, headers: { "content-type": "application/json" } },
    );
  }

  // Build response: start without headers so we can copy them one by one
  const res = new NextResponse(upstream.body, { status: upstream.status });

  upstream.headers.forEach((value, key) => {
    const lower = key.toLowerCase();
    if (HOP_BY_HOP.has(lower)) return;
    if (lower === "set-cookie") {
      // append preserves multiple Set-Cookie headers (JWT access + refresh)
      res.headers.append("set-cookie", value);
    } else {
      res.headers.set(key, value);
    }
  });

  return res;
}

export const GET    = (req: NextRequest, ctx: RouteContext) => proxy(req, ctx);
export const POST   = (req: NextRequest, ctx: RouteContext) => proxy(req, ctx);
export const PUT    = (req: NextRequest, ctx: RouteContext) => proxy(req, ctx);
export const PATCH  = (req: NextRequest, ctx: RouteContext) => proxy(req, ctx);
export const DELETE = (req: NextRequest, ctx: RouteContext) => proxy(req, ctx);
