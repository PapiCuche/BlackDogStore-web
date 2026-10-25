/**
 * MIGRATION-D — the accessory count screen's API, typed to the contract of
 * `docs/adr-migration-count.md` (§3 rule D, §5 increment/total/blank/zero, §6).
 *
 * TWO SERVERS, ONE SCREEN. The engine behind this contract is MIGRATION-C and is
 * not built yet. Until it is, `GET …/counts/<id>/` answers the Phase 2D shape,
 * and the screen has to keep today's flow working on it. The switch is
 * `supportsCountContract`: a server that implements C sends `kind` on every
 * count, and only then does the screen call the new routes (`scan/`,
 * `recount/`, `submit/`) or send the new fields (`entry`, `entry_note`). On the
 * old server those routes do not exist and those fields would be ignored at
 * best, so they are never sent.
 *
 * WHAT C MUST ANSWER (the contract as this screen reads it):
 *
 *   POST …/counts/<id>/scan/  { code | product, mode, expected?, step? }
 *     mode "lookup"    → 200 { result: "found", line }        (writes no quantity)
 *     mode "increment" → 200 { result: "incremented", line }   (+step under the line's lock)
 *       `expected` is the value the screen SHOWED (null = «no contado»). If the
 *       line holds something else → 409 { code: "expected_mismatch", line } and
 *       NOTHING is added. The screen never retries a 409 by itself.
 *     unknown code     → 404 { code: "not_found", detail }
 *     serialized       → 400 { code: "serialized_product", detail, product? }
 *     line must be recounted first → 409 { code: "recount_required", line }
 *
 *   PUT …/counts/<id>/items/  [{ product, physical_quantity, note?, entry?, entry_note? }]
 *     The existing additive endpoint. `entry` = "recount" | "correction" is only
 *     needed for a line whose observation window is not empty; without it the
 *     server answers 409 { code: "entry_required", products }.
 *
 *   POST …/counts/<id>/recount/ { products: [id] } → the count; those lines go
 *     back to «no contado» and lose their snapshot (§3.2 rule 3, §5).
 *   POST …/counts/<id>/submit/  → the count, now in `review` (§6).
 *   GET  …/counts/<id>/products/?search= → { results }: the count's own search,
 *     by name or code prefix, of the count's company only.
 *
 * NEVER HERE: approval. Applying a count is MIGRATION-F's screen; this module
 * has no function that calls `…/approve/`.
 *
 * Every request goes through `fetchWithAuth` (session cookies + CSRF). The
 * branch is never sent: the count already belongs to one, resolved by the
 * server.
 */

import { fetchWithAuth } from "../../lib/auth";
import { API_BASE } from "../../lib/api";
import type { InventoryCount, InventoryCountItem } from "../../lib/inventory";

export type CountKind = "regular" | "migration";

/** The server's review verdict for a line (C). Absent on the Phase 2D server. */
export type CountLineReviewState =
  | "pending"
  | "counted"
  | "ambiguous"
  | "overlapping"
  | "excluded";

export type CountLine = Omit<InventoryCountItem, "theoretical_at_start"> & {
  /** C: null until the first quantity — the snapshot is taken on observing. */
  theoretical_at_start: number | null;
  /** The product's code, as stored (text: zeros and case kept). */
  product_code?: string | null;
  /** Every code of the product, so a later scan can be matched locally. */
  product_codes?: string[];
  /**
   * The old system's quantity (MIGRATION-B `reference_quantities`). Reference
   * only: it is never applied and never becomes the counted value.
   */
  reference_quantity?: number | null;
  /** The live theoretical stock of this branch (not the snapshot). */
  current_theoretical?: number | null;
  /** `physical − theoretical_at_start`, the correction approval would apply. */
  expected_difference?: number | null;
  first_counted_at?: string | null;
  last_counted_at?: string | null;
  counted_by_username?: string | null;
  excluded?: boolean;
  exclusion_reason?: string;
  review_state?: CountLineReviewState;
  /** Why the line is ambiguous or blocked, in the server's words. */
  review_detail?: string;
  /** Goods in transit to this branch (§3.2 rule 5). */
  in_transit_quantity?: number;
  /** Paid on the web and not dispatched yet: not to be counted (§3.2 rule 6). */
  paid_pending_dispatch?: number;
  /** Only for `costs.view`; absent otherwise («you may not see it», never zero). */
  difference_value?: string | null;
};

export type CountDetail = Omit<InventoryCount, "items"> & {
  items: CountLine[];
  kind?: CountKind;
  source_job?: number | null;
  counting_started_at?: string | null;
  submitted_at?: string | null;
  submitted_by_username?: string | null;
};

/** True when the server implements MIGRATION-C (it sends `kind`). */
export function supportsCountContract(count: Pick<CountDetail, "kind"> | null | undefined): boolean {
  return typeof count?.kind === "string";
}

export type CountErrorCode =
  | "expected_mismatch"
  | "not_found"
  | "serialized_product"
  | "recount_required"
  | "entry_required"
  | "forbidden"
  | "unknown";

export class CountApiError extends Error {
  readonly status: number;
  readonly code: CountErrorCode;
  /** The line as the server holds it now (on a 409 mismatch: its current value). */
  readonly line?: CountLine;
  readonly products?: number[];
  readonly product?: { id: number; name: string };

  constructor(
    message: string,
    status: number,
    code: CountErrorCode,
    extra: { line?: CountLine; products?: number[]; product?: { id: number; name: string } } = {},
  ) {
    super(message);
    this.name = "CountApiError";
    this.status = status;
    this.code = code;
    this.line = extra.line;
    this.products = extra.products;
    this.product = extra.product;
  }
}

const KNOWN_CODES: readonly CountErrorCode[] = [
  "expected_mismatch",
  "not_found",
  "serialized_product",
  "recount_required",
  "entry_required",
];

async function toError(res: Response, fallback: string): Promise<CountApiError> {
  const body = (await res.json().catch(() => null)) as Record<string, unknown> | null;
  let message = fallback;
  if (body && typeof body.detail === "string" && body.detail) {
    message = body.detail;
  } else if (body && typeof body === "object") {
    const flat = Object.values(body).flat().filter((v) => typeof v === "string" && v);
    if (flat.length) message = flat.join(" ");
  }
  const raw = typeof body?.code === "string" ? (body.code as CountErrorCode) : undefined;
  const code: CountErrorCode =
    raw && KNOWN_CODES.includes(raw)
      ? raw
      : res.status === 403
        ? "forbidden"
        : res.status === 404
          ? "not_found"
          : "unknown";
  if (res.status === 403 && message === fallback) message = "No tienes permisos para este recuento.";
  return new CountApiError(message, res.status, code, {
    line: (body?.line as CountLine | undefined) ?? undefined,
    products: Array.isArray(body?.products) ? (body.products as number[]) : undefined,
    product: (body?.product as { id: number; name: string } | undefined) ?? undefined,
  });
}

async function request<T>(path: string, init: RequestInit | undefined, fallback: string): Promise<T> {
  const res = await fetchWithAuth(`${API_BASE}${path}`, init);
  if (!res.ok) throw await toError(res, fallback);
  return res.json() as Promise<T>;
}

const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });

export function fetchCountDetail(id: number): Promise<CountDetail> {
  return request(`/admin/inventory/counts/${id}/`, undefined, "No se pudo cargar el recuento.");
}

export type ScanRequest = (
  | { code: string; product?: never }
  | { product: number; code?: never }
) & {
  mode: "lookup" | "increment";
  /** Increment only: the value the screen showed. `null` = «no contado». */
  expected?: number | null;
  step?: number;
};

export type ScanResult = { result: "found" | "incremented"; line: CountLine };

/** C only. Never call it on a Phase 2D server (`supportsCountContract`). */
export function scanCount(id: number, body: ScanRequest): Promise<ScanResult> {
  return request(`/admin/inventory/counts/${id}/scan/`, json("POST", body), "No se pudo registrar la lectura.");
}

export type CountLineWrite = {
  product: number;
  /** A total: it replaces what the line held. Never `null` from this screen. */
  physical_quantity: number;
  note?: string;
  /** C only: how to treat a line whose observation window is not empty. */
  entry?: "recount" | "correction";
  entry_note?: string;
};

/**
 * `contract` = the server implements C. On the old server `entry` and
 * `entry_note` are dropped so the body is exactly what Phase 2D accepts.
 */
export function setCountLines(id: number, lines: CountLineWrite[], contract: boolean): Promise<CountDetail> {
  const body = contract
    ? lines
    : lines.map(({ product, physical_quantity, note }) => ({
        product,
        physical_quantity,
        ...(note ? { note } : {}),
      }));
  return request(`/admin/inventory/counts/${id}/items/`, json("PUT", body), "No se pudieron guardar las cantidades.");
}

export type CountSearchHit = {
  id: number;
  name: string;
  is_serialized: boolean;
  is_active?: boolean;
  product_code?: string | null;
  product_codes?: string[];
  physical_quantity?: number | null;
  in_count?: boolean;
};

/** C only: products of the count's company by name or code. */
export async function searchCountProducts(id: number, term: string): Promise<CountSearchHit[]> {
  const qs = new URLSearchParams({ search: term }).toString();
  const data = await request<{ results: CountSearchHit[] }>(
    `/admin/inventory/counts/${id}/products/?${qs}`,
    undefined,
    "No se pudo buscar.",
  );
  return data.results;
}

/** C only: those lines go back to «no contado» and take a new snapshot on the next entry. */
export function recountLines(id: number, products: number[]): Promise<CountDetail> {
  return request(`/admin/inventory/counts/${id}/recount/`, json("POST", { products }), "No se pudo pedir el reconteo.");
}

/** C only: `counting` → `review`. Not an approval: no stock moves. */
export function submitCountForReview(id: number): Promise<CountDetail> {
  return request(`/admin/inventory/counts/${id}/submit/`, { method: "POST" }, "No se pudo enviar a revisión.");
}
