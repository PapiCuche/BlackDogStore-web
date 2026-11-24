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
  /** "" when the device entered by its IMEI alone (SERIAL-OPTIONAL-01): name it with `unitIdentifier`. */
  serial_number: string;
  imei: string | null;
  imei2: string | null;
  condition: string;
  condition_label: string;
  status: "available" | "reserved" | "sold" | "written_off" | "voided" | "in_transit";
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
  /**
   * This device's own sale price. Only a USED device is priced one by one: null means it has none yet and it is sold
   * NOWHERE (neither at the till nor online) until somebody with `products.manage` sets it — never at the product's
   * price. A new device sells at its product's price and has none here.
   */
  sale_price?: string | null;
  condition_line?: "new" | "used";
  photos?: UnitPhoto[];
  /** A web buyer's cart or checkout holds it (status `reserved`), until `hold_until`. */
  held_online?: boolean;
  hold_until?: string | null;
  /** INV-RECEIVE-PRICE-03: whether the online shop offers it right now, and why not (codes). Only in the list. */
  web?: UnitWebStatus;
  // USED-READY-01 — a used device is offered online only once inspected (approved), published, priced and photographed.
  inspection_status?: UnitInspection;
  inspection_label?: string;
  /** Staff only: why it was rejected, or what the inspection noted. */
  inspection_note?: string;
  inspected_at?: string | null;
  published_online?: boolean;
  published_at?: string | null;
  /** Days of THIS device's warranty; null = the company's policy; 0 = no warranty. */
  warranty_days?: number | null;
  /** "" = not stated. */
  functional_state?: FunctionalState | "";
  defects?: string;
  accessories?: string;
};

export type UnitInspection = "pending" | "approved" | "rejected";
export type FunctionalState = "full" | "with_faults";

export const INSPECTION_TEXT: Record<UnitInspection, string> = {
  pending: "Pendiente de inspección",
  approved: "Inspección aprobada",
  rejected: "Inspección rechazada",
};

export const FUNCTIONAL_STATE_TEXT: Record<FunctionalState, string> = {
  full: "Sin fallas declaradas",
  with_faults: "Con fallas declaradas",
};

export type UnitWebReason =
  | "not_available" | "no_price" | "no_dispatch_branch" | "not_in_dispatch_branch" | "product_hidden" | "model_hidden"
  | "not_inspected" | "inspection_rejected" | "unit_not_published" | "no_photo" | "photo_not_attested";

export type UnitWebStatus = { offered: boolean; reasons: UnitWebReason[] | string[] };

/** The words of the screen for each reason the shop does not offer a device. */
export const UNIT_WEB_REASON_TEXT: Record<UnitWebReason, string> = {
  not_available: "no está disponible (vendido, apartado o dado de baja)",
  no_price: "no tiene precio de venta",
  no_dispatch_branch: "la empresa no tiene sucursal de despacho para la tienda en línea",
  not_in_dispatch_branch: "no está en la sucursal de despacho de la tienda en línea",
  product_hidden: "su variante no está publicada en la tienda",
  model_hidden: "su modelo está oculto en la tienda",
  not_inspected: "su inspección está pendiente",
  inspection_rejected: "su inspección fue rechazada",
  unit_not_published: "el equipo no está publicado",
  no_photo: "no tiene fotos reales del equipo",
  photo_not_attested: "ninguna de sus fotos está confirmada como de este equipo exacto",
};

export const unitWebReasonText = (code: string) =>
  (UNIT_WEB_REASON_TEXT as Record<string, string>)[code] ?? code;

/** UNIT-PHOTO-02 — which side of THIS device a photo shows. `defect` says what it shows (`defect_note`). */
export type UnitPhotoView = "main" | "front" | "back" | "left" | "right" | "top" | "bottom" | "defect" | "other";

/** The server's labels (`UnitPhoto.VIEW_CHOICES`), in the order the panel offers them. */
export const UNIT_PHOTO_VIEW_TEXT: Record<UnitPhotoView, string> = {
  main: "Principal",
  front: "Frontal",
  back: "Posterior",
  left: "Lateral izquierdo",
  right: "Lateral derecho",
  top: "Borde superior",
  bottom: "Borde inferior",
  defect: "Defecto",
  other: "Otra",
};

/**
 * A photo of one used device, as the panel reads it. UNIT-PHOTO-02: `attested` — somebody with
 * `inventory.units.inspect` confirmed it is of THIS device; `public` — the shop shows it (attested and its file an own
 * photo, verified). Older servers send only `id`, `url` and `alt_text`.
 */
export type UnitPhoto = {
  id: number;
  url: string;
  alt_text: string;
  view?: UnitPhotoView;
  view_label?: string;
  defect_note?: string;
  sort_order?: number;
  is_primary?: boolean;
  attested?: boolean;
  attested_at?: string | null;
  attested_by?: string | null;
  public?: boolean;
};

/** The photos the shop may show: all of them on a server before UNIT-PHOTO-02 (it sends no `public`). */
export const publicPhotoCount = (photos: UnitPhoto[] | undefined) =>
  (photos ?? []).filter((photo) => photo.public !== false).length;

/** What staff may change about a device's public listing. Cost is not here: it stays as it was. */
export type UnitListingWrite = Partial<{
  battery_health: number | null;
  cosmetic_grade: number | null;
  public_notes: string;
  sale_price: string | null;
  // USED-READY-01 — a used device only. The warranty needs `products.manage`, like the price.
  warranty_days: number | null;
  functional_state: FunctionalState | "";
  defects: string;
  accessories: string;
}>;

export type Option = { value: string; label: string };

export type StockUnitPage = {
  results: StockUnit[];
  count: number;
  page: number;
  page_size: number;
  statuses: Option[];
  conditions: Option[];
  /** USED-READY-01: whether this caller may approve or reject an inspection (`inventory.units.inspect`). */
  can_inspect?: boolean;
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
  serial_number: string; imei: string; imei2: string; condition: string;
  /** COST-WRITE-AUTHORITY: sent only by who holds `costs.view`; from anybody else the server answers 403 `costs_forbidden`. */
  cost?: string;
};

/** The inputs of a device the server can point at. */
export type UnitField =
  | "serial_number" | "imei" | "imei2" | "condition" | "cost" | "reason" | "cost_reason"
  // INV-LOAD-01 — what the entry is, who sold it and when it came in.
  | "entry_kind" | "supplier" | "received_on" | "notes";

/**
 * A refusal. `line` is the 1-based device of a batch the server turned down and
 * `field` the input that is wrong, so the form says it next to that input.
 */
export class StockUnitApiError extends Error {
  readonly status: number;
  readonly line: number | null;
  readonly field: UnitField | null;
  /** The server's machine-readable `code`, when it gave one (IMPORT-STALE-01: `preview_expired`, `preview_stale`). */
  readonly code: string | null;

  constructor(
    message: string, status: number, line: number | null = null, field: UnitField | null = null, code: string | null = null,
  ) {
    super(message);
    this.name = "StockUnitApiError";
    this.status = status;
    this.line = line;
    this.field = field;
    this.code = code;
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
    const code = body && typeof body === "object" && typeof body.code === "string" ? body.code : null;
    throw new StockUnitApiError(
      detail || (res.status === 403 ? "No tienes permisos sobre el inventario." : "No se pudo completar la operación."),
      res.status,
      line,
      field,
      code,
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
  line?: "new" | "used" | "";
  /** COMMERCE-ADMIN-UX-01: a model of the catalogue. */
  model?: string;
  /** COMMERCE-ADMIN-UX-01: option ids (capacity, colour…); every one must hold. */
  options?: number[];
  /** INV-RECEIVE-PRICE-03: `no` = the devices without a sale price (a zero is none); `yes` = priced. */
  priced?: "yes" | "no" | "";
  /** USED-READY-01: only the devices in this inspection state. */
  inspection?: UnitInspection | "";
  page?: number;
}): Promise<StockUnitPage> {
  const qs = new URLSearchParams();
  if (params.branch !== undefined) qs.set("branch", String(params.branch));
  for (const key of ["product", "status", "condition", "search", "line", "model", "priced", "inspection"] as const) {
    const value = params[key];
    if (value) qs.set(key, value);
  }
  for (const option of params.options ?? []) qs.append("option", String(option));
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

/**
 * INV-LOAD-01 — `entry_kind`: «purchase» (the default) or «opening», the stock the shop already had (only while that
 * product's Kardex in that branch holds nothing but opening lines). `supplier` and `received_on` (AAAA-MM-DD, never
 * future) are optional.
 */
export type UnitEntryKind = "purchase" | "opening";

export const receiveStockUnits = (body: {
  product_id: number; branch: number; reason: string; units: NewUnit[];
  entry_kind?: UnitEntryKind; supplier?: number | null; received_on?: string;
}) => post<{ results: StockUnit[] }>("/", body);

/** DC2: change a device's listing (battery, grade, public notes, its own price). */
export const updateStockUnitListing = (id: number, body: UnitListingWrite) =>
  call<StockUnit>(`/${id}/`, { method: "PATCH", body: JSON.stringify(body) });

/**
 * A1 — «Corregir costo» of ONE device. `costs.view` + `inventory.adjust`; the
 * reason is mandatory and audited (the amounts are not). A sold device keeps its
 * cost: the server answers 400 and says so.
 */
export const correctStockUnitCost = (id: number, cost: string, reason: string) =>
  call<StockUnit>(`/${id}/`, { method: "PATCH", body: JSON.stringify({ cost, cost_reason: reason }) });

/**
 * USED-READY-01 — approve or reject the inspection of ONE used device (`inventory.units.inspect`). A rejection needs
 * its note and takes the device off the web. Audited.
 */
export const inspectStockUnit = (id: number, decision: "approved" | "rejected", note = "") =>
  post<StockUnit>(`/${id}/inspection/`, { decision, note });

/**
 * USED-READY-01 — put ONE used device on the web or take it off (`products.manage` + `inventory.adjust`). Refused with
 * 409 and every reason (no price, inspection not approved, no photo); the message says them in words.
 */
export const setStockUnitPublication = (id: number, published: boolean) =>
  post<StockUnit>(`/${id}/publication/`, { published });

export type UnitPhotoUpload = { view?: UnitPhotoView; defectNote?: string; altText?: string };

/**
 * DC2 · UNIT-PHOTO-02: add a photo of THIS device, saying which side it shows. No Content-Type: the browser writes the
 * multipart boundary. The server answers with the whole device (its photos in order). The photo is NOT attested.
 */
export function uploadUnitPhoto(id: number, file: File, options: UnitPhotoUpload | string = {}): Promise<StockUnit> {
  const { view, defectNote, altText } = typeof options === "string" ? { altText: options } as UnitPhotoUpload : options;
  const form = new FormData();
  form.append("file", file);
  form.append("alt_text", altText ?? "");
  if (view) form.append("view", view);
  if (view === "defect") form.append("defect_note", defectNote ?? "");
  return call<StockUnit>(`/${id}/photos/`, { method: "POST", body: form });
}

export const deleteUnitPhoto = (id: number, photoId: number) =>
  call<StockUnit>(`/${id}/photos/${photoId}/`, { method: "DELETE" });

/** UNIT-PHOTO-02: its side (a defect says what it shows), its alternative text, or make it the primary. */
export const updateUnitPhoto = (
  id: number, photoId: number,
  body: Partial<{ view: UnitPhotoView; defect_note: string; alt_text: string; is_primary: true }>,
) => call<StockUnit>(`/${id}/photos/${photoId}/`, { method: "PATCH", body: JSON.stringify(body) });

/** UNIT-PHOTO-02: every photo of the device once, in its new order. */
export const reorderUnitPhotos = (id: number, order: number[]) =>
  post<StockUnit>(`/${id}/photos/order/`, { order });

/**
 * UNIT-PHOTO-02: a new file in the same place (order, primary, side). Its confirmation is cleared. The upload route
 * with `replace` (as a finish image): no other address reads files.
 */
export function replaceUnitPhoto(id: number, photoId: number, file: File): Promise<StockUnit> {
  const form = new FormData();
  form.append("file", file);
  form.append("replace", String(photoId));
  return call<StockUnit>(`/${id}/photos/`, { method: "POST", body: form });
}

/**
 * UNIT-PHOTO-02 — «Confirmo que esta foto es de este equipo exacto» (`inventory.units.inspect`), or its withdrawal.
 * Only a confirmed photo reaches the shop. Audited.
 */
export const attestUnitPhoto = (id: number, photoId: number, attested: boolean) =>
  post<StockUnit>(`/${id}/photos/${photoId}/attestation/`, { attested });

export type UnitAction = "reserve" | "release" | "write-off" | "return" | "void" | "move-variant";

/**
 * INV-UNIT-CORRECT-01 (UC-D3) — why an entry is voided: the server's closed list (`stock_unit_services.VOID_REASONS`),
 * plus the person's own words.
 */
export const VOID_REASONS: { value: string; label: string }[] = [
  { value: "typo", label: "Error de digitación (IMEI o serie)" },
  { value: "wrong_variant", label: "Variante equivocada" },
  { value: "wrong_branch", label: "Sucursal equivocada" },
  { value: "duplicate", label: "Ingreso duplicado" },
  { value: "not_arrived", label: "El equipo no llegó" },
];

/** INV-UNIT-CORRECT-01 §5.2 — a variant of the same model and line that a device may move to. */
export type VariantTarget = { id: number; name: string; requires_imei: boolean };

export const fetchVariantTargets = (id: number) =>
  call<{ results: VariantTarget[] }>(`/${id}/move-variant/`).then((body) => body.results);

/** The device in its new variant, with the new label batch (UC-D6) or `label_error` when it could not be made. */
export const moveUnitToVariant = (id: number, product: number, reason: string) =>
  post<StockUnit & { label_batch?: unknown; label_error?: boolean }>(`/${id}/move-variant/`, { product, reason });

export const actOnStockUnit = (id: number, action: UnitAction, reason = "", reasonCode = "") =>
  post<StockUnit>(`/${id}/${action}/`, action === "void" ? { reason, reason_code: reasonCode } : { reason });

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
    /** INV-LOAD-01 */
    supplier?: string;
    received_on?: string;
    notes?: string;
    public_notes?: string;
  };
};

/** INV-LOAD-01 — one shelf (branch × product) before and after the file, as the server simulated it. */
export type UnitImportShelf = {
  branch_id: number;
  branch: string;
  product_id: number;
  product: string;
  adds: number;
  units_before: number;
  units_after: number;
  stock_before: number;
  stock_after: number;
};

/** INV-LOAD-01 — what registering the file would do, read from the shop as it is now. */
export type UnitImportSimulation = {
  entry_type: "initial_stock" | "purchase_entry";
  movements: { initial_stock: number; purchase_entry: number };
  units: { initial_stock: number; purchase_entry: number };
  shelves: UnitImportShelf[];
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
    /** INV-LOAD-01 — absent in a job previewed before it. */
    simulation?: UnitImportSimulation;
    /** INV-LOAD-02 — the load was annulled whole: how many devices, when, by whom and why. */
    voided?: UnitImportVoided;
  };
  /** INV-LOAD-02 — `line`: the line the file loads («new», «used»), or "" for the combined template. */
  options?: { line?: UnitImportLine | ""; entry_kind?: "purchase" | "opening" };
  is_applicable: boolean;
  rows?: UnitImportRow[];
  rows_truncated?: boolean;
};

/**
 * The template of the company chosen in the switcher: its «Sucursal» list is
 * that company's branches. A plain link does not pass through `fetchWithAuth`,
 * so the company is put on the address here.
 */
export function unitImportTemplateUrl(line?: UnitImportLine): string {
  // INV-LOAD-02 — «Stock de equipos nuevos» / «Stock de seminuevos»; without a line, the combined template.
  return withSelectedCompany(`${BASE}/import/template/${line ? `?line=${line}` : ""}`);
}

/** INV-LOAD-02 — the two stock templates: each fixes the line it loads. */
export type UnitImportLine = "new" | "used";

export type UnitImportVoided = { units: number; at: string; by: string; reason: string };

/** INV-LOAD-02 — a device that stops the annulment of a load: found by its label code, never by its IMEI. */
export type UnitImportBlocking = {
  unit_id: number;
  label_code: string;
  product_id: number;
  branch_id: number;
  status: string;
  reason: string;
};

export class UnitImportVoidError extends StockUnitApiError {
  readonly blocking: UnitImportBlocking[];

  constructor(message: string, status: number, code: string | null, blocking: UnitImportBlocking[]) {
    super(message, status, null, null, code);
    this.name = "UnitImportVoidError";
    this.blocking = blocking;
  }
}

/**
 * INV-LOAD-02 — annul every device of an applied load. All or none: a 409 names each device that moved since it
 * entered (`blocking`). A second call answers `changed: false`.
 */
export async function voidUnitImport(
  jobId: number, reason: string, confirmUsed = false,
): Promise<UnitImportJob & { changed: boolean; voided: UnitImportVoided | null }> {
  const res = await fetchWithAuth(`${BASE}/import/${jobId}/void/`, {
    method: "POST", body: JSON.stringify({ reason, ...(confirmUsed ? { confirm_used: true } : {}) }),
  });
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? String(body.detail) : "";
    const code = body && typeof body === "object" && typeof body.code === "string" ? body.code : null;
    const blocking = body && typeof body === "object" && Array.isArray(body.blocking) ? body.blocking : [];
    throw new UnitImportVoidError(
      detail || (res.status === 403 ? "No tienes permisos sobre el inventario." : "No se pudo anular la carga."),
      res.status, code, blocking,
    );
  }
  return body;
}

/** INV-LOAD-02 — the device loads already registered (newest first), for their before/after and annulment. */
export async function fetchRecentUnitLoads(): Promise<(UnitImportJob & { applied_at?: string | null })[]> {
  // `status=applied` on the server: an older registered load stays listed however many previews came after it.
  const res = await fetchWithAuth(`${API_BASE}/admin/imports/?type=units&status=applied`);
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? String(body.detail) : "";
    throw new StockUnitApiError(detail || "No se pudo cargar el historial.", res.status);
  }
  const results: (UnitImportJob & { applied_at?: string | null })[] = body?.results ?? [];
  return results.filter((job) => job.status === "applied");
}

/**
 * INV-LOAD-02 review — what annulling a load would do NOW (writes nothing): devices it would annul, the ones that stop
 * it, how many were already inspected, published or photographed (that work is lost: it is confirmed), and how long
 * the reason may be.
 */
export type UnitImportVoidPreview = {
  units: number;
  blocking: UnitImportBlocking[];
  inspected: number;
  published: number;
  with_photos: number;
  reason_max: number;
  voided: UnitImportVoided | null;
};

export function fetchUnitImportVoidPreview(jobId: number): Promise<UnitImportVoidPreview> {
  return call<UnitImportVoidPreview>(`/import/${jobId}/void/`);
}

/** INV-LOAD-02 — the before/after of an applied load, per branch × product, as a file. */
export function unitImportReportUrl(jobId: number, kind: "csv" | "xlsx"): string {
  return withSelectedCompany(`${BASE}/import/${jobId}/report/?as=${kind}`);
}

export type UnitStockMismatch = {
  branch_id: number;
  branch: string;
  product_id: number;
  product: string;
  quantity: number;
  available: number;
  difference: number;
};

/** INV-LOAD-02 — «Conciliación»: the shelves whose stock is not their available devices (read-only). */
export function fetchUnitReconciliation(): Promise<{ checked: number; mismatches: UnitStockMismatch[] }> {
  return call("/reconciliation/");
}

/** The rows that failed, as a file to read next to the original. */
export function unitImportErrorsUrl(jobId: number): string {
  // A plain link too: without the company, somebody working in a second one gets a 403.
  return withSelectedCompany(`${API_BASE}/admin/imports/${jobId}/errors.csv/`);
}

/** Stage the file. Registers nothing: the answer is what WOULD happen to each row. */
export function previewUnitImport(
  file: File,
  defaults: { branch?: number | null; reason?: string; entryKind?: "purchase" | "opening"; line?: UnitImportLine } = {},
): Promise<UnitImportJob> {
  const form = new FormData();
  form.append("file", file);
  if (defaults.branch) form.append("branch", String(defaults.branch));
  if (defaults.reason?.trim()) form.append("reason", defaults.reason.trim());
  // INV-OPENING-01 — a purchase (default) or the devices the shop already had: INITIAL_STOCK in the Kardex.
  form.append("entry_kind", defaults.entryKind ?? "purchase");
  // INV-LOAD-02 — the line the person chose; a stock template says its own and the server holds the two together.
  if (defaults.line) form.append("line", defaults.line);
  // No Content-Type: the browser writes the multipart boundary itself.
  return call<UnitImportJob>("/import/preview/", { method: "POST", body: form });
}

/** Register every staged device — all of the file or none of it. */
export function applyUnitImport(jobId: number): Promise<UnitImportJob> {
  return post<UnitImportJob>(`/import/${jobId}/apply/`);
}
