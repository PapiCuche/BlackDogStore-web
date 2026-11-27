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

import { groupedImeiOf, imeiIsValid } from "./inventory-text-preview";
import { maskedSource } from "./inventory-text-match";
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
  /** Review M2 · whole lines that are not «serie IMEI [IMEI 2]», «IMEI IMEI» nor only codes of one kind, masked. */
  rejected: string[];
  /** Review L2 · devices that clash with a known one (same serial, another IMEI…), masked: never added. */
  conflicts: string[];
  /** Serials that look like an Apple box barcode («S» + a 12-character Apple serial). */
  sPrefixed: number;
};

/** As the review shows an identifier: never in full. */
export const maskIdentifier = (value: string): string => (value.trim() ? `••••${value.trim().slice(-4)}` : "—");

const digitsOnly = (value: string) => value.replace(/\D/g, "");
const normSerial = (value: string) => value.replace(/\s+/g, "").toUpperCase();

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

/**
 * Review L1 · a run with separators read as ONE IMEI only in a real grouping (`groupedImeiOf`: 2-6-6-1, 3-3-3-3-3, 8-7)
 * and only when some group is too short to be a serial. Two numeric serials («12345678 1234568», 8-7) that happen to
 * add up to a Luhn-valid 15 are never joined: each is a serial.
 */
function joinedImei(run: string): string | null {
  const imei = groupedImeiOf(run);
  return imei && run.trim().split(/[ .-]+/).some((group) => group.length < SERIAL_MIN) ? imei : null;
}

function readToken(raw: string): Token {
  if (/^\d{15}$/.test(raw) && imeiIsValid(raw)) return { kind: "imei", value: raw };
  const joined = /^[\d.-]+$/.test(raw) ? joinedImei(raw) : null;
  if (joined) return { kind: "imei", value: joined };
  if (/^[\d.-]+$/.test(raw) && digitsOnly(raw).length === 15) {
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

type ReadLine = { units: PastedUnit[]; invalid: Invalid[]; rejected: string | null; tokens: number };

/**
 * The devices one pasted line gives (review M2). One code: itself. A whole line that is one grouped IMEI: that IMEI.
 * Several codes: «serie IMEI [IMEI 2]» or «IMEI IMEI» is ONE device; only series, or three or more IMEIs, are one
 * device per code. Anything else (a model, a colour, an invalid code next to an IMEI…) rejects the WHOLE line: an IMEI
 * is never separated from its serial and no word becomes a serial.
 */
function readLine(line: string): ReadLine {
  const trimmed = line.trim();
  const none: ReadLine = { units: [], invalid: [], rejected: null, tokens: 0 };
  if (!trimmed) return none;
  if (/\s/.test(trimmed) && /^[\d .-]+$/.test(trimmed)) {
    const imei = joinedImei(trimmed);
    if (imei) return { ...none, units: [{ serial: "", imei, imei2: "" }], tokens: 1 };
  }
  const read = trimmed.split(/[\s,;]+/).filter(Boolean).map(readToken);
  const invalid = read.filter((t): t is Invalid => t.kind === "invalid");
  if (read.length === 1) {
    const [t] = read;
    return { ...none, tokens: 1, invalid, units: t.kind === "imei" ? [{ serial: "", imei: t.value, imei2: "" }] : t.kind === "serial" ? [{ serial: t.value, imei: "", imei2: "" }] : [] };
  }
  const serials = read.filter((t): t is Extract<Token, { kind: "serial" }> => t.kind === "serial").map((t) => t.value);
  const imeis = read.filter((t): t is Extract<Token, { kind: "imei" }> => t.kind === "imei").map((t) => t.value);
  const tokens = read.length;
  if (invalid.length === 0) {
    if (serials.length === 1 && imeis.length >= 1 && imeis.length <= 2) {
      return { ...none, tokens, units: [{ serial: serials[0], imei: imeis[0], imei2: imeis[1] ?? "" }] };
    }
    if (serials.length === 0 && imeis.length === 2) return { ...none, tokens, units: [{ serial: "", imei: imeis[0], imei2: imeis[1] }] };
    if (imeis.length === 0) return { ...none, tokens, units: serials.map((serial) => ({ serial, imei: "", imei2: "" })) };
    if (serials.length === 0) return { ...none, tokens, units: imeis.map((imei) => ({ serial: "", imei, imei2: "" })) };
  }
  return { ...none, tokens, rejected: maskedSource(trimmed) };
}

const keysOf = (u: { serial: string; imei: string; imei2: string }): { key: string; shown: string }[] => [
  ...(u.serial ? [{ key: `s|${normSerial(u.serial)}`, shown: u.serial }] : []),
  ...(u.imei ? [{ key: `i|${digitsOnly(u.imei) || u.imei.trim()}`, shown: u.imei }] : []),
  ...(u.imei2 ? [{ key: `i|${digitsOnly(u.imei2) || u.imei2.trim()}`, shown: u.imei2 }] : []),
];

/** A pasted device as the preview names it: every identifier masked. */
const describeMasked = (u: PastedUnit): string => [
  ...(u.serial ? [`serie ${maskIdentifier(u.serial)}`] : []),
  ...(u.imei ? [`IMEI ${maskIdentifier(u.imei)}`] : []),
  ...(u.imei2 ? [`IMEI 2 ${maskIdentifier(u.imei2)}`] : []),
].join(" · ");

/**
 * What a paste would add to a line whose devices are `existing`. With `stripS` the Apple-box «S» is taken off the
 * serials that carry it (the person chose so in the preview).
 *
 * Review L2 · a pasted device that matches ONE known device (of the line or of the paste) and brings nothing new is a
 * repeat («ya está» / «repetido»). One that shares an identifier but brings another (a serial known without that IMEI,
 * or a serial and an IMEI of two different devices) is a conflict: it is listed, masked, and never added half-way.
 */
export function parseUnitPaste(text: string, existing: DraftUnit[], opts: { stripS?: boolean } = {}): PastePreview {
  // D-SCAN-4: a scanner may end each code with CR, LF or both; a lone CR separates lines too.
  const lines = text.split(/\r\n?|\n/).map(readLine);
  const tokens = lines.reduce((n, l) => n + l.tokens, 0);
  const empty: PastePreview = {
    tokens, tooMany: false, units: [], imeis: 0, serials: 0, withoutSerial: 0, duplicates: [], already: [], invalid: [],
    rejected: [], conflicts: [], sPrefixed: 0,
  };
  if (tokens > PASTE_MAX_TOKENS) return { ...empty, tooMany: true };

  const owner = new Map<string, number>();
  const keysByOwner: Set<string>[] = [];
  const register = (keys: string[]) => {
    const id = keysByOwner.push(new Set(keys)) - 1;
    keys.forEach((k) => owner.set(k, id));
  };
  for (const u of existing) {
    const keys = keysOf({ serial: u.serial_number.trim(), imei: u.imei.trim(), imei2: u.imei2.trim() }).map((k) => k.key);
    if (keys.length) register(keys);
  }
  const lineDevices = keysByOwner.length;

  const invalid = lines.flatMap((l) => l.invalid).map((t) => ({ masked: maskIdentifier(t.raw), reason: t.reason }));
  const rejected = lines.flatMap((l) => (l.rejected === null ? [] : [l.rejected]));
  const out: PastePreview = { ...empty, invalid, rejected };
  for (const candidate of lines.flatMap((l) => l.units)) {
    const fix = candidate.serial ? appleBoxSerialFix(candidate.serial) : null;
    if (fix) out.sPrefixed++;
    const u = fix && opts.stripS ? { ...candidate, serial: fix } : candidate;
    const keys = keysOf(u);
    const owners = new Set(keys.flatMap((k) => (owner.has(k.key) ? [owner.get(k.key)!] : [])));
    if (owners.size === 0) {
      register(keys.map((k) => k.key));
      out.units.push(u);
      continue;
    }
    const [only] = [...owners];
    if (owners.size === 1 && keys.every((k) => keysByOwner[only].has(k.key))) {
      (only < lineDevices ? out.already : out.duplicates).push(maskIdentifier(keys[0].shown));
      continue;
    }
    out.conflicts.push(describeMasked(u));
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

/**
 * One more unit on a quantity line (a re-scanned article). Review H1: only on a whole number from 1 to
 * MAX_QUANTITY - 1; anything else (empty, half-typed, a scan typed into «Cantidad», at the maximum) is null and the
 * line stays as it is. Never clamped.
 */
export function addOneMore(line: Pick<DraftLine, "quantity">): Pick<DraftLine, "quantity"> | null {
  const raw = line.quantity.trim();
  if (!/^\d{1,6}$/.test(raw)) return null;
  const current = Number(raw);
  return current >= 1 && current < MAX_QUANTITY ? { quantity: String(current + 1) } : null;
}

/** What is said when a re-scan cannot add one (review H1): the quantity is left as it is. */
export const rescanRefused = (name: string): string =>
  `No se sumó: la cantidad de «${name}» debe ser un número entero de 1 a ${MAX_QUANTITY - 1}. Corrígela en su línea.`;

/** Ctrl+Enter (⌘+Enter on a Mac): «Siguiente: revisar» from the articles step. */
export function isReviewShortcut(e: Pick<KeyboardEvent, "key" | "ctrlKey" | "metaKey" | "altKey" | "shiftKey"> & { isComposing?: boolean }): boolean {
  return e.key === "Enter" && (e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && !e.isComposing;
}
