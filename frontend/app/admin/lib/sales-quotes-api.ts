/**
 * SALES-QUOTE-01 — cotizaciones comerciales (`/api/admin/sales/quotes/`).
 *
 * Una cotización NO cobra, NO mueve stock y NO es un comprobante. Convertirla es una venta de caja como
 * cualquier otra: la decide el servidor con las reglas de la caja. Aquí sólo se describe lo que se pide;
 * los precios y los totales los calcula siempre el servidor.
 */
import { fetchWithAuth } from "../../lib/auth";
import { API_BASE } from "../../lib/api";

const BASE = `${API_BASE}/admin/sales/quotes/`;

export type QuoteStatus =
  | "draft" | "approved" | "sent" | "accepted" | "rejected" | "expired" | "converted" | "superseded";

export const QUOTE_STATUS_LABEL: Record<QuoteStatus, string> = {
  draft: "Borrador",
  approved: "Aprobada",
  sent: "Enviada",
  accepted: "Aceptada",
  rejected: "Rechazada",
  expired: "Vencida",
  converted: "Convertida en venta",
  superseded: "Reemplazada",
};

/** Los filtros de la lista, en el orden del ciclo. «Vigentes» = todo menos las revisiones reemplazadas. */
export const QUOTE_STATUS_FILTERS: { value: "" | QuoteStatus; label: string }[] = [
  { value: "", label: "Vigentes" },
  { value: "draft", label: "Borradores" },
  { value: "approved", label: "Aprobadas" },
  { value: "sent", label: "Enviadas" },
  { value: "accepted", label: "Aceptadas" },
  { value: "rejected", label: "Rechazadas" },
  { value: "expired", label: "Vencidas" },
  { value: "converted", label: "Convertidas" },
  { value: "superseded", label: "Reemplazadas" },
];

export type QuoteRow = {
  id: number;
  number: string;
  label: string;
  revision: number;
  status: QuoteStatus;
  status_label: string;
  branch: number;
  branch_name: string;
  customer: number | null;
  customer_name: string;
  customer_document: string;
  seller_name: string;
  currency: string;
  total: string;
  valid_until: string | null;
  created_at: string;
  converted_order: number | null;
};

export type QuoteItem = {
  id: number;
  product: number;
  description: string;
  details: string;
  code: string;
  quantity: number;
  unit_price: string;
  line_total: string;
  on_order: boolean;
  supply_reason: string;
  priced_by: "product" | "offer" | "unit";
  is_serialized: boolean;
  unit: { id: number; imei: string; serial_number: string } | null;
};

/**
 * TRADE-IN-QUOTE-00 — el equipo que el cliente ofrece en parte de pago, valorizado de forma REFERENCIAL.
 * No descuenta el precio, no es pago ni compra y no entra al stock: al convertir, la venta se cobra completa.
 * Del IMEI sólo se guardan los últimos 4 dígitos.
 */
export type TradeInCondition = "like_new" | "good" | "fair" | "damaged" | "not_working";

export const TRADE_IN_CONDITIONS: { value: TradeInCondition; label: string }[] = [
  { value: "like_new", label: "Como nuevo" },
  { value: "good", label: "Buen estado" },
  { value: "fair", label: "Con detalles de uso" },
  { value: "damaged", label: "Con daños" },
  { value: "not_working", label: "No funciona" },
];

export const TRADE_IN_HEADING = "Equipo ofrecido en parte de pago (referencial)";
export const TRADE_IN_LEGEND =
  "Valorización referencial sujeta a inspección; no es crédito, pago ni compra. No es comprobante de pago.";
export const TRADE_IN_NOT_APPLIED = "La parte de pago aún no se puede aplicar: la venta se cobra completa";

export type QuoteTradeIn = {
  device_description: string;
  capacity: string;
  color: string;
  condition: TradeInCondition;
  condition_label: string;
  battery_health: number | null;
  accessories: string;
  imei_last4: string;
  customer_asking_price: string | null;
  estimated_valuation: string;
  estimated_balance: string;
  valuation_notes: string;
  valued_by: string;
  valued_at: string | null;
  applicable: false;
};

/** The block as the editor holds it: every field as typed. */
export type QuoteTradeInDraft = {
  device_description: string;
  capacity: string;
  color: string;
  condition: "" | TradeInCondition;
  battery_health: string;
  accessories: string;
  imei_last4: string;
  customer_asking_price: string;
  estimated_valuation: string;
  valuation_notes: string;
};

export type QuoteActions = {
  edit: boolean; revise: boolean; duplicate: boolean; approve: boolean; send: boolean; decide: boolean;
  convert: boolean; print: boolean;
};

export type QuoteDetail = QuoteRow & {
  origin: "new" | "revision" | "duplicate";
  source_quote: number | null;
  customer_document_type: string;
  customer_document_number: string;
  customer_phone: string;
  customer_email: string;
  seller: number | null;
  conditions: string;
  notes: string;
  subtotal: string;
  discount_amount: string;
  discount_source: "none" | "manual" | "promotion" | "coupon";
  discount_type: "" | "amount" | "percent";
  discount_value: string | null;
  discount_reason: string;
  discount_authorized_by: string;
  taxable_amount: string;
  tax_amount: string;
  tax_rate: string;
  items: QuoteItem[];
  created_by: string;
  approved_by: string;
  approved_at: string | null;
  sent_at: string | null;
  decided_by: string;
  decided_at: string | null;
  decision_note: string;
  converted_at: string | null;
  history: { id: number; revision: number; label: string; status: QuoteStatus; total: string; created_at: string }[];
  /** TRADE-IN-QUOTE-00: null when the customer offers no device. Never applied to the sale. */
  trade_in: QuoteTradeIn | null;
  actions: QuoteActions;
};

export type QuoteContext = {
  branches: { id: number; name: string }[];
  default_branch: number | null;
  default_validity_days: number;
  max_validity_days: number;
  seller: { id: number; name: string };
  sellers: { id: number; name: string }[];
  payment_methods: { value: string; label: string }[];
  receipt_options: { value: string; label: string; branches: number[]; enabled: boolean; disabled_reason: string }[];
  can_manage: boolean;
  can_approve: boolean;
  can_convert: boolean;
  can_apply_discount: boolean;
  can_assign_seller: boolean;
  can_order_over_stock: boolean;
  can_search_customers: boolean;
  can_use_device_picker: boolean;
};

export type QuoteProductHit = {
  id: number;
  name: string;
  price: string;
  available: number;
  barcode: string;
  is_serialized: boolean;
  priced_by: "product" | "unit";
  on_order: { price: string; lead_time_days?: number | null } | null;
};

export type QuoteUnitHit = {
  id: number; imei: string; serial_number: string; sale_price: string; grade: string; ready: boolean;
};

/** One line as the editor holds it. Prices are only shown: the server prices every line again. */
export type QuoteDraftLine = {
  key: string;
  product: number;
  name: string;
  quantity: number;
  /** The price the editor shows until the server answers (never sent). */
  shownPrice: string;
  on_order: boolean;
  supply_reason: string;
  unit: number | null;
  unitLabel: string;
  pricedBy: "product" | "offer" | "unit";
};

export type QuoteDraft = {
  branch: number | null;
  customer: number | null;
  customerLabel: string;
  customer_name: string;
  customer_document_type: string;
  customer_document_number: string;
  customer_phone: string;
  customer_email: string;
  seller: number | null;
  lines: QuoteDraftLine[];
  discount_type: "" | "amount" | "percent";
  discount_value: string;
  discount_reason: string;
  valid_until: string;
  conditions: string;
  notes: string;
  trade_in: QuoteTradeInDraft | null;
};

export class QuoteApiError extends Error {
  status: number;
  code: string;
  body: Record<string, unknown>;
  constructor(message: string, status: number, body: Record<string, unknown>) {
    super(message);
    this.status = status;
    this.code = typeof body.code === "string" ? body.code : "";
    this.body = body;
  }
}

async function failure(res: Response, fallback: string): Promise<QuoteApiError> {
  const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
  let message = typeof body.detail === "string" ? body.detail : "";
  if (!message && res.status === 403) message = "No tienes permiso para esta operación.";
  if (!message && res.status === 404) message = "Cotización no encontrada.";
  return new QuoteApiError(message || fallback, res.status, body);
}

async function json<T>(url: string, init: RequestInit | undefined, fallback: string): Promise<T> {
  const res = await fetchWithAuth(url, init);
  if (!res.ok) throw await failure(res, fallback);
  return res.json();
}

function query(params: Record<string, string | number | null | undefined>): string {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    qs.set(key, String(value));
  }
  const text = qs.toString();
  return text ? `?${text}` : "";
}

function post(body: unknown, headers: Record<string, string> = {}): RequestInit {
  return { method: "POST", headers: { "Content-Type": "application/json", ...headers }, body: JSON.stringify(body) };
}

export function newQuoteKey(prefix = "cot"): string {
  const random = typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID().replace(/-/g, "")
    : `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
  return `${prefix}-${random}`.slice(0, 64);
}

// -- reads --------------------------------------------------------------------

export function fetchQuoteContext(): Promise<QuoteContext> {
  return json(`${BASE}context/`, undefined, "No se pudo abrir cotizaciones.");
}

export function fetchQuotes(filters: { status?: string; branch?: number | null; q?: string; page?: number } = {}):
  Promise<{ results: QuoteRow[]; count: number; page: number; page_size: number }> {
  return json(`${BASE}${query({ status: filters.status, branch: filters.branch, q: filters.q, page: filters.page })}`,
    undefined, "No se pudieron cargar las cotizaciones.");
}

export function fetchQuote(id: number): Promise<QuoteDetail> {
  return json(`${BASE}${id}/`, undefined, "No se pudo cargar la cotización.");
}

export function searchQuoteProducts(branch: number, q: string): Promise<{ results: QuoteProductHit[] }> {
  return json(`${BASE}products/${query({ branch, q })}`, undefined, "No se pudo buscar productos.");
}

export function fetchQuoteUnits(branch: number, product: number): Promise<{ results: QuoteUnitHit[] }> {
  return json(`${BASE}units/${query({ branch, product })}`, undefined, "No se pudieron cargar los equipos.");
}

// -- writes -------------------------------------------------------------------

/** The request body for a draft: what was chosen, never a price or a total. */
export function draftBody(draft: QuoteDraft): Record<string, unknown> {
  return {
    branch: draft.branch,
    customer: draft.customer,
    customer_name: draft.customer ? "" : draft.customer_name.trim(),
    customer_document_type: draft.customer ? "" : draft.customer_document_type,
    customer_document_number: draft.customer ? "" : draft.customer_document_number.trim(),
    customer_phone: draft.customer ? "" : draft.customer_phone.trim(),
    customer_email: draft.customer ? "" : draft.customer_email.trim(),
    seller: draft.seller,
    items: draft.lines.map((line) => ({
      product: line.product,
      quantity: line.quantity,
      ...(line.on_order ? { on_order: true, supply_reason: line.supply_reason.trim() } : {}),
      ...(line.unit ? { unit: line.unit } : {}),
    })),
    discount_type: draft.discount_type,
    discount_value: draft.discount_type ? draft.discount_value : null,
    discount_reason: draft.discount_type ? draft.discount_reason.trim() : "",
    valid_until: draft.valid_until || null,
    conditions: draft.conditions,
    notes: draft.notes,
    trade_in: draft.trade_in ? tradeInBody(draft.trade_in) : null,
  };
}

/** The block as the server reads it. Empty optional fields go as null; the valuation is never a discount. */
export function tradeInBody(block: QuoteTradeInDraft): Record<string, unknown> {
  return {
    device_description: block.device_description.trim(),
    capacity: block.capacity.trim(),
    color: block.color.trim(),
    condition: block.condition,
    battery_health: block.battery_health.trim() === "" ? null : Number(block.battery_health),
    accessories: block.accessories.trim(),
    imei_last4: block.imei_last4.trim(),
    customer_asking_price: block.customer_asking_price.trim() === "" ? null : block.customer_asking_price.trim(),
    estimated_valuation: block.estimated_valuation.trim(),
    valuation_notes: block.valuation_notes.trim(),
  };
}

export function emptyTradeIn(): QuoteTradeInDraft {
  return {
    device_description: "", capacity: "", color: "", condition: "", battery_health: "", accessories: "",
    imei_last4: "", customer_asking_price: "", estimated_valuation: "", valuation_notes: "",
  };
}

export function tradeInDraftOf(block: QuoteTradeIn | null): QuoteTradeInDraft | null {
  if (!block) return null;
  return {
    device_description: block.device_description,
    capacity: block.capacity,
    color: block.color,
    condition: block.condition,
    battery_health: block.battery_health === null ? "" : String(block.battery_health),
    accessories: block.accessories,
    imei_last4: block.imei_last4,
    customer_asking_price: block.customer_asking_price ?? "",
    estimated_valuation: block.estimated_valuation,
    valuation_notes: block.valuation_notes,
  };
}

const IDENTIFIER_LIKE = /\d{8,}/;

/** Why the block cannot be saved yet ('' when it can). The server checks it all again. */
export function tradeInProblem(block: QuoteTradeInDraft): string {
  if (!block.device_description.trim()) return "Describe el equipo ofrecido en parte de pago.";
  if (!block.condition) return "Indica el estado declarado del equipo ofrecido.";
  const texts = [block.device_description, block.capacity, block.color, block.accessories, block.valuation_notes];
  if (texts.some((text) => IDENTIFIER_LIKE.test(text.replace(/(?<=\d)[\s.-]+(?=\d)/g, "")))) {
    return "No escribas el IMEI completo ni números de documento: del IMEI sólo los últimos 4 dígitos.";
  }
  if (block.imei_last4.trim() && !/^\d{4}$/.test(block.imei_last4.trim())) {
    return "Del IMEI sólo se anotan los últimos 4 dígitos.";
  }
  if (block.battery_health.trim()) {
    const battery = Number(block.battery_health);
    if (!Number.isInteger(battery) || battery < 0 || battery > 100) return "La batería se indica en % (de 0 a 100).";
  }
  const valuation = Number(block.estimated_valuation);
  if (block.estimated_valuation.trim() === "" || !Number.isFinite(valuation) || valuation < 0) {
    return "Indica la valorización estimada del equipo ofrecido.";
  }
  if (block.customer_asking_price.trim()) {
    const asked = Number(block.customer_asking_price);
    if (!Number.isFinite(asked) || asked < 0) return "El precio que pide el cliente no es válido.";
  }
  return "";
}

/** total − valuation, as shown (the server computes the real one; nothing charges it). */
export function estimatedBalance(total: string | number, valuation: string | number): number {
  const balance = Number(total) - Number(valuation);
  return Number.isFinite(balance) ? Math.round(balance * 100) / 100 : 0;
}

export function createQuote(draft: QuoteDraft): Promise<QuoteDetail> {
  return json(BASE, post(draftBody(draft)), "No se pudo guardar la cotización.");
}

/** A draft is edited in place (200); anything already offered comes back as a NEW revision (201). */
export function updateQuote(id: number, draft: QuoteDraft): Promise<QuoteDetail> {
  return json(`${BASE}${id}/`, { ...post(draftBody(draft)), method: "PATCH" }, "No se pudo guardar la cotización.");
}

export type QuoteTransition = "approve" | "send" | "accept" | "reject" | "duplicate" | "revise";

export function quoteTransition(id: number, action: QuoteTransition, body: Record<string, unknown> = {}):
  Promise<QuoteDetail> {
  return json(`${BASE}${id}/${action}/`, post(body), "No se pudo completar la acción.");
}

export type ConvertRequest = {
  payment_method: string;
  amount_received?: string;
  payment_reference?: string;
  receipt_type?: string | null;
  terms_confirmed: boolean;
  units?: Record<number, { id: number; method: "scanner" | "manual" }[]>;
  price_decision?: "" | "current" | "quoted";
  expected_total?: string;
};

export type ConvertResult = { quote: QuoteDetail; sale: { order_id: number; total: string; created?: boolean } };

export type PriceChanged = {
  quoted_total: string;
  current_total: string;
  can_keep_quoted: boolean;
  detail: string;
};

/** The 409 `price_changed` of a conversion, or null for any other failure. */
export function priceChangedOf(error: unknown): PriceChanged | null {
  if (!(error instanceof QuoteApiError) || error.code !== "price_changed") return null;
  const body = error.body;
  return {
    quoted_total: String(body.quoted_total ?? ""),
    current_total: String(body.current_total ?? ""),
    can_keep_quoted: body.can_keep_quoted === true,
    detail: error.message,
  };
}

export function convertQuote(id: number, request: ConvertRequest, key: string): Promise<ConvertResult> {
  const body: Record<string, unknown> = { ...request };
  if (!request.receipt_type) delete body.receipt_type;
  if (!request.price_decision) delete body.price_decision;
  if (request.payment_method !== "cash") delete body.amount_received;
  return json(`${BASE}${id}/convert/`, post(body, { "Idempotency-Key": key }), "No se pudo convertir en venta.");
}

export function printQuoteTicket(id: number, key: string, printer?: number | null):
  Promise<{ id: number; status: string; printer_name: string }> {
  return json(`${BASE}${id}/print/`, post(printer ? { printer } : {}, { "Idempotency-Key": key }),
    "No se pudo enviar a la impresora.");
}

export async function downloadQuotePdf(id: number, format: "a4" | "ticket80"): Promise<{ blob: Blob; filename: string }> {
  const res = await fetchWithAuth(`${BASE}${id}/pdf/${query({ format })}`);
  if (!res.ok) throw await failure(res, "No se pudo generar el PDF.");
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = /filename="([^"]+)"/.exec(disposition);
  return { blob: await res.blob(), filename: match?.[1] ?? `cotizacion-${id}.pdf` };
}

// -- pure helpers ---------------------------------------------------------------

export function formatMoney(value: string | number, currency = "PEN"): string {
  const amount = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(amount)) return String(value);
  const symbol = currency === "PEN" ? "S/" : currency;
  return `${symbol} ${amount.toFixed(2)}`;
}

export function addDays(isoDate: string, days: number): string {
  const [y, m, d] = isoDate.split("-").map(Number);
  const date = new Date(Date.UTC(y, m - 1, d + days));
  return date.toISOString().slice(0, 10);
}

export function todayIso(now: Date = new Date()): string {
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 10);
}

/** What a line costs as shown before the server prices it: quantity × shown price. */
export function lineTotal(line: Pick<QuoteDraftLine, "quantity" | "shownPrice">): number {
  const price = Number(line.shownPrice);
  return Number.isFinite(price) ? price * line.quantity : 0;
}

/** Why the editor cannot save yet ('' when it can). The server checks it all again. */
export function draftProblem(draft: QuoteDraft, canDiscount: boolean): string {
  if (!draft.branch) return "Elige la sucursal que cotiza.";
  if (draft.lines.length === 0) return "Agrega al menos un producto.";
  if (draft.lines.some((line) => !Number.isInteger(line.quantity) || line.quantity < 1)) {
    return "Cada línea necesita una cantidad entera mayor que cero.";
  }
  if (draft.lines.some((line) => line.pricedBy === "unit" && !line.unit && !line.on_order)) {
    return "Elige el equipo seminuevo de cada línea que se cotiza por equipo.";
  }
  if (draft.discount_type) {
    if (!canDiscount) return "No tienes permiso para aplicar descuentos manuales.";
    const value = Number(draft.discount_value);
    if (!Number.isFinite(value) || value <= 0) return "Indica el valor del descuento.";
    if (draft.discount_type === "percent" && value > 100) return "El porcentaje debe estar entre 0 y 100.";
    if (!draft.discount_reason.trim()) return "Indica el motivo del descuento.";
  }
  if (draft.trade_in) return tradeInProblem(draft.trade_in);
  return "";
}

/** The editor's state for an existing quote (to edit a draft or revise an offered one). */
export function draftFromQuote(quote: QuoteDetail): QuoteDraft {
  return {
    branch: quote.branch,
    customer: quote.customer,
    customerLabel: quote.customer ? quote.customer_name : "",
    customer_name: quote.customer ? "" : quote.customer_name,
    customer_document_type: quote.customer ? "" : quote.customer_document_type,
    customer_document_number: quote.customer ? "" : quote.customer_document_number,
    customer_phone: quote.customer ? "" : quote.customer_phone,
    customer_email: quote.customer ? "" : quote.customer_email,
    seller: quote.seller,
    lines: quote.items.map((item) => ({
      key: `item-${item.id}`,
      product: item.product,
      name: item.description,
      quantity: item.quantity,
      shownPrice: item.unit_price,
      on_order: item.on_order,
      supply_reason: item.supply_reason,
      unit: item.unit?.id ?? null,
      unitLabel: item.unit ? [item.unit.imei && `IMEI ${item.unit.imei}`, item.unit.serial_number && `Serie ${item.unit.serial_number}`].filter(Boolean).join(" · ") : "",
      pricedBy: item.priced_by,
    })),
    discount_type: quote.discount_source === "manual" ? quote.discount_type : "",
    discount_value: quote.discount_source === "manual" ? (quote.discount_value ?? "") : "",
    discount_reason: quote.discount_source === "manual" ? quote.discount_reason : "",
    valid_until: quote.valid_until ?? "",
    conditions: quote.conditions,
    notes: quote.notes,
    trade_in: tradeInDraftOf(quote.trade_in ?? null),
  };
}

export function emptyDraft(context: Pick<QuoteContext, "default_branch" | "default_validity_days">, today = todayIso()):
  QuoteDraft {
  return {
    branch: context.default_branch, customer: null, customerLabel: "", customer_name: "",
    customer_document_type: "", customer_document_number: "", customer_phone: "", customer_email: "",
    seller: null, lines: [], discount_type: "", discount_value: "", discount_reason: "",
    valid_until: addDays(today, context.default_validity_days), conditions: "", notes: "", trade_in: null,
  };
}

/** Serialized lines whose devices the cashier must identify at conversion (the quote names none). */
export function linesNeedingDevices(quote: Pick<QuoteDetail, "items">): QuoteItem[] {
  return quote.items.filter((item) => item.is_serialized && !item.on_order && !item.unit);
}
