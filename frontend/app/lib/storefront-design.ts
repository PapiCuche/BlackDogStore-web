/**
 * P2 · F5a — the storefront design switch (`page.storefront_design`, per company).
 *
 * `v3` is every shop's design and the default. `v4` (Manual V4) is chosen by the owner from the panel once
 * the manual is formally adopted. Then, and only then:
 *
 *   - `/` and `/modelo/<slug>` send the visitor to `/v4` and `/v4/modelo/<slug>` with a TEMPORARY redirect
 *     (307), made in `proxy.ts` before anything is rendered;
 *   - the V4 pages are indexed with an absolute canonical (`lib/canonical.ts`);
 *   - the sitemap names the V4 addresses instead of the ones that redirect.
 *
 * Cart, checkout, account and admin stay on V3 in both modes. Choosing V3 again in the panel undoes all of
 * it: the pages and the sitemap read the config on every request, and the proxy keeps the value for at most
 * `DESIGN_CACHE_TTL_MS`.
 *
 * Anything that is not exactly `"v4"` — a missing field (older backend), a failed read, a typo — is V3:
 * the shop that nobody switched never changes.
 */

import { API_BASE } from "./api";

export type StorefrontDesign = "v3" | "v4";

export function designOf(page: { storefront_design?: unknown } | null | undefined): StorefrontDesign {
  return page?.storefront_design === "v4" ? "v4" : "v3";
}

/** The V4 page that replaces a V3 one, or `null` when the route has none (cart, checkout, account, admin…). */
export function v4Path(pathname: string): string | null {
  if (pathname === "/") return "/v4";
  const model = /^\/modelo\/([^/]+)$/.exec(pathname);
  return model ? `/v4/modelo/${model[1]}` : null;
}

/**
 * How long the proxy keeps a shop's design. Short on purpose: it is how long a switch back to V3 can take to
 * stop the redirect. It only spares the API one config read per visit to the home page or a model page.
 */
export const DESIGN_CACHE_TTL_MS = 15_000;

/** The proxy runs before the page: a hung API must not hold the visit. Same idea as `catalog-models-server`. */
const DESIGN_READ_TIMEOUT_MS = 2_000;

/**
 * The design, read from the public storefront config (same API, same tenant rule as `fetchStorefrontConfig`),
 * or `null` when the read failed — a failure is never cached and never means V4.
 */
export async function fetchStorefrontDesign(): Promise<StorefrontDesign | null> {
  try {
    const res = await fetch(`${API_BASE}/storefront/config/`, {
      cache: "no-store",
      signal: AbortSignal.timeout(DESIGN_READ_TIMEOUT_MS),
    });
    if (!res.ok) return null;
    const data = await res.json();
    return designOf(data?.page);
  } catch {
    return null;
  }
}

type ReaderOptions = {
  read?: () => Promise<StorefrontDesign | null>;
  ttlMs?: number;
  now?: () => number;
};

/** Small enough to never matter; a deployment serves a handful of hosts. */
const MAX_HOSTS = 64;

/**
 * A design reader with a short memory, keyed by request host (the tenant is chosen by host). A failed read
 * answers V3 and is not remembered, so the next request tries again.
 */
export function createDesignReader({ read = fetchStorefrontDesign, ttlMs = DESIGN_CACHE_TTL_MS, now = Date.now }: ReaderOptions = {}) {
  const memory = new Map<string, { design: StorefrontDesign; at: number }>();
  return async function readDesign(host: string): Promise<StorefrontDesign> {
    const hit = memory.get(host);
    if (hit && now() - hit.at < ttlMs) return hit.design;
    const design = await read();
    if (design === null) return "v3";
    if (memory.size >= MAX_HOSTS) memory.clear();
    memory.set(host, { design, at: now() });
    return design;
  };
}
