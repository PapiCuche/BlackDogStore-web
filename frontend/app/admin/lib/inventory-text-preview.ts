/**
 * INV-TEXT-01 — Vista previa determinista, sin red ni escrituras.
 * No sustituye a los importadores Django, sus permisos o su conciliación.
 * La procedencia es EXTERNA o PROPIA; jamás se incrementa stock aquí.
 */
export type InventoryLine = "new" | "used" | "accessory";
export type InventoryOrigin = "supplier" | "own";
export type TextPreviewOptions = { line: InventoryLine; origin: InventoryOrigin };
export type TextCandidate = {
  id: number;
  source: string;
  line: InventoryLine;
  origin: InventoryOrigin;
  model: string;
  capacityGb: number | null;
  ramGb: number | null;
  batteryPercent: number | null;
  colour: string;
  sim: "chip" | "esim" | "";
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
  truncated: boolean;
};

export const MAX_TEXT_CHARS = 60000;
export const MAX_TEXT_CANDIDATES = 300;
const PRODUCT_START = /^(?:iphone\s*\d|iph\s*\d|samsung\s*\w*\d|s\d{2,3}\s*(?:fe|ultra|plus|\b)|poco\s+\w|xiaomi\s+\w|redmi\s+\w|note\s+\w|ipad\s+\w|macbook\s+\w|apple\s+watch)/i;
const FIELD_START = /^(?:bater[ií]a|n\.?s\.?|s\/n|serie|imei\s*[12]?|versi[oó]n|color|colour|capacidad|ram|costo|precio)\s*:/i;
const HEADER_LINE = /^(?:ayer|hoy|lista(?:do)?(?:\s+completa)?|stock(?:\s+disponible)?|proveedor|actualizado)\s*:?\s*$/i;

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

export function previewSupplierText(text: string, options: TextPreviewOptions): TextPreview {
  if (typeof text !== "string" || text.length > MAX_TEXT_CHARS) {
    throw new Error("La lista excede el límite de 60 000 caracteres.");
  }
  const allowedLines: InventoryLine[] = ["new", "used", "accessory"];
  if (!allowedLines.includes(options.line) || !["supplier", "own"].includes(options.origin)) {
    throw new Error("Clasificación u origen no válido.");
  }

  const chunks: string[][] = [];
  let current: string[] = [];
  let discardedLines = 0;
  function flush() {
    if (current.length) chunks.push(current);
    current = [];
  }
  for (const inputLine of text.replace(/\r\n?/g, "\n").split("\n")) {
    const line = inputLine.trim();
    if (!line) {
      flush();
      continue;
    }
    if (HEADER_LINE.test(line)) {
      flush();
      discardedLines++;
      continue;
    }
    if (PRODUCT_START.test(line) && current.length) flush();
    if (!current.length && !PRODUCT_START.test(line) && !FIELD_START.test(line)
        && options.line !== "accessory") {
      // Un encabezado o texto suelto nunca se inventa como producto.
      discardedLines++;
      continue;
    }
    current.push(line);
  }
  flush();
  const result: TextCandidate[] = [];
  const seen = new Map<string, number>();
  const truncated = chunks.length > MAX_TEXT_CANDIDATES;

  for (const chunk of chunks.slice(0, MAX_TEXT_CANDIDATES)) {
    const source = chunk.join("\n");
    const head = chunk[0] ?? "";
    const warnings: string[] = [];
    const cap = source.match(/\b(?:\d{1,2}\/)?(\d{2,4})\s*(?:gb|g\b)/i)
      ?? source.match(/\b\d{1,2}\/(\d{2,4})\b/);
    const ram = source.match(/\b(\d{1,2})\/\d{2,4}/);
    const battery = source.match(/\bbater[ií]a\s*:?\s*(\d{1,3})\s*%/i);
    const serial = source.match(/\b(?:n\.?s\.?|s\/n|serie)\s*:\s*([a-z0-9/-]{4,40})/i)?.[1]?.toUpperCase() ?? "";
    const imei = digitsOnly(source.match(/\bimei\s*1?\s*:\s*([0-9 .-]{14,22})/i)?.[1] ?? "");
    const imei2 = digitsOnly(source.match(/\bimei\s*2\s*:\s*([0-9 .-]{14,22})/i)?.[1] ?? "");
    const sim: TextCandidate["sim"] = /\be\s*sim\b/i.test(source) ? "esim" : /\bchip\b/i.test(source) ? "chip" : "";
    const explicitColour = source.match(/\b(?:color|colour)\s*:\s*([^\n]+)/i)?.[1]?.trim() ?? "";
    const colour = explicitColour || "";
    const costExplicit = source.match(/\b(?:costo|precio)\s*:\s*(?:s\/\s*)?(\d{2,6}(?:[.,]\d{1,2})?)/i)?.[1] ?? "";
    // Precios de una línea libre: solo cifras monetarias de 4–5 dígitos;
    // no leer números de serie, baterías ni IMEI como precios.
    const priceCandidates = chunk.length === 1
      ? head.match(/\b\d{4,5}(?:[.,]\d{2})?\b/g)?.filter(n => !["1024", "2048", "4096"].includes(n)) ?? []
      : [];
    const supplierCost = costExplicit || (priceCandidates.length === 1 ? priceCandidates[0] : "");
    const name = head.replace(/\b\d{1,2}\/\d{2,4}(?:gb)?\b/gi, "")
      .replace(/\b\d{2,4}\s*gb\b/gi, "")
      .replace(/\b(?:s\/\s*)?\d{4,5}(?:[.,]\d{2})?\b/g, "")
      .replace(/\s+/g, " ").trim();
    const model = name || head;
    const batteryPercent = battery ? Number(battery[1]) : null;
    if (batteryPercent !== null && batteryPercent > 100) warnings.push("Batería fuera de rango.");
    if (imei && !imeiIsValid(imei)) warnings.push("IMEI 1 no supera Luhn o no tiene 15 dígitos.");
    if (imei2 && !imeiIsValid(imei2)) warnings.push("IMEI 2 no supera Luhn o no tiene 15 dígitos.");
    if (imei2 && (!imei || imei2 === imei)) warnings.push("IMEI 2 requiere un IMEI 1 diferente.");
    if (options.origin === "supplier") warnings.push("Cantidad no confirmada: no habilitar venta automática.");
    if (options.origin === "own" && options.line === "used" && (!serial || !imei)) {
      warnings.push("Comprobar los identificadores físicos antes de recibir.");
    }
    if (options.line === "new" && /\b(?:rfb|refurbished|reacondicionado|seminuevo|usado)\b/i.test(source)) {
      warnings.push("La condición informada contradice «Nuevo».");
    }
    if (!supplierCost && options.origin === "supplier") warnings.push("Costo de proveedor no indicado.");
    if (!PRODUCT_START.test(head) && options.line !== "accessory") warnings.push("Modelo no reconocido; revisar.");
    const key = imei || serial
      ? "serial:" + (imei || serial)
      : "variant:" + [model.replace(/\b\d{4,5}(?:[.,]\d{2})?\b/g, ""),cap?.[1] ?? "",colour,sim,options.line].map(normalized).join("|");
    const earlier = seen.get(key);
    const duplicateOf = earlier ?? null;
    if (earlier) warnings.push("Posible duplicado del registro " + earlier + ": no sumar unidades.");
    else seen.set(key, result.length + 1);
    result.push({
      id: result.length + 1, source, line: options.line, origin: options.origin,
      model, capacityGb: cap ? Number(cap[1]) : null, ramGb: ram ? Number(ram[1]) : null,
      batteryPercent, colour, sim, serial, imei, imei2, supplierCost, quantity: null,
      duplicateOf, warnings,
    });
  }
  return { candidates: result, discardedLines, truncated };
}
