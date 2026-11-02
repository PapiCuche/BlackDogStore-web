/**
 * INV-RECEIVE-UX-02 — la lista pegada dentro del asistente «Recibir mercadería».
 *
 * Del texto a las filas (`stageRows`), de las filas a la variante del catálogo (`groupRows`, `applyMatches`,
 * `applyChoice`), de lo que falta a lo que bloquea (`evaluateRows`, `summarize`, `nextPending`) y de las filas
 * listas a las líneas de la recepción (`commitRows`).
 *
 * Este módulo no habla con el servidor y no escribe nada: sólo transforma datos en memoria. La búsqueda en el
 * catálogo es `inventory-text-match`; la recepción es `inventory-receipts` (`preview` y `confirm`). Serie, IMEI,
 * batería y costos viven en las filas, en la memoria de la página: nunca en almacenamiento, URL ni registros.
 *
 * Decide la pantalla qué se puede agregar, no qué es válido: el servidor vuelve a validar todo al revisar.
 */

import type { MatchOption, MatchResult } from "./inventory-text-match";
import { imeiIsValid, type InventoryLine, type TextCandidate, type TextPreview } from "./inventory-text-preview";
import { withoutAmounts } from "./inventory-text-stages";
import {
  batteryValue, blankUnit, costIsValid, DEVICES_LIMIT_TEXT, isUsedLine, priceIsValid, PUBLIC_NOTES_MAX, LINES_LIMIT_TEXT, MAX_DEVICES, MAX_LINES, MAX_QUANTITY,
  newLine, UNIT_NOTES_MAX, unitsInUse, type DraftLine, type DraftUnit, type ReceiptProduct,
} from "./inventory-receipts";

export type StagedRow = {
  /** Local, stable, never sent. */
  id: number;
  kind: "device" | "accessory";
  line: InventoryLine;
  model: string;
  capacityGb: number | null;
  colour: string;
  /** RAM, chip or eSIM and region: what the catalogue does not distinguish yet. Shown, never dropped in silence. */
  ramGb: number | null;
  sim: "chip" | "esim" | "";
  region: string;
  serial: string;
  imei: string;
  imei2: string;
  /** `StockUnit.condition`: follows the line until the person says otherwise. */
  condition: string;
  /** Typed as is: a half-typed number is never rewritten. */
  battery: string;
  grade: string;
  notes: string;
  cost: string;
  /** INV-COM-01A · what the web asks for this used device, as typed; needs `products.manage`. */
  price: string;
  /** INV-COM-01A · what the buyer reads (the internal `notes` stay internal). */
  publicNotes: string;
  quantity: string;
  /** The pasted text of this row. Held in memory only, shown masked. */
  source: string;
  /** Notices of the recognizer that do not become a block. */
  hints: string[];
  product: ReceiptProduct | null;
  /**
   * INV-TEXT-03 · amounts the text does not place (a bare «Precio», two amounts without labels): a person says
   * whether one is the cost or the selling price, or drops them. Until then the row waits; nothing is assigned.
   */
  amounts?: string[];
  /** INV-TEXT-03 · what the text said that somebody should look at once (a cost deduced, a line read from a word…). */
  doubts?: Doubt[];
  /** INV-TEXT-03 · the fields a person corrected or confirmed after the analysis, shown as such. */
  corrected?: string[];
  /** INV-TEXT-03 · «estado A» as written: linked to one of the company's grades only when the label is the same. */
  gradeText?: string;
  /**
   * INV-TEXT-03 · an accessory whose article already has units in the receipt (or in an earlier row of the list):
   * «add» sums them, «replace» puts this quantity in place of the receipt's. Never summed without saying so.
   */
  merge?: "" | MergeDecision;
};

/**
 * INV-TEXT-03 · a «Sumar» / «Reemplazar» is valid only for what the person saw when choosing it: the units already in the
 * receipt, its lines and the earlier row of the list. Any change of that context (a row removed, the line edited)
 * voids it and the row asks again.
 */
export type MergeDecision = { choice: "add" | "replace"; inReceipt: number; receiptLines: number; earlierRow: number | null };

/** INV-TEXT-03 · a field the recognizer filled on its own and a person should confirm; `blocking` until confirmed. */
export type Doubt = { field: IssueField; text: string; blocking?: boolean };

/** `prices`: `products.manage` — may carry the selling price the text gives a used device (INV-COM-01A). */
export type StageOptions = { costs: boolean; prices?: boolean };

// ── From the text to the rows ─────────────────────────────────────────────

/** Words that say a device is not new. */
const USED_WORDS = /\b(?:seminuevo|semi-nuevo|usado|reacondicionado|refurbished|rfb)\b/i;
/** Recognizer notices worth showing: the rest is either a block of this screen or about supplier offers. */
const KEPT_HINTS = [/^Importe \S+ demasiado bajo/, /^Importe en dólares/, /^Capacidad no indicada/,
  /^Parece un equipo/, /^Varias capacidades/, /^Varios colores/, /^Cantidades distintas/, /^El texto dice nuevo y seminuevo/,
  /^Precios de venta distintos/];

const norm = (value: string): string => value.trim().toLocaleLowerCase("es").replace(/\s+/g, " ");

/** What a device row may be split into when the text says «x2»: one row per device, up to this many. */
export const MAX_EXPANDED = 50;

export function stageRows(preview: TextPreview, options: StageOptions, startId = 1): StagedRow[] {
  const prices = Boolean(options.prices);
  const staged = preview.candidates.flatMap((candidate: TextCandidate): Omit<StagedRow, "id">[] => {
    const hints = candidate.warnings
      .filter((warning) => KEPT_HINTS.some((pattern) => pattern.test(warning)))
      .map((warning) => (options.costs ? warning : withoutAmounts(warning)));
    const doubts: Doubt[] = [];
    // The line a row states wins over the list's: «seminuevo» in a list of new ones is used, «nvo» in a list of used
    // ones is new. Said, and the person can turn it back. Nothing is deduced from silence.
    let line = candidate.line;
    const stated = candidate.statedLine || (USED_WORDS.test(candidate.source) ? "used" : "");
    if (candidate.kind === "device" && stated && stated !== line) {
      line = stated;
      // Against the list's own line: a person confirms it before the row can enter (a word can be incidental).
      doubts.push({ field: "line", blocking: true, text: stated === "used"
        ? "El texto dice «seminuevo»: se marcó así. Corrígelo si no es correcto."
        : "El texto dice «nuevo»: se marcó así. Corrígelo si no es correcto." });
    }
    if (candidate.batteryPercent !== null && line === "new") {
      hints.push("El texto trae batería, pero un equipo nuevo no la lleva: no se usa. Si es seminuevo, cambia la línea.");
    }
    // Amounts. A labelled cost only for who may see costs. INV-TEXT-03 review: the lone amount of a row is never
    // written as the cost by itself — it waits, with the other undecided amounts, for «Es el costo» (or «Son costos»).
    const lone = candidate.costFrom === "lone" ? [candidate.supplierCost] : [];
    const cost = options.costs && candidate.costFrom === "label" ? candidate.supplierCost : "";
    // A selling price only for a used device and only for who may set prices; never derived from the cost.
    const priceable = candidate.kind === "device" && line === "used" && prices;
    const price = priceable ? candidate.salePrice : "";
    if (candidate.salePrice && !priceable) {
      hints.push(!prices
        ? "El texto trae un precio de venta, pero tu usuario no puede fijar precios: no se usa."
        : "El texto trae un precio de venta: sólo un seminuevo lleva precio propio; este se vende al precio del artículo. No se usa.");
    }
    // An amount nobody can place waits for a person who may decide it (cost or selling price); else it is said, unused.
    // Without `costs.view` neither the lone amount (a supplier's cost by convention) nor amounts a label called «costo»
    // are ever shown or offered.
    const amounts = options.costs ? [...lone, ...candidate.ambiguousAmounts]
      : priceable ? candidate.ambiguousAmounts.filter((amount) => !candidate.ambiguousCosts.includes(amount)) : [];
    if (candidate.ambiguousAmounts.length && !amounts.length) hints.push("El texto trae un importe sin etiqueta clara: no se usa.");
    if (candidate.imeiFrom === "bare") doubts.push({ field: "imei", text: "IMEI tomado de un número sin etiqueta: revisa que sea el IMEI." });
    const quantity = candidate.kind === "accessory" && candidate.statedQuantity !== null ? String(candidate.statedQuantity) : "";
    // «x 25» with a space and no other amount in the row could be «por 25 soles»: confirmed before the row can enter.
    const couldBePrice = candidate.quantitySpaced && (candidate.statedQuantity ?? 0) >= 10 && !candidate.supplierCost
      && !candidate.salePrice && !candidate.ambiguousAmounts.length;
    if (quantity && couldBePrice) {
      doubts.push({ field: "quantity", blocking: true, text: `«x ${quantity}» puede ser la cantidad o un precio («por ${quantity}»): confirma que llegan ${quantity} unidades.` });
    } else if (quantity) doubts.push({ field: "quantity", text: `Cantidad tomada del texto (${quantity}): confírmala.` });
    // «x2» of a device: one row per device (each has its own serial), when the text names no identifier.
    let copies = 1;
    const said = candidate.statedQuantity ?? 1;
    if (candidate.kind === "device" && said > 1) {
      if (candidate.serial || candidate.imei) hints.push(`El texto dice ${said} unidades pero trae una sola serie o IMEI: agrega los otros equipos a mano.`);
      else if (said > MAX_EXPANDED) hints.push(`El texto dice ${said} unidades: se preparó una fila; agrega las demás en otra tanda.`);
      else {
        copies = said;
        hints.push(`El texto dice ${said} unidades: se preparó una fila por equipo.`);
      }
    }
    if (candidate.kind === "device" && line === "used" && candidate.gradeFrom === "bare") {
      hints.push(`El texto dice «${candidate.gradeText}»: si es el estado estético, elígelo.`);
    }
    const row: Omit<StagedRow, "id"> = {
      kind: candidate.kind,
      line,
      model: candidate.model,
      capacityGb: candidate.capacityGb,
      colour: candidate.colour,
      ramGb: candidate.ramGb,
      sim: candidate.sim,
      region: candidate.region,
      serial: candidate.serial,
      imei: candidate.imei,
      imei2: candidate.imei2,
      condition: line === "used" ? "used" : "new",
      battery: candidate.batteryPercent === null || line === "new" ? "" : String(candidate.batteryPercent),
      grade: "",
      notes: candidate.notes.slice(0, UNIT_NOTES_MAX),
      cost,
      price,
      publicNotes: "",
      quantity,
      source: candidate.source,
      hints,
      product: null,
      amounts,
      doubts,
      corrected: [],
      // Only a grade after «estado»/«grado» is linked by itself; a bare «9/10» is said, for a person to choose.
      gradeText: candidate.kind === "device" && line === "used" && candidate.gradeFrom === "label" ? candidate.gradeText : "",
      merge: "",
    };
    return Array.from({ length: copies }, () => ({ ...row, hints: [...hints], doubts: [...doubts], amounts: [...amounts] }));
  });
  return staged.map((row, index) => ({ ...row, id: startId + index }));
}

type Grade = { id: number; label: string; code: string };

/** INV-TEXT-03 · the company's grade whose label (or code) is the text's, letter for letter; else none. */
export function gradeFor(text: string, grades: Grade[]): number | null {
  const wanted = norm(text);
  if (!wanted) return null;
  const found = grades.filter((g) => norm(g.label) === wanted || norm(g.code) === wanted);
  return found.length === 1 ? found[0].id : null;
}

/** Links the text's grade where exactly one of the company's grades says the same. The SAME array when nothing changes. */
export function applyGrades(rows: StagedRow[], grades: Grade[]): StagedRow[] {
  let changed = false;
  const next = rows.map((row) => {
    if (!row.gradeText || row.grade) return row;
    const id = gradeFor(row.gradeText, grades);
    if (id === null) return row;
    changed = true;
    return { ...row, grade: String(id) };
  });
  return changed ? next : rows;
}

// ── Catalogue: one decision per combination ───────────────────────────────

/** What makes two rows the same variant of the catalogue: kind, line, model, capacity and colour. */
export const rowSignature = (row: Pick<StagedRow, "kind" | "line" | "model" | "capacityGb" | "colour">): string =>
  [row.kind, row.line, norm(row.model), row.capacityGb ?? "", norm(row.colour)].join("|");

export type Group = { key: string; rows: StagedRow[] };

/** Rows grouped by signature, in the order they first appear. */
export function groupRows(rows: StagedRow[]): Group[] {
  const groups = new Map<string, Group>();
  for (const row of rows) {
    const key = rowSignature(row);
    const group = groups.get(key);
    if (group) group.rows.push(row);
    else groups.set(key, { key, rows: [row] });
  }
  return [...groups.values()];
}

export type MatchKind = "exact" | "suggested" | "ambiguous" | "missing" | "unsearched";

export function classify(result: MatchResult | undefined): MatchKind {
  if (!result) return "unsearched";
  if (result.status === "exact" && result.options.length === 1) return "exact";
  if (result.status === "none" || result.options.length === 0) return "missing";
  return result.options.length === 1 ? "suggested" : "ambiguous";
}

/** An option of the catalogue as the line of a receipt needs it. */
export function optionToProduct(option: MatchOption, kind: "device" | "accessory"): ReceiptProduct {
  return {
    id: option.product_id,
    name: option.name,
    is_serialized: option.is_serialized ?? kind === "device",
    requires_imei: option.requires_imei ?? kind === "device",
    condition_line: option.condition_line === "used" ? "used" : "new",
    ...(option.is_active === undefined ? {} : { is_active: option.is_active }),
  };
}

/**
 * The exact answers choose by themselves; a choice the person already made is kept. The SAME array comes back when
 * nothing changed, so it can run on every change of the list without a render loop.
 */
export function applyMatches(rows: StagedRow[], answers: Record<string, MatchResult>): StagedRow[] {
  // A combination that already has an article keeps THAT one — chosen by hand or linked before — so a row that
  // arrives later (another paste, a model corrected into this combination) joins the group instead of taking
  // whatever the server's exact answer says while the header shows another article.
  const settled = new Map<string, ReceiptProduct>();
  for (const row of rows) if (row.product && !settled.has(rowSignature(row))) settled.set(rowSignature(row), row.product);
  let changed = false;
  const next = rows.map((row) => {
    if (row.product) return row;
    const signature = rowSignature(row);
    const kept = settled.get(signature);
    if (kept) { changed = true; return { ...row, product: kept }; }
    const result = answers[signature];
    if (classify(result) !== "exact") return row;
    changed = true;
    return { ...row, product: optionToProduct(result!.options[0], row.kind) };
  });
  return changed ? next : rows;
}

/** One choice for the whole group, and only for it. Identifiers, battery and costs stay each row's own. */
export function applyChoice(rows: StagedRow[], groupKey: string, product: ReceiptProduct | null): StagedRow[] {
  // Another article is another decision about adding to what the receipt already has.
  return rows.map((row) => (rowSignature(row) === groupKey ? { ...row, product, merge: row.product?.id === product?.id ? row.merge : ("" as const) } : row));
}

/** The fields that decide WHICH variant a row is: changing one releases the row's article. */
const IDENTITY: (keyof StagedRow)[] = ["kind", "line", "model", "capacityGb", "colour"];

/** The fields a person corrects in a row, by the name the screen shows («Corregido: costo, serie»). */
export const FIELD_LABEL: Record<string, string> = {
  serial: "serie", imei: "IMEI", imei2: "IMEI 2", quantity: "cantidad", battery: "batería", cost: "costo", notes: "observaciones",
  price: "precio de venta", publicNotes: "detalles", line: "línea", model: "modelo", capacityGb: "capacidad", colour: "color",
  condition: "estado", grade: "estado estético", amount: "importe", merge: "cantidad en la recepción",
};

export function editRow(rows: StagedRow[], id: number, change: Partial<StagedRow>): StagedRow[] {
  return rows.map((row) => {
    if (row.id !== id) return row;
    const next: StagedRow = { ...row, ...change };
    // INV-TEXT-03: what the person changed is shown as corrected, and a doubt about that field is settled by it.
    const touched = (Object.keys(change) as (keyof StagedRow)[]).filter((key) => key in FIELD_LABEL && change[key] !== row[key]);
    if (touched.length) {
      next.corrected = [...new Set([...(row.corrected ?? []), ...touched])];
      next.doubts = (row.doubts ?? []).filter((d) => !touched.includes(d.field as keyof StagedRow));
    }
    // The condition follows the line only while it is still the line's own default: a state set by hand stays.
    if (change.line && change.line !== row.line && change.condition === undefined
      && row.condition === (row.line === "used" ? "used" : "new")) {
      next.condition = change.line === "used" ? "used" : "new";
    }
    // Battery and grade stay in the row while the device is new: they are neither shown, checked nor sent for it
    // (see `unitOf`), and a misclick on «Son nuevos» does not lose what was measured.
    if (IDENTITY.some((field) => field in change && change[field] !== row[field])) { next.product = null; next.merge = ""; }
    return next;
  });
}

/** INV-TEXT-03 · «Es correcto»: the doubt about that field goes, and the field shows as confirmed. Nothing else changes. */
export function confirmDoubt(rows: StagedRow[], id: number, field: IssueField): StagedRow[] {
  return rows.map((row) => (row.id !== id || !(row.doubts ?? []).some((d) => d.field === field) ? row : {
    ...row, doubts: (row.doubts ?? []).filter((d) => d.field !== field), corrected: [...new Set([...(row.corrected ?? []), field])],
  }));
}

/**
 * INV-TEXT-03 · a person says what an amount the text did not place is: the cost, the selling price, or nothing
 * («drop»). Only the rows given, only that amount; the other amounts of the row are dropped with it (one decision).
 * Never both: an amount is a cost OR a price, and neither is derived from the other.
 */
export function resolveAmount(rows: StagedRow[], ids: number[], as: "cost" | "price" | "drop", pick?: (row: StagedRow) => string | undefined): StagedRow[] {
  const wanted = new Set(ids);
  return rows.map((row) => {
    if (!wanted.has(row.id) || !(row.amounts ?? []).length) return row;
    const value = (pick?.(row) ?? row.amounts![0]) || "";
    const next: StagedRow = { ...row, amounts: [], corrected: [...new Set([...(row.corrected ?? []), "amount"])] };
    if (as === "cost") next.cost = value;
    if (as === "price") next.price = value;
    if (as !== "drop") next.doubts = (row.doubts ?? []).filter((d) => d.field !== as);
    return next;
  });
}

/**
 * INV-TEXT-03 review · the rows a «Son costos» / «Son precios de venta» / «No usarlos» for the whole list acts on. A row
 * that already has that value (a labelled cost, a labelled price) is never overwritten in bulk; a row with several
 * undecided amounts is decided one by one.
 */
export function bulkTargets(rows: StagedRow[], as: "cost" | "price" | "drop"): StagedRow[] {
  const pending = rows.filter((row) => (row.amounts ?? []).length);
  if (as === "drop") return pending;
  const single = pending.filter((row) => row.amounts!.length === 1);
  if (as === "cost") return single.filter((row) => !row.cost.trim());
  return single.filter((row) => !row.price.trim() && row.kind === "device" && row.line === "used" && (!row.product || isUsedLine(row.product)));
}

/** The next free id for a new paste: never one that is already in the list, whatever the screen remembers. */
export function nextRowId(rows: StagedRow[], floor = 1): number {
  return rows.reduce((top, row) => Math.max(top, row.id + 1), Math.max(1, floor));
}

/** A list in parts of at most `size`, in order, nothing lost or repeated. */
export function chunk<T>(items: T[], size: number): T[][] {
  const parts: T[][] = [];
  for (let at = 0; at < items.length; at += size) parts.push(items.slice(at, at + size));
  return parts;
}

/**
 * The scanner types a value and presses Enter: where the cursor goes next. The scan order is every device's serial
 * and, where it matters (the article needs it or one was typed), its IMEI. A field outside that order (an optional
 * IMEI left empty, IMEI 2, a cost) continues after the LAST scan field of its own row.
 */
export function nextScanTarget(rows: StagedRow[], rowId: number, field: string): { row: number; field: "serial" | "imei" } | null {
  const order: { row: number; field: "serial" | "imei" }[] = [];
  for (const row of rows) {
    if (row.kind !== "device") continue;
    order.push({ row: row.id, field: "serial" });
    if (row.product?.requires_imei || row.imei.trim()) order.push({ row: row.id, field: "imei" });
  }
  let at = order.findIndex((o) => o.row === rowId && o.field === field);
  if (at < 0) at = order.map((o) => o.row).lastIndexOf(rowId);
  const empty = (o: { row: number; field: "serial" | "imei" }) => {
    const row = rows.find((r) => r.id === o.row);
    return !(o.field === "serial" ? row?.serial : row?.imei)?.trim();
  };
  const here = order[at];
  const after = order.slice(at + 1).find(empty);
  if (after) return after;
  return order.slice(0, Math.max(at, 0)).find((o) => empty(o) && !(here && o.row === here.row && o.field === here.field)) ?? null;
}

/** Rows the working list holds at most (the contract takes 200 devices per reception: the rest waits its turn). */
export const MAX_STAGED = 600;
/** What the proposal endpoint reads per request. */
export const MAX_PROPOSAL_ROWS = 300;

/**
 * The devices that ask the catalogue to propose what is missing: every device of a combination with no article
 * (so the counts say the truth); one per combination when they would exceed what the server reads. Accessories
 * are never created from a list.
 */
export function proposalRowsFor(rows: StagedRow[]): StagedRow[] {
  const devices = rows.filter((row) => row.kind === "device" && !row.product);
  if (devices.length <= MAX_PROPOSAL_ROWS) return devices;
  return groupRows(devices).map((group) => group.rows[0]);
}

// ── What is missing and what blocks ───────────────────────────────────────

export type IssueKind = "catalog" | "info" | "invalid" | "warn";
export type IssueField = "product" | "serial" | "imei" | "imei2" | "quantity" | "battery" | "cost" | "notes" | "price" | "publicNotes"
  | "line" | "amount" | "merge";
export type RowIssue = { kind: IssueKind; field: IssueField; text: string };
/** A row is ready, or it waits for the catalogue, for a missing datum, or it is blocked. A `warn` never blocks. */
export type Bucket = "ready" | "catalog" | "info" | "invalid";
/**
 * INV-TEXT-03 · an accessory whose article is already counted: how many units the receipt has (in how many lines) and
 * the earlier row of the list with the same article. Present only when there is something to add to.
 */
export type MergeContext = { inReceipt: number; receiptLines: number; earlierRow: number | null };
export type RowEvaluation = { row: StagedRow; issues: RowIssue[]; bucket: Bucket; merge?: MergeContext };

const ASCII = /^[\x20-\x7E]+$/;
const NOT_ASCII = "Escribe sólo letras sin tilde, números y signos comunes (revisa el teclado del lector).";
const digits = (value: string) => value.replace(/[^0-9]/g, "");

/** The choice a row made, if it was made for exactly this context; else none. */
export function mergeChoice(row: Pick<StagedRow, "merge">, context: MergeContext | undefined): "add" | "replace" | "" {
  const decided = row.merge;
  if (!decided || !context) return "";
  return decided.inReceipt === context.inReceipt && decided.receiptLines === context.receiptLines
    && decided.earlierRow === context.earlierRow ? decided.choice : "";
}

export function evaluateRows(input: { rows: StagedRow[]; lines: DraftLine[]; costs: boolean; prices?: boolean }): RowEvaluation[] {
  const { rows, lines, costs } = input;
  const prices = Boolean(input.prices);
  // Repeats are counted across the whole reception: the list and what is already in it.
  const seen = new Map<string, number>();
  const bump = (key: string) => seen.set(key, (seen.get(key) ?? 0) + 1);
  const serialKey = (owner: string, value: string) => `s${owner}|${norm(value)}`;
  const imeiKey = (value: string) => `i|${digits(value)}`;
  const owner = (row: StagedRow) => (row.product ? String(row.product.id) : `g${rowSignature(row)}`);
  for (const row of rows) {
    if (row.kind !== "device") continue;
    if (row.serial.trim()) bump(serialKey(owner(row), row.serial));
    if (row.imei.trim()) bump(imeiKey(row.imei));
    if (row.imei2.trim()) bump(imeiKey(row.imei2));
  }
  for (const line of lines) {
    if (!line.product.is_serialized) continue;
    for (const unit of unitsInUse(line)) {
      if (unit.serial_number.trim()) bump(serialKey(String(line.product.id), unit.serial_number));
      if (unit.imei.trim()) bump(imeiKey(unit.imei));
      if (unit.imei2.trim()) bump(imeiKey(unit.imei2));
    }
  }
  const repeated = (key: string) => (seen.get(key) ?? 0) > 1;
  // The first row of the list for each quantity article: a later one with the same article would add to it.
  const firstRow = new Map<number, number>();

  return rows.map((row): RowEvaluation => {
    const issues: RowIssue[] = [];
    let merge: MergeContext | undefined;
    const add = (kind: IssueKind, field: IssueField, text: string) => issues.push({ kind, field, text });
    const product = row.product;
    if (!product) add("catalog", "product", "Falta asociar el artículo del catálogo.");

    if (product && product.is_active === false) {
      add("invalid", "product", `«${product.name}» está desactivado: actívalo antes de recibirlo o elige otro artículo.`);
    }
    // The article's line (commercial) and the unit's condition (physical) are two things: said, never forced.
    if (product?.condition_line && row.kind === "device" && row.line !== "accessory" && product.condition_line !== row.line) {
      add("warn", "product", `«${product.name}» es un artículo ${product.condition_line === "used" ? "seminuevo" : "nuevo"} y esta fila es ${row.line === "used" ? "seminuevo" : "nuevo"}: revisa que sea el artículo correcto.`);
    }
    if (row.kind === "device") {
      if (product && !product.is_serialized) {
        add("invalid", "product", `«${product.name}» se recibe por cantidad, no por serie: elige otro artículo.`);
      }
      const serial = row.serial.trim();
      if (!serial) add("info", "serial", "Falta el número de serie.");
      else if (!ASCII.test(serial)) add("invalid", "serial", NOT_ASCII);
      else if (repeated(serialKey(owner(row), serial))) add("invalid", "serial", "Este número de serie está repetido en la recepción.");

      const imei = row.imei.trim();
      const imei2 = row.imei2.trim();
      if (!imei) {
        if (product?.requires_imei) add("info", "imei", "Falta el IMEI: este equipo lo necesita.");
      } else if (!imeiIsValid(imei)) add("invalid", "imei", "El IMEI no tiene 15 dígitos o no supera su dígito de control: revisa que esté bien copiado.");
      else if (repeated(imeiKey(imei))) add("invalid", "imei", "Este IMEI está repetido en la recepción.");
      if (imei2) {
        if (!imei) add("invalid", "imei2", "El IMEI 2 acompaña al IMEI 1: escribe primero el principal.");
        else if (digits(imei2) === digits(imei)) add("invalid", "imei2", "El IMEI 2 debe ser distinto del IMEI 1.");
        else if (!imeiIsValid(imei2)) add("invalid", "imei2", "El IMEI 2 no tiene 15 dígitos o no supera su dígito de control.");
        else if (repeated(imeiKey(imei2))) add("invalid", "imei2", "Este IMEI 2 está repetido en la recepción.");
      }
      if (row.condition !== "new" && row.battery.trim() && batteryValue(row.battery) === null) add("invalid", "battery", "La batería se indica en % entero, de 0 a 100.");
      if (row.notes.trim().length > UNIT_NOTES_MAX) add("invalid", "notes", `Las observaciones admiten hasta ${UNIT_NOTES_MAX} caracteres.`);
    } else {
      if (product?.is_serialized) {
        add("invalid", "product", `«${product.name}» se controla por serie: recíbelo como equipo, no como accesorio.`);
      }
      const quantity = row.quantity.trim();
      if (!quantity) add("info", "quantity", "Escribe cuántas unidades entran.");
      else if (!/^\d+$/.test(quantity) || Number(quantity) < 1) add("invalid", "quantity", "La cantidad es un número entero, mínimo 1.");
      else if (Number(quantity) > MAX_QUANTITY) add("invalid", "quantity", `Son demasiadas unidades de una vez (máximo ${MAX_QUANTITY}).`);
      // INV-TEXT-03: units of an article that is already counted never add up by themselves (the «x5 onto 1 = 6» case).
      if (product && !product.is_serialized) {
        const inReceipt = lines.filter((l) => l.product.id === product.id && !l.product.is_serialized);
        const earlierRow = firstRow.get(product.id) ?? null;
        if (earlierRow === null) firstRow.set(product.id, row.id);
        if (inReceipt.length || earlierRow !== null) {
          merge = { inReceipt: inReceipt.reduce((n, l) => n + (Number(l.quantity) || 0), 0), receiptLines: inReceipt.length, earlierRow };
          const canReplace = earlierRow === null && inReceipt.length === 1;
          const chosen = mergeChoice(row, merge);
          if (!chosen || (chosen === "replace" && !canReplace)) {
            add("info", "merge", earlierRow !== null
              ? `«${product.name}» ya está en otra fila de esta lista${merge.inReceipt ? ` y la recepción ya tiene ${merge.inReceipt}` : ""}: elige «Sumar» si llegaron todas, o quita esta fila.`
              : canReplace
                ? `La recepción ya tiene ${merge.inReceipt} de «${product.name}»: elige «Sumar» o «Reemplazar».`
                : `La recepción ya tiene ${merge.inReceipt} de «${product.name}» en varias líneas: elige «Sumar» o quita esta fila.`);
          }
        }
      }
    }
    if (costs && row.cost.trim() && !costIsValid(row.cost)) add("invalid", "cost", "Escribe el costo con hasta 2 decimales, por ejemplo 12.50.");
    // INV-COM-01A: a used device's own price and buyer notes — checked where they are sent. Never a block for a
    // missing price: the device is received and waits for somebody with authority to price it.
    if (product && isUsedLine(product) && row.kind === "device") {
      if (prices && row.price.trim() && !priceIsValid(row.price)) add("invalid", "price", "Escribe el precio con hasta 2 decimales y mayor que cero, por ejemplo 1899.00.");
      if (row.publicNotes.trim().length > PUBLIC_NOTES_MAX) add("invalid", "publicNotes", `Los detalles para el comprador admiten hasta ${PUBLIC_NOTES_MAX} caracteres.`);
    } else if (product && prices && row.price.trim()) {
      // A price written for a row whose article turned out new (or an accessory) is not sent: said, never in silence.
      add("warn", "price", "El precio de venta de esta fila no se usa: el artículo elegido no es seminuevo y se vende a su propio precio.");
    }
    // INV-TEXT-03: an amount the text did not place waits for a person: cost, selling price, or not used.
    if ((row.amounts ?? []).length) {
      add("info", "amount", "El texto trae un importe sin etiqueta clara: indica si es el costo o el precio de venta, o no lo uses.");
    }
    // What the recognizer filled on its own is shown on its field until somebody confirms or corrects it; a blocking one
    // (the line the row states against the list's) holds the row until then.
    for (const doubt of row.doubts ?? []) add(doubt.blocking ? "info" : "warn", doubt.field, doubt.text);

    const bucket: Bucket = issues.some((i) => i.kind === "catalog") ? "catalog"
      : issues.some((i) => i.kind === "invalid") ? "invalid"
        : issues.some((i) => i.kind === "info") ? "info" : "ready";
    return merge ? { row, issues, bucket, merge } : { row, issues, bucket };
  });
}

/** The reason to SHOW for a row: what blocks first, then what is missing, a notice last. The catalogue is the group's. */
export function leadIssue(issues: RowIssue[]): RowIssue | undefined {
  return issues.find((i) => i.kind === "invalid") ?? issues.find((i) => i.kind === "info") ?? issues.find((i) => i.kind === "warn");
}

export type Counts = { total: number; ready: number; catalog: number; info: number; invalid: number };

export function summarize(evaluation: RowEvaluation[]): Counts {
  const counts: Counts = { total: evaluation.length, ready: 0, catalog: 0, info: 0, invalid: 0 };
  for (const item of evaluation) counts[item.bucket] += 1;
  return counts;
}

export type Pending =
  | { kind: "catalog"; group: string }
  | { kind: "info" | "invalid" | "warn"; row: number; field: IssueField };

const samePending = (a: Pending, b: Pending) => a.kind === b.kind
  && (a.kind === "catalog" ? a.group === (b as { group: string }).group
    : (a as { row: number }).row === (b as { row: number }).row && (a as { field: string }).field === (b as { field: string }).field);

/**
 * Everything still to look at, in the order «Siguiente error» walks it: the catalogue first (one decision per group),
 * then what blocks or is missing, row by row and field by field, and last what the recognizer filled on its own and
 * nobody confirmed yet (a doubt never blocks, but it is visited).
 */
export function pendingItems(evaluation: RowEvaluation[]): Pending[] {
  const out: Pending[] = [];
  const groups = new Set<string>();
  for (const item of evaluation) {
    if (item.bucket !== "catalog") continue;
    const group = rowSignature(item.row);
    if (!groups.has(group)) { groups.add(group); out.push({ kind: "catalog", group }); }
  }
  for (const item of evaluation) {
    if (item.bucket === "catalog") continue;
    for (const issue of item.issues) {
      if (issue.kind === "invalid" || issue.kind === "info") {
        const pending: Pending = { kind: issue.kind, row: item.row.id, field: issue.field };
        if (!out.some((p) => samePending(p, pending))) out.push(pending);
      }
    }
  }
  for (const item of evaluation) {
    for (const doubt of item.row.doubts ?? []) {
      if (doubt.blocking) continue; // already walked with what blocks
      const pending: Pending = { kind: "warn", row: item.row.id, field: doubt.field };
      if (!out.some((p) => samePending(p, pending))) out.push(pending);
    }
  }
  return out;
}

/**
 * Where «Siguiente error» goes: the first item, or the one after `after` (wrapping to the start), so pressing it again
 * moves on instead of landing on the same field. Null when nothing is left.
 */
export function nextPending(rows: StagedRow[], evaluation: RowEvaluation[], after: Pending | null = null): Pending | null {
  void rows;
  const items = pendingItems(evaluation);
  if (!items.length) return null;
  const at = after ? items.findIndex((item) => samePending(item, after)) : -1;
  if (at < 0) {
    // The previous place was resolved: what comes next is what follows it in the same walk, else the first one.
    return items[0];
  }
  return items[(at + 1) % items.length];
}

// ── From the list to the receipt ──────────────────────────────────────────

export type Commit = {
  lines: DraftLine[];
  /** Ids of the rows that entered. */
  committed: number[];
  /** The rows that did not enter, still to resolve (or waiting for room). */
  left: StagedRow[];
  /** Said when the caps of one reception stopped some ready rows. */
  limit: string | null;
};

function unitOf(row: StagedRow, costs: boolean, prices = false): DraftUnit {
  return {
    ...blankUnit(row.condition),
    serial_number: row.serial.trim(), imei: row.imei.trim(), imei2: row.imei2.trim(),
    cost: costs ? row.cost.trim() : "",
    battery: row.condition === "new" ? "" : row.battery.trim(), grade: row.condition === "new" ? "" : row.grade.trim(),
    notes: row.notes.trim(),
    price: prices ? row.price.trim() : "",
    publicNotes: row.publicNotes.trim(),
  };
}

/**
 * The READY rows join the receipt's lines — a device as a unit of its article's line, an accessory as quantity.
 * A row that is not ready stays in the list with its reasons: nothing enters half-way and nothing is lost.
 * The caps of one reception are respected: what does not fit stays, and the person is told how many.
 */
export function commitRows(rows: StagedRow[], lines: DraftLine[], options: { costs: boolean; prices?: boolean; hold?: number[] }): Commit {
  const evaluation = evaluateRows({ rows, lines, costs: options.costs, prices: options.prices });
  const next: DraftLine[] = lines.map((line) => ({ ...line, units: [...line.units] }));
  const committed = new Set<number>();
  let devices = next.reduce((n, l) => n + (l.product.is_serialized ? unitsInUse(l).length : 0), 0);
  let limit: string | null = null;

  // Rows set aside for another reception are still checked (a duplicate against them is a duplicate), never received here.
  const held = new Set(options.hold ?? []);
  for (const { row, bucket, merge } of evaluation) {
    if (bucket !== "ready" || !row.product || held.has(row.id)) continue;
    // INV-TEXT-03 review: a row that adds to an earlier row of the list enters only together with it — never alone
    // onto what the receipt already has.
    if (merge && merge.earlierRow !== null && !committed.has(merge.earlierRow)) {
      limit ??= "Una fila que se suma a otra de la lista entra junto con ella: agrega o resuelve primero la otra.";
      continue;
    }
    const product = row.product;
    let target = next.find((l) => l.product.id === product.id);
    if (row.kind === "device") {
      if (devices + 1 > MAX_DEVICES) { limit ??= DEVICES_LIMIT_TEXT; continue; }
      if (!target) {
        if (next.length >= MAX_LINES) { limit ??= LINES_LIMIT_TEXT; continue; }
        target = { ...newLine(product), units: [] };
        next.push(target);
      } else {
        // The blank starter row of a line is not a device: it makes room for the first one.
        target.units = target.units.filter((u) => unitsInUse({ ...target!, units: [u] }).length > 0);
      }
      target.units.push(unitOf(row, options.costs, options.prices));
      devices += 1;
    } else {
      const cost = options.costs ? row.cost.trim() : "";
      const add = Number(row.quantity.trim());
      // INV-TEXT-03: «Reemplazar» — the receipt's one line of this article takes THIS quantity (and this cost), it
      // does not add to it. Only reached when the person chose it (`evaluateRows` asks for it first).
      const own = lines.filter((l) => l.product.id === product.id && !l.product.is_serialized);
      if (mergeChoice(row, merge) === "replace" && own.length === 1) {
        const target = next[lines.indexOf(own[0])];
        target.quantity = row.quantity.trim();
        // The line's cost stays unless this row brings one (and only for who may see costs).
        if (options.costs && cost) target.unitCost = cost;
        committed.add(row.id);
        continue;
      }
      // «Sumar» (or nothing to add to): same article, same cost, the quantities add up. Another cost (or a cost
      // against none) stays its own line — the earlier units are never re-priced and no cost is dropped — and so
      // does a sum over the cap.
      const same = next.find((l) => l.product.id === product.id && !l.product.is_serialized && l.unitCost.trim() === cost
        && (Number(l.quantity) || 0) + add <= MAX_QUANTITY);
      if (same) {
        same.quantity = String((Number(same.quantity) || 0) + add);
      } else {
        if (next.length >= MAX_LINES) { limit ??= LINES_LIMIT_TEXT; continue; }
        next.push({ ...newLine(product), quantity: row.quantity.trim(), unitCost: cost });
      }
    }
    committed.add(row.id);
  }
  return { lines: next, committed: [...committed], left: rows.filter((r) => !committed.has(r.id)), limit };
}

// ── INV-TEXT-03 · the pasted text itself ──────────────────────────────────

/**
 * The text as pasted, kept in the memory of the wizard (like the rows) so a step back, a tab switch or a repeated
 * «Analizar» does not lose it. `analysed` is the text as it was last analysed; `seen` counts, per normalised source,
 * how many rows an analysis already produced, so analysing the same message again adds nothing twice.
 */
export type PasteState = { raw: string; analysed: string; seen: Record<string, number> };

export const emptyPaste = (): PasteState => ({ raw: "", analysed: "", seen: {} });

/** There is pasted text that was never analysed (what a «¿salir?» or «Siguiente» must not lose in silence). */
export const unanalysedText = (paste: PasteState): boolean => paste.raw.trim() !== "" && paste.raw !== paste.analysed;

const sourceKey = (source: string) => norm(source);

/** INV-TEXT-03 review · what the list still holds, as «already analysed» counts: rebuilt, never carried over blindly. */
export function seenFrom(rows: StagedRow[]): Record<string, number> {
  const seen: Record<string, number> = {};
  for (const row of rows) seen[sourceKey(row.source)] = (seen[sourceKey(row.source)] ?? 0) + 1;
  return seen;
}

/**
 * The rows of a new analysis that were not already produced by an earlier one (counted per source text, so two
 * identical lines of ONE message both stay), and the ones held back as repeated. Nothing is dropped in silence:
 * the caller says how many and may add them on purpose.
 */
export function withoutRepeats(rows: StagedRow[], seen: Record<string, number>): { fresh: StagedRow[]; repeated: StagedRow[]; seen: Record<string, number> } {
  const budget = { ...seen };
  const produced: Record<string, number> = {};
  const fresh: StagedRow[] = [];
  const repeated: StagedRow[] = [];
  for (const row of rows) {
    const key = sourceKey(row.source);
    if ((budget[key] ?? 0) > 0) { budget[key] -= 1; repeated.push(row); continue; }
    fresh.push(row);
    produced[key] = (produced[key] ?? 0) + 1;
  }
  const next = { ...seen };
  for (const [key, count] of Object.entries(produced)) next[key] = (next[key] ?? 0) + count;
  return { fresh, repeated, seen: next };
}

/**
 * Rows a person took out of the list on purpose (removed, discarded) no longer count as produced: pasting their
 * text again brings them back. Rows that went into the receipt keep counting, so they are not added twice.
 */
export function forgetSources(seen: Record<string, number>, rows: StagedRow[], back = false): Record<string, number> {
  const next = { ...seen };
  for (const row of rows) {
    const key = sourceKey(row.source);
    next[key] = Math.max(0, (next[key] ?? 0) + (back ? 1 : -1));
    if (!next[key]) delete next[key];
  }
  return next;
}
