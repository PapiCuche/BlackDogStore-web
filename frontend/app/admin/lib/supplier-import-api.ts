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
  /** B0: the supplier's last registered declaration on this offer (null: none yet). */
  declared_message_date?: string | null;
  last_declared_quantity?: number | null;
  last_confirmed_at?: string | null;
};

/**
 * B0 — the row against its offer's registered declaration: `new` (writes, owner D1 minus what sold since the
 * message), `repetition` (the registered declaration again: writes nothing) or `older` (writes nothing).
 */
export type DeclarationKind = "new" | "repetition" | "older";

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
    /** B0: how the row compares with the declaration registered on its offer. */
    declaration?: DeclarationKind;
    /** B0 review: units sold since the registered declaration that this row would put back on sale. */
    would_restore?: number;
    /** B0: what the supplier declared (null = unknown); `available` is it minus what sold since the message. */
    declared_quantity?: number | null;
    /** B0: units sold against this supplier and variant since the message day began. */
    sold_since_message?: number;
    registered_message_date?: string;
    /** B0: an expired offer this row would switch back on — apply asks for an explicit, counted confirmation. */
    reactivation_requires_review?: boolean;
    /** B0: no quantity over an offer with a confirmed one — apply asks to clear it or keep it. */
    quantity_cleared?: boolean;
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
  /** B0 — absent on an older server. */
  repeated?: number;
  older?: number;
  reactivations?: number;
  quantity_cleared?: number;
};

export type WeeklyImportPreview = Omit<SupplierImportPreview, "rows"> & {
  /** Absent on an answer of an older server: then the counts are the `rows_*` fields. */
  summary?: WeeklyImportSummary;
  /** The very same file (SHA-256) was already applied: applying it again sets what is left once more. */
  already_applied?: AlreadyApplied | null;
  /** B0: the date of the supplier's message the list comes from. */
  message_date?: string | null;
  /** B0: the very same CONTENT (whatever the file) was applied: who, when, and the server's explanation. */
  already_applied_content?: AppliedContent | null;
  /**
   * B0 review: the same rows (validity aside) were applied under an EARLIER message date. Applying needs the
   * explicit confirmation that the supplier re-sent the list (`confirmNewDeclaration` + a note).
   */
  same_rows_applied?: SameRowsApplied | null;
  rows: WeeklyImportRow[];
  /** INV-SNAPSHOT-01: present only for a complete list — what would be switched off on confirmation. */
  snapshot?: SnapshotPreview;
  /** INV-SWITCH-01: what the server decided about AI for this import. Absent on an older server. */
  ai?: ImportAIDecision;
};

/** INV-SWITCH-01 — `used` stays false until the AI adapter exists (INV-AI-01): the file is read the local way. */
export type ImportAIDecision = { requested: boolean; allowed: boolean; reason: string | null; used: boolean };

const AI_REASONS: Record<string, string> = {
  global_off: "La IA no está habilitada en esta instalación",
  company_off: "La IA está desactivada para esta empresa",
  forbidden: "No tienes permiso para usar IA en esta importación",
  not_configured: "IA no configurada",
  unavailable: "El servicio de IA no está disponible",
  quota_exhausted: "La cuota de IA está agotada",
};

/** What the preview says about AI, or null when nobody asked for it. Never claims AI was used when it was not. */
export function aiImportNotice(ai: ImportAIDecision | undefined): string | null {
  if (!ai || !ai.requested) return null;
  if (ai.used) return "El archivo se leyó con ayuda de IA; revisa cada fila antes de aplicar.";
  const why = ai.reason ? AI_REASONS[ai.reason] : undefined;
  return why ? `${why}: el archivo se leyó sin IA.` : "El archivo se leyó sin IA.";
}

export type SnapshotPreview = {
  supplier_id: number;
  supplier_name: string;
  deactivate_count: number;
  with_commitments: number;
  /** It would switch off most of what runs: refused by the server (a message or part of the file is missing). */
  looks_truncated?: boolean;
  absent: { offer_id: number; product_id: number; product_name: string; committed: number; available: number | null }[];
};

export type PriceDecision = "accept" | "keep";

export type AlreadyApplied = { job_id: number; applied_at: string | null };

/** B0 — an earlier APPLIED load: which, when, by whom, and the date of its message. */
export type AppliedReference = AlreadyApplied & { applied_by: string | null; message_date: string | null };
export type AppliedContent = AppliedReference & { detail?: string };

/** B0 review — per offer, the units sold since its registered declaration that the list would sell again. */
export type RestoredUnits = { row_number: number; offer_id: number | null; product_name: string | null; units: number };
export type SameRowsApplied = AppliedReference & { restores?: RestoredUnits[]; detail?: string };

/** B0 — a row the server asked to review before applying (a reactivation, a cleared quantity). */
export type ReviewRow = {
  row_number: number;
  offer_id: number | null;
  product_name: string | null;
  valid_from?: string | null;
  valid_until?: string | null;
};

export type QuantityDecision = "clear" | "keep";

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
  /** INV-SNAPSHOT-01: absent offers switched off by a complete list. */
  deactivated?: number;
  /** B0: rows that repeated the registered declaration / came from an older message: nothing written. */
  repeated?: number;
  older?: number;
  /** B0: supplier declarations registered (verifiable confirmations). */
  confirmed?: number;
  reactivated?: number;
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
    readonly alreadyAppliedContent: AppliedContent | null = null,
    readonly reactivations: ReviewRow[] = [],
    readonly quantityCleared: ReviewRow[] = [],
    readonly sameRowsApplied: SameRowsApplied | null = null,
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
    body && body.already_applied_content && typeof body.already_applied_content === "object"
      ? (body.already_applied_content as AppliedContent) : null,
    body && Array.isArray(body.reactivations) ? (body.reactivations as ReviewRow[]) : [],
    body && Array.isArray(body.quantity_cleared) ? (body.quantity_cleared as ReviewRow[]) : [],
    body && body.same_rows_applied && typeof body.same_rows_applied === "object"
      ? (body.same_rows_applied as SameRowsApplied) : null,
  );
}

/** Multipart, WITHOUT a Content-Type: `fetchWithAuth` leaves it to the browser (IMPORT-UPLOAD-415). */
export function previewWeeklyImport(
  file: File, companyId?: number | null,
  options: { useAi?: boolean; completeSupplierId?: number | null; messageDate?: string } = {},
): Promise<WeeklyImportPreview> {
  const form = new FormData();
  form.append("file", file);
  // B0: the day of the supplier's message (required by the server; never «today» by default).
  if (options.messageDate) form.append("message_date", options.messageDate);
  // INV-SNAPSHOT-01: only when the person said this is the supplier's COMPLETE list.
  if (options.completeSupplierId) {
    form.append("complete_snapshot", "true");
    form.append("supplier_id", String(options.completeSupplierId));
  }
  // INV-SWITCH-01: only when the person asked for it in THIS import; the server decides whether it may.
  if (options.useAi) form.append("use_ai", "true");
  return call(withCompany("/admin/supplier-offers/import/preview/", companyId), { method: "POST", body: form },
    "No se pudo leer el archivo.");
}

/**
 * Apply a previewed job. A file that changes a commercial price needs a
 * decision: `accept` writes the new prices, `keep` writes the rest and keeps
 * every current price. Without one the server answers 409
 * `price_confirmation_required`; a job whose offers changed since, 400 `stale`; the very same file
 * applied before, 409 `already_applied` unless `confirmReapply`. B0: the same CONTENT applied before, 409
 * `already_applied_content` unless `confirmReapply` with a `reapplyReason`; expired offers switched back on, 409
 * `reactivation_confirmation_required` unless `confirmReactivations` is their exact number; quantities cleared,
 * 409 `quantity_decision_required` unless `quantityDecisions` names each offer; the same rows under a later message
 * date, 409 `same_rows_new_date` unless `confirmNewDeclaration` with a `newDeclarationNote`.
 */
export function applyWeeklyImport(
  id: number, decision: PriceDecision | null, companyId?: number | null,
  options: {
    confirmReapply?: boolean; reapplyReason?: string; confirmReactivations?: number;
    /** B0 review: one decision per offer whose quantity the list clears. */
    quantityDecisions?: Record<number, QuantityDecision>;
    confirmNewDeclaration?: boolean; newDeclarationNote?: string;
    snapshot?: { allMessagesIncluded: boolean; deactivations: number };
  } = {},
): Promise<WeeklyImportResult> {
  const body: Record<string, boolean | number | string | Record<string, string>> = decision === "accept" ? { accept_price_changes: true } : decision === "keep" ? { keep_prices: true } : {};
  if (options.confirmReapply) body.confirm_reapply = true;
  if (options.reapplyReason?.trim()) body.reapply_reason = options.reapplyReason.trim();
  if (typeof options.confirmReactivations === "number") body.confirm_reactivations = options.confirmReactivations;
  if (options.quantityDecisions && Object.keys(options.quantityDecisions).length) {
    body.quantity_decisions = Object.fromEntries(Object.entries(options.quantityDecisions).map(([id, v]) => [String(id), v]));
  }
  if (options.confirmNewDeclaration) {
    body.confirm_new_declaration = true;
    body.new_declaration_note = (options.newDeclarationNote ?? "").trim();
  }
  if (options.snapshot) {
    // INV-SNAPSHOT-01: both marks, and the exact count the person confirmed.
    body.all_messages_included = options.snapshot.allMessagesIncluded;
    body.confirm_deactivations = options.snapshot.deactivations;
  }
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

/** B0 — what the person reads for a row's classification (point 7 of the owner's list). */
export function declarationLabel(row: WeeklyImportRow): { text: string; tone: "info" | "warning" | "muted" } | null {
  const data = row.data;
  if (row.action === "error" || row.action === "skip") return null;
  if (data.reactivation_requires_review) return { text: "Reactivación: requiere revisión", tone: "warning" };
  if (data.quantity_cleared) return { text: "Cantidad vacía: decide", tone: "warning" };
  if (data.declaration === "repetition") {
    // A repetition still applies the person's switches («Activa», «Bajo pedido»); its declaration stays frozen.
    return row.action === "update"
      ? { text: "Repetición: sólo cambia «Activa» / «Bajo pedido»", tone: "info" }
      : { text: "Repetición: no cambia nada", tone: "muted" };
  }
  if (data.declaration === "older") return { text: "Mensaje anterior al registrado", tone: "muted" };
  if (data.declaration === "new") return { text: "Declaración nueva", tone: "info" };
  return null;
}

/** A day as the import reads it (AAAA-MM-DD), in the browser's own calendar. */
export function isoDay(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/**
 * B0 — the default validity of a supplier's list: from the day of its MESSAGE to six days later, never from
 * «today». An empty or unreadable message date gives no validity (the screen asks for the date first).
 */
export function validityFromMessage(messageDate: string): { validFrom: string; validUntil: string } | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(messageDate);
  if (!match) return null;
  const start = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  if (Number.isNaN(start.getTime()) || isoDay(start) !== messageDate) return null;
  const end = new Date(start);
  end.setDate(start.getDate() + 6);
  return { validFrom: messageDate, validUntil: isoDay(end) };
}

/** B0 — a message date the server accepts: a real day, not after today. The message, when it is not. */
export function messageDateProblem(messageDate: string, today: Date = new Date()): string | null {
  if (!messageDate) return "Indica la fecha del mensaje del proveedor: con ella se calcula la vigencia y se reconoce una lista ya cargada.";
  if (!validityFromMessage(messageDate)) return "La fecha del mensaje no es válida (AAAA-MM-DD).";
  if (messageDate > isoDay(today)) return "La fecha del mensaje no puede ser posterior a hoy.";
  return null;
}
