/**
 * INV-TEXT-02 — the stages of reading a supplier's list, one small function
 * each: clean a line, tell what it is, split it into tokens, give each token
 * its meaning (capacity, RAM, SIM, region, watts, length, generation, colour,
 * amount), then build the commercial name from what is left. Numbers are read
 * by their position and their unit, never by how many digits they have.
 * Nothing here writes, calls the network or decides a quantity.
 */

// ── 1. Conservative cleaning ─────────────────────────────────────────────────

const PICTOGRAPHS = /[\p{Extended_Pictographic}\u{FE0F}\u{200D}]/gu;
/** «[22/10/26, 10:15:03] Proveedor X: …» or «22/10/26 10:15 - Proveedor X: …» (exported chat). */
const CHAT_PREFIX = /^\[?\d{1,2}\/\d{1,2}\/\d{2,4},?\s+\d{1,2}:\d{2}(?::\d{2})?\s*(?:[ap]\.?\s?m\.?)?\]?\s*(?:-\s*)?[^:\n]{1,40}:\s+/i;
const BULLET = /^(?:[-–—•·▪►➤>*+]\s*)+/;
const TRAILING_DECORATION = /(?:\s+[-–—•·*]+)+$/;

/** One line as a person would read it: chat prefix, emojis, bullets and *bold* out; inner dashes kept. */
export function cleanLine(raw: string): string {
  let line = raw.replace(CHAT_PREFIX, "").replace(PICTOGRAPHS, " ");
  line = line.replace(/(^|\s)[*_~]+(?=\S)/g, "$1").replace(/(?<=\S)[*_~]+(?=\s|$)/g, "");
  line = line.trim().replace(BULLET, "").replace(TRAILING_DECORATION, "");
  return line.replace(/\s+/g, " ").trim();
}

// ── 2. What a line is ────────────────────────────────────────────────────────

export type LineKind = "device" | "accessory" | "field" | "header" | "date" | "other";

const DEVICE_START = /^(?:iphone|iph|ip)\s*\d|^(?:iphone|iph)\s+(?:x[rs]?|se|air)\b|^(?:samsung|galaxy)\b|^s\d{2,3}(?:fe|ultra|plus|\+)?\b|^a\d{2}\b|^z\s?(?:flip|fold)|^(?:ipad|macbook|imac|mac\s?mini|apple\s?watch|watch\s?(?:se|ultra|series|s\d)|pixel|redmi|xiaomi|poco|motorola|moto\s?g|honor)\b/i;
const ACCESSORY_START = /^(?:airpods?|apple\s?pencil|pencil|cargador(?:es)?|charger|cable|funda|case|carcasa|adaptador|teclado|magic\s?(?:keyboard|mouse|trackpad)|keyboard|mouse|protector|vidrio|mica|correa|pulsera|power\s?bank|bater[ií]a\s+externa|magsafe|airtag|earpods|aud[ií]fonos|parlante|soporte|hub|lapiz|lápiz)\b/i;
const FIELD = /^(?:bater[ií]a|bat|salud|n\.?\s?s\.?|n\/s|s\/n|sn|serie|serial|imei\s*[12]?|versi[oó]n|color|colour|capacidad|ram|costo|cto|precio|valor|pvp|venta|condici[oó]n|estado|grado|cantidad|cant|obs|observaci[oó]n(?:es)?|notas?|detalles?)\b\s*:?/i;
/** A line that is only an attribute of the product above: a capacity, a SIM, a battery, an amount with its currency. */
const LONE_ATTRIBUTE = /^(?:\d{2,4}\s*(?:gb|tb)|\d\s*tb|chip|e-?sim|\d{2,3}\s*%|(?:s\/\.?|us\$|\$|usd)\s*\d[\d.,]*|\d[\d.,]*\s*(?:soles|usd|d[oó]lares)|\d{1,2}\s*\/\s*10)$/i;
const DATE = /^\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}$/;

export function kindOfLine(line: string): LineKind {
  if (!line) return "other";
  if (DATE.test(line)) return "date";
  if (DEVICE_START.test(line)) return "device";
  if (ACCESSORY_START.test(line)) return "accessory";
  if (FIELD.test(line) || LONE_ATTRIBUTE.test(line) || COLOURS.includes(line.toLocaleLowerCase("es"))) return "field";
  if (/^(?:lista|stock|proveedor|actualizad|ayer|hoy)\b/i.test(line)) return "header";
  return "other";
}

/** Why a line that is not a product was left out: never silently. */
export function reasonFor(line: string, kind: LineKind): string {
  if (kind === "date") return "Fecha";
  if (kind === "header") return "Encabezado";
  return /\d/.test(line) ? "Modelo no reconocido" : "Texto sin producto";
}

// ── 3. Tokens and their meaning ──────────────────────────────────────────────

const STORAGE = new Set([16, 32, 64, 128, 256, 512]);
const REGIONS: Record<string, string> = {
  lla: "LL/A", "ll/a": "LL/A", zaa: "ZA/A", "za/a": "ZA/A", zpa: "ZP/A", "zp/a": "ZP/A", cha: "CH/A",
  "ch/a": "CH/A", "j/a": "J/A", ja: "J/A", "e/a": "E/A", "b/a": "B/A", "kh/a": "KH/A", kha: "KH/A",
  "lz/a": "LZ/A", lza: "LZ/A", "be/a": "BE/A", "x/a": "X/A", "hn/a": "HN/A", "qn/a": "QN/A", "c/a": "C/A",
  "t/a": "T/A", "vc/a": "VC/A",
};
/** Longest first: «titanio desierto» before «titanio». Spanish and English as suppliers write them. */
const COLOURS = [
  "titanio desierto", "titanio natural", "titanio negro", "titanio blanco", "titanio azul", "desert titanium",
  "natural titanium", "black titanium", "white titanium", "blue titanium", "azul medianoche", "azul alpino",
  "azul sierra", "azul pacifico", "azul pacífico", "verde alpino", "verde noche", "gris espacial", "space gray",
  "space grey", "oro rosa", "rose gold", "blanco estelar", "luz estelar", "deep purple", "morado oscuro",
  "negro espacial", "space black", "cosmic orange", "naranja cósmico", "naranja cosmico", "azul niebla",
  "mist blue", "lavanda", "medianoche", "midnight", "starlight", "negro", "blanco", "azul", "rojo", "verde",
  "amarillo", "morado", "rosado", "rosa", "dorado", "plateado", "plata", "gris", "grafito", "natural", "titanio",
  "black", "white", "blue", "navy", "red", "green", "yellow", "purple", "pink", "gold", "silver", "graphite",
  "gray", "grey", "cream", "crema", "mint", "menta", "ultramarine", "ultramarino", "teal", "coral", "violeta",
];

export type Attributes = {
  capacityGb: number | null; ramGb: number | null; sim: "chip" | "esim" | ""; region: string;
  watts: number | null; length: string; generation: string; colour: string; amounts: string[];
  battery: number | null; nameTokens: string[];
  /** INV-TEXT-03: every capacity and every colour the line names; more than one means no single variant. */
  capacities: number[]; colours: string[];
};

/** RAM/storage: the storage is a real size («8/256»), so a grade «9/10» is never 9 GB of RAM. */
const RAM_STORAGE = /^(\d{1,2})\/(16|32|64|128|256|512|1024|2048)(?:gb)?$/i;
const CAPACITY = /^(\d{2,4})(?:gb|g)$/i;
const TERA = /^(\d)tb$/i;
const WATTS = /^(\d{1,3})w$/i;
const LENGTH = /^(\d+(?:[.,]\d+)?)(m|cm|mts?)$/i;
/** A bare amount token (a currency glued in front is read by `readDetails` first). */
const MONEY = /^(\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d{1,7}(?:[.,]\d{1,2})?)$/i;
/** Tokens that only separate fields: «|», «/», «-», «→»… */
const SEPARATOR = /^[|;,/\\\-–—:=→>+#·•]+$/;
const ORDINAL = /^(\d)(?:da|ra|ta|ma|va|na|ra|°|º|ª|nd|rd|th|st|a)$/i;
const GEN_WORD = /^(?:gen|generaci[oó]n|generacion|generation)$/i;
const MODEL_WORDS = new Set(["pro", "max", "plus", "mini", "fe", "ultra", "se", "air", "lite", "edge", "neo"]);

function joinUnits(tokens: string[]): string[] {
  // «256 gb», «1 tb», «20 w», «1 m», «e sim», «ll /a» → one token each.
  const out: string[] = [];
  for (const token of tokens) {
    const previous = out[out.length - 1];
    if (previous && /^\d+(?:[.,]\d+)?$/.test(previous) && /^(?:gb|tb|w|m|cm|mts?|%)$/i.test(token)) {
      out[out.length - 1] = previous + token;
    } else if (previous && /^e$/i.test(previous) && /^-?sim$/i.test(token)) {
      out[out.length - 1] = "esim";
    } else {
      out.push(token);
    }
  }
  return out;
}

function takeColour(words: string[]): [string, Set<number>] {
  const lower = words.map(word => word.toLocaleLowerCase("es"));
  for (const colour of COLOURS) {
    const parts = colour.split(" ");
    for (let i = 0; i + parts.length <= lower.length; i++) {
      if (parts.every((part, k) => lower[i + k] === part)) {
        return [colour, new Set(parts.map((_part, k) => i + k))];
      }
    }
  }
  return ["", new Set()];
}

const ID_LABEL = /^(?:imei[12]?|n\.?s\.?|n\/s|s\/n|sn|serie|serial)[:#=]?$/i;
const UNIT = /^\d{1,4}(?:gb|tb|w|mm|m|mah)$/i;

/** A serial-shaped code: 8+ letters and digits (hyphens aside), both present — not a «word-number» («Thunderbolt-4»). */
function looksLikeCode(token: string): boolean {
  const bare = token.replace(/-/g, "");
  return /^[a-z0-9]{8,}$/i.test(bare) && /\d/.test(bare) && (bare.match(/[a-z]/gi) ?? []).length >= 2
    && !UNIT.test(token) && !/^[a-z]+-\d{1,2}$/i.test(token);
}

const ID_GLUED = /^(?:imei[12]?|n\.?s\.?|n\/s|s\/n|sn|serie|serial)[:#=]\S+$/i;

/**
 * Pre-merge review: the tokens of a line that are an identifier (IMEI, serial, document) and never part of a
 * name — a label with its value, a run of 7+ digits, a code of 8+ that mixes letters and digits. Shared with
 * what travels to the catalogue (`matchPayload`), so an identifier typed into the model does not travel either.
 */
export function identifierTokens(tokens: string[]): Set<number> {
  const marked = new Set<number>();
  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i];
    if (ID_GLUED.test(token) || /^\d{7,}$/.test(token.replace(/[.\-/]/g, "")) || looksLikeCode(token)) {
      marked.add(i);
      continue;
    }
    if (!ID_LABEL.test(token)) continue;
    let j = i + 1;
    const sep = /^[:#=]$/.test(tokens[j] ?? "");
    if (/^imei/i.test(token)) {
      marked.add(i);
      if (sep) marked.add(j++);
      let digits = 0;
      while (j < tokens.length && digits < 15 && /^\d[\d.\-]*$/.test(tokens[j])) {
        digits += tokens[j].replace(/\D/g, "").length;
        marked.add(j++);
      }
    } else {
      const value = tokens[sep ? j + 1 : j] ?? "";
      // «Serie 9», «Serie 45mm» are names; a serial label is followed by a code of 4 or more.
      // A unit after «serie» is a name; after «SN»/«N/S» it is still a serial.
      if (!/^[a-z0-9][a-z0-9/-]{3,39}$/i.test(value) || (/^serie/i.test(token) && UNIT.test(value))) continue;
      marked.add(i);
      if (sep) marked.add(j++);
      marked.add(j++);
    }
    i = j - 1;
  }
  return marked;
}

/**
 * The meaning of each token of a product line. `familyEnd`: how many tokens
 * name the family and its model number («IPH 16», «AirPods Pro 2»); those are
 * never an amount or a capacity.
 */
export function readAttributes(line: string, kind: "device" | "accessory", { field = false } = {}): Attributes {
  // Separators («|», «-», «/» between fields) and the punctuation stuck to a word are not part of any name.
  const words = splitFamily(line).split(" ").map(tidyToken).filter((token) => token && !SEPARATOR.test(token));
  const all = joinUnits(words);
  // Identifiers are read from the whole line elsewhere (IMEI, serial); here they are never a name or an amount.
  const ids = identifierTokens(all);
  const tokens = all.filter((_token, index) => !ids.has(index));
  const attrs: Attributes = {
    capacityGb: null, ramGb: null, sim: "", region: "", watts: null, length: "", generation: "", colour: "",
    amounts: [], battery: null, nameTokens: [], capacities: [], colours: [],
  };
  const left: string[] = [];
  // A field line («128 GB», «Versión: Chip») has no family in front.
  const familyEnd = field ? 0 : familyLength(tokens, kind);
  const capacity = (gb: number) => { if (!attrs.capacities.includes(gb)) attrs.capacities.push(gb); };
  tokens.forEach((token, index) => {
    const lower = token.toLocaleLowerCase("es");
    if (index < familyEnd) { left.push(token); return; }
    let m: RegExpMatchArray | null;
    if ((m = token.match(RAM_STORAGE))) { attrs.ramGb = Number(m[1]); capacity(Number(m[2])); return; }
    if ((m = token.match(CAPACITY)) && kind === "device") { capacity(Number(m[1])); return; }
    if ((m = token.match(TERA)) && kind === "device") { capacity(Number(m[1]) * 1024); return; }
    if ((m = token.match(WATTS))) { attrs.watts = Number(m[1]); left.push(token); return; }
    if ((m = token.match(LENGTH))) { attrs.length = `${m[1]}${m[2].toLowerCase()}`; left.push(token); return; }
    if ((m = token.match(/^(\d{2,3})%$/))) { attrs.battery = Number(m[1]); return; }
    if (lower === "chip" || lower === "fisico" || lower === "físico") { attrs.sim = "chip"; return; }
    if (lower === "esim" || lower === "e-sim") { attrs.sim = "esim"; return; }
    if (REGIONS[lower]) { attrs.region = REGIONS[lower]; return; }
    if ((m = token.match(ORDINAL)) && GEN_WORD.test(tokens[index + 1] ?? "")) {
      attrs.generation = m[1]; left.push(token); return;
    }
    if (GEN_WORD.test(token) && attrs.generation) { left.push(/^generacion$/i.test(token) ? "generación" : token); return; }
    if ((m = token.match(MONEY))) {
      const amount = normalizeAmount(m[1]);
      const value = Number(amount);
      // A storage size after the model, before any amount, is the capacity («IPH 15 128 2300»).
      if (kind === "device" && attrs.capacities.length === 0 && STORAGE.has(value) && !/[.,]/.test(m[1])) {
        capacity(value);
        return;
      }
      // «Funda 15 pro», «Funda iPhone 15»: a number that names a compatible model is part of the name.
      if (kind === "accessory" && (MODEL_WORDS.has((tokens[index + 1] ?? "").toLowerCase())
        || /^(?:iphone|iph|ip|galaxy|ipad|watch|series|serie|pixel|redmi)$/i.test(tokens[index - 1] ?? ""))) {
        left.push(token); return;
      }
      if (amount) { attrs.amounts.push(amount); return; }
    }
    left.push(token);
  });
  // Two different capacities in one line («128GB 256GB») name two variants: none is chosen.
  attrs.capacityGb = attrs.capacities.length === 1 ? attrs.capacities[0] : null;
  // Every known colour of the line, longest first; two different ones name two variants: none is chosen.
  let rest = left.slice(familyEnd);
  for (;;) {
    const [colour, used] = takeColour(rest);
    if (!colour) break;
    if (!attrs.colours.includes(colour)) attrs.colours.push(colour);
    rest = rest.filter((_token, index) => !used.has(index));
  }
  attrs.colour = attrs.colours.length === 1 ? attrs.colours[0] : "";
  attrs.nameTokens = [...left.slice(0, familyEnd), ...rest];
  return attrs;
}

/** A word without the punctuation a chat sticks to it («Azul,», «(nuevo», «*bold*» already out). */
function tidyToken(token: string): string {
  return token.replace(/^[(\[{«"']+/, "").replace(/[)\]}»"',;:!?¡¿]+$/, "");
}

/** «s25fe» → «s25 fe», «iph15» → «iph 15»: the family and its number apart. */
function splitFamily(line: string): string {
  return line
    .replace(/^(iphone|iph|ip)(\d)/i, "$1 $2")
    .replace(/^(s\d{2,3})(fe|ultra|plus|\+)\b/i, "$1 $2");
}

function familyLength(tokens: string[], kind: "device" | "accessory"): number {
  const lower = tokens.map(token => token.toLocaleLowerCase("es"));
  let end = 1;
  if (kind === "device") {
    if (["samsung", "galaxy"].includes(lower[0]) && lower[1]) end = 2;
    if (["iphone", "iph", "ip", "pixel", "redmi", "poco", "honor"].includes(lower[0]) && /^\d{1,2}[a-z]?$/.test(lower[1] ?? "")) end = 2;
    if (["apple"].includes(lower[0]) && lower[1] === "watch") end = /^\d{1,2}$/.test(lower[2] ?? "") ? 3 : 2;
  } else {
    if (lower[0] === "apple" && lower[1]) end = 2;
    if (lower[0] === "magic" && lower[1]) end = 2;
  }
  // Model words and one short generation number right after the family: «Pro Max», «AirPods Pro 2», «AirPods 4».
  while (end < tokens.length && MODEL_WORDS.has(lower[end])) end++;
  if (kind === "accessory" && /^\d$/.test(lower[end] ?? "")) end++;
  // «iPad Air 5», «MacBook Pro 14»: a generation or a screen size of one or two digits names the model.
  else if (kind === "device" && ["ipad", "macbook", "imac"].includes(lower[0]) && /^\d{1,2}$/.test(lower[end] ?? "")) end++;
  return end;
}

// ── 4. The commercial name ───────────────────────────────────────────────────

const CASING: Record<string, string> = {
  iph: "iPhone", ip: "iPhone", iphone: "iPhone", pro: "Pro", max: "Max", plus: "Plus", mini: "Mini", fe: "FE",
  ultra: "Ultra", se: "SE", air: "Air", airpods: "AirPods", airpod: "AirPods", apple: "Apple", pencil: "Pencil",
  "usb-c": "USB-C", usbc: "USB-C", lightning: "Lightning", magsafe: "MagSafe", ipad: "iPad", macbook: "MacBook",
  watch: "Watch", galaxy: "Galaxy", samsung: "Samsung", cargador: "Cargador", cable: "Cable", funda: "Funda",
  adaptador: "Adaptador", teclado: "Teclado", magic: "Magic", keyboard: "Keyboard", mouse: "Mouse",
  "generación": "generación", airtag: "AirTag", earpods: "EarPods",
};

export function commercialName(tokens: string[], kind: "device" | "accessory"): string {
  const words = tokens.map((token, index) => {
    const lower = token.toLocaleLowerCase("es");
    if (CASING[lower]) return CASING[lower];
    if (/^s\d{2,3}$/i.test(token)) return token.toUpperCase();
    if (/^\d+w$/i.test(token)) return token.toUpperCase();
    // The rest is the supplier's own wording: kept, only the first letter of the line capitalised.
    return index === 0 ? token.charAt(0).toLocaleUpperCase("es") + token.slice(1) : token;
  });
  if (kind === "device" && /^S\d{2,3}$/.test(words[0] ?? "")) words.unshift("Galaxy");
  if (kind === "device" && words[0] === "Samsung" && words[1] === "Galaxy") words.shift();
  return words.join(" ");
}

// ── 5. INV-TEXT-03 · amounts, quantities and the words around a product ──────
//
// Read BEFORE the tokens, from the text as written: what a label says («Costo:», «Precio de venta»), an amount
// with its currency («S/ 2,500.00», «2500 soles»), the quantity («x2», «2 unidades»), the battery («bat 89%»), the
// condition the row states («seminuevo», «nvo»), the cosmetic grade («estado A») and the notes («con caja», «(…)»).
// Each is taken out of the line, so the rest (model, capacity, colour) is read as before. Nothing is decided here:
// what the label does not say is left for a person (`ambiguous`), and no amount is ever derived from another.

/** An amount as people write it in Peru: «2500», «2,500», «2.500», «4,250.00», «2.500,00», «1850.5». */
const AMOUNT = String.raw`\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d{1,7}(?:[.,]\d{1,2})?`;
const AMOUNT_END = String.raw`(?![\d.,]*\d)`;
const CURRENCY = String.raw`s\/\.?|us\$|\$|usd|pen`;
const CURRENCY_WORD = String.raw`soles|sol|usd|d[oó]lares|pen`;
const NOT_LETTER = String.raw`(?![\p{L}])`;
const NO_WORD_BEFORE = String.raw`(?<![\p{L}\d])`;

/**
 * The amount with a dot for decimals and no thousands separator, or null if it is not an amount. A group of three
 * digits after a separator is thousands («2.500» = 2500); one or two are decimals («12,5» = 12.50).
 */
export function normalizeAmount(raw: string): string | null {
  const value = raw.trim();
  let m: RegExpExecArray | null;
  if ((m = /^(\d{1,3}(?:,\d{3})+)(\.\d{1,2})?$/.exec(value))) return m[1].replace(/,/g, "") + decimals(m[2]);
  if ((m = /^(\d{1,3}(?:\.\d{3})+)(,\d{1,2})?$/.exec(value))) return m[1].replace(/\./g, "") + decimals(m[2]);
  if ((m = /^(\d{1,7})([.,]\d{1,2})?$/.exec(value))) return m[1] + decimals(m[2]);
  return null;
}
const decimals = (part: string | undefined) => (part ? "." + part.slice(1).padEnd(2, "0") : "");

/** A notice as somebody without `costs.view` may read it: the amounts in brackets and after «Importe» out. */
export function withoutAmounts(warning: string): string {
  return warning.replace(/\s*\([^)]*\d[^)]*\)/g, "").replace(/^Importe \S+ demasiado bajo/, "Importe demasiado bajo");
}

/** What a label says an amount is. Longest first: «precio de venta» before «precio». */
const SALE_LABEL = String.raw`precio\s+(?:de\s+)?venta|precio\s+(?:al\s+)?p[uú]blico|precio\s+final(?:\s+al\s+cliente)?|precio\s+(?:al\s+)?cliente|p\.?v\.?p\.?|venta`;
const COST_LABEL = String.raw`costo(?:\s+(?:de\s+)?(?:compra|proveedor|unitario))?|precio\s+(?:de\s+)?(?:compra|costo)|precio\s+(?:del\s+)?proveedor|cto\.?`;
const BARE_LABEL = String.raw`precio|price|valor`;
/** Words that may qualify a label before its amount («precio al por mayor», «precio de venta sugerido»): a closed list. */
const QUALIFIER = String.raw`(?:\s+(?:al|de|por|mayor|mayorista|sugerido|final|cliente|lista|unitario|especial|normal|referencial|tienda|unidad)){0,4}`;
const LABELLED = new RegExp(
  String.raw`${NO_WORD_BEFORE}(${SALE_LABEL}|${COST_LABEL}|${BARE_LABEL})${QUALIFIER}\s*(?:[:=\-–]\s*)?(${CURRENCY})?\s*(${AMOUNT})${AMOUNT_END}\s*(${CURRENCY_WORD})?${NOT_LETTER}`,
  "giu");
const WITH_CURRENCY = new RegExp(String.raw`${NO_WORD_BEFORE}(${CURRENCY})\s*(${AMOUNT})${AMOUNT_END}${NOT_LETTER}`, "giu");
const CURRENCY_AFTER = new RegExp(String.raw`(?<![\p{L}\d.,])(${AMOUNT})${AMOUNT_END}\s*(${CURRENCY_WORD})${NOT_LETTER}`, "giu");
const IS_USD = /^(?:us\$|\$|usd|d[oó]lares)$/i;

const BATTERY_LABELLED = new RegExp(
  String.raw`${NO_WORD_BEFORE}(?:salud\s+de\s+(?:la\s+)?bater[ií]a|condici[oó]n\s+de\s+(?:la\s+)?bater[ií]a|bater[ií]a|bat\.?)\s*[:=]?\s*(\d{1,3})\s*%?(?![\d\p{L}])`,
  "giu");
const BATTERY_PERCENT = /(?<![\d.,])(\d{2,3})\s*%/g;

/**
 * Read and thrown away before anything else (never a model, an amount or a note): a Peruvian mobile («+51 987 654 321»,
 * «987654321»), a RUC or DNI, with the word that introduces them.
 */
const CONTACT = /(?<![\p{L}\d])(?:(?:llamar(?:\s+al)?|cel(?:ular)?|tel(?:[eé]fono)?|whats?app|wsp|contacto|n[uú]mero)\.?\s*:?\s*)?(?:\+?51[\s-]?)?9\d{2}[\s-]?\d{3}[\s-]?\d{3}(?!\d)|(?<![\p{L}])(?:ruc|dni)\s*:?\s*\d{8,11}(?!\d)/giu;
/** «pedido 12345», «orden #77», «n° 12»: a reference, kept as a note, never an amount. */
const REFERENCE = /(?<![\p{L}])((?:pedido|orden|order|n[°º]|nro\.?)\s*:?\s*#?\s*\d{1,10})(?!\d)/giu;
/** «desc. 10%», «dscto 50», «10% off»: a discount, kept as a note, never a battery or an amount. */
const DISCOUNT = /(?<![\p{L}])((?:desc(?:uento)?|dscto|dto)\.?\s*:?\s*(?:s\/\.?\s*)?\d{1,7}(?:[.,]\d{1,2})?\s*%?|\d{1,3}\s*%\s*(?:de\s+)?(?:desc(?:uento)?|dscto|dto|off))(?![\p{L}\d])/giu;
/**
 * «5 x 15», «3x45», «2 x S/ 2500»: a number, «x», an amount. Units at a price, or a price for units: nobody can tell, so
 * neither is taken (`pair`). When the first number belongs to the model («iPhone 15 x 25») it stays in the name.
 */
const PAIR = new RegExp(String.raw`(?<![\p{L}\d.,])(\d{1,4})\s*[x×]\s*(?:(${CURRENCY})\s*)?(${AMOUNT})${AMOUNT_END}(?!\s*(?:gb|tb|g|w|mm|cm|m|mah|%)(?![\p{L}]))${NOT_LETTER}`, "giu");
const MODEL_BEFORE = /(?<![\p{L}])(?:iphone|iph|ip|galaxy|ipad|airpods|watch|series|serie|pro|max|plus|mini|air|ultra|fe|note|pixel|redmi|s)\s*$/iu;
/** A storage size right before «x N» in a device line («iPhone 15 128 x2»): the capacity, then the quantity. */
const STORAGE_BEFORE = /(?<![\d.,])(?:16|32|64|128|256|512|1024|2048)\s*(?:gb|g)?\s*$/i;
/** «c/u», «cada uno» and the «a» of «a 15» say «each»: read, then out of the name. */
const EACH = /(?<![\p{L}])(?:c\/u|c\/\s?u|cada\s+un[oa])(?![\p{L}])|(?<![\p{L}])a\s+(?=(?:s\/\.?\s*)?\d)/giu;
/** «2500 y 2800»: amounts whose label is a word of selling («oferta», «final», «venta»…) anywhere in the row. */
const SALE_WORD = /(?<![\p{L}])(?:precios?|oferta|final|pvp|venta|p[uú]blico|cliente|remate|liquidaci[oó]n|antes|ahora)(?![\p{L}])/giu;
const SELLING = /^(?:oferta|final|pvp|venta|p[uú]blico|cliente|remate|liquidaci[oó]n|antes|ahora)$/iu;
/** «1800/2000»: two amounts (not «8/256», not «128/256»). */
const AMOUNT_PAIR = /(?<![\d/.,])(\d{3,7})\s*\/\s*(\d{3,7})(?![\d/])/g;
/** «31/10», «31/10/26»: a date, noise. */
/** «iPhone 13 x2»: the 13 is the model's number, so the «x2» is a quantity. */
const MODEL_NUMBER_BEFORE = /(?<![\p{L}])(?:iphone|iph|ip|galaxy|ipad(?:\s+(?:air|mini|pro))?|airpods(?:\s+(?:pro|max))?|watch(?:\s+(?:series|se|ultra))?|macbook(?:\s+(?:air|pro))?|series|serie|ultra|pixel|redmi|s|a)\s*\d{1,2}(?:\s*(?:pro|max|plus|mini|fe|ultra|e))*\s*$/iu;
const DATE_IN_LINE = /(?:(?<![\p{L}])(?:hoy|ayer|fecha|llega(?:\s+el)?|para\s+el|del|el)\s+)?(?<![\d/])\d{1,2}\/\d{1,2}(?:\/\d{2,4})?(?![\d/])/giu;
const DATE_WORD = /(?:hoy|ayer|el|del|fecha|d[ií]a|llega|lleg[oó]|para|desde|hasta)\s*$/i;

const QUANTITY_TIMES = /(?<![\p{L}\d])[x×]\s?(\d{1,4})(?!\s*(?:gb|tb|g|w|mm|m|cm|mah|%)(?![\p{L}]))(?![\d.,\p{L}])/giu;
const QUANTITY_UNITS = /(?<![\p{L}\d.,])(\d{1,4})\s?(?:unidades|unidad|unids?\.?|unds?\.?|uds?\.?|pzas?\.?|piezas?)(?![\p{L}])/giu;
const QUANTITY_LABEL = /(?<![\p{L}])(?:cantidad|cant\.?|qty)\s*[:=]?\s*(\d{1,4})(?![\d.,\p{L}])/giu;
/** «iPhone X»: an «x» right after the family is a model, not «times». */
const BEFORE_MODEL_X = /(?:iphone|iph|ip)\s*$/i;

const STATE_LABEL = /(?<![\p{L}])(?:estado|condici[oó]n)\s*[:=]?\s*(?=(?:semi|nuev|usad|sellad|como\s+nuev|reacond|refurb|nvo|sem\b))/giu;
/** A condition word right after a part («pantalla nueva», «funda usada») is about the part, never the device. */
const PART = String.raw`(?:pantalla|bater[ií]a|funda|caja|cargador|cable|mica|vidrio|carcasa|tapa|correa|repuesto|case|cover|screen|battery)`;
const USED_WORD = /(?<![\p{L}])(?<!(?:pantalla|bater[ií]a|funda|caja|cargador|cable|mica|vidrio|carcasa|tapa|correa|repuesto)\s+)(como\s+nuev[oa]|semi[\s-]?nuev[oa]s?|seminuev[oa]s?|semi|sem|usad[oa]s?|used|reacondicionad[oa]s?|refurbished|rfb|cpo)(?![\p{L}])/giu;
const NEW_WORD = /(?<![\p{L}])(?<!(?:pantalla|bater[ií]a|funda|caja|cargador|cable|mica|vidrio|carcasa|tapa|correa|repuesto)\s+)(nuev[oa]s?|nvo|new|sellad[oa]s?|sealed)(?![\p{L}])(?!\s+(?:battery|screen|case|cable|charger))/giu;

const GRADE = /(?<![\p{L}])(?:estado|grado|est[eé]tica|cosm[eé]tic[oa])(?:\s+est[eé]tic[oa])?\s*[:=]?\s*(a\+|a-|b\+|b-|a|b|c|d|\d{1,2}\s*\/\s*10|excelente|impecable|muy\s+bueno|bueno|regular)(?![\p{L}\d+-])/giu;
/** «9/10» alone: a grade only from 0 to 10 out of 10, never a date («31/10», «hoy 10/10», «10/10/26»). */
const GRADE_ALONE = /(?<![\d/])(10|\d)\s*\/\s*10(?![\d/])/g;

const RAM = /(?<![\p{L}\d])(?:(\d{1,2})\s?gb\s*(?:de\s+)?ram|ram\s*:?\s*(\d{1,2})\s?gb)(?![\p{L}])/giu;
const NOTE = new RegExp(String.raw`\(([^()]{1,80})\)|(?<![\p{L}])((?:con|sin)\s+(?:caja|cargador|cable|accesorios|funda|cubo)(?:\s+(?:nuev[oa]|original|usad[oa]|cambiad[oa]))?|caja\s+original|solo\s+equipo|liberado|libre|${PART}\s+(?:nuev[oa]|cambiad[oa]|original|usad[oa]|new|replaced)|new\s+(?:battery|screen|case))(?![\p{L}])`, "giu");
/** A note that names a person or a contact is left out (it is not about the device). */
const PERSONAL = /(?<![\p{L}])(?:cliente|sr\.?|sra\.?|srta\.?|contacto|llamar|cel(?:ular)?|tel[eé]fono|whats?app|wsp|correo|email)(?![\p{L}])|@/iu;
const NOTE_FIELD = /^(?:obs(?:ervaci[oó]n(?:es)?)?|notas?|detalles?|comentarios?)\s*[:.\-]?\s*(.+)$/i;

export type Details = {
  /** The line without what was read here. */
  rest: string;
  cost: string[];
  sale: string[];
  /** «Precio: 2500» — a price, but whose? */
  bare: string[];
  /** Amounts in dollars: never a cost in soles. */
  usd: string[];
  /** An amount with its currency and no label. */
  unlabelled: string[];
  quantity: number[];
  battery: number | null;
  stated: ("new" | "used")[];
  grade: string;
  ramGb: number | null;
  notes: string[];
  /** INV-TEXT-03 review: amounts of a «N x amount» pair — never a cost, never a quantity. */
  pair: string[];
  /** Said about the line, when a quantity or a pair could not be read safely. */
  warnings: string[];
  /** A word of selling («oferta», «precio final»…) somewhere in the line. */
  selling: boolean;
  /** The grade was a bare «9/10», not after «estado»/«grado». */
  gradeBare: boolean;
  /** Something personal (a contact) was left out of the notes. */
  personal: boolean;
  /** The quantity came from «x N» with a space («x 25»), which could also read «por 25». */
  spacedQuantity: boolean;
};

/** A note never carries an identifier: an IMEI, a serial or a long number stays where the identifier reader sees it. */
const hasIdentifier = (text: string) => identifierTokens(text.split(/\s+/).filter(Boolean)).size > 0 || /\d{7,}/.test(text.replace(/[ .-]/g, ""));

/** «a+» → «A+», «9 / 10» → «9/10», «Muy  bueno» → «muy bueno»: as the screen compares it with the company's grades. */
const gradeText = (raw: string) => {
  const value = raw.trim().replace(/\s+/g, " ").replace(/\s*\/\s*/, "/");
  return /^[a-d][+-]?$/i.test(value) ? value.toUpperCase() : value.toLocaleLowerCase("es");
};

/** `device`: the line names a device, so a storage size before «x N» is its capacity. */
export function readDetails(line: string, { device = false } = {}): Details {
  const d: Details = {
    rest: ` ${line} `, cost: [], sale: [], bare: [], usd: [], unlabelled: [], quantity: [], battery: null, stated: [],
    grade: "", ramGb: null, notes: [], pair: [], warnings: [], selling: false, gradeBare: false, personal: false, spacedQuantity: false,
  };
  /** `read` answers false to leave the match in the line, a text to put in its place, or nothing to take it out. */
  const take = (pattern: RegExp, read: (m: string[], offset: number, all: string) => boolean | string | void) => {
    d.rest = d.rest.replace(pattern, (...args: unknown[]) => {
      const groups = args.slice(0, -2) as string[];
      const offset = args[args.length - 2] as number;
      const all = args[args.length - 1] as string;
      const answer = read(groups, offset, all);
      return answer === false ? groups[0] : typeof answer === "string" ? answer : " ";
    });
  };
  const amount = (raw: string, currency: string | undefined, word: string | undefined, into: string[]) => {
    const value = normalizeAmount(raw);
    if (!value) return false;
    ((currency && IS_USD.test(currency)) || (word && IS_USD.test(word)) ? d.usd : into).push(value);
  };
  const note = (text: string) => {
    const clean = text.trim().replace(/\s+/g, " ");
    if (!clean || hasIdentifier(clean)) return false;
    if (PERSONAL.test(clean)) { d.personal = true; return; }
    d.notes.push(clean);
  };

  // Contacts first: a phone or a RUC is never read as an amount, a quantity or a word of the model.
  take(CONTACT, () => undefined);
  const field = NOTE_FIELD.exec(d.rest.trim());
  if (field) {
    if (note(field[1]) !== false) d.rest = " ";
    return d;
  }
  take(LABELLED, ([, label, currency, raw, word]) => {
    const kind = new RegExp(`^(?:${SALE_LABEL})$`, "iu").test(label) ? d.sale
      : new RegExp(`^(?:${COST_LABEL})$`, "iu").test(label) ? d.cost : d.bare;
    return amount(raw, currency, word, kind);
  });
  take(REFERENCE, ([, reference]) => { note(reference); });
  take(DISCOUNT, ([, discount]) => { note(discount); });
  take(PAIR, ([whole, first, currency, raw], offset, all) => {
    // «iPhone 15 x 25», «iPhone 13 x2»: the first number is the model's, so this is not a pair; «x N» is read below.
    if (MODEL_BEFORE.test(all.slice(0, offset))) return false;
    // «iPhone 15 128 x2 2500»: 128 is the capacity and «x2» the quantity (read below), not a pair.
    if (device && [16, 32, 64, 128, 256, 512, 1024, 2048].includes(Number(first))) return false;
    // The larger number is the amount (unless the currency says which one): «5 x 15» → 15, «1800 x 2» → 1800.
    const bigger = !currency && Number(normalizeAmount(first)) > Number(normalizeAmount(raw)) ? first : raw;
    if (amount(bigger, currency, undefined, d.pair) === false) return whole;
    d.warnings.push(`Cantidad y precio juntos («${first} x ${raw}»): indica cuántas unidades llegan y qué es el importe.`);
  });
  take(WITH_CURRENCY, ([, currency, raw]) => amount(raw, currency, undefined, d.unlabelled));
  take(CURRENCY_AFTER, ([, raw, word]) => amount(raw, undefined, word, d.unlabelled));
  take(NOTE, ([whole, inside, phrase]) => {
    // Not a note (it holds an identifier): the brackets go, the content stays for the identifier reader.
    if (inside !== undefined) return note(inside) === false ? ` ${inside} ` : undefined;
    return phrase !== undefined ? note(phrase) : whole;
  });
  take(RAM, ([, before, after]) => { d.ramGb = Number(before ?? after); });
  take(BATTERY_LABELLED, ([, value]) => { d.battery ??= Number(value); });
  take(BATTERY_PERCENT, ([, value]) => { d.battery ??= Number(value); });
  const quantity = (value: string) => { d.quantity.push(Number(value)); };
  take(QUANTITY_LABEL, ([, value]) => quantity(value));
  take(QUANTITY_UNITS, ([, value]) => quantity(value));
  take(QUANTITY_TIMES, ([whole, value], offset, all) => {
    if (BEFORE_MODEL_X.test(all.slice(0, offset))) return false;
    // «15 x5»: a bare number right before the «x» — the pair could not be read; the «x5» is not a quantity either.
    const before = all.slice(0, offset);
    if (/\d\s*$/.test(before) && !MODEL_NUMBER_BEFORE.test(before) && !(device && STORAGE_BEFORE.test(before))) {
      d.warnings.push(`«x ${value}» va después de otro número: no se sabe si es la cantidad. Indica cuántas unidades llegan.`);
      return " ";
    }
    // «x 25» with a space could also be «por 25 soles»: said, for the screen to ask when nothing else is an amount.
    if (/^[x×]\s/i.test(whole)) d.spacedQuantity = true;
    quantity(value);
  });
  take(GRADE, ([, grade]) => { d.grade ||= gradeText(grade); });
  take(GRADE_ALONE, ([whole, value], offset, all) => {
    if (DATE_WORD.test(all.slice(0, offset))) return whole;
    if (!d.grade) { d.grade = `${value}/10`; d.gradeBare = true; }
  });
  take(DATE_IN_LINE, () => undefined);
  take(AMOUNT_PAIR, ([whole, first, second]) => {
    if ([...STORAGE, 1024].includes(Number(first)) && [...STORAGE, 1024].includes(Number(second))) return whole;
    d.unlabelled.push(normalizeAmount(first)!, normalizeAmount(second)!);
  });
  take(EACH, () => undefined);
  take(SALE_WORD, ([word]) => { if (SELLING.test(word)) d.selling = true; });
  take(STATE_LABEL, () => undefined);
  take(USED_WORD, () => { d.stated.push("used"); });
  take(NEW_WORD, () => { d.stated.push("new"); });
  d.rest = d.rest.replace(/\s+/g, " ").trim();
  return d;
}

/**
 * Several products in one line («iPhone 13 128 1800, iPhone 12 64 1300 / iPhone 11 …»): split only at a separator
 * followed by another product, and only when the line starts with a device (a case «para iPhone 15, iPhone 14» is
 * one accessory) or both parts are accessories.
 */
export function splitProducts(line: string): string[] {
  const first = kindOfLine(line);
  if (first !== "device" && first !== "accessory") return [line];
  const parts: string[] = [];
  let from = 0;
  const separator = /\s*(?:[,;|+]|\s\/\s|\sy\s|\s-\s)\s*/g;
  for (let m = separator.exec(line); m; m = separator.exec(line)) {
    const after = line.slice(m.index + m[0].length);
    const next = kindOfLine(after);
    if ((first === "device" && (next === "device" || next === "accessory")) || (first === "accessory" && next === "accessory")) {
      parts.push(line.slice(from, m.index));
      from = m.index + m[0].length;
    }
  }
  parts.push(line.slice(from));
  return parts.map((part) => part.trim()).filter(Boolean);
}

/** «2x AirPods», «3 und. Cable»: a quantity in front of the product goes behind it, where it is read. */
export function quantityFirst(line: string): string {
  const m = /^(\d{1,3})\s?(?:x|×|und\.?|uds?\.?|unidades)\s+(.+)$/i.exec(line);
  if (!m) return line;
  const kind = kindOfLine(m[2]);
  return kind === "device" || kind === "accessory" ? `${m[2]} x${m[1]}` : line;
}
