import { NextResponse, type NextRequest } from "next/server";
import { createDesignReader, v4Path } from "./app/lib/storefront-design";
import { isE2eMockedModel } from "./app/lib/e2e-mocked-models";
import { createExistenceChecker, hostSelectsTenant, storefrontItemOf, type StorefrontItemKind } from "./app/lib/storefront-existence";

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
 *
 * SEO-AUDIT #4 — a REAL 404 for a model or product that does not exist, for the same reason: from the page,
 * `notFound()` comes after the root `loading.tsx` has streamed a 200, so it is only a soft 404 with noindex
 * (measured with `next build` + `next start`; see `__tests__/seo-proxy-404.test.ts`). Here, before the render,
 * a page load of `/modelo/<slug>` (shop in V3) or `/product/<slug>` asks the API whether it exists; when the API
 * says it does not, the request is rewritten to an address no page serves, so Next answers 404 with
 * `app/not-found.tsx`. A failed or slow API lets the request through: the page decides, and never calls a
 * failure «not found». Client navigations (RSC requests) are not asked here: the page's own `notFound()` shows
 * the same screen there.
 *
 * Review of #4:
 *  - the API reads do not carry the visitor's host, so they answer for the deployment's fallback company. On a
 *    host from which the backend would pick a company of its own (a tenant subdomain, `hostSelectsTenant`), that
 *    answer could be about another company: the question is not asked and the request goes on as before;
 *  - on a model page the design read and the existence read go out TOGETHER: a hung API costs one timeout (2 s),
 *    not two. With the shop in V4 the existence answer is simply not used (the V4 page decides its own);
 *  - `E2E_MOCKED_MODEL_PREFIX` (never in production): see `app/lib/e2e-mocked-models.ts`.
 */
const readDesign = createDesignReader();
const exists = createExistenceChecker();

/** No page serves it (`_` folders are private in the App Router), so Next renders `app/not-found.tsx` with a 404. */
const NOT_FOUND_REWRITE = "/_storefront-not-found";

/** The model or product this request names, when its existence is worth asking here; otherwise `null`. */
function itemToCheck(request: NextRequest, host: string): { kind: StorefrontItemKind; slug: string } | null {
  if (!["GET", "HEAD"].includes(request.method) || request.headers.has("rsc") || hostSelectsTenant(host)) return null;
  const item = storefrontItemOf(request.nextUrl.pathname);
  if (!item || (item.kind === "model" && isE2eMockedModel(item.slug))) return null;
  return item;
}

export async function proxy(request: NextRequest) {
  const host = request.headers.get("host") ?? "";
  const target = v4Path(request.nextUrl.pathname);
  const item = itemToCheck(request, host);
  const [design, existence] = await Promise.all([
    target ? readDesign(host) : null,
    item ? exists(host, item.kind, item.slug) : null,
  ]);
  if (target && design === "v4") {
    const url = request.nextUrl.clone();
    url.pathname = target;
    const response = NextResponse.redirect(url, 307);
    response.headers.set("Cache-Control", "no-store");
    return response;
  }
  if (existence !== "missing") return NextResponse.next();
  const url = request.nextUrl.clone();
  url.pathname = NOT_FOUND_REWRITE;
  url.search = "";
  return NextResponse.rewrite(url, { status: 404 });
}

export const config = {
  matcher: [
    { source: "/", missing: [{ type: "header", key: "next-router-prefetch" }] },
    { source: "/modelo/:slug", missing: [{ type: "header", key: "next-router-prefetch" }] },
    { source: "/product/:slug", missing: [{ type: "header", key: "next-router-prefetch" }] },
  ],
};
