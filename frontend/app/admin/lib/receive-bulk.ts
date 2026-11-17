/**
 * INV-INTAKE-BULK-01 — reglas puras del ingreso rápido en «Recibir mercadería».
 *
 * - «Pegar varios»: lee series e IMEI pegados en UNA línea por equipo. Un valor de 15 dígitos que supera Luhn es un
 *   IMEI; cualquier otro, una serie. Una línea «serie IMEI [IMEI 2]» es un solo equipo. Lo repetido, lo que ya está en
 *   la línea y lo que no puede ser un identificador se informa ENMASCARADO (como la revisión: •••• y 4 últimos).
 * - «Aplicar a todos»: costo (sólo con `costs.view`), grado, batería y precio (sólo donde la pantalla los ofrece: una
 *   variante de seminuevos, y el precio sólo con `products.manage`). Por defecto sólo completa lo vacío.
 * - +1 al volver a escanear un artículo por cantidad, el atajo Ctrl/⌘+Enter y la sugerencia de la «S» de la caja.
 *
 * Nada decide stock: el servidor vuelve a revisar todo en «Revisar y confirmar». Nada se guarda fuera de la memoria
 * de la página.
 */

import { imeiIsValid } from "./inventory-text-preview";
import {
  batteryValue, blankUnit, costIsValid, MAX_QUANTITY, priceIsValid, type DraftLine, type DraftUnit,
} from "./inventory-receipts";

/** At most this many codes in one paste: more asks to paste a part (nothing is cut silently). */
export const PASTE_MAX_TOKENS = 500;

/** The server's serial rule (`device_identity.clean_serial`). */
const SERIAL_MIN = 4;
const SERIAL_MAX = 40;
const SERIAL_SHAPE = /^[A-Z0-9][A-Z0-9\-/._]*$/;

export type PastedUnit = { serial: string; imei: string; imei2: string };
export type PasteIssue = { masked: string; reason: string };
export type PastePreview = {
  /** Codes read in the paste. */
  tokens: number;
  /** More than `PASTE_MAX_TOKENS`: nothing is added. */
  tooMany: boolean;
  /** What would be added, one entry per device. */
  units: PastedUnit[];
  /** Devices to add carrying an IMEI / a serial. */
  imeis: number;
  serials: number;
  /** Devices to add without a serial (the server asks for one: it is scanned on its row afterwards). */
  withoutSerial: number;
  /** Repeated inside the paste (added once), masked. */
  duplicates: string[];
  /** Already in this line (not added again), masked. */
  already: string[];
  /** Not an identifier, masked, with the reason. */
  invalid: PasteIssue[];
  /** Serials that look like an Apple box barcode («S» + a 12-character Apple serial). */
  sPrefixed: number;
};

/** As the review shows an identifier: never in full. */
export const maskIdentifier = (value: string): string => (value.trim() ? `••••${value.trim().slice(-4)}` : "—");

const digitsOnly = (value: string) => value.replace(/\D/g, "");
const normSerial = (value: string) => value.replace(/\s+/g, "").toUpperCase();

/** An IMEI written as one token, also grouped with dashes or dots («49-015420-323751-8»). */
const imeiToken = (token: string): string | null =>
  /^[\d.-]+$/.test(token) && imeiIsValid(digitsOnly(token)) ? digitsOnly(token) : null;

/** A 12-character Apple serial (the pre-2021 form): capitals and digits, with at least one of each. */
const APPLE_SERIAL_12 = /^(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*\d)[A-Z0-9]{12}$/;

/**
 * NOT VERIFIED with a real box (audit 2026-11-17, friction 10): the serial barcode on an Apple box may carry an «S»
 * before the serial. Never stripped by itself: when a serial has 13 characters, starts with «S» and the rest is a
 * 12-character Apple serial, this is the serial without it, offered to the person. Null otherwise.
 */
export function appleBoxSerialFix(serial: string): string | null {
  const value = normSerial(serial);
  if (value.length !== 13 || !value.startsWith("S")) return null;
  const rest = value.slice(1);
  return APPLE_SERIAL_12.test(rest) ? rest : null;
}

type Invalid = { kind: "invalid"; raw: string; reason: string };
type Token = { kind: "imei"; value: string } | { kind: "serial"; value: string } | Invalid;

function readToken(raw: string): Token {
  const imei = imeiToken(raw);
  if (imei) return { kind: "imei", value: imei };
  const digits = digitsOnly(raw);
  if (/^[\d.-]+$/.test(raw) && digits.length === 15) {
    return { kind: "invalid", raw, reason: "Parece un IMEI pero no supera el dígito de control: revisa que esté bien copiado." };
  }
  const serial = normSerial(raw);
  if (serial.length < SERIAL_MIN || serial.length > SERIAL_MAX) {
    return { kind: "invalid", raw, reason: `Una serie tiene entre ${SERIAL_MIN} y ${SERIAL_MAX} caracteres.` };
  }
  if (!SERIAL_SHAPE.test(serial)) {
    return { kind: "invalid", raw, reason: "Una serie sólo lleva letras sin tilde, números y guiones." };
  }
  return { kind: "serial", value: serial };
}

/** The devices one pasted line gives: a whole line that is one grouped IMEI, «serie IMEI [IMEI 2]», or one per code. */
function readLine(line: string): { units: PastedUnit[]; invalid: Invalid[]; tokens: number } {
  const trimmed = line.trim();
  if (!trimmed) return { units: [], invalid: [], tokens: 0 };
  // «35 678901 234567 2» (as iOS shows it): spaces inside one IMEI.
  if (/^[\d .-]+$/.test(trimmed) && /\s/.test(trimmed) && imeiIsValid(digitsOnly(trimmed)) && digitsOnly(trimmed).length === 15) {
    return { units: [{ serial: "", imei: digitsOnly(trimmed), imei2: "" }], invalid: [], tokens: 1 };
  }
  const read = trimmed.split(/[\s,;]+/).filter(Boolean).map(readToken);
  const invalid = read.filter((t): t is Invalid => t.kind === "invalid");
  const serials = read.filter((t): t is Extract<Token, { kind: "serial" }> => t.kind === "serial");
  const imeis = read.filter((t): t is Extract<Token, { kind: "imei" }> => t.kind === "imei");
  if (read.length >= 2 && invalid.length === 0 && serials.length === 1 && imeis.length >= 1 && imeis.length <= 2) {
    return { units: [{ serial: serials[0].value, imei: imeis[0].value, imei2: imeis[1]?.value ?? "" }], invalid, tokens: read.length };
  }
  const units = read.flatMap((t): PastedUnit[] => (
    t.kind === "imei" ? [{ serial: "", imei: t.value, imei2: "" }]
      : t.kind === "serial" ? [{ serial: t.value, imei: "", imei2: "" }] : []
  ));
  return { units, invalid, tokens: read.length };
}

const keysOf = (u: { serial: string; imei: string; imei2: string }): { key: string; shown: string }[] => [
  ...(u.serial ? [{ key: `s|${normSerial(u.serial)}`, shown: u.serial }] : []),
  ...(u.imei ? [{ key: `i|${digitsOnly(u.imei) || u.imei.trim()}`, shown: u.imei }] : []),
  ...(u.imei2 ? [{ key: `i|${digitsOnly(u.imei2) || u.imei2.trim()}`, shown: u.imei2 }] : []),
];

/**
 * What a paste would add to a line whose devices are `existing`. With `stripS` the Apple-box «S» is taken off the
 * serials that carry it (the person chose so in the preview).
 */
export function parseUnitPaste(text: string, existing: DraftUnit[], opts: { stripS?: boolean } = {}): PastePreview {
  const lines = text.split(/\r?\n/).map(readLine);
  const tokens = lines.reduce((n, l) => n + l.tokens, 0);
  const empty: PastePreview = {
    tokens, tooMany: false, units: [], imeis: 0, serials: 0, withoutSerial: 0, duplicates: [], already: [], invalid: [], sPrefixed: 0,
  };
  if (tokens > PASTE_MAX_TOKENS) return { ...empty, tooMany: true };

  const inLine = new Set(existing.flatMap((u) => keysOf({ serial: u.serial_number.trim(), imei: u.imei.trim(), imei2: u.imei2.trim() }).map((k) => k.key)));
  const seen = new Set<string>();
  const invalid = lines.flatMap((l) => l.invalid).map((t) => ({ masked: maskIdentifier(t.raw), reason: t.reason }));
  const out: PastePreview = { ...empty, invalid };
  for (const candidate of lines.flatMap((l) => l.units)) {
    const fix = candidate.serial ? appleBoxSerialFix(candidate.serial) : null;
    if (fix) out.sPrefixed++;
    const u = fix && opts.stripS ? { ...candidate, serial: fix } : candidate;
    const keys = keysOf(u);
    const repeated = keys.find((k) => seen.has(k.key));
    const present = keys.find((k) => inLine.has(k.key));
    keys.forEach((k) => seen.add(k.key));
    if (repeated) { out.duplicates.push(maskIdentifier(repeated.shown)); continue; }
    if (present) { out.already.push(maskIdentifier(present.shown)); continue; }
    out.units.push(u);
  }
  out.imeis = out.units.filter((u) => u.imei).length;
  out.serials = out.units.filter((u) => u.serial).length;
  out.withoutSerial = out.units.filter((u) => !u.serial).length;
  return out;
}

const baseConditionOf = (line: DraftLine) => (line.product.condition_line === "used" ? "used" : "new");

/** A row nobody touched: nothing typed and the line's own state. */
const pristine = (u: DraftUnit, base: string) =>
  !u.serial_number.trim() && !u.imei.trim() && !u.imei2.trim() && !u.cost.trim() && !u.battery.trim() && !u.grade.trim()
  && !u.notes.trim() && !u.price.trim() && !u.publicNotes.trim() && u.condition === base;

/** The line's devices with the pasted ones added at the end; untouched blank rows are dropped (they are never sent). */
export function mergePastedUnits(line: DraftLine, pasted: PastedUnit[]): DraftUnit[] {
  const base = baseConditionOf(line);
  const kept = line.units.filter((u) => !pristine(u, base));
  const added = pasted.map((p) => ({ ...blankUnit(base), serial_number: p.serial, imei: p.imei, imei2: p.imei2 }));
  return [...kept, ...added];
}

// ── «Aplicar a todos» ─────────────────────────────────────────────────────

export type ApplyValues = Partial<Record<"cost" | "grade" | "battery" | "price", string>>;
/** What the screen lets this person write on this line (the same rules as each device's own fields). */
export type ApplyAccess = {
  /** `costs.view`. */
  costs: boolean;
  /** `products.manage`. */
  prices: boolean;
  /** A used-line variant: grade, battery and own price. */
  used: boolean;
  /** The company has cosmetic grades the person can read. */
  grades: boolean;
};

/** The fields this person may apply on this line. */
export function applicableFields(access: ApplyAccess): (keyof ApplyValues)[] {
  return [
    ...(access.costs ? ["cost" as const] : []),
    ...(access.used && access.grades ? ["grade" as const] : []),
    ...(access.used ? ["battery" as const] : []),
    ...(access.used && access.prices ? ["price" as const] : []),
  ];
}

/** The values as the reception reads them; a field left empty is not applied and is not a problem. */
export function applyProblems(values: ApplyValues): Partial<Record<keyof ApplyValues, string>> {
  const out: Partial<Record<keyof ApplyValues, string>> = {};
  if (values.cost?.trim() && !costIsValid(values.cost)) out.cost = "Escribe el costo con hasta 2 decimales, por ejemplo 2500.00.";
  if (values.price?.trim() && !priceIsValid(values.price)) out.price = "Escribe el precio con hasta 2 decimales y mayor que cero, por ejemplo 1899.00.";
  if (values.battery?.trim() && batteryValue(values.battery) === null) out.battery = "La batería se indica en % entero, de 0 a 100.";
  return out;
}

const hasIdentifier = (u: DraftUnit) => Boolean(u.serial_number.trim() || u.imei.trim() || u.imei2.trim());

/**
 * The line's devices with `values` written on each device that has a serial or IMEI. Only the fields `access` allows;
 * grade and battery only on a device not marked «Nuevo» (they are neither shown nor sent there). Without `overwrite`
 * a field that already holds something keeps it.
 */
export function applyToAllUnits(
  units: DraftUnit[], values: ApplyValues, access: ApplyAccess, opts: { overwrite: boolean },
): { units: DraftUnit[]; changed: number; kept: number } {
  const allowed = applicableFields(access).filter((f) => values[f]?.trim());
  let changed = 0;
  let kept = 0;
  const next = units.map((u) => {
    if (!hasIdentifier(u)) return u;
    const patch: Partial<DraftUnit> = {};
    for (const field of allowed) {
      if ((field === "grade" || field === "battery") && u.condition === "new") continue;
      const value = values[field]!.trim();
      if (u[field].trim() && !opts.overwrite) { if (u[field].trim() !== value) kept++; continue; }
      if (u[field] !== value) patch[field] = value;
    }
    if (Object.keys(patch).length === 0) return u;
    changed++;
    return { ...u, ...patch };
  });
  return { units: next, changed, kept };
}

// ── Re-scan, shortcut ─────────────────────────────────────────────────────

/** One more unit on a quantity line (a re-scanned article). A half-typed quantity counts as zero. */
export function addOneMore(line: Pick<DraftLine, "quantity">): Pick<DraftLine, "quantity"> {
  const raw = line.quantity.trim();
  const current = /^\d+$/.test(raw) ? Number(raw) : 0;
  return { quantity: String(Math.min(current + 1, MAX_QUANTITY)) };
}

/** Ctrl+Enter (⌘+Enter on a Mac): «Siguiente: revisar» from the articles step. */
export function isReviewShortcut(e: Pick<KeyboardEvent, "key" | "ctrlKey" | "metaKey" | "altKey" | "shiftKey"> & { isComposing?: boolean }): boolean {
  return e.key === "Enter" && (e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && !e.isComposing;
}
