import { NextResponse, type NextRequest } from "next/server";
import { createDesignReader, v4Path } from "./app/lib/storefront-design";

/**
 * P2 · F5a — the storefront design switch, applied before anything is rendered.
 *
 * Only two routes pass through here: the home page and a model page. With the shop in `v3` (the default)
 * the request goes on untouched — the V3 pages are not edited by this phase. With `v4`, the visitor is sent
 * to the V4 equivalent with a TEMPORARY redirect (307) that keeps the query string (`?line=used`, campaign
 * tags) and that no browser or CDN may store (`no-store`): choosing V3 again in the panel stops it as soon
 * as the reader's memory expires (`DESIGN_CACHE_TTL_MS`, 15 s).
 *
 * WHY HERE AND NOT IN THE PAGE. The root `loading.tsx` wraps every page in a Suspense boundary, so a
 * `redirect()` from the page is a STREAMED redirect: a 200 with a `<meta http-equiv="refresh">`, which
 * search engines read as permanent. The proxy answers before the render with a real 307.
 *
 * Router prefetches are left out (`missing` below): a page of model cards would otherwise read the config
 * once per card. The navigation itself is a normal request and is redirected.
 */
const readDesign = createDesignReader();

export async function proxy(request: NextRequest) {
  const target = v4Path(request.nextUrl.pathname);
  if (!target) return NextResponse.next();
  const design = await readDesign(request.headers.get("host") ?? "");
  if (design !== "v4") return NextResponse.next();
  const url = request.nextUrl.clone();
  url.pathname = target;
  const response = NextResponse.redirect(url, 307);
  response.headers.set("Cache-Control", "no-store");
  return response;
}

export const config = {
  matcher: [
    { source: "/", missing: [{ type: "header", key: "next-router-prefetch" }] },
    { source: "/modelo/:slug", missing: [{ type: "header", key: "next-router-prefetch" }] },
  ],
};
