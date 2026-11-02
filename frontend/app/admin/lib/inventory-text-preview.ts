/**
 * INV-TEXT-01 — Vista previa determinista, sin red ni escrituras.
 * No sustituye a los importadores Django, sus permisos o su conciliación.
 * La procedencia es EXTERNA o PROPIA; jamás se incrementa stock aquí.
 *
 * INV-TEXT-02: read in stages (`inventory-text-stages`): clean, segment, tell
 * device from accessory, give each token its meaning, separate the cost, name
 * the variant, check identifiers, mark duplicates. A preview for a person.
 */
import {
  cleanLine, commercialName, kindOfLine, quantityFirst, readAttributes, readDetails, reasonFor, splitProducts, type Details,
} from "./inventory-text-stages";

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
  /**
   * INV-TEXT-03 · how `supplierCost` was read: «label» (Costo:, costo de compra, costo proveedor…) or «lone» (the only
   * amount of the row, with no label at all: a supplier list's convention, to be confirmed by a person).
   */
  costFrom: "label" | "lone" | "";
  /** INV-TEXT-03 · what a label calls the selling price (precio de venta / público / final / al cliente, PVP). Never a cost. */
  salePrice: string;
  /** INV-TEXT-03 · amounts nobody can place: a bare «Precio», two amounts without labels… shown, never assigned. */
  ambiguousAmounts: string[];
  /** INV-TEXT-03 · of those, the ones a label called «costo» (several different): never shown without `costs.view`. */
  ambiguousCosts: string[];
  /** INV-TEXT-03 · the line the row itself states («seminuevo», «sem», «nvo», «sellado»…); the person's choice is `line`. */
  statedLine: "new" | "used" | "";
  /** INV-TEXT-03 · «x2», «2 unidades», «cantidad: 2»: what the text says, for the receipt to show. */
  statedQuantity: number | null;
  /** INV-TEXT-03 · «estado A», «grado B+», «9/10»: compared with the company's own grades by the screen; never invented. */
  gradeText: string;
  /** «label» after «estado»/«grado»; «bare» for a lone «9/10» (shown, never linked by itself). */
  gradeFrom: "label" | "bare" | "";
  /** INV-TEXT-03 · «con caja», «(detalle en el marco)», «Obs: …»; never one that holds an identifier. */
  notes: string;
  /** INV-TEXT-03 · «label» when an «IMEI» label introduced it, «bare» when it is a lone 15-digit number that passes Luhn. */
  imeiFrom: "label" | "bare" | "";
  /** INV-TEXT-03 third review · a bare IMEI written with separators (a grouping): checked before the row can enter. */
  imeiGrouped: boolean;
  /** INV-TEXT-03 review · the stated quantity came from «x N» with a space (could be «por N»). */
  quantitySpaced: boolean;
  /** A CONFIRMED quantity (supplier offers): never read from a text. */
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

/**
 * INV-TEXT-03 third review · an UNLABELLED IMEI only in a real grouping: 15 digits together, 2-6-6-1 (iOS «Ajustes»),
 * 3-3-3-3-3 or 8-7 — never separate numbers that happen to add up to 15 digits and pass Luhn («64 1004 987654321»).
 */
// The distinctive forms (15 together, 2-6-6-1, 8-7) are read wherever they stand, also right after a capacity or a
// price (fourth review); only the ambiguous 3-3-3-3-3 must not follow another number.
const IMEI_GROUPED = /(?<!\d)(?:\d{15}|\d{2}[ .\-]\d{6}[ .\-]\d{6}[ .\-]\d|\d{8}[ .\-]\d{7})(?!\d)|(?<!\d[ .\-]?)\d{3}(?:[ .\-]\d{3}){4}(?!\d)/g;
const STORAGE_GROUPS = new Set(["016", "032", "064", "128", "256", "512"]);
/** A grouped run that is really an IMEI: passes Luhn, does not start with a capacity, does not end with a mobile. */
function isGroupedImei(run: string): boolean {
  const digits = digitsOnly(run);
  if (!imeiIsValid(digits)) return false;
  const groups = run.split(/[ .\-]/);
  if (groups.length === 5 && (STORAGE_GROUPS.has(groups[0]) || /^9/.test(groups[2]))) return false;
  return true;
}
/** The 15-digit IMEIs of a text in a real grouping, and whether each was written with separators. */
function groupedImeis(text: string): { digits: string; spaced: boolean }[] {
  return [...text.matchAll(IMEI_GROUPED)].filter((m) => isGroupedImei(m[0])).map((m) => ({ digits: digitsOnly(m[0]), spaced: /\D/.test(m[0]) }));
}
/** Tokens that are never part of an IMEI: a mobile (9xx…, 51 9xx…) or a RUC. */
const NOT_IMEI_PART = /^(?:9\d{8}|519\d{8}|(?:10|15|17|20)\d{9})$/;
const STORAGE_TOKEN = /^(?:16|32|64|128|256|512|1024|2048)$/;

/**
 * INV-TEXT-03 fourth review · defence in depth: every run of consecutive number tokens (one space, dot or dash apart)
 * whose digits together are 15 and pass Luhn, whatever the grouping — so no fragment of an IMEI is ever offered as an
 * amount or shown. A run that starts with a capacity, or holds a mobile or a RUC, is not one (those are real data).
 */
export function luhnSpans(text: string): { start: number; end: number; digits: string }[] {
  // Fewer than 15 digits in the whole text: nothing to find (most lines of a list).
  let count = 0;
  for (let i = 0; i < text.length && count < 15; i++) if (text.charCodeAt(i) >= 48 && text.charCodeAt(i) <= 57) count++;
  if (count < 15) return [];
  const tokens = [...text.matchAll(/\d+/g)].map((m) => ({ start: m.index!, end: m.index! + m[0].length, value: m[0] }));
  const spans: { start: number; end: number; digits: string }[] = [];
  for (let i = 0; i < tokens.length; i++) {
    let digits = "";
    for (let j = i; j < tokens.length; j++) {
      if (j > i && !/^[ .\-]$/.test(text.slice(tokens[j - 1].end, tokens[j].start))) break;
      if (NOT_IMEI_PART.test(tokens[j].value)) break;
      digits += tokens[j].value;
      if (digits.length > 15) break;
      if (digits.length === 15) {
        const capacityFirst = j > i && STORAGE_TOKEN.test(tokens[i].value);
        if (!capacityFirst && imeiIsValid(digits)) spans.push({ start: tokens[i].start, end: tokens[j].end, digits });
        break;
      }
    }
  }
  // Overlapping candidates: keep the first of each overlap.
  return spans.filter((span, index) => !spans.slice(0, index).some((o) => span.start < o.end && o.start < span.end));
}

/** The text with every such span replaced (by `with`). */
export function replaceLuhnSpans(text: string, by: (digits: string) => string): string {
  let out = "";
  let at = 0;
  for (const span of luhnSpans(text)) {
    out += text.slice(at, span.start) + by(span.digits);
    at = span.end;
  }
  return out + text.slice(at);
}

/** A line that is nothing but one IMEI, written any way (a person's own line: «35 9999 0091 2345 6»). */
const isImeiRun = (text: string) => /^[\d .\-]+$/.test(text.trim()) && imeiIsValid(digitsOnly(text));

/**
 * A labelled IMEI value never takes the number written after it: «IMEI 359…456 2 unid» is the 15 digits that pass Luhn,
 * then «2». Returns the IMEI digits and what is left of the value.
 */
function labelledImei(raw: string): { digits: string; rest: string } {
  const digits = digitsOnly(raw);
  if (digits.length === 16 && imeiIsValid(digits.slice(0, 15))) {
    let seen = 0;
    let at = 0;
    while (at < raw.length && seen < 15) { if (/\d/.test(raw[at])) seen++; at++; }
    if (/^[ .\-]/.test(raw.slice(at))) return { digits: digits.slice(0, 15), rest: raw.slice(at) };
  }
  return { digits, rest: "" };
}
/** A field line that holds an identifier: nothing on it is ever an amount, a quantity or a contact. */
const IDENTIFIER_FIELD = /^(?:imei\s*[12]?|n\.?\s?s\.?|n\/s|s\/n|sn|serie|serial)\b/i;
const IMEI_SPAN = /\bimei\s*[12]?\s*[:#=]?\s*[0-9](?:[ .\-]?[0-9]){13,15}(?![0-9])(?:\s*(?:\/|,|\sy\s)\s*[0-9](?:[ .\-]?[0-9]){13,15}(?![0-9]))?/gi;
const SERIAL_SPAN = /(?<![\p{L}\d])(?:n\.?\s?s\.?|n\/s|s\/n|sn|serie|serial)(?:\s*[:#=]\s*[a-z0-9][a-z0-9/-]{3,39}|\s+(?!\d{1,4}(?:gb|tb|w|mm|m|mah)(?![\p{L}\d]))(?:(?=[a-z0-9-]*\d)(?=[a-z0-9-]*[a-z])[a-z0-9][a-z0-9-]{4,39}|\d{6,20}))(?![\p{L}\d])/giu;

/**
 * INV-TEXT-03 review (P2-A): the identifiers of a line — an IMEI after its label (also with spaces, dots or dashes), a
 * serial after its label, a 15-digit run that passes Luhn — are cut out BEFORE amounts, contacts or quantities are
 * read, so a fragment of an IMEI is never an amount on screen. They are read from the untouched text elsewhere.
 */
function withoutIdentifiers(line: string): string {
  if (isImeiRun(line)) return "";
  return line
    .replace(IMEI_SPAN, (span) => {
      // Only the IMEI itself: a number written after it («… 2 unid») stays to be read.
      const value = span.replace(/^\s*imei\s*[12]?\s*[:#=]?\s*/i, "");
      const { rest } = labelledImei(value);
      return rest.trim() ? ` ${rest.trim()}` : " ";
    })
    .replace(SERIAL_SPAN, " ")
    .replace(IMEI_GROUPED, (run) => (isGroupedImei(run) ? " " : run))
    // Defence in depth: whatever is left that adds up to an IMEI is not read as anything.
    .replace(/[^\n]+/g, (rest) => replaceLuhnSpans(rest, () => " "));
}

/** Stage 2 — products and their field lines, in order; the rest counted with its reason. */
function segment(text: string, options: TextPreviewOptions) {
  const chunks: Chunk[] = [];
  const unrecognized: TextPreview["unrecognized"] = [];
  let current: Chunk | null = null;
  const lines = text.replace(/\r\n?/g, "\n").split("\n").flatMap((raw) => {
    const line = cleanLine(raw);
    return line ? splitProducts(line).map(quantityFirst) : [""];
  });
  for (const line of lines) {
    if (!line) { current = null; continue; }
    let kind = kindOfLine(line);
    // «Serie 40 …» under a phone is a field of that phone, not a new watch: a «Series N» row needs no device above it or
    // the word «Watch» in the device above.
    if (kind === "device" && /^series?\s?\d/i.test(line) && current && !/watch|series?/i.test(current.lines[0])) kind = "field";
    if (kind === "device" || kind === "accessory") {
      current = { kind, lines: [line] };
      chunks.push(current);
    } else if ((kind === "field" || (kind === "other" && current?.kind === "device" && isImeiRun(line))) && current) {
      // A field, or a device's IMEI alone on its line (with or without spaces): read with the device above.
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

type Amounts = Pick<TextCandidate, "supplierCost" | "costFrom" | "salePrice" | "ambiguousAmounts" | "ambiguousCosts">;

const unique = (values: string[]) => [...new Set(values)];

/**
 * Stage 5 — the amounts. A label decides («Costo» is a cost, «Precio de venta» a selling price); a bare «Precio» does
 * not, and neither do two amounts without labels: those are left for a person, never assigned. Only the one amount of
 * a row that has no label at all is taken as the cost (a supplier list's convention) — marked «lone» to be confirmed.
 * A price is never derived from a cost, nor a cost from a price.
 */
function amountsOf(details: Details[], tokens: string[], warnings: string[], kind: "device" | "accessory", quantity: number | null): Amounts {
  const cost = unique(details.flatMap((d) => d.cost));
  const sale = unique(details.flatMap((d) => d.sale));
  const bare = unique(details.flatMap((d) => d.bare));
  const usd = unique(details.flatMap((d) => d.usd));
  const unlabelled = unique([...tokens, ...details.flatMap((d) => d.unlabelled)]);
  const pair = unique(details.flatMap((d) => d.pair));
  const selling = details.some((d) => d.selling);
  const out: Amounts = { supplierCost: "", costFrom: "", salePrice: "", ambiguousAmounts: [], ambiguousCosts: cost.length > 1 ? cost : [] };
  if (usd.length) warnings.push(`Importe en dólares (US$ ${usd.join(", ")}): no se usa como costo ni como precio en soles; indícalo a mano.`);
  if (cost.length === 1) Object.assign(out, { supplierCost: cost[0], costFrom: "label" });
  if (sale.length === 1) out.salePrice = sale[0];
  const labelled = cost.length > 0 || sale.length > 0;
  // INV-TEXT-03 review: a lone amount is a cost candidate only when nothing else speaks: no word of selling in the row
  // («oferta», «precio final»…), no stated quantity above 1 (it could be the total) and no «N x amount» pair.
  const lone = !labelled && bare.length === 0 && pair.length === 0 && unlabelled.length === 1;
  if (lone && selling) warnings.push(`Importe sin etiqueta en una fila que habla de venta u oferta (${unlabelled[0]}): no se sabe si es costo o precio de venta.`);
  else if (lone && (quantity ?? 1) > 1) warnings.push(`Importe sin etiqueta con ${quantity} unidades (${unlabelled[0]}): puede ser el precio de cada una o el total.`);
  if (lone && !selling && (quantity ?? 1) <= 1) {
    const [amount] = unlabelled;
    if (kind === "device" && Number(amount) < 100) {
      warnings.push(`Importe ${amount} demasiado bajo para un equipo: revisar si es el costo.`);
      return out;
    }
    return { ...out, supplierCost: amount, costFrom: "lone" };
  }
  const ambiguous = unique([...(cost.length > 1 ? cost : []), ...(sale.length > 1 ? sale : []), ...bare, ...pair, ...unlabelled]);
  out.ambiguousAmounts = ambiguous;
  if (cost.length > 1 || (!labelled && bare.length === 0 && pair.length === 0 && unlabelled.length > 1)) {
    warnings.push(`Costos ambiguos (${(cost.length > 1 ? cost : unlabelled).join(", ")}): indicar cuál es el costo.`);
  }
  if (sale.length > 1) warnings.push(`Precios de venta distintos (${sale.join(", ")}): indicar cuál es.`);
  if (bare.length) warnings.push(`Importe sin etiqueta clara (Precio: ${bare.join(", ")}): no se sabe si es costo o precio de venta.`);
  if (labelled && unlabelled.length) warnings.push(`Importe sin etiqueta (${unlabelled.join(", ")}) además del indicado: revisar qué es.`);
  return out;
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
    // Stages 3–4 — what the labels say (amounts, quantity, battery, condition, grade, notes) comes out of each
    // line first; then the head line's tokens by meaning, and what each field line adds.
    const head = readDetails(withoutIdentifiers(chunk.lines[0]), { device: kind === "device" });
    const details: Details[] = [head];
    const attrs = readAttributes(head.rest, kind);
    let explicitColour = /\b(?:color|colour)\s*:\s*([^\n,;|]+)/i.exec(head.rest)?.[1]?.trim() ?? "";
    for (const field of chunk.lines.slice(1)) {
      // An identifier line (IMEI, serial) is never read for amounts, quantities or contacts (P2-A).
      // What remains once the identifier is cut («NS 912345678 1800» → «1800») follows the normal rules; a label whose value
      // could not be cut (a broken IMEI) leaves the whole line unread.
      const cut = withoutIdentifiers(field);
      const unreadable = IDENTIFIER_FIELD.test(field) && IDENTIFIER_FIELD.test(cut.trim());
      // «NS 1800»: a label with no readable identifier — its number is not used, and that is said, never silent.
      if (unreadable) warnings.push(`La línea «${/^\S+/.exec(field)?.[0] ?? ""} …» no trae una serie o un IMEI que se pueda leer: revísala; su número no se usó.`);
      const d = readDetails(isImeiRun(field) || unreadable ? "" : cut);
      details.push(d);
      const extra = readAttributes(d.rest.replace(/^[^:]*:\s*/, ""), "device", { field: true });
      // An amount left in a field line («Precio al por mayor…», «/ 1100 por mayor») is never dropped: unlabelled.
      d.unlabelled.push(...extra.amounts);
      attrs.capacityGb ??= /^\d/.test(d.rest) || /capacidad/i.test(d.rest) ? extra.capacityGb : null;
      attrs.ramGb ??= /ram/i.test(field) ? extra.ramGb : null;
      if (!attrs.sim && /versi[oó]n|chip|sim/i.test(field)) attrs.sim = extra.sim;
      const named = /^(?:color|colour)\s*:\s*(.+)$/i.exec(d.rest)?.[1]?.trim();
      // «Color: azul medianoche», or a line that is nothing but a known colour.
      if (!explicitColour && named) explicitColour = named;
      else if (!explicitColour && extra.colours.length === 1 && extra.nameTokens.length === 0) explicitColour = extra.colour;
    }
    attrs.ramGb ??= details.find((d) => d.ramGb !== null)?.ramGb ?? null;
    const battery = details.find((d) => d.battery !== null)?.battery ?? null;
    const serial = (source.match(/\b(?:n\.?\s?s\.?|n\/s|s\/n|sn|serie|serial)\s*[:#=]\s*([a-z0-9][a-z0-9/-]{3,39})/i)?.[1]
      // Without a colon only a code («Serie F2LX…», «SN ABC12…»): «Serie 9» or «serie 45mm» are names.
      ?? source.match(/(?<![\p{L}\d])(?:n\.?\s?s\.?|n\/s|s\/n|sn|serie|serial)\s+(?!\d{1,4}(?:gb|tb|w|mm|m|mah)(?![\p{L}\d]))((?=[a-z0-9-]*\d)(?=[a-z0-9-]*[a-z])[a-z0-9][a-z0-9-]{4,39}|\d{6,20})(?![\p{L}\d])/iu)?.[1]
    )?.toUpperCase() ?? "";
    // The colon is optional («IMEI 490…»); «IMEI 2» is never read as the first one.
    // The slot «1» only before a colon or a space and more digits; the IMEI is 14–16 digits, nothing after.
    let imei = labelledImei(source.match(/\bimei(?!\s*2\b)(?:\s*1(?=\s*:|\s+[0-9]))?\s*:?\s*([0-9](?:[ .-]?[0-9]){13,15})(?![0-9])/i)?.[1] ?? "").digits;
    let imei2 = labelledImei(source.match(/\bimei\s*2\b\s*:?\s*([0-9](?:[ .-]?[0-9]){13,15})(?![0-9])/i)?.[1] ?? "").digits;
    let imeiGrouped = false;
    let imeiFrom: TextCandidate["imeiFrom"] = imei ? "label" : "";
    // «IMEI: 35… / 35…»: the second one of the same label is the IMEI 2.
    const pair = imei && !imei2 ? /\bimei[^\n]*?(\d{15})\s*(?:\/|,|\sy\s)\s*(\d{15})(?!\d)/i.exec(source) : null;
    if (pair && pair[1] === imei) imei2 = pair[2];
    if (!imei && kind === "device") {
      // A lone 15-digit number that passes Luhn is most likely the IMEI: taken, and said, to be checked.
      // A real grouping in any line, or a line that is nothing but an IMEI.
      const found = [...groupedImeis(source), ...source.split("\n").filter(isImeiRun).map((line) => ({ digits: digitsOnly(line), spaced: /\D/.test(line.trim()) }))];
      const bare = [...new Map(found.filter((f) => f.digits !== imei2).map((f) => [f.digits, f])).values()];
      if (bare.length) {
        imei = bare[0].digits;
        imeiFrom = "bare";
        imeiGrouped = bare[0].spaced;
        if (bare.length === 2 && !imei2) imei2 = bare[1].digits;
        warnings.push("IMEI tomado de un número sin etiqueta: revisar que sea el IMEI.");
      }
    }
    const colour = explicitColour || attrs.colour;
    if (!explicitColour && attrs.colours.length > 1) warnings.push(`Varios colores en la línea: ${attrs.colours.join(", ")}. Elegir uno.`);
    if (attrs.capacities.length > 1) warnings.push(`Varias capacidades en la línea: ${attrs.capacities.join(" GB, ")} GB. Elegir una.`);
    const quantities = [...new Set(details.flatMap((d) => d.quantity))];
    for (const d of details) warnings.push(...d.warnings);
    if (details.some((d) => d.personal)) warnings.push("Se omitió un dato de contacto del texto: no es del equipo.");
    const amounts = amountsOf(details, attrs.amounts, warnings, kind, quantities.length === 1 ? quantities[0] : null);
    const supplierCost = amounts.supplierCost;
    const model = commercialName(attrs.nameTokens, kind);
    const batteryPercent = battery ?? attrs.battery;
    if (quantities.length > 1) warnings.push(`Cantidades distintas en el texto: ${quantities.join(" y ")}. Indicar cuántas llegan.`);
    const stated = [...new Set(details.flatMap((d) => d.stated))];
    if (stated.length > 1) warnings.push("El texto dice nuevo y seminuevo a la vez: revisar la línea.");
    const statedLine = stated.length === 1 ? stated[0] : "";
    const graded = details.find((d) => d.grade);
    const gradeText = graded?.grade ?? "";
    const gradeFrom: TextCandidate["gradeFrom"] = graded ? (graded.gradeBare ? "bare" : "label") : "";
    const notes = details.flatMap((d) => d.notes).join("; ").slice(0, 300);
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
    if (line === "new" && (statedLine === "used" || /\b(?:rfb|refurbished|reacondicionado|seminuevo|usado)\b/i.test(source))) {
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
      watts: attrs.watts, length: attrs.length, generation: attrs.generation, serial, imei, imei2, imeiFrom, imeiGrouped, ...amounts,
      statedLine, statedQuantity: quantities.length === 1 ? quantities[0] : null,
      quantitySpaced: quantities.length === 1 && details.some((d) => d.spacedQuantity), gradeText, gradeFrom, notes,
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
