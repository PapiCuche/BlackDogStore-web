/**
 * INV-TEXT-01 — Vista previa determinista, sin red ni escrituras.
 * No sustituye a los importadores Django, sus permisos o su conciliación.
 * La procedencia es EXTERNA o PROPIA; jamás se incrementa stock aquí.
 *
 * INV-TEXT-02: read in stages (`inventory-text-stages`): clean, segment, tell
 * device from accessory, give each token its meaning, separate the cost, name
 * the variant, check identifiers, mark duplicates. A preview for a person.
 */
import { cleanLine, commercialName, kindOfLine, readAttributes, reasonFor } from "./inventory-text-stages";

export type InventoryLine = "new" | "used" | "accessory";
export type InventoryOrigin = "supplier" | "own";
export type TextPreviewOptions = { line: InventoryLine; origin: InventoryOrigin };
export type TextCandidate = {
  id: number;
  source: string;
  /** INV-TEXT-02: what the line names — a device or an accessory. */
  kind: "device" | "accessory";
  line: InventoryLine;
  origin: InventoryOrigin;
  model: string;
  capacityGb: number | null;
  ramGb: number | null;
  batteryPercent: number | null;
  colour: string;
  sim: "chip" | "esim" | "";
  region: string;
  watts: number | null;
  length: string;
  generation: string;
  serial: string;
  imei: string;
  imei2: string;
  supplierCost: string;
  quantity: null;
  duplicateOf: number | null;
  warnings: string[];
};
export type TextPreview = {
  candidates: TextCandidate[];
  discardedLines: number;
  /** INV-TEXT-02: every line that did not become a product, and why. Never silent. */
  unrecognized: { line: string; reason: string }[];
  truncated: boolean;
};

export const MAX_TEXT_CHARS = 60000;
export const MAX_TEXT_CANDIDATES = 300;

const normalized = (v: string): string => v.trim().toLocaleLowerCase("es").replace(/\s+/g, " ");
const digitsOnly = (v: string): string => v.replace(/[^0-9]/g, "");

export function luhnCheckDigit(body: string): string | null {
  if (!/^\d{14}$/.test(body) || new Set(body).size === 1) return null;
  let sum = 0;
  for (let i = body.length - 1, j = 0; i >= 0; i--, j++) {
    let digit = Number(body[i]);
    if (j % 2 === 0) {
      digit *= 2;
      if (digit > 9) digit -= 9;
    }
    sum += digit;
  }
  return String((10 - sum % 10) % 10);
}

export function imeiIsValid(value: string): boolean {
  const imei = digitsOnly(value);
  return /^\d{15}$/.test(imei) && luhnCheckDigit(imei.slice(0, 14)) === imei[14];
}

type Chunk = { kind: "device" | "accessory"; lines: string[] };

/** Stage 2 — products and their field lines, in order; the rest counted with its reason. */
function segment(text: string, options: TextPreviewOptions) {
  const chunks: Chunk[] = [];
  const unrecognized: TextPreview["unrecognized"] = [];
  let current: Chunk | null = null;
  for (const raw of text.replace(/\r\n?/g, "\n").split("\n")) {
    const line = cleanLine(raw);
    if (!line) { current = null; continue; }
    const kind = kindOfLine(line);
    if (kind === "device" || kind === "accessory") {
      current = { kind, lines: [line] };
      chunks.push(current);
    } else if (kind === "field" && current) {
      current.lines.push(line);
    } else if (options.line === "accessory" && kind === "other" && /\d/.test(line)) {
      // «Todos son accesorios»: a line with an amount is a generic accessory to review.
      current = { kind: "accessory", lines: [line] };
      chunks.push(current);
    } else {
      unrecognized.push({ line, reason: reasonFor(line, kind) });
      if (kind !== "field") current = null;
    }
  }
  return { chunks, unrecognized };
}

/** Stage 5 — the cost: an explicit «Costo:» wins; else ONE amount in the line; two or more, nobody guesses. */
function costOf(source: string, amounts: string[], warnings: string[], kind: "device" | "accessory"): string {
  const explicit = source.match(/\b(?:costo|precio)\s*:\s*(?:s\/\.?\s*|\$\s*)?(\d{1,6}(?:[.,]\d{1,2})?)/i)?.[1];
  if (explicit) return explicit;
  if (amounts.length > 1) {
    warnings.push(`Costos ambiguos (${amounts.join(", ")}): indicar cuál es el costo.`);
    return "";
  }
  const [amount] = amounts;
  if (amount && kind === "device" && Number(amount.replace(",", ".")) < 100) {
    warnings.push(`Importe ${amount} demasiado bajo para un equipo: revisar si es el costo.`);
    return "";
  }
  return amount ?? "";
}

export function previewSupplierText(text: string, options: TextPreviewOptions): TextPreview {
  if (typeof text !== "string" || text.length > MAX_TEXT_CHARS) {
    throw new Error("La lista excede el límite de 60 000 caracteres.");
  }
  const allowedLines: InventoryLine[] = ["new", "used", "accessory"];
  if (!allowedLines.includes(options.line) || !["supplier", "own"].includes(options.origin)) {
    throw new Error("Clasificación u origen no válido.");
  }

  const { chunks, unrecognized } = segment(text, options);
  const result: TextCandidate[] = [];
  const seen = new Map<string, number>();
  const truncated = chunks.length > MAX_TEXT_CANDIDATES;

  for (const chunk of chunks.slice(0, MAX_TEXT_CANDIDATES)) {
    const source = chunk.lines.join("\n");
    const kind = chunk.kind;
    const warnings: string[] = [];
    // Stages 3–4 — the head line's tokens by meaning; field lines add what they say.
    const attrs = readAttributes(chunk.lines[0], kind);
    for (const field of chunk.lines.slice(1)) {
      const extra = readAttributes(field.replace(/^[^:]*:\s*/, ""), "device", { field: true });
      attrs.capacityGb ??= /^\d/.test(field) || /capacidad/i.test(field) ? extra.capacityGb : null;
      attrs.ramGb ??= /ram/i.test(field) ? extra.ramGb : null;
      if (!attrs.sim && /versi[oó]n|chip|sim/i.test(field)) attrs.sim = extra.sim;
    }
    const battery = source.match(/\bbater[ií]a\s*:?\s*(\d{1,3})\s*%/i);
    const serial = source.match(/\b(?:n\.?\s?s\.?|n\/s|s\/n|serie)\s*:\s*([a-z0-9][a-z0-9/-]{3,39})/i)?.[1]?.toUpperCase() ?? "";
    // The colon is optional («IMEI 490…»); «IMEI 2» is never read as the first one.
    const imei = digitsOnly(source.match(/\bimei(?!\s*2\b)\s*1?\s*:?\s*([0-9][0-9 .-]{13,21})/i)?.[1] ?? "");
    const imei2 = digitsOnly(source.match(/\bimei\s*2\b\s*:?\s*([0-9][0-9 .-]{13,21})/i)?.[1] ?? "");
    const explicitColour = source.match(/\b(?:color|colour)\s*:\s*([^\n]+)/i)?.[1]?.trim() ?? "";
    const colour = explicitColour || attrs.colour;
    const supplierCost = costOf(source, attrs.amounts, warnings, kind);
    const model = commercialName(attrs.nameTokens, kind);
    const batteryPercent = battery ? Number(battery[1]) : attrs.battery;
    // Stage 6 — the line it goes to: an accessory is an accessory; the person's choice for the rest.
    const line: InventoryLine = kind === "accessory" ? "accessory" : options.line === "accessory" ? "new" : options.line;
    if (kind === "device" && options.line === "accessory") warnings.push("Parece un equipo, no un accesorio: revisar.");

    // Stage 7 — identifiers.
    if (batteryPercent !== null && batteryPercent > 100) warnings.push("Batería fuera de rango.");
    if (imei && !imeiIsValid(imei)) warnings.push("IMEI 1 no supera Luhn o no tiene 15 dígitos.");
    if (imei2 && !imeiIsValid(imei2)) warnings.push("IMEI 2 no supera Luhn o no tiene 15 dígitos.");
    if (imei2 && (!imei || imei2 === imei)) warnings.push("IMEI 2 requiere un IMEI 1 diferente.");
    if (options.origin === "supplier") warnings.push("Cantidad no confirmada: no habilitar venta automática.");
    if (options.origin === "own" && line === "used" && (!serial || !imei)) {
      warnings.push("Comprobar los identificadores físicos antes de recibir.");
    }
    if (line === "new" && /\b(?:rfb|refurbished|reacondicionado|seminuevo|usado)\b/i.test(source)) {
      warnings.push("La condición informada contradice «Nuevo».");
    }
    if (!supplierCost && options.origin === "supplier" && !warnings.some(w => w.startsWith("Costos ambiguos") || w.startsWith("Importe"))) {
      warnings.push("Costo de proveedor no indicado.");
    }
    if (kind === "device" && attrs.capacityGb === null) warnings.push("Capacidad no indicada.");

    // Stage 8 — duplicates: the same identifier, or the same variant; never added up.
    const key = imei || serial
      ? "serial:" + (imei || serial)
      : "variant:" + [kind, model, attrs.capacityGb ?? "", attrs.ramGb ?? "", colour, attrs.sim, attrs.region, line]
        .map(value => normalized(String(value))).join("|");
    const earlier = seen.get(key);
    const duplicateOf = earlier ?? null;
    if (earlier) warnings.push("Posible duplicado del registro " + earlier + ": no sumar unidades.");
    else seen.set(key, result.length + 1);
    result.push({
      id: result.length + 1, source, kind, line, origin: options.origin, model,
      capacityGb: attrs.capacityGb, ramGb: attrs.ramGb, batteryPercent, colour, sim: attrs.sim, region: attrs.region,
      watts: attrs.watts, length: attrs.length, generation: attrs.generation, serial, imei, imei2, supplierCost,
      quantity: null, duplicateOf, warnings,
    });
  }
  return { candidates: result, discardedLines: unrecognized.length, unrecognized, truncated };
}

const EDIT_SENSITIVE = [/^Capacidad no indicada\.$/, /^Posible duplicado del registro \d+: no sumar unidades\.$/];

/**
 * INV-TEXT-02 — after the person corrected some rows: the capacity notice and the duplicate marks are
 * worked out again on the current values; every other notice stays as the recognizer wrote it.
 */
export function refreshAfterEdits(candidates: TextCandidate[]): TextCandidate[] {
  const seen = new Map<string, number>();
  return candidates.map(candidate => {
    const warnings = candidate.warnings.filter(warning => !EDIT_SENSITIVE.some(pattern => pattern.test(warning)));
    if (candidate.kind === "device" && candidate.capacityGb === null) warnings.push("Capacidad no indicada.");
    const key = candidate.imei || candidate.serial
      ? "serial:" + (candidate.imei || candidate.serial)
      : "variant:" + [candidate.kind, candidate.model, candidate.capacityGb ?? "", candidate.ramGb ?? "",
        candidate.colour, candidate.sim, candidate.region, candidate.line].map(value => normalized(String(value))).join("|");
    const earlier = seen.get(key);
    if (earlier) warnings.push("Posible duplicado del registro " + earlier + ": no sumar unidades.");
    else seen.set(key, candidate.id);
    return { ...candidate, duplicateOf: earlier ?? null, warnings };
  });
}
