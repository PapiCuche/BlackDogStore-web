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
import {
  batteryValue, blankUnit, costIsValid, DEVICES_LIMIT_TEXT, LINES_LIMIT_TEXT, MAX_DEVICES, MAX_LINES, MAX_QUANTITY,
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
  quantity: string;
  /** The pasted text of this row. Held in memory only, shown masked. */
  source: string;
  /** Notices of the recognizer that do not become a block. */
  hints: string[];
  product: ReceiptProduct | null;
};

export type StageOptions = { costs: boolean };

// ── From the text to the rows ─────────────────────────────────────────────

/** Words that say a device is not new. */
const USED_WORDS = /\b(?:seminuevo|semi-nuevo|usado|reacondicionado|refurbished|rfb)\b/i;
/** Recognizer notices worth showing: the rest is either a block of this screen or about supplier offers. */
const KEPT_HINTS = [/^Costos ambiguos/, /^Importe /, /^Capacidad no indicada/, /^Parece un equipo/];

const norm = (value: string): string => value.trim().toLocaleLowerCase("es").replace(/\s+/g, " ");

export function stageRows(preview: TextPreview, options: StageOptions, startId = 1): StagedRow[] {
  return preview.candidates.map((candidate: TextCandidate, index): StagedRow => {
    const hints = candidate.warnings
      .filter((warning) => KEPT_HINTS.some((pattern) => pattern.test(warning)))
      .map((warning) => (options.costs ? warning : warning.replace(/^Costos ambiguos \([^)]*\)/, "Costos ambiguos").replace(/^Importe \S+ demasiado bajo/, "Importe demasiado bajo")));
    // A list declared «nuevos» that says «seminuevo» in one row: that row is used. The opposite is never deduced.
    let line = candidate.line;
    if (candidate.kind === "device" && line === "new" && USED_WORDS.test(candidate.source)) {
      line = "used";
      hints.push("El texto dice «seminuevo»: se marcó así. Corrígelo si no es correcto.");
    }
    if (candidate.batteryPercent !== null && line === "new") {
      hints.push("El texto trae batería, pero un equipo nuevo no la lleva: no se usa. Si es seminuevo, cambia la línea.");
    }
    return {
      id: startId + index,
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
      notes: "",
      cost: options.costs ? candidate.supplierCost : "",
      quantity: "",
      source: candidate.source,
      hints,
      product: null,
    };
  });
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
  return rows.map((row) => (rowSignature(row) === groupKey ? { ...row, product } : row));
}

/** The fields that decide WHICH variant a row is: changing one releases the row's article. */
const IDENTITY: (keyof StagedRow)[] = ["kind", "line", "model", "capacityGb", "colour"];

export function editRow(rows: StagedRow[], id: number, change: Partial<StagedRow>): StagedRow[] {
  return rows.map((row) => {
    if (row.id !== id) return row;
    const next: StagedRow = { ...row, ...change };
    // The condition follows the line only while it is still the line's own default: a state set by hand stays.
    if (change.line && change.line !== row.line && change.condition === undefined
      && row.condition === (row.line === "used" ? "used" : "new")) {
      next.condition = change.line === "used" ? "used" : "new";
    }
    // Battery and grade stay in the row while the device is new: they are neither shown, checked nor sent for it
    // (see `unitOf`), and a misclick on «Son nuevos» does not lose what was measured.
    if (IDENTITY.some((field) => field in change && change[field] !== row[field])) next.product = null;
    return next;
  });
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
export type IssueField = "product" | "serial" | "imei" | "imei2" | "quantity" | "battery" | "cost" | "notes";
export type RowIssue = { kind: IssueKind; field: IssueField; text: string };
/** A row is ready, or it waits for the catalogue, for a missing datum, or it is blocked. A `warn` never blocks. */
export type Bucket = "ready" | "catalog" | "info" | "invalid";
export type RowEvaluation = { row: StagedRow; issues: RowIssue[]; bucket: Bucket };

const ASCII = /^[\x20-\x7E]+$/;
const NOT_ASCII = "Escribe sólo letras sin tilde, números y signos comunes (revisa el teclado del lector).";
const digits = (value: string) => value.replace(/[^0-9]/g, "");

export function evaluateRows(input: { rows: StagedRow[]; lines: DraftLine[]; costs: boolean }): RowEvaluation[] {
  const { rows, lines, costs } = input;
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

  return rows.map((row): RowEvaluation => {
    const issues: RowIssue[] = [];
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
    }
    if (costs && row.cost.trim() && !costIsValid(row.cost)) add("invalid", "cost", "Escribe el costo con hasta 2 decimales, por ejemplo 12.50.");

    const bucket: Bucket = issues.some((i) => i.kind === "catalog") ? "catalog"
      : issues.some((i) => i.kind === "invalid") ? "invalid"
        : issues.some((i) => i.kind === "info") ? "info" : "ready";
    return { row, issues, bucket };
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
  | { kind: "info" | "invalid"; row: number; field: IssueField };

/** Where «Resolver pendientes» goes: the catalogue first (one decision per group), then the first row with something to fix. */
export function nextPending(rows: StagedRow[], evaluation: RowEvaluation[]): Pending | null {
  const needsCatalog = evaluation.find((item) => item.bucket === "catalog");
  if (needsCatalog) return { kind: "catalog", group: rowSignature(needsCatalog.row) };
  for (const item of evaluation) {
    const issue = item.issues.find((i) => i.kind === "invalid") ?? item.issues.find((i) => i.kind === "info");
    if (issue && (issue.kind === "info" || issue.kind === "invalid")) return { kind: issue.kind, row: item.row.id, field: issue.field };
  }
  void rows;
  return null;
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

function unitOf(row: StagedRow, costs: boolean): DraftUnit {
  return {
    ...blankUnit(row.condition),
    serial_number: row.serial.trim(), imei: row.imei.trim(), imei2: row.imei2.trim(),
    cost: costs ? row.cost.trim() : "",
    battery: row.condition === "new" ? "" : row.battery.trim(), grade: row.condition === "new" ? "" : row.grade.trim(),
    notes: row.notes.trim(),
  };
}

/**
 * The READY rows join the receipt's lines — a device as a unit of its article's line, an accessory as quantity.
 * A row that is not ready stays in the list with its reasons: nothing enters half-way and nothing is lost.
 * The caps of one reception are respected: what does not fit stays, and the person is told how many.
 */
export function commitRows(rows: StagedRow[], lines: DraftLine[], options: { costs: boolean; hold?: number[] }): Commit {
  const evaluation = evaluateRows({ rows, lines, costs: options.costs });
  const next: DraftLine[] = lines.map((line) => ({ ...line, units: [...line.units] }));
  const committed = new Set<number>();
  let devices = next.reduce((n, l) => n + (l.product.is_serialized ? unitsInUse(l).length : 0), 0);
  let limit: string | null = null;

  // Rows set aside for another reception are still checked (a duplicate against them is a duplicate), never received here.
  const held = new Set(options.hold ?? []);
  for (const { row, bucket } of evaluation) {
    if (bucket !== "ready" || !row.product || held.has(row.id)) continue;
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
      target.units.push(unitOf(row, options.costs));
      devices += 1;
    } else {
      // Same article, same cost: the quantities add up. Another cost (or a cost against none) stays its own
      // line — the earlier units are never re-priced and no cost is dropped — and so does a sum over the cap.
      const cost = options.costs ? row.cost.trim() : "";
      const add = Number(row.quantity.trim());
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
