/**
 * SEO-AUDIT #4 — does a public model or product exist? Asked by `proxy.ts` BEFORE the page renders, because that is
 * the only place where a missing one can still get a real 404: the root `app/loading.tsx` streams every page's
 * shell with a 200 before the page has read anything (see `__tests__/seo-proxy-404.test.ts`).
 *
 * Same public API and same `no-store` as the pages' own reads (`catalog-models-server.ts`).
 *
 * WHICH COMPANY ANSWERS. These reads do not carry the visitor's host: Django sees the internal address and resolves
 * the deployment's fallback tenant (`DEFAULT_STOREFRONT_COMPANY_SLUG`, or the only active company). Django resolves
 * a tenant BY HOST only for a tenant subdomain (`backend/store/tenancy.py · resolve_company_from_host`). On such a
 * host the answer would be about another company, so it is never asked there (`hostSelectsTenant`) and the request
 * goes on as before. Nothing forwards the visitor's host to the API: that would be a new trust of a client header.
 * If the backend starts resolving tenants by other hosts (custom domains), `hostSelectsTenant` must follow it.
 *
 *  - «missing» only when the API answered that there is no such thing: 404 for a model, an empty list for a product;
 *  - «unknown» for anything else (an error status, a timeout, the network, an odd body): the request goes on and the
 *    page decides, as before — a failure is never a 404;
 *  - «found» is remembered for `EXISTENCE_CACHE_TTL_MS`, so a page that is being visited costs the API one extra
 *    read per minute at most, not one per visit. The memory is keyed by host too, so it stays right if the reads
 *    ever carry the tenant. «missing» and «unknown» are never remembered: a model that
 *    was just published must not keep answering 404.
 */

import { apiUrl } from "./api";

export type StorefrontItemKind = "model" | "product";
export type Existence = "found" | "missing" | "unknown";

/** The proxy runs before the page: a hung API must not hold the visit (same cap as the design read). */
const EXISTENCE_READ_TIMEOUT_MS = 2_000;

/** How long a «found» is trusted. Short: within it, an unpublished item is still a streamed not-found (noindex). */
export const EXISTENCE_CACHE_TTL_MS = 60_000;

/** Small enough to never matter; cleared whole when full, as the design reader does. */
const MAX_ENTRIES = 512;

const READ = { cache: "no-store" } as const;

function signal() {
  return AbortSignal.timeout(EXISTENCE_READ_TIMEOUT_MS);
}

/** Drops a body nobody reads, so the connection is freed. */
function discard(res: Response) {
  res.body?.cancel().catch(() => {});
}

export async function fetchExistence(kind: StorefrontItemKind, slug: string): Promise<Existence> {
  try {
    if (kind === "model") {
      const res = await fetch(apiUrl(`/catalog/models/${encodeURIComponent(slug)}/`), { ...READ, signal: signal() });
      discard(res);
      if (res.status === 404) return "missing";
      return res.ok ? "found" : "unknown";
    }
    const res = await fetch(apiUrl(`/products?slug=${encodeURIComponent(slug)}`), { ...READ, signal: signal() });
    if (!res.ok) {
      discard(res);
      return "unknown";
    }
    const data = await res.json();
    if (!Array.isArray(data)) return "unknown";
    return data.length === 0 ? "missing" : "found";
  } catch {
    return "unknown";
  }
}

type CheckerOptions = {
  read?: (kind: StorefrontItemKind, slug: string) => Promise<Existence>;
  ttlMs?: number;
  now?: () => number;
};

/** A checker with a short memory of «found», keyed by request host, kind and slug. */
export function createExistenceChecker({ read = fetchExistence, ttlMs = EXISTENCE_CACHE_TTL_MS, now = Date.now }: CheckerOptions = {}) {
  const found = new Map<string, number>();
  return async function exists(host: string, kind: StorefrontItemKind, slug: string): Promise<Existence> {
    const key = `${host}\n${kind}\n${slug}`;
    const at = found.get(key);
    if (at !== undefined && now() - at < ttlMs) return "found";
    const answer = await read(kind, slug);
    if (answer === "found") {
      if (found.size >= MAX_ENTRIES) found.clear();
      found.set(key, now());
    } else {
      found.delete(key);
    }
    return answer;
  };
}

/**
 * Whether the backend would pick a company FROM this host — the mirror of `resolve_company_from_host` in
 * `backend/store/tenancy.py`: three labels or more, and a first label that is not www/api/admin/app. Such a host may
 * belong to a company other than the one the proxy's reads resolve, so its pages are never declared missing here.
 * An IP address counts as one too (its first label is a «subdomain» to that rule): skipping is the safe side.
 */
export function hostSelectsTenant(host: string): boolean {
  const hostname = host.split(":")[0].trim().toLowerCase();
  const labels = hostname.split(".");
  return labels.length >= 3 && !["www", "api", "admin", "app"].includes(labels[0]);
}

/** The model or product a public path names (`/modelo/<slug>`, `/product/<slug>`), decoded; `null` for anything else. */
export function storefrontItemOf(pathname: string): { kind: StorefrontItemKind; slug: string } | null {
  const match = /^\/(modelo|product)\/([^/]+)$/.exec(pathname);
  if (!match) return null;
  try {
    const slug = decodeURIComponent(match[2]);
    return slug ? { kind: match[1] === "modelo" ? "model" : "product", slug } : null;
  } catch {
    // A malformed escape is not something the API could know: the page decides.
    return null;
  }
}
