/**
 * SUPPLIER-DEVICE-FLOW-02 · A — the supplier's weekly Excel: what the preview
 * says each row changes, the explicit decision on new commercial prices, and
 * the supplier's own codes for the shop's variants.
 *
 * Kept apart from `internal-api.ts` so this contract reads in one place. The
 * company id selects context only; the server re-validates it.
 */

import { fetchWithAuth } from "../../lib/auth";
import { API_BASE } from "../../lib/api";
import { CatalogApiError, type SupplierImportPreview, type SupplierImportRow } from "./internal-api";

/** What the offer was before the row — sent for rows that update one. The cost only to `costs.view`. */
export type SupplierImportBefore = {
  commercial_price: string;
  confirmed_quantity: number | null;
  committed_quantity: number;
  valid_from: string;
  valid_until: string;
  lead_time_days: number;
  is_active: boolean;
  /** What was left to sell (confirmed − committed); null = unconfirmed. Owner D1. */
  available?: number | null;
  agreed_cost?: string | null;
};

export type SupplierImportChange =
  | "commercial_price" | "available" | "confirmed_quantity" | "valid_from" | "valid_until" | "lead_time_days"
  | "is_active" | "agreed_cost" | "sale_mode";

export type WeeklyImportRow = Omit<SupplierImportRow, "data"> & {
  data: SupplierImportRow["data"] & {
    offer_id?: number;
    product_id?: number;
    before?: SupplierImportBefore;
    changes?: SupplierImportChange[];
    /**
     * Owner D1: the file's quantity is the supplier's NET availability — what is left to sell after
     * applying (confirmed becomes committed + this). Explicit 0: «agotada».
     */
    available?: number | null;
    sold_out?: boolean;
    price_change?: { before: string; after: string };
    /** The supplier code this row will remember for the variant, when applied. */
    map_supplier_code?: string;
    matched_by?: "identifier" | "barcode" | "supplier_code" | "model" | "name";
    /** An unmatched or ambiguous row: a person may pick the variant for this supplier code. */
    mappable?: { supplier_id: number; supplier_code: string };
  };
};

export type WeeklyImportSummary = {
  new: number;
  updated: number;
  unchanged: number;
  sold_out: number;
  errors: number;
  skipped: number;
  price_changes: number;
  mappings: number;
};

export type WeeklyImportPreview = Omit<SupplierImportPreview, "rows"> & {
  /** Absent on an answer of an older server: then the counts are the `rows_*` fields. */
  summary?: WeeklyImportSummary;
  /** The very same file (SHA-256) was already applied: applying it again sets what is left once more. */
  already_applied?: AlreadyApplied | null;
  rows: WeeklyImportRow[];
};

export type PriceDecision = "accept" | "keep";

export type AlreadyApplied = { job_id: number; applied_at: string | null };

export type WeeklyImportResult = {
  status: "applied";
  created: number;
  updated: number;
  unchanged: number;
  sold_out?: number;
  prices_changed?: number;
  prices_kept?: number;
  mappings?: number;
  price_decision?: "accepted" | "kept" | null;
};

export type PriceChangeRow = {
  row_number: number;
  offer_id: number | null;
  supplier: string | null;
  product_name: string | null;
  before: string;
  after: string;
};

export type SupplierProductRef = {
  id: number;
  supplier_id: number;
  supplier_code: string;
  product_id: number;
  product_name: string;
};

/** A refusal of the server, with what it sent besides the message (e.g. `price_changes`). */
export class WeeklyImportError extends CatalogApiError {
  constructor(
    message: string,
    status: number,
    code: string | null,
    field: string | null,
    readonly priceChanges: PriceChangeRow[] = [],
    readonly alreadyApplied: AlreadyApplied | null = null,
  ) {
    super(message, status, code, field);
    this.name = "WeeklyImportError";
  }
}

function withCompany(path: string, companyId?: number | null): string {
  return companyId ? `${path}?company=${encodeURIComponent(String(companyId))}` : path;
}

async function call<T>(path: string, init: RequestInit, fallback: string): Promise<T> {
  const res = await fetchWithAuth(`${API_BASE}${path}`, init);
  const body = await res.json().catch(() => null);
  if (res.ok) return body as T;
  const detail = body && typeof body.detail === "string" ? body.detail : "";
  const message = detail || (res.status === 403 ? "No tienes permisos para esta operación." : fallback);
  throw new WeeklyImportError(
    message,
    res.status,
    body && typeof body.code === "string" ? body.code : null,
    body && typeof body.field === "string" ? body.field : null,
    body && Array.isArray(body.price_changes) ? (body.price_changes as PriceChangeRow[]) : [],
    body && body.already_applied && typeof body.already_applied === "object" ? (body.already_applied as AlreadyApplied) : null,
  );
}

/** Multipart, WITHOUT a Content-Type: `fetchWithAuth` leaves it to the browser (IMPORT-UPLOAD-415). */
export function previewWeeklyImport(file: File, companyId?: number | null): Promise<WeeklyImportPreview> {
  const form = new FormData();
  form.append("file", file);
  return call(withCompany("/admin/supplier-offers/import/preview/", companyId), { method: "POST", body: form },
    "No se pudo leer el archivo.");
}

/**
 * Apply a previewed job. A file that changes a commercial price needs a
 * decision: `accept` writes the new prices, `keep` writes the rest and keeps
 * every current price. Without one the server answers 409
 * `price_confirmation_required`; a job whose offers changed since, 400 `stale`; the very same file
 * applied before, 409 `already_applied` unless `confirmReapply`.
 */
export function applyWeeklyImport(
  id: number, decision: PriceDecision | null, companyId?: number | null, options: { confirmReapply?: boolean } = {},
): Promise<WeeklyImportResult> {
  const body: Record<string, boolean> = decision === "accept" ? { accept_price_changes: true } : decision === "keep" ? { keep_prices: true } : {};
  if (options.confirmReapply) body.confirm_reapply = true;
  return call(withCompany(`/admin/supplier-offers/import/${id}/apply/`, companyId),
    { method: "POST", body: JSON.stringify(body) }, "No se pudo aplicar el archivo.");
}

/** `products.manage`. 201 new, 200 already so; 409 `ref_conflict` when the code names another variant. */
export function saveSupplierProductRef(
  supplierId: number, body: { supplier_code: string; product_id: number; replace?: boolean }, companyId?: number | null,
): Promise<SupplierProductRef> {
  return call(withCompany(`/admin/suppliers/${supplierId}/product-refs/`, companyId),
    { method: "POST", body: JSON.stringify(body) }, "No se pudo guardar el código del proveedor.");
}

/** Counts of a preview, from `summary` or — older answers — from the `rows_*` fields. */
export function summaryOf(preview: WeeklyImportPreview): WeeklyImportSummary {
  if (preview.summary) return preview.summary;
  return {
    new: preview.rows_create, updated: preview.rows_update, unchanged: preview.rows_no_change, sold_out: 0,
    errors: preview.rows_error, skipped: 0, price_changes: 0, mappings: 0,
  };
}

export const CHANGE_LABELS: Record<SupplierImportChange, string> = {
  commercial_price: "Precio comercial",
  available: "Disponible para vender",
  confirmed_quantity: "Cantidad confirmada",
  valid_from: "Vigente desde",
  valid_until: "Vigente hasta",
  lead_time_days: "Entrega (días)",
  is_active: "Activa",
  agreed_cost: "Costo acordado",
  sale_mode: "Bajo pedido",
};
