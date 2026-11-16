/**
 * P2 · F2 — the catalogue read ON THE SERVER, for the V4 preview.
 *
 * The same public API the browser reads (`lib/catalog-models.ts`) and the same
 * tenant rule as every other server read (`fetchStorefrontConfig`, the V3
 * model metadata): the backend resolves the company, and the response is never
 * kept in a cache shared between hosts (`no-store`).
 *
 * WRAPPED IN React's `cache()`. On the server it lives as long as ONE request:
 * `generateMetadata` and the page ask for the same model and the API is read
 * once. It is never shared between requests, so never between two shops.
 *
 * Never throws: a page must render. «There is no such model» and «the read
 * failed» are told apart, because the page says the first one and lets the
 * browser try the second one again.
 *
 * Only types come from `catalog-models.ts`: its cart writes are browser code.
 */

import { cache } from "react";
import { apiUrl } from "./api";
import type { CatalogModel, CatalogModelList } from "./catalog-models";

/**
 * Review of P2-F2: a hung API must not hold the page. Same cap as `sitemap.ts` and `legal-policies.ts`; an abort lands
 * in the `catch` below, so the page falls back to the browser read (the F1 behaviour).
 */
const SERVER_READ_TIMEOUT_MS = 5000;

export type ServerModelRead =
  | { status: "found"; model: CatalogModel }
  | { status: "missing" }
  | { status: "error" };

export const readCatalogModel = cache(async (slug: string): Promise<ServerModelRead> => {
  try {
    const res = await fetch(apiUrl(`/catalog/models/${encodeURIComponent(slug)}/`), { cache: "no-store", signal: AbortSignal.timeout(SERVER_READ_TIMEOUT_MS) });
    if (res.status === 404) return { status: "missing" };
    if (!res.ok) return { status: "error" };
    const model = await res.json();
    return model && typeof model.name === "string" ? { status: "found", model } : { status: "error" };
  } catch {
    return { status: "error" };
  }
});

/** The published models (both lines), or `null` when the read failed. */
export const readCatalogModelList = cache(async (): Promise<CatalogModelList | null> => {
  try {
    const res = await fetch(apiUrl("/catalog/models/"), { cache: "no-store", signal: AbortSignal.timeout(SERVER_READ_TIMEOUT_MS) });
    if (!res.ok) return null;
    const body = await res.json();
    return {
      lines: body?.lines ?? { new: { label: "", count: 0 }, used: { label: "", count: 0 } },
      results: Array.isArray(body?.results) ? body.results : [],
    };
  } catch {
    return null;
  }
});
