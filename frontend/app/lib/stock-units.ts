/**
 * SERIALIZED-STOCK — devices tracked by serial number, as part of the stock.
 *
 * This layer validates nothing about an identifier and counts nothing: the
 * server decides whether an IMEI is valid, whether a device may enter, and how
 * many are on the shelf. What it is told to send, it sends as typed.
 */

import { API_BASE } from "./api";
import { fetchWithAuth, withSelectedCompany } from "./auth";

export type StockUnit = {
  id: number;
  product_id: number;
  product_name: string;
  branch_id: number;
  branch_name: string;
  serial_number: string;
  imei: string | null;
  imei2: string | null;
  condition: string;
  condition_label: string;
  status: "available" | "reserved" | "sold" | "written_off";
  status_label: string;
  /** DC1: only present for a caller holding `costs.view`; absent otherwise (not null). */
  cost?: string | null;
  received_at: string;
  sold_at: string | null;
  order_id: number | null;
  notes: string;
  // DC2 — what the shop publishes about a device it sells one by one.
  public_id?: string;
  /** Battery health in %, or null when nobody measured it (never shown as a number then). */
  battery_health?: number | null;
  /** Id of the company's cosmetic grade, or null. */
  cosmetic_grade?: number | null;
  cosmetic_grade_label?: string;
  public_notes?: string;
  /** This device's own price; null = the product's price. */
  sale_price?: string | null;
  condition_line?: "new" | "used";
  photos?: UnitPhoto[];
};

export type UnitPhoto = { id: number; url: string; alt_text: string };

/** What staff may change about a device's public listing. Cost is not here: it stays as it was. */
export type UnitListingWrite = Partial<{
  battery_health: number | null;
  cosmetic_grade: number | null;
  public_notes: string;
  sale_price: string | null;
}>;

export type Option = { value: string; label: string };

export type StockUnitPage = {
  results: StockUnit[];
  count: number;
  page: number;
  page_size: number;
  statuses: Option[];
  conditions: Option[];
};

export type SerializedProduct = { id: number; name: string; requires_imei: boolean; price: string };

/**
 * Any active product, with how it is counted. `can_change_tracking` is the
 * server's own rule told in advance: tracking by serial can only be switched
 * while the product has no stock.
 */
export type UnitProduct = SerializedProduct & {
  is_serialized: boolean;
  stock: number;
  can_change_tracking: boolean;
};

/** ONE physical device. There is no quantity: a second device is a second one of these. */
export type NewUnit = {
  serial_number: string; imei: string; imei2: string; condition: string; cost: string;
};

/** The inputs of a device the server can point at. */
export type UnitField = "serial_number" | "imei" | "imei2" | "condition" | "cost" | "reason";

/**
 * A refusal. `line` is the 1-based device of a batch the server turned down and
 * `field` the input that is wrong, so the form says it next to that input.
 */
export class StockUnitApiError extends Error {
  readonly status: number;
  readonly line: number | null;
  readonly field: UnitField | null;

  constructor(message: string, status: number, line: number | null = null, field: UnitField | null = null) {
    super(message);
    this.name = "StockUnitApiError";
    this.status = status;
    this.line = line;
    this.field = field;
  }
}

const BASE = `${API_BASE}/admin/inventory/units`;

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetchWithAuth(`${BASE}${path}`, init);
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? String(body.detail) : "";
    const line = body && typeof body === "object" && typeof body.line === "number" ? body.line : null;
    const field = body && typeof body === "object" && typeof body.field === "string" ? (body.field as UnitField) : null;
    throw new StockUnitApiError(
      detail || (res.status === 403 ? "No tienes permisos sobre el inventario." : "No se pudo completar la operación."),
      res.status,
      line,
      field,
    );
  }
  return body as T;
}

const post = <T,>(path: string, payload: unknown = {}) =>
  call<T>(path, { method: "POST", body: JSON.stringify(payload) });

export function fetchStockUnits(params: {
  branch: number | "all" | undefined;
  product?: string;
  status?: string;
  condition?: string;
  search?: string;
  /** DC2: `used` lists the second-hand devices. */
  line?: "new" | "used";
  page?: number;
}): Promise<StockUnitPage> {
  const qs = new URLSearchParams();
  if (params.branch !== undefined) qs.set("branch", String(params.branch));
  for (const key of ["product", "status", "condition", "search", "line"] as const) {
    const value = params[key];
    if (value) qs.set(key, value);
  }
  if (params.page && params.page > 1) qs.set("page", String(params.page));
  const query = qs.toString();
  return call<StockUnitPage>(`/${query ? `?${query}` : ""}`);
}

export const fetchSerializedProducts = () =>
  call<{ results: SerializedProduct[] }>("/products/");

/** Every active product and how each is counted — for the registration form. */
export const fetchUnitProducts = () =>
  call<{ results: UnitProduct[] }>("/products/?scope=all");

/** Switch a product to (or from) tracking by serial. Refused while it has stock. */
export const setProductTracking = (productId: number, body: { is_serialized: boolean; requires_imei: boolean }) =>
  post<{ id: number; is_serialized: boolean; requires_imei: boolean }>(
    `/products/${productId}/serialization/`, body,
  );

export const receiveStockUnits = (body: {
  product_id: number; branch: number; reason: string; units: NewUnit[];
}) => post<{ results: StockUnit[] }>("/", body);

/** DC2: change a device's listing (battery, grade, public notes, its own price). */
export const updateStockUnitListing = (id: number, body: UnitListingWrite) =>
  call<StockUnit>(`/${id}/`, { method: "PATCH", body: JSON.stringify(body) });

/** DC2: add a photo of THIS device. No Content-Type: the browser writes the multipart boundary. */
export function uploadUnitPhoto(id: number, file: File, altText = ""): Promise<UnitPhoto> {
  const form = new FormData();
  form.append("file", file);
  form.append("alt_text", altText);
  return call<UnitPhoto>(`/${id}/photos/`, { method: "POST", body: form });
}

export const deleteUnitPhoto = (id: number, photoId: number) =>
  call<unknown>(`/${id}/photos/${photoId}/`, { method: "DELETE" });

export type UnitAction = "reserve" | "release" | "write-off" | "return";

export const actOnStockUnit = (id: number, action: UnitAction, reason = "") =>
  post<StockUnit>(`/${id}/${action}/`, { reason });

// ---------------------------------------------------------------------------
// UNIT-IMPORT — the device upload: one row, one device
// ---------------------------------------------------------------------------

export type UnitImportRow = {
  sheet: string;
  row: number;
  action: "create" | "skip" | "error";
  match_key: string;
  errors: string[];
  warnings: string[];
  data: {
    name?: string;
    branch?: string;
    serial_number?: string;
    imei?: string;
    imei2?: string;
    condition?: string;
    cost?: string;
    reason?: string;
  };
};

/** A staged upload, as the server describes it. Nothing here is computed in the browser. */
export type UnitImportJob = {
  id: number;
  import_type: "units";
  status: "previewed" | "applied" | "failed";
  original_filename: string;
  counts: { total: number; create: number; skip: number; error: number };
  summary: {
    units?: number;
    /** Rows that carry an IMEI under a product not marked «lleva IMEI». Kept, and said. */
    imei_on_unmarked_products?: number;
    applied?: { units: number; movements: number; imei_on_unmarked_products?: number };
  };
  is_applicable: boolean;
  rows?: UnitImportRow[];
  rows_truncated?: boolean;
};

/**
 * The template of the company chosen in the switcher: its «Sucursal» list is
 * that company's branches. A plain link does not pass through `fetchWithAuth`,
 * so the company is put on the address here.
 */
export function unitImportTemplateUrl(): string {
  return withSelectedCompany(`${BASE}/import/template/`);
}

/** The rows that failed, as a file to read next to the original. */
export function unitImportErrorsUrl(jobId: number): string {
  // A plain link too: without the company, somebody working in a second one gets a 403.
  return withSelectedCompany(`${API_BASE}/admin/imports/${jobId}/errors.csv/`);
}

/** Stage the file. Registers nothing: the answer is what WOULD happen to each row. */
export function previewUnitImport(
  file: File,
  defaults: { branch?: number | null; reason?: string } = {},
): Promise<UnitImportJob> {
  const form = new FormData();
  form.append("file", file);
  if (defaults.branch) form.append("branch", String(defaults.branch));
  if (defaults.reason?.trim()) form.append("reason", defaults.reason.trim());
  // No Content-Type: the browser writes the multipart boundary itself.
  return call<UnitImportJob>("/import/preview/", { method: "POST", body: form });
}

/** Register every staged device — all of the file or none of it. */
export function applyUnitImport(jobId: number): Promise<UnitImportJob> {
  return post<UnitImportJob>(`/import/${jobId}/apply/`);
}
