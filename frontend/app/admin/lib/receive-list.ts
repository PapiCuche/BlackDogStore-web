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
      battery: candidate.batteryPercent === null ? "" : String(candidate.batteryPercent),
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
  };
}

/** The exact answers choose by themselves; a choice the person already made is kept. */
export function applyMatches(rows: StagedRow[], answers: Record<string, MatchResult>): StagedRow[] {
  return rows.map((row) => {
    if (row.product) return row;
    const result = answers[rowSignature(row)];
    if (classify(result) !== "exact") return row;
    return { ...row, product: optionToProduct(result!.options[0], row.kind) };
  });
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
    if (change.line && change.line !== row.line && change.condition === undefined) {
      next.condition = change.line === "used" ? "used" : "new";
    }
    if (IDENTITY.some((field) => field in change && change[field] !== row[field])) next.product = null;
    return next;
  });
}

// ── What is missing and what blocks ───────────────────────────────────────

export type IssueKind = "catalog" | "info" | "invalid";
export type IssueField = "product" | "serial" | "imei" | "imei2" | "quantity" | "battery" | "cost" | "notes";
export type RowIssue = { kind: IssueKind; field: IssueField; text: string };
export type Bucket = "ready" | IssueKind;
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
      if (row.battery.trim() && batteryValue(row.battery) === null) add("invalid", "battery", "La batería se indica en % entero, de 0 a 100.");
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
    const issue = item.issues.find((i) => i.kind === "invalid") ?? item.issues[0];
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
    cost: costs ? row.cost.trim() : "", battery: row.battery.trim(), grade: row.grade.trim(), notes: row.notes.trim(),
  };
}

/**
 * The READY rows join the receipt's lines — a device as a unit of its article's line, an accessory as quantity.
 * A row that is not ready stays in the list with its reasons: nothing enters half-way and nothing is lost.
 * The caps of one reception are respected: what does not fit stays, and the person is told how many.
 */
export function commitRows(rows: StagedRow[], lines: DraftLine[], options: { costs: boolean }): Commit {
  const evaluation = evaluateRows({ rows, lines, costs: options.costs });
  const next: DraftLine[] = lines.map((line) => ({ ...line, units: [...line.units] }));
  const committed = new Set<number>();
  let devices = next.reduce((n, l) => n + (l.product.is_serialized ? unitsInUse(l).length : 0), 0);
  let limit: string | null = null;

  for (const { row, bucket } of evaluation) {
    if (bucket !== "ready" || !row.product) continue;
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
    } else if (target) {
      target.quantity = String((Number(target.quantity) || 0) + Number(row.quantity.trim()));
      if (options.costs && row.cost.trim() && !target.unitCost.trim()) target.unitCost = row.cost.trim();
    } else {
      if (next.length >= MAX_LINES) { limit ??= LINES_LIMIT_TEXT; continue; }
      next.push({ ...newLine(product), quantity: row.quantity.trim(), unitCost: options.costs ? row.cost.trim() : "" });
    }
    committed.add(row.id);
  }
  return { lines: next, committed: [...committed], left: rows.filter((r) => !committed.has(r.id)), limit };
}
