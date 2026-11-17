/**
 * INV-OPS-01 — cliente y reglas del asistente «Recibir mercadería».
 *
 * Un único contrato con el servidor (`/admin/inventory/receipts/preview/` y
 * `/confirm/`). TODO lo que cambie el contrato se adapta aquí: las pantallas
 * hablan con `ReceiptDraft`, no con el cuerpo de la petición.
 *
 * Este módulo no decide nada de stock: arma el cuerpo, valida lo evidente
 * antes de molestar al servidor (campos vacíos, repetidos en la misma lista) y
 * guarda la forma de las respuestas. Qué es un IMEI válido, si un equipo ya
 * existe y cuántas etiquetas salen lo decide el servidor.
 *
 * Nada de esto se escribe en `localStorage`/`sessionStorage`: serie, IMEI y
 * costo viven en la memoria de la página (ver `stashDraft`) y, para que una
 * recarga no los pierda, en el borrador del SERVIDOR (INV-DRAFT-01, ver
 * `saveReceiptDraft`): de esa persona en esa empresa, 24 h como máximo.
 */

import { API_BASE } from "../../lib/api";
import { fetchWithAuth } from "../../lib/auth";
import { isEnded, subscribe } from "../../lib/session-state";

// ── Draft (what the screens hold) ─────────────────────────────────────────

export type ReceiptProduct = {
  id: number;
  name: string;
  sku?: string;
  is_serialized: boolean;
  requires_imei: boolean;
  /** `used` opens the condition list on «Usado». */
  condition_line?: "new" | "used";
  /** False for a switched-off article: the server refuses it, the screen says so before the review. */
  is_active?: boolean;
  /** Nothing on any shelf: how it is counted may still change (INV-S5-01, the first reception decides). */
  can_change_tracking?: boolean;
  /**
   * INV-INTAKE-GUARD-01 · the device model this article is a variant of; null (or absent) for an accessory or any
   * article outside the catalogue of models. A variant starts «por equipo».
   */
  product_model_id?: number | null;
};

export type DraftUnit = {
  key: string;
  serial_number: string;
  imei: string;
  imei2: string;
  condition: string;
  cost: string;
  /** Measured battery health, in % as typed. Empty is «no informado»: never guessed. */
  battery: string;
  /** The id of the company's cosmetic grade (`CosmeticGrade`), as a string; empty is none. */
  grade: string;
  /** The device's internal notes (the server keeps up to 300 characters). */
  notes: string;
  /** INV-COM-01A · what the web asks for THIS used device, as typed. Needs `products.manage`; empty is «pending». */
  price: string;
  /** INV-COM-01A · what the buyer reads about this used device (up to 500 characters); internal `notes` stay internal. */
  publicNotes: string;
};

export type DraftLine = {
  key: string;
  product: ReceiptProduct;
  /** Quantity lines only: kept as typed so a half-typed number is never rewritten. */
  quantity: string;
  unitCost: string;
  /** Serialized lines only. */
  units: DraftUnit[];
  /**
   * INV-S5-01 — a product not counted by device, with nothing on any shelf, received device by device: confirming the
   * reception switches it to «por equipo» (owner's decision S5). Ignored for a product already counted by device.
   */
  byUnit?: boolean;
};

/** Whether this line is received device by device (its product is, or this first reception makes it so). */
export const countsByUnit = (line: DraftLine): boolean => line.product.is_serialized || Boolean(line.byUnit);

/** Whether the person may choose to receive this product device by device (it is not yet, and has no stock). */
export const canChooseUnits = (product: ReceiptProduct): boolean =>
  !product.is_serialized && Boolean(product.can_change_tracking);

/** INV-INTAKE-GUARD-01 · a variant of a device model (not an accessory): it is received device by device. */
export const isDeviceVariant = (product: ReceiptProduct): boolean => typeof product.product_model_id === "number";

/**
 * INV-INTAKE-GUARD-01 · a NEW device variant may still be received by quantity, but only after the person read that it
 * cannot be undone. A used variant never: each device carries its own grade, battery and price (the server refuses).
 */
export const mayChooseQuantity = (product: ReceiptProduct): boolean => !isUsedLine(product);

/** The sentence said before (and while) a device variant goes «por cantidad». */
export const QUANTITY_LOCK_TEXT =
  "Si lo recibes por cantidad, este artículo quedará contado por cantidad y ya no se podrá pasar a control por equipo "
  + "(serie o IMEI) mientras tenga stock. Esto no se puede deshacer desde aquí.";

/**
 * The state a device of this article may NOT be received in (the server refuses it, `condition_line_mismatch`): «new»
 * in a used article, «used» in a new variant of a device model; none for an article outside the catalogue of models.
 * Shared by the wizard and «Registrar equipo».
 */
export function excludedCondition(product: { condition_line?: string | null; product_model_id?: number | null }): string | null {
  if (product.condition_line === "used") return "new";
  return typeof product.product_model_id === "number" ? "used" : null;
}

/** The state a device of this article starts in: its line's. */
export const lineCondition = (product: { condition_line?: string | null }): string =>
  product.condition_line === "used" ? "used" : "new";

/** The states a device of this article may be received in: a variant's line excludes the opposite one (the server refuses it). */
export function conditionsFor(product: ReceiptProduct, current?: string): { value: string; label: string }[] {
  const excluded = excludedCondition(product);
  return UNIT_CONDITIONS.filter((c) => c.value !== excluded || c.value === current);
}

/** Said of a graded device in a NEW device variant (the selector stays in sight until the grade is removed). */
export const GRADE_ON_NEW_TEXT =
  "Este artículo es una variante nueva y el equipo trae estado estético, que es de un equipo seminuevo: quita el "
  + "estado estético o recíbelo en la variante seminueva.";

/** Whether a NEW device variant may carry a cosmetic grade: no (a graded device is used, the server refuses it). */
export const takesGrade = (product: ReceiptProduct): boolean => isUsedLine(product) || !isDeviceVariant(product);

/** The change that switches a line between «por cantidad» and «por equipo», keeping what was already typed. */
export function countingPatch(line: DraftLine, byUnit: boolean): Partial<DraftLine> {
  if (byUnit) {
    const condition = line.product.condition_line === "used" ? "used" : "new";
    return { byUnit: true, units: line.units.length ? line.units : [blankUnit(condition)] };
  }
  return { byUnit: false, quantity: line.quantity || "1" };
}

export type ReceiptOrigin = {
  branch: number | null;
  supplier: number | null;
  document_ref: string;
  notes: string;
};

export const DOCUMENT_REF_MAX = 60;
export const NOTES_MAX = 300;
/** The server keeps 300 characters of a device's own notes. */
export const UNIT_NOTES_MAX = 300;
export const MAX_QUANTITY = 100000;
/** Per request, as the server enforces them (`receipt_services`). */
export const MAX_LINES = 50;
export const MAX_DEVICES = 200;

/** The closed list the server accepts for `StockUnit.condition` (fixed by the model, not by the tenant). */
export const UNIT_CONDITIONS: { value: string; label: string }[] = [
  { value: "new", label: "Nuevo" },
  { value: "open_box", label: "Caja abierta" },
  { value: "refurbished", label: "Reacondicionado" },
  { value: "used", label: "Usado" },
];

let counter = 0;
/** A local id for React keys and «Corregir» links. Never sent, never a secret. */
export const localKey = (prefix: string): string => `${prefix}${++counter}`;

export const blankUnit = (condition = "new"): DraftUnit => ({
  key: localKey("u"), serial_number: "", imei: "", imei2: "", condition, cost: "", battery: "", grade: "", notes: "",
  price: "", publicNotes: "",
});

export function newLine(product: ReceiptProduct): DraftLine {
  const condition = product.condition_line === "used" ? "used" : "new";
  // INV-INTAKE-GUARD-01: a device variant (or a used article) that may still be counted by device starts «por equipo».
  const byUnit = canChooseUnits(product) && (isDeviceVariant(product) || isUsedLine(product));
  return {
    key: localKey("l"),
    product,
    quantity: product.is_serialized ? "" : "1",
    unitCost: "",
    units: product.is_serialized || byUnit ? [blankUnit(condition)] : [],
    ...(byUnit ? { byUnit: true } : {}),
  };
}

const unitInUse = (unit: DraftUnit) => Boolean(unit.serial_number.trim() || unit.imei.trim() || unit.imei2.trim());

export const unitsInUse = (line: DraftLine): DraftUnit[] => line.units.filter(unitInUse);

// ── Validation (obvious things only) ──────────────────────────────────────

export type UnitProblems = Partial<Record<"serial_number" | "imei" | "imei2" | "cost" | "battery" | "notes" | "price" | "publicNotes" | "row", string>>;
export type LineProblems = {
  quantity?: string;
  unitCost?: string;
  /** About the whole line: no device filled in yet. */
  line?: string;
  units: Record<string, UnitProblems>;
};

const COST = /^\d{1,8}(\.\d{1,2})?$/;
/** An amount with at most two decimals, as the server reads it. */
export const costIsValid = (raw: string): boolean => COST.test(raw.trim());

/** A price is an amount like a cost, and greater than zero (the server refuses «0.00»). */
export const priceIsValid = (raw: string): boolean => costIsValid(raw) && Number(raw.trim()) > 0;

export const PUBLIC_NOTES_MAX = 500;

/** Only a used-line article is priced and described device by device; the others sell at the product's price. */
export const isUsedLine = (product: ReceiptProduct): boolean => product.condition_line === "used";

/**
 * INV-RECEIVE-PRICE-03 — the one sentence for a used device without its own price, in the reception and in
 * «Equipos seminuevos». There is no fallback: such a device is sold nowhere until somebody prices it.
 */
export const PENDING_PRICE_TEXT =
  "Pendiente de precio de venta: este equipo no podrá ofrecerse en línea ni venderse en caja hasta configurarlo.";

/** A whole percent, 0 to 100; «93%» is read as 93. */
const BATTERY = /^(\d{1,3})\s*%?$/;

/** The battery as the server reads it, or null when empty or out of range. */
export function batteryValue(raw: string): number | null {
  const found = BATTERY.exec(raw.trim());
  if (!found) return null;
  const value = Number(found[1]);
  return value >= 0 && value <= 100 ? value : null;
}
/** The server takes identifiers as printable ASCII only (a scanner in another layout may type accents). */
const ASCII = /^[\x20-\x7E]+$/;
const NOT_ASCII = "Escribe sólo letras sin tilde, números y signos comunes (revisa el teclado del lector).";
const norm = (value: string) => value.trim().toLowerCase();

export const LINES_LIMIT_TEXT = `Se pueden recibir hasta ${MAX_LINES} artículos por vez. Registra el resto en otra recepción.`;
export const DEVICES_LIMIT_TEXT = `Se pueden recibir hasta ${MAX_DEVICES} equipos por vez. Registra el resto en otra recepción.`;

/** The caps of one request, said before the server has to. Null when within them. */
export function limitProblem(lines: DraftLine[]): string | null {
  if (lines.length > MAX_LINES) return LINES_LIMIT_TEXT;
  const devices = lines.reduce((n, l) => n + (countsByUnit(l) ? unitsInUse(l).length : 0), 0);
  if (devices > MAX_DEVICES) return DEVICES_LIMIT_TEXT;
  return null;
}

export function validateLines(lines: DraftLine[], opts: { costs: boolean; prices?: boolean }): Record<string, LineProblems> {
  const out: Record<string, LineProblems> = {};
  // Repeats are counted across the WHOLE reception, not row by row.
  // A serial belongs to its product (the server keys it that way); an IMEI is unique across the company.
  const seen = new Map<string, number>();
  const bump = (value: string) => { if (value) seen.set(value, (seen.get(value) ?? 0) + 1); };
  const serialOf = (line: DraftLine, value: string) => `s${line.product.id}|${norm(value)}`;
  for (const line of lines) {
    for (const unit of unitsInUse(line)) {
      if (unit.serial_number.trim()) bump(serialOf(line, unit.serial_number));
      if (unit.imei.trim()) bump(`i|${norm(unit.imei)}`);
      if (unit.imei2.trim()) bump(`i|${norm(unit.imei2)}`);
    }
  }
  const repeatedSerial = (line: DraftLine, value: string) => (seen.get(serialOf(line, value)) ?? 0) > 1;
  const repeatedImei = (value: string) => (seen.get(`i|${norm(value)}`) ?? 0) > 1;

  for (const line of lines) {
    const problems: LineProblems = { units: {} };
    if (countsByUnit(line)) {
      const used = unitsInUse(line);
      if (used.length === 0) problems.line = "Agrega al menos un equipo con su número de serie.";
      for (const unit of used) {
        const p: UnitProblems = {};
        if (!unit.serial_number.trim()) p.serial_number = "Falta el número de serie.";
        else if (!ASCII.test(unit.serial_number.trim())) p.serial_number = NOT_ASCII;
        else if (repeatedSerial(line, unit.serial_number)) p.serial_number = "Este número de serie está repetido en esta recepción.";
        if (!unit.imei.trim()) {
          if (line.product.requires_imei) p.imei = "Falta el IMEI: este equipo lo necesita.";
        } else if (!ASCII.test(unit.imei.trim())) p.imei = NOT_ASCII;
        else if (repeatedImei(unit.imei)) p.imei = "Este IMEI está repetido en esta recepción.";
        if (unit.imei2.trim() && !ASCII.test(unit.imei2.trim())) p.imei2 = NOT_ASCII;
        else if (unit.imei2.trim() && repeatedImei(unit.imei2)) p.imei2 = "Este IMEI 2 está repetido en esta recepción.";
        if (opts.costs && unit.cost.trim() && !COST.test(unit.cost.trim())) p.cost = "Escribe el costo con hasta 2 decimales, por ejemplo 12.50.";
        // A new device carries no measured battery: a value left in a hidden field is neither sent nor a reason to block.
        if (unit.condition !== "new" && unit.battery.trim() && batteryValue(unit.battery) === null) p.battery = "La batería se indica en % entero, de 0 a 100.";
        // INV-INTAKE-GUARD-01 review: a grade (copied from a pasted list) on a NEW device variant makes the device a
        // used one, which the server refuses. Said here, next to the device, before it is sent.
        if (unit.condition !== "new" && unit.grade.trim() && !takesGrade(line.product)) p.row = GRADE_ON_NEW_TEXT;
        if (unit.notes.trim().length > UNIT_NOTES_MAX) p.notes = `Las observaciones admiten hasta ${UNIT_NOTES_MAX} caracteres.`;
        // INV-COM-01A: a price is checked only where it is sent (a used-line article, by whoever may set prices).
        if (opts.prices && isUsedLine(line.product) && unit.price.trim() && !priceIsValid(unit.price)) {
          p.price = "Escribe el precio con hasta 2 decimales y mayor que cero, por ejemplo 1899.00.";
        }
        if (isUsedLine(line.product) && unit.publicNotes.trim().length > PUBLIC_NOTES_MAX) {
          p.publicNotes = `Los detalles para el comprador admiten hasta ${PUBLIC_NOTES_MAX} caracteres.`;
        }
        if (Object.keys(p).length) problems.units[unit.key] = p;
      }
    } else {
      const raw = line.quantity.trim();
      if (!/^\d+$/.test(raw) || Number(raw) < 1) problems.quantity = "Escribe cuántas unidades entran (un número entero, mínimo 1).";
      else if (Number(raw) > MAX_QUANTITY) problems.quantity = `Son demasiadas unidades de una vez (máximo ${MAX_QUANTITY}).`;
      if (opts.costs && line.unitCost.trim() && !COST.test(line.unitCost.trim())) problems.unitCost = "Escribe el costo con hasta 2 decimales, por ejemplo 12.50.";
    }
    if (problems.quantity || problems.unitCost || problems.line || Object.keys(problems.units).length) out[line.key] = problems;
  }
  return out;
}

export const hasProblems = (problems: Record<string, LineProblems>): boolean => Object.keys(problems).length > 0;

// ── Request body ──────────────────────────────────────────────────────────

export type ReceiptLineBody =
  | { kind: "quantity"; product: number; quantity: number; unit_cost?: string | null }
  | {
    kind: "units";
    product: number;
    units: {
      serial: string; imei: string; imei2?: string; condition: string; unit_cost?: string | null;
      battery_health?: string; cosmetic_grade?: number; notes?: string; sale_price?: string; public_notes?: string;
    }[];
  };

export type ReceiptBody = {
  branch: number;
  supplier: number | null;
  document_ref: string;
  notes: string;
  lines: ReceiptLineBody[];
};

/**
 * The body for the server, and which draft line each body line came from (the
 * server reports problems by index). Costs go only when the person may see them.
 */
export function buildReceiptBody(
  origin: ReceiptOrigin, lines: DraftLine[], opts: { costs: boolean; prices?: boolean },
): { body: ReceiptBody; lineKeys: string[]; unitKeys: string[][] } {
  const lineKeys: string[] = [];
  /** Per body line, the draft key of each device sent (the server reports a device by its 0-based position). */
  const unitKeys: string[][] = [];
  const body: ReceiptLineBody[] = [];
  for (const line of lines) {
    lineKeys.push(line.key);
    unitKeys.push(countsByUnit(line) ? unitsInUse(line).map((u) => u.key) : []);
    if (countsByUnit(line)) {
      body.push({
        kind: "units",
        product: line.product.id,
        units: unitsInUse(line).map((unit) => ({
          serial: unit.serial_number.trim(),
          imei: unit.imei.trim(),
          ...(unit.imei2.trim() ? { imei2: unit.imei2.trim() } : {}),
          condition: unit.condition,
          // What was measured and seen belongs to a used device; a new one is never sent a battery or a grade.
          ...(unit.condition !== "new" && unit.battery.trim() ? { battery_health: String(batteryValue(unit.battery) ?? unit.battery.trim()) } : {}),
          ...(unit.condition !== "new" && /^\d+$/.test(unit.grade.trim()) ? { cosmetic_grade: Number(unit.grade.trim()) } : {}),
          ...(unit.notes.trim() ? { notes: unit.notes.trim() } : {}),
          ...(opts.costs && unit.cost.trim() ? { unit_cost: unit.cost.trim() } : {}),
          // INV-COM-01A: the device's own price and the buyer's notes — only for a used-line article, and the price
          // only for whoever may set prices (the server asks again). Without it the device is received and waits.
          ...(isUsedLine(line.product) && opts.prices && unit.price.trim() ? { sale_price: unit.price.trim() } : {}),
          ...(isUsedLine(line.product) && unit.publicNotes.trim() ? { public_notes: unit.publicNotes.trim() } : {}),
        })),
      });
    } else {
      body.push({
        kind: "quantity",
        product: line.product.id,
        quantity: Number(line.quantity.trim()),
        ...(opts.costs && line.unitCost.trim() ? { unit_cost: line.unitCost.trim() } : {}),
      });
    }
  }
  return {
    body: {
      branch: origin.branch as number,
      supplier: origin.supplier,
      document_ref: origin.document_ref.trim(),
      notes: origin.notes.trim(),
      lines: body,
    },
    lineKeys,
    unitKeys,
  };
}

/** Same content ⇒ same text. A change of anything the server would see changes it. */
export const fingerprint = (body: ReceiptBody): string => JSON.stringify(body);

// ── Responses ─────────────────────────────────────────────────────────────

/** A failed batch comes as `{ id: null, status: "error" }`. There is no `url`: the labels screen is the fallback. */
export type LabelBatch = { id?: number | null; status?: string; count?: number; url?: string; [more: string]: unknown };

/**
 * INV-COM-01A · the server's reading of a line's place in the shop. `model_page`: a used device is offered device by
 * device on its model's page; `card`: anything else has its own card. `ready` is the operator's decision to publish:
 * nothing here publishes anything.
 */
export type Commercial = {
  placement: "model_page" | "card";
  state: "published" | "ready" | "pending";
  published: boolean;
  /** `inactive` · `no_price` · `no_image` · `no_category` — what `product_publication` says is missing. */
  problems: string[];
  model: { id: number; name: string; published: boolean } | null;
  /** The shop sells from ONE branch: what is received elsewhere is not offered online until it is there. */
  has_dispatch_branch: boolean;
  in_dispatch_branch: boolean;
  units_priced: number;
  units_unpriced: number;
  priced_by: "unit" | "product";
};

export type ReceiptPreview = {
  ok: boolean;
  branch: { id: number; name: string };
  supplier: { id: number; name: string } | null;
  document_ref: string;
  totals: { lines: number; quantity_units: number; devices: number; labels: number };
  lines: {
    index: number;
    kind: "quantity" | "units";
    product: { id: number; name: string; sku?: string; is_serialized: boolean; price?: string; condition_line?: "new" | "used" };
    /** INV-COM-01A · where the line lands in the shop and what is missing (no amounts of a device). */
    commercial?: Commercial;
    status: "ok" | "error";
    errors: string[];
    /** A problem pinned to one device: `unit` is its 0-based position among the devices sent. */
    unit_issues?: { unit: number; field: string; kind: "error" | "duplicate"; message: string; code?: string }[];
    /**
     * INV-INTAKE-GUARD-01 · what the server decided, by code: `used_requires_units` and `condition_line_mismatch`
     * (errors), `quantity_locks_tracking` (a warning that must not be missed). Absent from an older server.
     */
    codes?: string[];
    warnings: string[];
    will_receive: number;
    labels: number;
    duplicates: string[];
  }[];
};

/**
 * INV-RECEIVE-PRICE-03 — what a received line still needs before the shop can offer it, read by the server at the
 * moment of the answer (also on a replay). Counts and codes only: never an amount, a cost or an identifier. Nothing in
 * it publishes anything.
 */
export type ReceiptFollowUp = {
  received: number;
  priced_by: "unit" | "product";
  units_priced: number;
  units_unpriced: number;
  units_without_battery: number;
  units_without_grade: number;
  units_without_photos: number;
  storage_branch: { id: number; name: string } | null;
  dispatch_branch: { id: number; name: string } | null;
  in_dispatch_branch: boolean;
  state: Commercial["state"];
  problems: string[];
  model: Commercial["model"];
  placement: Commercial["placement"];
  requires_image: boolean;
  /** Devices of this line the web offers now; null for a line by quantity. */
  units_offered_online: number | null;
  online: "offered" | "blocked";
  /** `no_dispatch_branch` · `not_in_dispatch_branch` · `not_published` · `model_hidden` · `unpriced` · `not_available`. */
  blocked_reasons: string[];
};

export type ReceiptResult = {
  receipt_id: string | number;
  branch: { id: number; name: string } | number;
  supplier: { id: number; name: string } | number | null;
  document_ref: string;
  totals: { lines: number; quantity_units: number; devices: number; labels: number };
  lines: {
    index: number;
    kind: "quantity" | "units";
    product: { id: number; name: string };
    received: number;
    movement_id: number | null;
    units: number;
    /** Devices received without their own price: they wait, unpriced, to be completed. */
    units_unpriced?: number;
    /** INV-RECEIVE-PRICE-03: null/absent when the server could not read it (the reception is saved anyway). */
    follow_up?: ReceiptFollowUp | null;
    label_batch: LabelBatch | null;
    label_error: boolean;
  }[];
  label_batches: LabelBatch[];
  label_error: boolean;
  replayed?: boolean;
};

// ── HTTP ──────────────────────────────────────────────────────────────────

export class ReceiptApiError extends Error {
  readonly status: number;
  /** No answer, or a server failure: the reception may or may not have been saved. */
  readonly ambiguous: boolean;
  /** `invalid` · `costs_forbidden` · `prices_forbidden` · `idempotency_key` · `idempotency_conflict` · `invalid_lines` · `not_found` · `forbidden`. */
  readonly code: string;
  /** Body line the server points at (0-based), when it names one. */
  readonly line: number | null;
  /** 400 `invalid_lines`: the full review, with nothing received. */
  readonly preview: ReceiptPreview | null;

  constructor(
    message: string, status: number, ambiguous = false,
    extra: { code?: string; line?: number | null; preview?: ReceiptPreview | null } = {},
  ) {
    super(message);
    this.name = "ReceiptApiError";
    this.status = status;
    this.ambiguous = ambiguous;
    this.code = extra.code ?? "";
    this.line = extra.line ?? null;
    this.preview = extra.preview ?? null;
  }
}

const BASE = `${API_BASE}/admin/inventory/receipts`;
const AMBIGUOUS = "No llegó la respuesta del servidor, así que no sabemos si la recepción se guardó. Vuelve a pulsar «Confirmar»: no se duplicará.";

function hasShape(data: unknown): boolean {
  const o = data && typeof data === "object" ? (data as Record<string, unknown>) : null;
  return Boolean(o && Array.isArray(o.lines) && o.lines.length > 0 && o.totals && typeof o.totals === "object");
}

async function send<T>(path: string, body: ReceiptBody, headers: Record<string, string> = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetchWithAuth(`${BASE}${path}`, { method: "POST", body: JSON.stringify(body), headers });
  } catch {
    throw new ReceiptApiError(path === "/confirm/" ? AMBIGUOUS : "No hay conexión con el servidor. Revisa tu internet e inténtalo de nuevo.", 0, path === "/confirm/");
  }
  const data = await res.json().catch(() => null);
  if (res.ok && !hasShape(data)) {
    // A 2xx that is not a reception (empty, not JSON, no lines or totals) proves nothing: never a blank success.
    if (path === "/confirm/") throw new ReceiptApiError(AMBIGUOUS, res.status, true);
    throw new ReceiptApiError("El servidor respondió algo que no se entiende. Inténtalo de nuevo.", res.status);
  }
  if (!res.ok) {
    const detail = data && typeof data === "object" && "detail" in data ? String((data as { detail: unknown }).detail) : "";
    if (res.status >= 500 && path === "/confirm/") throw new ReceiptApiError(AMBIGUOUS, res.status, true);
    const obj = (data && typeof data === "object" ? data : {}) as Record<string, unknown>;
    const code = typeof obj.code === "string" ? obj.code : "";
    const line = typeof obj.line === "number" ? obj.line : null;
    const review = code === "invalid_lines" && Array.isArray(obj.lines) ? (data as ReceiptPreview) : null;
    throw new ReceiptApiError(
      detail || (res.status === 403
        ? "Tu usuario no puede recibir mercadería en esta sucursal. Pídeselo a quien administra el inventario."
        : "No se pudo completar la operación. Inténtalo de nuevo."),
      res.status,
      false,
      { code, line, preview: review },
    );
  }
  return data as T;
}

export const previewReceipt = (body: ReceiptBody) => send<ReceiptPreview>("/preview/", body);

export const confirmReceipt = (body: ReceiptBody, key: string) =>
  send<ReceiptResult>("/confirm/", body, { "Idempotency-Key": key });

/**
 * A key for ONE attempt, from the platform's CSPRNG only — never `Math.random`
 * (same rule as the till and the service console). With no secure source the
 * screen refuses to confirm and says why.
 */
export function newReceiptKey(): string | null {
  const source = (globalThis as { crypto?: Partial<Crypto> }).crypto;
  if (typeof source?.randomUUID === "function") return `rcv-${source.randomUUID()}`;
  if (typeof source?.getRandomValues === "function") {
    const bytes = source.getRandomValues(new Uint8Array(16));
    return "rcv-" + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  }
  return null;
}

// ── INV-DRAFT-01 · the draft kept on the server ───────────────────────────
//
// A reload used to lose everything typed (200 scanned serials). The wizard now saves its content on the SERVER
// (`/admin/inventory/receipts/draft/`), tied to the person and the company (`fetchWithAuth` names the company of a
// platform master), and offers it back on the next visit. Never browser storage: it holds serials and IMEI. The
// server keeps it 24 h after the last save (`RECEIPT_DRAFT_TTL_SECONDS`) and deletes it on confirmation; the screen
// deletes it on «Descartar» and «Registrar otra recepción». Costs travel only for who may see them.

/** The server refuses a bigger draft (`receipt_drafts.MAX_BYTES`). */
export const DRAFT_MAX_BYTES = 256 * 1024;
/** How long the wizard waits after the last change before saving. */
export const DRAFT_SAVE_DELAY_MS = 1500;

export type DraftPayload = { origin: ReceiptOrigin; lines: DraftLine[]; staged: unknown[] };

export type SavedDraft = DraftPayload & {
  /** The branch as the server reads it now: null when it is not chosen or no longer reachable. */
  branch: number | null;
  updatedAt: string;
  expiresAt: string;
};

/** `conflict`: the draft on the server is not the version this page saw (another tab, a confirmation, a discard). */
export type DraftSaveOutcome = "saved" | "too_large" | "failed" | "conflict";

/** What a save gave: on «saved», the new version to name on the next save (`updated_at`), null if unreadable. */
export type DraftSaveResult = { outcome: DraftSaveOutcome; version: string | null };

/** «found», «none» (the server said there is none) and «failed» (refused, throttled, offline, unreadable) differ. */
export type DraftLookup = { status: "found"; draft: SavedDraft } | { status: "none" } | { status: "failed" };

const DRAFT_URL = `${API_BASE}/admin/inventory/receipts/draft/`;

/** A recognizer's notice that speaks of a cost: the server keeps it only for who holds `costs.view`. */
const COST_WORDS = /cost/i;

/** A staged row without what can carry a cost: the cost, the undecided amounts and a notice that names a cost. */
function stagedWithoutCosts(row: unknown): unknown {
  if (!row || typeof row !== "object") return row;
  const out: Record<string, unknown> = { ...(row as Record<string, unknown>) };
  if ("cost" in out) out.cost = "";
  if ("amounts" in out) out.amounts = [];
  if (Array.isArray(out.hints)) out.hints = out.hints.filter((hint) => !(typeof hint === "string" && COST_WORDS.test(hint)));
  return out;
}

/** What is saved. Without `costs.view` every cost goes blank: the server refuses them, and they are not the person's to keep. */
export function draftPayload(origin: ReceiptOrigin, lines: DraftLine[], staged: unknown[], opts: { costs: boolean }): DraftPayload {
  if (opts.costs) return { origin, lines, staged };
  return {
    origin,
    lines: lines.map((line) => ({ ...line, unitCost: "", units: line.units.map((unit) => ({ ...unit, cost: "" })) })),
    staged: staged.map(stagedWithoutCosts),
  };
}

const text = (value: unknown): string => (typeof value === "string" ? value : "");
const anId = (value: unknown): number | null => (typeof value === "number" && Number.isInteger(value) && value > 0 ? value : null);

function reviveProduct(raw: unknown): ReceiptProduct | null {
  if (!raw || typeof raw !== "object") return null;
  const p = raw as Record<string, unknown>;
  const id = anId(p.id);
  if (id === null || typeof p.name !== "string") return null;
  return {
    id, name: p.name, is_serialized: p.is_serialized === true, requires_imei: p.requires_imei === true,
    ...(typeof p.sku === "string" ? { sku: p.sku } : {}),
    ...(p.condition_line === "used" || p.condition_line === "new" ? { condition_line: p.condition_line } : {}),
    ...(typeof p.is_active === "boolean" ? { is_active: p.is_active } : {}),
    ...(typeof p.can_change_tracking === "boolean" ? { can_change_tracking: p.can_change_tracking } : {}),
    ...(anId(p.product_model_id) !== null ? { product_model_id: anId(p.product_model_id) } : {}),
  };
}

function reviveUnit(raw: unknown): DraftUnit | null {
  if (!raw || typeof raw !== "object") return null;
  const u = raw as Record<string, unknown>;
  return {
    ...blankUnit(text(u.condition) || "new"),
    serial_number: text(u.serial_number), imei: text(u.imei), imei2: text(u.imei2), cost: text(u.cost),
    battery: text(u.battery), grade: text(u.grade), notes: text(u.notes), price: text(u.price), publicNotes: text(u.publicNotes),
  };
}

/** The lines as the screen holds them, with NEW local keys (the counter of this page starts again after a reload). */
function reviveLines(raw: unknown): DraftLine[] {
  if (!Array.isArray(raw)) return [];
  const out: DraftLine[] = [];
  for (const item of raw.slice(0, MAX_LINES)) {
    if (!item || typeof item !== "object") continue;
    const l = item as Record<string, unknown>;
    const product = reviveProduct(l.product);
    if (!product) continue;
    const units = (Array.isArray(l.units) ? l.units : []).map(reviveUnit).filter((u): u is DraftUnit => u !== null);
    out.push({
      key: localKey("l"), product, quantity: text(l.quantity), unitCost: text(l.unitCost), units,
      ...(l.byUnit === true ? { byUnit: true } : {}),
    });
  }
  return out;
}

function reviveOrigin(raw: unknown): ReceiptOrigin {
  const o = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  return {
    branch: anId(o.branch), supplier: anId(o.supplier),
    document_ref: text(o.document_ref).slice(0, DOCUMENT_REF_MAX), notes: text(o.notes).slice(0, NOTES_MAX),
  };
}

/**
 * The person's draft in the company of the request. «none» only when the server SAID there is none (or it expired);
 * a refusal, a throttle, a server error, no network or an unreadable answer is «failed»: nothing is known, so the
 * screen must not save over what may be there.
 */
export async function fetchReceiptDraft(): Promise<DraftLookup> {
  try {
    const res = await fetchWithAuth(DRAFT_URL, { method: "GET" });
    if (!res.ok) return { status: "failed" };
    const data = (await res.json()) as { draft?: Record<string, unknown> | null } | null;
    if (!data || typeof data !== "object" || !("draft" in data)) return { status: "failed" };
    const draft = data.draft;
    if (draft === null) return { status: "none" };
    if (!draft || typeof draft !== "object" || Array.isArray(draft)) return { status: "failed" };
    return { status: "found", draft: {
      origin: reviveOrigin(draft.origin),
      lines: reviveLines(draft.lines),
      // The rows of a pasted list, as the same screen wrote them; only well-formed ones come back.
      staged: Array.isArray(draft.staged)
        ? draft.staged.filter((r) => r && typeof r === "object" && anId((r as { id?: unknown }).id) !== null)
        : [],
      branch: anId(draft.branch),
      updatedAt: text(draft.updated_at),
      expiresAt: text(draft.expires_at),
    } };
  } catch {
    return { status: "failed" };
  }
}

function utf8Length(value: string): number {
  let n = 0;
  for (const ch of value) {
    const c = ch.codePointAt(0) ?? 0;
    n += c < 0x80 ? 1 : c < 0x800 ? 2 : c < 0x10000 ? 3 : 4;
  }
  return n;
}

/**
 * Save (replace) the person's draft over the version this page last saw (`base`: the `updated_at` of the last GET
 * or PUT, null when it knows there is none). The server refuses (409 → «conflict») a draft changed, confirmed or
 * discarded since. Never throws: a failed save leaves the page as it is.
 */
export async function saveReceiptDraft(branch: number | null, payload: DraftPayload, base: string | null): Promise<DraftSaveResult> {
  const fail = (outcome: DraftSaveOutcome): DraftSaveResult => ({ outcome, version: null });
  let body: string;
  try {
    body = JSON.stringify({ branch, payload, base });
  } catch {
    return fail("failed");
  }
  // The server's limit is on the payload; the envelope is a few bytes more. UTF-8, as the server counts.
  if (utf8Length(body) > DRAFT_MAX_BYTES) return fail("too_large");
  try {
    const res = await fetchWithAuth(DRAFT_URL, { method: "PUT", body });
    const data = await res.json().catch(() => null) as { code?: unknown; draft?: { updated_at?: unknown } } | null;
    if (res.ok) {
      const version = data?.draft?.updated_at;
      return { outcome: "saved", version: typeof version === "string" && version ? version : null };
    }
    if (res.status === 409 && data?.code === "draft_conflict") return fail("conflict");
    return fail(data?.code === "too_large" ? "too_large" : "failed");
  } catch {
    return fail("failed");
  }
}

/** Discard the person's draft (idempotent on the server). Never throws. */
export async function deleteReceiptDraft(): Promise<boolean> {
  try {
    const res = await fetchWithAuth(DRAFT_URL, { method: "DELETE" });
    return res.ok;
  } catch {
    return false;
  }
}

/** «hace 5 min», for the offer to recover it. */
export function draftAge(updatedAt: string, now = Date.now()): string {
  const at = Date.parse(updatedAt);
  if (Number.isNaN(at)) return "hace un rato";
  const minutes = Math.floor(Math.max(0, now - at) / 60_000);
  if (minutes < 1) return "hace menos de 1 min";
  if (minutes < 60) return `hace ${minutes} min`;
  return `hace ${Math.floor(minutes / 60)} h`;
}

// ── In-memory hand-over while the person creates a missing article ────────

/** Whose draft it is: the person and the company it was typed for. */
export type DraftScope = { user: number | string | null; company: number | null };

/** INV-RECEIVE-FLOW-04 · a list pasted in «Carga masiva» and handed to the wizard, with the line it was declared as. */
export type PastedList = { text: string; line: "new" | "used" | "accessory" };

/**
 * What the wizard that leaves to «crear artículo» knows of its server draft, for the one that comes back:
 * `settled` — the question about a saved draft was answered (else the next one asks again and saves nothing until
 * then); `idle` — its last save, finished; `version` — read AFTER `idle`: the version to name next (undefined:
 * unknown, nothing is saved).
 */
export type DraftHandOff = { settled: boolean; idle: Promise<unknown>; version: () => string | null | undefined };

type Stash = {
  at: number; scope: DraftScope; origin: ReceiptOrigin; lines: DraftLine[]; staged: unknown[]; pasted?: PastedList;
  draft?: DraftHandOff;
};
let stash: Stash | null = null;
const STASH_TTL_MS = 30 * 60 * 1000;

/**
 * Leaving to «Registrar artículo» must not cost the lines already typed. The
 * draft is held in a module variable (memory of the page: it survives moving
 * between admin screens and dies on reload or sign-out) and given back ONCE to
 * the wizard that comes back — and only to the same person in the same company.
 * Never storage: it holds serials and IMEI.
 */
export function stashDraft(
  origin: ReceiptOrigin, lines: DraftLine[], scope: DraftScope, staged: unknown[] = [], draft?: DraftHandOff,
): void {
  stash = { at: Date.now(), scope, origin, lines, staged, ...(draft ? { draft } : {}) };
}

/**
 * INV-RECEIVE-FLOW-04 · «Continuar en Recibir mercadería» from the WhatsApp reader of «Carga masiva»: the pasted
 * text goes to the ONE way devices are received, by the same in-memory hand-over (never storage, never the URL:
 * it holds serials and IMEI). The wizard analyses it once, with the same reader.
 */
export function stashPastedList(pasted: PastedList, scope: DraftScope, now = Date.now()): boolean {
  // A reception left half-done to create an article is never overwritten by another list (review of #FLOW-04).
  if (stash && now - stash.at <= STASH_TTL_MS && (stash.lines.length > 0 || stash.staged.length > 0)) return false;
  stash = {
    at: Date.now(), scope, origin: { branch: null, supplier: null, document_ref: "", notes: "" }, lines: [], staged: [],
    pasted: { text: pasted.text, line: pasted.line },
  };
  return true;
}

export function takeStashedDraft(scope: DraftScope, now = Date.now()): {
  origin: ReceiptOrigin; lines: DraftLine[]; staged: unknown[]; pasted?: PastedList; draft?: DraftHandOff;
} | null {
  const held = stash;
  stash = null; // taken or refused, it is gone either way
  if (!held || now - held.at > STASH_TTL_MS) return null;
  if (held.scope.user !== scope.user || held.scope.company !== scope.company) return null;
  return {
    origin: held.origin, lines: held.lines, staged: held.staged,
    ...(held.pasted ? { pasted: held.pasted } : {}), ...(held.draft ? { draft: held.draft } : {}),
  };
}

/** Sign-out, an ended session or a change of company: nothing typed for the previous one is kept. */
export const clearReceivingMemory = (): void => { stash = null; };

/** For tests only. */
export const forgetStashedDraft = clearReceivingMemory;

// ── A way out that asks first (company switch, …) ─────────────────────────

type LeaveGate = (proceed: () => void) => boolean;
let leaveGate: LeaveGate | null = null;

/** The open wizard registers how to ask «¿salir?» for things that are not a link (the company selector). */
export const registerLeaveGate = (gate: LeaveGate | null): void => { leaveGate = gate; };

/**
 * True when the open wizard took over: it asks the person and calls `proceed` only if they leave. False when
 * there is nothing to lose (or no wizard open): the caller goes ahead at once.
 */
export const askBeforeLeaving = (proceed: () => void): boolean => (leaveGate ? leaveGate(proceed) : false);

// The session ending (logout, idle or absolute expiry, from any tab) empties it too.
if (typeof window !== "undefined") subscribe(() => { if (isEnded()) clearReceivingMemory(); });

/** INV-INTAKE-GUARD-01 · the server warns that this line, by quantity, locks its variant out of «por equipo». */
export const locksTracking = (line: Pick<ReceiptPreview["lines"][number], "codes">): boolean =>
  Boolean(line.codes?.includes("quantity_locks_tracking"));
