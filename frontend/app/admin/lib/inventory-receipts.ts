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
 * costo viven sólo en la memoria de la página (ver `stashDraft`).
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
};

export type DraftUnit = {
  key: string;
  serial_number: string;
  imei: string;
  imei2: string;
  condition: string;
  cost: string;
};

export type DraftLine = {
  key: string;
  product: ReceiptProduct;
  /** Quantity lines only: kept as typed so a half-typed number is never rewritten. */
  quantity: string;
  unitCost: string;
  /** Serialized lines only. */
  units: DraftUnit[];
};

export type ReceiptOrigin = {
  branch: number | null;
  supplier: number | null;
  document_ref: string;
  notes: string;
};

export const DOCUMENT_REF_MAX = 60;
export const NOTES_MAX = 300;
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
  key: localKey("u"), serial_number: "", imei: "", imei2: "", condition, cost: "",
});

export function newLine(product: ReceiptProduct): DraftLine {
  const condition = product.condition_line === "used" ? "used" : "new";
  return {
    key: localKey("l"),
    product,
    quantity: product.is_serialized ? "" : "1",
    unitCost: "",
    units: product.is_serialized ? [blankUnit(condition)] : [],
  };
}

const unitInUse = (unit: DraftUnit) => Boolean(unit.serial_number.trim() || unit.imei.trim() || unit.imei2.trim());

export const unitsInUse = (line: DraftLine): DraftUnit[] => line.units.filter(unitInUse);

// ── Validation (obvious things only) ──────────────────────────────────────

export type UnitProblems = Partial<Record<"serial_number" | "imei" | "imei2" | "cost" | "row", string>>;
export type LineProblems = {
  quantity?: string;
  unitCost?: string;
  /** About the whole line: no device filled in yet. */
  line?: string;
  units: Record<string, UnitProblems>;
};

const COST = /^\d{1,8}(\.\d{1,2})?$/;
/** The server takes identifiers as printable ASCII only (a scanner in another layout may type accents). */
const ASCII = /^[\x20-\x7E]+$/;
const NOT_ASCII = "Escribe sólo letras sin tilde, números y signos comunes (revisa el teclado del lector).";
const norm = (value: string) => value.trim().toLowerCase();

/** The caps of one request, said before the server has to. Null when within them. */
export function limitProblem(lines: DraftLine[]): string | null {
  if (lines.length > MAX_LINES) return `Se pueden recibir hasta ${MAX_LINES} artículos por vez. Registra el resto en otra recepción.`;
  const devices = lines.reduce((n, l) => n + (l.product.is_serialized ? unitsInUse(l).length : 0), 0);
  if (devices > MAX_DEVICES) return `Se pueden recibir hasta ${MAX_DEVICES} equipos por vez. Registra el resto en otra recepción.`;
  return null;
}

export function validateLines(lines: DraftLine[], opts: { costs: boolean }): Record<string, LineProblems> {
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
    if (line.product.is_serialized) {
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
    units: { serial: string; imei: string; imei2?: string; condition: string; unit_cost?: string | null }[];
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
  origin: ReceiptOrigin, lines: DraftLine[], opts: { costs: boolean },
): { body: ReceiptBody; lineKeys: string[]; unitKeys: string[][] } {
  const lineKeys: string[] = [];
  /** Per body line, the draft key of each device sent (the server reports a device by its 0-based position). */
  const unitKeys: string[][] = [];
  const body: ReceiptLineBody[] = [];
  for (const line of lines) {
    lineKeys.push(line.key);
    unitKeys.push(line.product.is_serialized ? unitsInUse(line).map((u) => u.key) : []);
    if (line.product.is_serialized) {
      body.push({
        kind: "units",
        product: line.product.id,
        units: unitsInUse(line).map((unit) => ({
          serial: unit.serial_number.trim(),
          imei: unit.imei.trim(),
          ...(unit.imei2.trim() ? { imei2: unit.imei2.trim() } : {}),
          condition: unit.condition,
          ...(opts.costs && unit.cost.trim() ? { unit_cost: unit.cost.trim() } : {}),
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

export type ReceiptPreview = {
  ok: boolean;
  branch: { id: number; name: string };
  supplier: { id: number; name: string } | null;
  document_ref: string;
  totals: { lines: number; quantity_units: number; devices: number; labels: number };
  lines: {
    index: number;
    kind: "quantity" | "units";
    product: { id: number; name: string; sku?: string; is_serialized: boolean };
    status: "ok" | "error";
    errors: string[];
    /** A problem pinned to one device: `unit` is its 0-based position among the devices sent. */
    unit_issues?: { unit: number; field: string; kind: "error" | "duplicate"; message: string }[];
    warnings: string[];
    will_receive: number;
    labels: number;
    duplicates: string[];
  }[];
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
  /** `invalid` · `costs_forbidden` · `idempotency_key` · `idempotency_conflict` · `invalid_lines` · `not_found` · `forbidden`. */
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

// ── In-memory hand-over while the person creates a missing article ────────

/** Whose draft it is: the person and the company it was typed for. */
export type DraftScope = { user: number | string | null; company: number | null };

type Stash = { at: number; scope: DraftScope; origin: ReceiptOrigin; lines: DraftLine[] };
let stash: Stash | null = null;
const STASH_TTL_MS = 30 * 60 * 1000;

/**
 * Leaving to «Registrar artículo» must not cost the lines already typed. The
 * draft is held in a module variable (memory of the page: it survives moving
 * between admin screens and dies on reload or sign-out) and given back ONCE to
 * the wizard that comes back — and only to the same person in the same company.
 * Never storage: it holds serials and IMEI.
 */
export function stashDraft(origin: ReceiptOrigin, lines: DraftLine[], scope: DraftScope): void {
  stash = { at: Date.now(), scope, origin, lines };
}

export function takeStashedDraft(scope: DraftScope, now = Date.now()): { origin: ReceiptOrigin; lines: DraftLine[] } | null {
  const held = stash;
  stash = null; // taken or refused, it is gone either way
  if (!held || now - held.at > STASH_TTL_MS) return null;
  if (held.scope.user !== scope.user || held.scope.company !== scope.company) return null;
  return { origin: held.origin, lines: held.lines };
}

/** Sign-out, an ended session or a change of company: nothing typed for the previous one is kept. */
export const clearReceivingMemory = (): void => { stash = null; };

/** For tests only. */
export const forgetStashedDraft = clearReceivingMemory;

// The session ending (logout, idle or absolute expiry, from any tab) empties it too.
if (typeof window !== "undefined") subscribe(() => { if (isEnded()) clearReceivingMemory(); });
