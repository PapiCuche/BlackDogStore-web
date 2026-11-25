/**
 * SCAN-TO-STOCK-01 — «Registrar equipo por escaneo»: lo que la pantalla hace con cada lectura.
 *
 * Un lector de códigos es un teclado: escribe el código y pulsa Enter (o Tab, según cómo esté configurado). Cada
 * lectura se manda al servidor (`/admin/inventory/scan/check/`), que dice QUÉ es (IMEI, EID, MEID, código comercial,
 * etiqueta interna, serie o «no estoy seguro») y si la empresa ya tiene un equipo con ese identificador. Aquí sólo se
 * decide a qué casilla va, sin adivinar:
 *
 *   - nunca por el ritmo de las teclas: el ritmo dice cómo llegó (lector o a mano), no qué es;
 *   - un código dudoso se pregunta («¿Es la serie?»), nunca se coloca solo;
 *   - un código comercial (UPC/EAN) elige la variante, nunca es un equipo;
 *   - un EID o un MEID nunca es un IMEI;
 *   - leer dos veces lo mismo no cambia nada: la segunda lectura se ignora y se dice;
 *   - ninguna lectura confirma el ingreso: eso es siempre un botón.
 *
 * El servidor vuelve a decidirlo todo al confirmar (AGENTS §7).
 */

import { fetchWithAuth } from "../../lib/auth";
import { API_BASE } from "../../lib/api";
import { storageGet, storageSet } from "../../lib/browser-storage";
import type { EntryMethod } from "./unit-entry";
import type { PosModelConfigurations, PosModelHit, PosPendingOrderLine } from "./internal-api";

export type Slot = "imei" | "imei2" | "serial";
export type Choice = "imei" | "serial" | "gtin";
export type ScanKind = "imei" | "eid" | "meid" | "imeisv" | "gtin" | "label_code" | "serial" | "unknown";
export type Terminator = "enter" | "tab" | "both";

export type ScanReading = {
  value: string;
  kind: ScanKind;
  certain: boolean;
  alternatives: Choice[];
  hint: string;
  problem: string;
  detail: string;
};

export type ScanProduct = {
  id: number;
  name: string;
  model_id: number | null;
  condition_line: string;
  is_serialized: boolean;
  requires_imei: boolean;
  is_active: boolean;
  archived: boolean;
  option_ids: number[];
};

export type ScanUnitBrief = {
  standing: "registered" | "sold" | "written_off";
  status: string;
  status_label: string;
  detail: string;
  id?: number;
  label_code?: string;
  product_name?: string;
  branch_name?: string;
};

export type ScanCheck = {
  reading: ScanReading;
  product: ScanProduct | null;
  unit: ScanUnitBrief | null;
  conflict: { code: string; detail: string } | null;
  serial_elsewhere: string;
};

export type Identifiers = { imei: string; imei2: string; serial: string };
export const EMPTY_IDENTIFIERS: Identifiers = { imei: "", imei2: "", serial: "" };

/** Lo que se espera de este equipo: si lleva IMEI y qué casillas dijo la persona que no tiene. */
export type SlotPlan = { requiresImei: boolean; noImei2: boolean; noSerial: boolean };

export const SLOT_LABEL: Record<Slot, string> = { imei: "IMEI 1", imei2: "IMEI 2", serial: "Número de serie" };
export const CHOICE_LABEL: Record<Choice, string> = {
  imei: "Es el IMEI",
  serial: "Es la serie",
  gtin: "Es el código comercial (de la caja)",
};

/** La casilla que la pantalla espera ahora, en el orden de la caja del equipo: IMEI 1 → IMEI 2 → serie. */
export function expectedSlot(ids: Identifiers, plan: SlotPlan): Slot | null {
  if (plan.requiresImei && !ids.imei) return "imei";
  if (ids.imei && !ids.imei2 && !plan.noImei2) return "imei2";
  if (!ids.serial && !plan.noSerial) return "serial";
  return null;
}

export type ScanOutcome =
  | { type: "filled"; slot: Slot; value: string; message: string; warning?: string }
  | { type: "repeat"; message: string }
  | { type: "product"; product: ScanProduct; message: string }
  | { type: "ask"; value: string; options: Choice[]; message: string }
  | { type: "rejected"; message: string };

const last4 = (value: string) => (value.length > 4 ? `…${value.slice(-4)}` : value);

/**
 * Qué hacer con una lectura, ya contestada por el servidor. Puro: la misma lectura sobre el mismo estado da lo mismo.
 */
export function decide(check: ScanCheck, ids: Identifiers): ScanOutcome {
  const { reading } = check;
  const value = reading.value;
  const filled = (Object.keys(ids) as Slot[]).find((slot) => ids[slot] && ids[slot] === value);
  if (value && filled) {
    return { type: "repeat", message: `Lectura repetida: ${SLOT_LABEL[filled]} ya es ${last4(value)}. No cambia nada.` };
  }
  if (check.product) {
    return {
      type: "product", product: check.product,
      message: `Código comercial de «${check.product.name}»: identifica la variante, no el equipo.`,
    };
  }
  if (check.conflict) return { type: "rejected", message: check.conflict.detail };
  if (check.unit) return { type: "rejected", message: check.unit.detail };

  if (reading.kind === "imei" && reading.certain) {
    let slot: Slot | null = null;
    if (reading.hint === "imei2") slot = ids.imei2 ? null : "imei2";
    else if (!ids.imei) slot = "imei";
    else if (!ids.imei2) slot = "imei2";
    if (!slot) {
      return { type: "rejected", message: "Este equipo ya tiene sus dos IMEI. Si es otro equipo, confirma o limpia este primero." };
    }
    return { type: "filled", slot, value, message: `${SLOT_LABEL[slot]} leído: ${value}.` };
  }
  if (reading.kind === "serial" && reading.certain) {
    if (ids.serial) {
      return { type: "rejected", message: `La serie ya está leída (${ids.serial}). Si te equivocaste, bórrala primero.` };
    }
    return {
      type: "filled", slot: "serial", value, message: `Serie leída: ${value}.`,
      ...(check.serial_elsewhere
        ? { warning: `Esa serie ya existe en «${check.serial_elsewhere}». Si es el mismo equipo, no lo registres dos veces.` }
        : {}),
    };
  }
  if (reading.kind === "gtin" && reading.certain) {
    return {
      type: "ask", value, options: ["serial"],
      message: "Es un código comercial, pero ningún producto de la empresa lo tiene. Si en realidad es la serie del equipo, indícalo.",
    };
  }
  const options = reading.alternatives.filter((choice): choice is Choice => ["imei", "serial", "gtin"].includes(choice));
  if (options.length) {
    const question = reading.kind === "serial" ? "No es seguro que sea la serie. ¿Qué identificador es?" : reading.detail;
    return { type: "ask", value: value || reading.value, options, message: question };
  }
  return { type: "rejected", message: reading.detail || "No se reconoce este código." };
}

/** El identificador de una casilla, al aplicarle una lectura. */
export function applyOutcome(ids: Identifiers, outcome: ScanOutcome): Identifiers {
  return outcome.type === "filled" ? { ...ids, [outcome.slot]: outcome.value } : ids;
}

/**
 * La tecla termina la lectura. Con el campo vacío nada termina: un Enter doble (o un Tab de más) no hace nada, y
 * nunca confirma el ingreso.
 */
export function isTerminator(key: string, terminator: Terminator, value: string): boolean {
  if (!value.trim()) return false;
  if (key === "Enter") return terminator === "enter" || terminator === "both";
  if (key === "Tab") return terminator === "tab" || terminator === "both";
  return false;
}

/** Varias lecturas pueden llegar mientras la anterior se consulta: se atienden en orden, de una en una. */
export class ReadQueue {
  private chain: Promise<void> = Promise.resolve();

  push(task: () => Promise<void>): Promise<void> {
    this.chain = this.chain.then(task, task);
    return this.chain;
  }
}

/** Lo que el formulario pide antes de habilitar «Confirmar ingreso». Vacío: se puede confirmar. */
export function reviewProblems(input: {
  ids: Identifiers;
  plan: SlotPlan;
  variantChosen: boolean;
  needsPrice: boolean;
  price: string;
  cost: string;
  salePrice?: string;
  battery?: string;
  waitingHere: boolean;
  orderChoice: number | "stock" | null;
  stockReason: string;
}): string[] {
  const problems: string[] = [];
  const amount = /^\d{1,9}(\.\d{1,2})?$/;
  if (!input.variantChosen) problems.push("Elige el modelo y la variante.");
  if (input.plan.requiresImei && !input.ids.imei) problems.push("Falta el IMEI 1.");
  if (!input.plan.requiresImei && !input.ids.imei && !input.ids.serial) problems.push("Falta la serie.");
  if (input.ids.imei2 && !input.ids.imei) problems.push("El IMEI 2 acompaña al IMEI 1: lee primero el IMEI 1.");
  if (input.needsPrice && !(amount.test(input.price.trim()) && Number(input.price) > 0)) {
    problems.push("Indica el precio de venta de la variante nueva.");
  }
  if (input.cost.trim() && !amount.test(input.cost.trim())) problems.push("Escribe el costo con punto decimal, p. ej. 3100.00.");
  if (input.salePrice?.trim() && !(amount.test(input.salePrice.trim()) && Number(input.salePrice) > 0)) {
    problems.push("El precio de venta debe ser un importe mayor que cero.");
  }
  if (input.battery?.trim() && !/^(100|[1-9]?\d)$/.test(input.battery.trim())) {
    problems.push("La batería es un porcentaje entre 0 y 100.");
  }
  if (input.waitingHere) {
    if (input.orderChoice === null) problems.push("Hay un pedido pagado que espera este equipo: elige si es de ese pedido o entra como stock.");
    if (input.orderChoice === "stock" && input.stockReason.trim().length < 5) {
      problems.push("Escribe por qué entra como stock (al menos 5 caracteres).");
    }
  }
  return problems;
}

// ---------------------------------------------------------------------------
// The server
// ---------------------------------------------------------------------------

export type ScanConfigurations = PosModelConfigurations & {
  used_variants: {
    id: number; name: string; is_serialized: boolean; requires_imei: boolean; pending_draft: boolean; option_ids: number[];
  }[];
  branch: { id: number; name: string };
};

export type ScanReceived = {
  replayed: boolean;
  mode: "stock" | "order";
  condition: "new" | "used";
  unit: {
    id: number; product_id: number; product_name: string; branch_name: string; label_code: string | null;
    imei: string | null; imei2: string | null; serial_number: string; status_label: string;
    inspection_label?: string;
  };
  branch: { id: number; name: string };
  created_variant: boolean;
  order: { id: number; item_id: number } | null;
  method: EntryMethod;
  warnings: string[];
  label_batch?: unknown;
  label_error?: boolean;
};

export class ScanIntakeError extends Error {
  code: string;
  field: string;
  status: number;
  lines: PosPendingOrderLine[];
  constructor(message: string, code: string, field: string, status: number, lines: PosPendingOrderLine[] = []) {
    super(message);
    this.name = "ScanIntakeError";
    this.code = code;
    this.field = field;
    this.status = status;
    this.lines = lines;
  }
}

async function failure(res: Response, fallback: string): Promise<ScanIntakeError> {
  const payload = (await res.json().catch(() => null)) as
    | { detail?: string; code?: string; field?: string; lines?: PosPendingOrderLine[] }
    | null;
  return new ScanIntakeError(
    payload?.detail ? String(payload.detail) : fallback, payload?.code ?? "", payload?.field ?? "", res.status,
    Array.isArray(payload?.lines) ? payload.lines : [],
  );
}

function query(companyId: number | null, extra: Record<string, string> = {}) {
  const qs = new URLSearchParams(extra);
  if (companyId) qs.set("company", String(companyId));
  return qs.toString();
}

export async function scanCheck(
  companyId: number | null, code: string, opts: { productId?: number | null; choice?: Choice } = {},
): Promise<ScanCheck> {
  const res = await fetchWithAuth(`${API_BASE}/admin/inventory/scan/check/?${query(companyId)}`, {
    method: "POST",
    body: JSON.stringify({
      code, ...(opts.productId ? { product_id: opts.productId } : {}), ...(opts.choice ? { choice: opts.choice } : {}),
    }),
  });
  if (res.ok) return res.json();
  throw await failure(res, "No se pudo consultar el código.");
}

export async function scanModelSearch(companyId: number | null, branchId: number, q: string): Promise<PosModelHit[]> {
  const res = await fetchWithAuth(`${API_BASE}/admin/inventory/scan/models/?${query(companyId, { branch: String(branchId), q })}`);
  if (!res.ok) throw await failure(res, "No se pudo buscar el modelo.");
  const body = await res.json();
  return Array.isArray(body?.results) ? body.results : [];
}

export async function scanConfigurations(companyId: number | null, branchId: number, modelId: number): Promise<ScanConfigurations> {
  const res = await fetchWithAuth(
    `${API_BASE}/admin/inventory/scan/models/${modelId}/configurations/?${query(companyId, { branch: String(branchId) })}`);
  if (!res.ok) throw await failure(res, "No se pudo leer el modelo.");
  return res.json();
}

export async function scanReceive(
  companyId: number | null, branchId: number, body: Record<string, unknown>, idempotencyKey: string,
): Promise<ScanReceived> {
  const res = await fetchWithAuth(`${API_BASE}/admin/inventory/scan/receive/?${query(companyId, { branch: String(branchId) })}`, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(body),
  });
  if (res.ok) return res.json();
  throw await failure(res, "No se pudo registrar el equipo.");
}

const TERMINATOR_KEY = "scan-intake-terminator";

/** Preferencia de este navegador; sin almacenamiento (ventana privada) vale Enter. */
export function readTerminator(): Terminator {
  try {
    const value = storageGet("local", TERMINATOR_KEY);
    return value === "tab" || value === "both" ? value : "enter";
  } catch {
    return "enter";
  }
}

export function saveTerminator(value: Terminator): void {
  try {
    storageSet("local", TERMINATOR_KEY, value);
  } catch {
    /* sin almacenamiento: se queda para esta visita */
  }
}
