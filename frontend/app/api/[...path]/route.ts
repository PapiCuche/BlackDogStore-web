/**
 * Catch-all proxy Route Handler: /api/* → Django API
 *
 * Why a Route Handler instead of next.config rewrites:
 * - rewrites in Turbopack dev mode can conflict with trailing-slash normalization
 * - Route Handlers are explicit code, not config, so they behave consistently
 *
 * Key behaviours:
 * - Always appends trailing slash to the Django path (avoids APPEND_SLASH redirect/error)
 * - Follows redirects server-side (browser never sees cross-origin Django URLs)
 * - Forwards all request headers including cookies (auth) and X-CSRFToken (CSRF)
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
]);

type RouteContext = { params: Promise<{ path: string[] }> };

async function proxy(req: NextRequest, ctx: RouteContext): Promise<NextResponse> {
  const { path } = await ctx.params;
  const segments = path ?? [];

  // Always add trailing slash — Django's APPEND_SLASH=True expects it, and DRF
  // raises a RuntimeError in DEBUG mode when a POST arrives without trailing slash.
  const djangoPath = segments.join("/") + "/";
  const targetUrl = `${BACKEND_API}/${djangoPath}${req.nextUrl.search}`;

  // Forward all safe headers (cookies, CSRF, content-type, etc.)
  const forwardHeaders = new Headers();
  req.headers.forEach((value, key) => {
    const lower = key.toLowerCase();
    if (!HOP_BY_HOP.has(lower) && lower !== "host") {
      forwardHeaders.set(lower, value);
    }
  });

  let body: ArrayBuffer | undefined;
  if (!["GET", "HEAD"].includes(req.method.toUpperCase())) {
    body = await req.arrayBuffer();
  }

  let upstream: Response;
  try {
    upstream = await fetch(targetUrl, {
      method: req.method,
      headers: forwardHeaders,
      body,
      // Follow Django redirects server-side so the browser never receives a
      // cross-origin 301 pointing to http://127.0.0.1:8000
      redirect: "follow",
    });
  } catch (err) {
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
