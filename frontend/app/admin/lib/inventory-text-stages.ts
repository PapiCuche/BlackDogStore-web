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

const DEVICE_START = /^(?:iphone|iph|ip)\s*\d|^(?:samsung|galaxy)\b|^s\d{2,3}(?:fe|ultra|plus|\+)?\b|^a\d{2}\b|^z\s?(?:flip|fold)|^(?:ipad|macbook|imac|mac\s?mini|apple\s?watch|watch\s?(?:se|ultra|series|s\d)|pixel|redmi|xiaomi|poco|motorola|moto\s?g|honor)\b/i;
const ACCESSORY_START = /^(?:airpods?|apple\s?pencil|pencil|cargador(?:es)?|charger|cable|funda|case|carcasa|adaptador|teclado|magic\s?(?:keyboard|mouse|trackpad)|keyboard|mouse|protector|vidrio|mica|correa|pulsera|power\s?bank|bater[ií]a\s+externa|magsafe|airtag|earpods|aud[ií]fonos|parlante|soporte|hub|lapiz|lápiz)\b/i;
const FIELD = /^(?:bater[ií]a|n\.?\s?s\.?|n\/s|s\/n|serie|imei\s*[12]?|versi[oó]n|color|colour|capacidad|ram|costo|precio|condici[oó]n|estado)\b\s*:?/i;
const LONE_ATTRIBUTE = /^(?:\d{2,4}\s*(?:gb|tb)|\d\s*tb|chip|e-?sim|\d{2,3}\s*%)$/i;
const DATE = /^\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}$/;

export function kindOfLine(line: string): LineKind {
  if (!line) return "other";
  if (DATE.test(line)) return "date";
  if (DEVICE_START.test(line)) return "device";
  if (ACCESSORY_START.test(line)) return "accessory";
  if (FIELD.test(line) || LONE_ATTRIBUTE.test(line)) return "field";
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
};

const RAM_STORAGE = /^(\d{1,2})\/(\d{2,4})(?:gb)?$/i;
const CAPACITY = /^(\d{2,4})(?:gb|g)$/i;
const TERA = /^(\d)tb$/i;
const WATTS = /^(\d{1,3})w$/i;
const LENGTH = /^(\d+(?:[.,]\d+)?)(m|cm|mts?)$/i;
const MONEY = /^(?:s\/\.?|\$|usd)?(\d{1,6}(?:[.,]\d{1,2})?)$/i;
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

/** A serial-shaped code: 8+ letters and digits (hyphens aside), at least two of each — not «Thunderbolt-4». */
function looksLikeCode(token: string): boolean {
  const bare = token.replace(/-/g, "");
  return /^[a-z0-9]{8,}$/i.test(bare) && (bare.match(/\d/g) ?? []).length >= 2
    && (bare.match(/[a-z]/gi) ?? []).length >= 2 && !UNIT.test(token);
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
      if (!/^[a-z0-9][a-z0-9/-]{3,39}$/i.test(value) || UNIT.test(value)) continue;
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
  const all = joinUnits(splitFamily(line).split(" ").filter(Boolean));
  // Identifiers are read from the whole line elsewhere (IMEI, serial); here they are never a name or an amount.
  const ids = identifierTokens(all);
  const tokens = all.filter((_token, index) => !ids.has(index));
  const attrs: Attributes = {
    capacityGb: null, ramGb: null, sim: "", region: "", watts: null, length: "", generation: "", colour: "",
    amounts: [], battery: null, nameTokens: [],
  };
  const left: string[] = [];
  // A field line («128 GB», «Versión: Chip») has no family in front.
  const familyEnd = field ? 0 : familyLength(tokens, kind);
  tokens.forEach((token, index) => {
    const lower = token.toLocaleLowerCase("es");
    if (index < familyEnd) { left.push(token); return; }
    let m: RegExpMatchArray | null;
    if ((m = token.match(RAM_STORAGE))) { attrs.ramGb = Number(m[1]); attrs.capacityGb = Number(m[2]); return; }
    if ((m = token.match(CAPACITY)) && kind === "device") { attrs.capacityGb = Number(m[1]); return; }
    if ((m = token.match(TERA)) && kind === "device") { attrs.capacityGb = Number(m[1]) * 1024; return; }
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
      const value = Number(m[1].replace(",", "."));
      // A storage size after the model, before any amount, is the capacity («IPH 15 128 2300»).
      if (kind === "device" && attrs.capacityGb === null && STORAGE.has(value) && !/[.,]/.test(m[1])) {
        attrs.capacityGb = value;
        return;
      }
      // «Funda 15 pro»: a number that names a compatible model is part of the name.
      if (kind === "accessory" && MODEL_WORDS.has((tokens[index + 1] ?? "").toLowerCase())) {
        left.push(token); return;
      }
      attrs.amounts.push(m[1]);
      return;
    }
    left.push(token);
  });
  const [colour, used] = takeColour(left.slice(familyEnd));
  attrs.colour = colour;
  attrs.nameTokens = left.filter((_token, index) => !(index >= familyEnd && used.has(index - familyEnd)));
  return attrs;
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
