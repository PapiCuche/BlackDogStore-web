/**
 * CATALOG-COMMERCIAL-01 · B1-1 — the grammar of a supplier's price list («una línea = una oferta»).
 *
 *     THE COMPANY'S VOCABULARY DECIDES WHAT A WORD IS. THIS FILE KNOWS NO BRAND, MODEL, COLOUR, SIM OR REGION.
 *
 * A line of a WhatsApp list is split into the fields the server's resolver reads (`POST /admin/catalog/resolve/`,
 * `configuration_resolver`): `model_text`, `capacity_text`, `sim_text`, `region_text` and `colour_text`. Which words
 * are which comes ONLY from `GET /admin/catalog/vocabulary/` (model names, model aliases, model words, option labels
 * and option aliases by role). The rest is a CLOSED technical grammar of the platform (AGENTS.md §2): amounts, the
 * marks of a currency, a stated quantity («x3», «3 und»), separators and the few words that label an amount.
 *
 * What it never does (owner's D-B1-1 to D-B1-4, 2026-11-14):
 *   * classify an amount: every amount is returned UNCLASSIFIED, with the problems a person must look at
 *     (an ambiguous separator, a range, two amounts, «x3» next to it) — the person decides cost / reference / ignore;
 *   * confirm a quantity: «x3» is what the text says, nothing more;
 *   * split or match colours: the colour words go RAW in `colour_text` and R-COL runs on the server;
 *   * glue an unknown word to the model: it stays a doubt («palabras sin reconocer») and is never sent;
 *   * send anything personal: phones, IMEIs, serials, e-mails, links, long numbers and a contact word with whatever
 *     follows it are hidden BEFORE the line is read. They are not tokens, so they can reach no field of /resolve.
 *
 * Pure functions: no network, no storage, no clock. The pasted text lives only in the page's memory.
 */
import type { AttributeRole, CatalogVocabulary, ResolveRowInput } from "./internal-api";
import { cleanLine } from "./inventory-text-stages";
import { luhnSpans } from "./inventory-text-preview";

export const MAX_LIST_CHARS = 60000;
/** What one resolve request takes (`catalog_alias_views.MAX_ROWS`). */
export const MAX_LIST_LINES = 300;

// ── Keys, exactly as the server writes them (`configuration_resolver.fold` / `tokens` / `phrase_key`) ─────────

/** Lower case, accents folded (NFKD without combining marks), whitespace collapsed. */
export function fold(text: string): string {
  return String(text ?? "").normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase().split(/\s+/).filter(Boolean).join(" ");
}

/** A run of letters and digits, or ONE other visible character (`[^\W_]+|[^\w\s]`). */
const KEY_TOKEN = /[\p{L}\p{N}]+|[^\p{L}\p{N}\s_]/gu;

/** The key a label, an alias and a supplier's text share: folded tokens joined by one space. */
export function phraseKey(text: string): string {
  return (String(text ?? "").normalize("NFC").match(KEY_TOKEN) ?? []).map(fold).filter(Boolean).join(" ");
}

/** A capacity key without spaces: «256 gb» = «256gb» = «256-gb» (`configuration_resolver.compact`). */
function compact(key: string): string {
  const joined = key.replace(/ /g, "");
  const hyphen = /^(\d+(?:[.,]\d+)?)-(\p{L}{1,3})$/u.exec(joined);
  return hyphen ? hyphen[1] + hyphen[2] : joined;
}

const isPunct = (key: string) => !/[\p{L}\p{N}]/u.test(key);

// ── The company's lexicon ───────────────────────────────────────────────────────────────────────────────────────

type OptionHit = { role: AttributeRole; id: number };

export type SupplierLexicon = {
  /** phrase key of a model name or of a whole-name alias → model id. */
  models: Map<string, number>;
  /** a word the company rewrites («iph» → «iphone»). */
  modelWords: Map<string, string>;
  /** every word of the company's model names, model aliases and model words. */
  modelVocabulary: Set<string>;
  /** phrase key of an option label or alias → the options it names (any role). */
  options: Map<string, OptionHit[]>;
  capacityCompact: Map<string, OptionHit[]>;
  /** «128» of «128 GB»: a bare number in the capacity position. */
  capacityNumbers: Set<string>;
  /** every word of the colour labels and colour aliases (R-COL reads the phrases on the server). */
  colourWords: Set<string>;
  separatorWords: Set<string>;
  /** every word of any option (a contact word that is the company's vocabulary is not a contact). */
  optionWords: Set<string>;
  longestModel: number;
  longestOption: number;
};

const SEPARATOR_PUNCT = new Set([",", ";", "/", "+", "&", "|"]);
/**
 * Joiners inside one colour («azul-marino», «(PRODUCT)RED»): every other punctuation mark, as the server's
 * `_is_joiner` — part of the colour, never a split.
 */
const isJoiner = (text: string) => !/[\p{L}\p{N}]/u.test(text) && !SEPARATOR_PUNCT.has(text);
const OPENING = new Set(["(", "[", "{", "«", "\""]);
const CLOSING = new Set([")", "]", "}", "»", "\""]);

export function supplierLexicon(vocabulary: CatalogVocabulary): SupplierLexicon {
  const lexicon: SupplierLexicon = {
    models: new Map(), modelWords: new Map(), modelVocabulary: new Set(), options: new Map(), capacityCompact: new Map(),
    capacityNumbers: new Set(), colourWords: new Set(), separatorWords: new Set(vocabulary.separator_words.map(fold)),
    optionWords: new Set(), longestModel: 1, longestOption: 1,
  };
  const words = (key: string) => key.split(" ").filter((word) => word && !isPunct(word));
  const addModel = (key: string, id: number) => {
    if (!key) return;
    if (!lexicon.models.has(key)) lexicon.models.set(key, id);
    for (const word of words(key)) lexicon.modelVocabulary.add(word);
    lexicon.longestModel = Math.max(lexicon.longestModel, key.split(" ").length);
  };
  for (const model of vocabulary.models) {
    addModel(model.key || phraseKey(model.name), model.id);
    for (const alias of model.aliases) addModel(phraseKey(alias.alias), model.id);
  }
  for (const word of vocabulary.model_words) {
    const key = phraseKey(word.alias);
    const replacement = phraseKey(word.replacement);
    if (!key || !replacement) continue;
    lexicon.modelWords.set(key, replacement);
    lexicon.modelVocabulary.add(key);
    for (const part of words(replacement)) lexicon.modelVocabulary.add(part);
  }
  for (const [role, options] of Object.entries(vocabulary.options) as [AttributeRole, CatalogVocabulary["options"][AttributeRole]][]) {
    for (const option of options) {
      const keys = [option.key || phraseKey(option.label), ...option.aliases.map((alias) => phraseKey(alias.alias))].filter(Boolean);
      for (const key of keys) {
        const hit = { role, id: option.id };
        lexicon.options.set(key, [...(lexicon.options.get(key) ?? []), hit]);
        lexicon.longestOption = Math.max(lexicon.longestOption, key.split(" ").length);
        for (const word of words(key)) {
          lexicon.optionWords.add(word);
          // A grammar word inside a label («de» of «Azul de medianoche») is never a colour word by itself (review N2).
          if (role === "color" && !lexicon.separatorWords.has(word) && !GRAMMAR.has(word)) lexicon.colourWords.add(word);
        }
        if (role === "capacity") {
          const tight = compact(key);
          lexicon.capacityCompact.set(tight, [...(lexicon.capacityCompact.get(tight) ?? []), hit]);
          const number = /^(\d+) \p{L}+$/u.exec(key);
          if (number) lexicon.capacityNumbers.add(number[1]);
        }
      }
    }
  }
  return lexicon;
}

// ── Amounts (the same rule as the server's B1-0 `supply_services._amount_text`, for DISPLAY only) ───────────────

export type AmountTextAnswer =
  | { ok: true; value: string }
  | { ok: false; code: "ambiguous_amount" | "several_amounts" | "invalid"; detail: string };

const SPACE_GROUPED = /^\d{1,3}(?:[    ]\d{3})+(?:[.,]\d{1,2})?$/;
const SEVERAL = /\d[\d.,]*\s*(?:[-/;|]|\s)\s*\d/;
const GROUPED = /^\d{1,3}(?:X\d{3})+$/;

/**
 * An amount typed as text, read as the server reads it — or why not. «5500», «5500.00», «5.500,00» are amounts;
 * «5,500» / «5.500» are AMBIGUOUS (5500 or 5.50); «1800,2000» or «5400 5500» are not one amount. The server stays
 * the authority: this only lets the screen say it before sending.
 */
export function parseAmountText(text: string): AmountTextAnswer {
  const said = String(text ?? "").trim();
  const invalid = (detail = `«${said}» no es un importe válido: escribe sólo el número, sin moneda ni signos.`) =>
    ({ ok: false as const, code: "invalid" as const, detail });
  if (!said || said.length > 40) return invalid();
  if (SPACE_GROUPED.test(said)) {
    return { ok: false, code: "invalid", detail: `«${said}» no es claro: puede ser un importe con espacios de miles o varios importes.` };
  }
  if (SEVERAL.test(said)) return { ok: false, code: "several_amounts", detail: `«${said}» tiene más de un importe.` };
  if (!/^[0-9.,]+$/.test(said) || !/^\d/.test(said) || !/\d$/.test(said)) return invalid();
  const dots = said.split(".").length - 1;
  const commas = said.split(",").length - 1;
  if (dots && commas) {
    const decimal = said.lastIndexOf(",") > said.lastIndexOf(".") ? "," : ".";
    const thousands = decimal === "," ? "." : ",";
    const at = said.lastIndexOf(decimal);
    const whole = said.slice(0, at);
    const decimals = said.slice(at + 1);
    if (whole.includes(decimal) || decimals.length < 1 || decimals.length > 2 || !GROUPED.test(whole.split(thousands).join("X"))) return invalid();
    return { ok: true, value: `${whole.split(thousands).join("")}.${decimals}` };
  }
  const separator = dots ? "." : commas ? "," : "";
  if (!separator) return { ok: true, value: said };
  if (said.split(separator).length > 2) {
    if (!GROUPED.test(said.split(separator).join("X"))) return invalid();
    return { ok: true, value: said.split(separator).join("") };
  }
  const [head, tail] = said.split(separator);
  if (tail.length === 3 && head.length <= 3 && !head.startsWith("0")) {
    return {
      ok: false, code: "ambiguous_amount",
      detail: `«${said}» es ambiguo: puede ser ${head}${tail} o ${head}.${tail}. Escríbelo sin separador de miles (${head}${tail}) o con dos decimales.`,
    };
  }
  if (tail.length > 2) return invalid(`«${said}» no es un importe válido: lleva a lo sumo dos decimales; si son dos importes, escribe uno.`);
  return { ok: true, value: `${head}.${tail}` };
}

// ── What a line says ────────────────────────────────────────────────────────────────────────────────────────────

/** What was hidden from a line before reading it. None of it is a token: it reaches no field. */
export type HiddenKind = "contact" | "phone" | "imei" | "serial" | "number";

export type AmountProblem =
  /** «5,500» / «5.500»: thousands or decimals? */
  | "ambiguous_separator"
  /** «5400-5500», «5400/5500». */
  | "range"
  /** «1800,2000»: two amounts written together. */
  | "several"
  /** two separate amounts in the same line: which one is what? */
  | "two_amounts"
  /** an amount next to a stated quantity above one: each or the total? */
  | "per_unit_or_total"
  /** not a number the server would read. */
  | "invalid";

export type SupplierAmount = {
  /** `L<line>-A<n>:<raw>[@<currency>]`: names the amount by what it says, not only by where it is. */
  key: string;
  /** As written, without its currency mark. */
  raw: string;
  /** The plain decimal when it is unambiguous; null otherwise (never guessed). */
  value: string | null;
  /** ISO code of a currency WRITTEN next to it; null = the list's currency (chosen by the person). */
  currency: string | null;
  /** Written with a bare «$»: a dollar of SOME currency, never assumed to be USD (it takes the list's currency). */
  dollarSign: boolean;
  problem: AmountProblem | null;
  /** A HINT from a label before it («costo», «precio de venta», «precio»). Never a classification. */
  label: "cost" | "sale" | "price" | null;
};

export type SupplierLine = {
  key: string;
  /** Line number in the pasted text (1-based), to find it. */
  number: number;
  /** The line as it can be shown: personal data and identifiers replaced by «•••». */
  display: string;
  /** The same, with every amount (and its currency mark) also replaced: for whoever may not see costs. */
  displayWithoutAmounts: string;
  hidden: HiddenKind[];
  /** The model words, as written; `known`: a model or alias of the company IS that text (the server confirms). */
  model: { text: string; known: boolean } | null;
  capacityText: string;
  simText: string;
  regionText: string;
  colourText: string;
  /**
   * The colour could not be sent as written: an unknown word touches it («glaciar blanco» with no «Glaciar…» label),
   * colours stand apart in the line, or the text is longer than the server reads. A doubt that blocks the line;
   * a colour is NEVER trimmed, split or joined with a separator the supplier did not write.
   */
  colourDoubt: {
    text: string; reason: "unknown_words" | "separated" | "too_long";
    /** The words of the colour zone (unknown_words only), for a person to say which are NOT the colour. */
    words: ColourWord[];
  } | null;
  /** A person chose which words are the colour (`chooseColour`): what is sent and what was left out. */
  colourChosen: { text: string; left: string[] } | null;
  /** The condition word as written («seminuevo», «nueva»…). */
  lineText: string;
  /** Its canonical form in the server's closed `LINE_WORDS`, sent as `line` («nueva» → «nuevo»). */
  lineWord: string;
  /** Options of a role the resolver does not read from a text (role «other»): shown, not sent. */
  otherTexts: string[];
  amounts: SupplierAmount[];
  /** «x3», «3 und»: what the text says. NEVER a confirmed quantity. */
  statedQuantity: { text: string; value: number } | null;
  /** Words that are neither the company's vocabulary nor grammar: a doubt, never sent. */
  unknownWords: string[];
  /** Of those, the ones that touch no model, capacity or colour: only these may be ignored by a person. */
  ignorableWords: string[];
  /** What makes the line contradict itself («dos capacidades»): a doubt. */
  conflicts: string[];
};

export type SkippedLine = { number: number; display: string; displayWithoutAmounts: string };

/** One word of a colour zone and where it is in the zone's text. */
export type ColourWord = { text: string; colour: boolean; start: number; end: number };

export type SupplierListReading = {
  lines: SupplierLine[];
  /** Lines with nothing to offer (a greeting, a header): never sent. */
  skipped: SkippedLine[];
  /** More offer lines than one request takes: the rest is not read. */
  truncated: boolean;
  /** A currency the text names away from any amount («precios en dólares»): a hint for the list's currency. */
  mentionedCurrencies: string[];
};

// ── 1. What is hidden before reading ────────────────────────────────────────────────────────────────────────────

type Span = { start: number; end: number; kind: HiddenKind | "date" };

/** A contact word and EVERYTHING after it on the line («llamar al 987…», «cel …», «atiende Carlos»). */
const CONTACT_WORD = String.raw`ll[aá]m\p{L}*|cel(?:ular)?|tel(?:[eé]fono|f)?|wh?ats?app|wsp|contact\p{L}*|escr[ií]b\p{L}*|atiende|asesora?|vendedora?|sra?|srta|correo|e-?mail|mail|dni|ruc|carn[eé]t?`;
const CONTACT = new RegExp(String.raw`(?<![\p{L}\d])(${CONTACT_WORD})\.?(?![\p{L}])`, "iu");
const EMAIL = /[\p{L}\d._%+-]*@[\p{L}\d._-]*/gu;
const URL_TEXT = /(?:https?:\/\/|www\.)\S+/giu;
const IMEI_LABELLED = /(?<![\p{L}])imei\s*[12]?\s*[:#=]?\s*\d[\d .\-]{12,22}\d/giu;
const SERIAL_LABELLED = /(?<![\p{L}\d])(?:n\.?\s?s\.?|n\/s|s\/n|sn|serie|serial)\s*[:#=]?\s*(?=[a-z0-9-]*\d)[a-z0-9][a-z0-9-]{3,39}(?![\p{L}\d])/giu;
const PHONE_PLUS = /\+\s?\d{1,3}[\s-]?(?:\d[\s-]?){6,12}\d/gu;
const MOBILE = /(?<![\d.,])9\d{2}[\s-]?\d{3}[\s-]?\d{3}(?![\d.,])/gu;
const DASHED_PHONE = /(?<![\d.,])\d{3}-\d{3}-\d{3,4}(?![\d.,])/gu;
const LONG_NUMBER = /(?<![\d.,])\d{7,}(?![\d.,])/gu;
/** A code that mixes 8+ letters and digits: a serial written without its label. */
const CODE = /(?<![\p{L}\d])(?=[\p{L}\d-]*\d)(?=(?:[\p{L}\d-]*\p{L}){2})[\p{L}\d][\p{L}\d-]{7,}(?![\p{L}\d])/gu;
const DATE = /(?<![\d/.\-])(\d{1,2})[/-](\d{1,2})(?:[/-]\d{2,4})?(?![\d/.\-])/gu;

/** «2300soles», «4370dolares»: an amount glued to a currency word (never a serial). */
function gluedCurrency(text: string): boolean {
  const glued = /^\d+(?:[.,]\d+)*(\p{L}+)$/u.exec(text);
  return Boolean(glued && CURRENCY_WORDS[fold(glued[1])]);
}

function spansOf(line: string, lexicon: SupplierLexicon): Span[] {
  const spans: Span[] = [];
  const all = (pattern: RegExp, kind: Span["kind"], keep?: (m: RegExpMatchArray) => boolean) => {
    for (const m of line.matchAll(pattern)) {
      if (keep && !keep(m)) continue;
      spans.push({ start: m.index!, end: m.index! + m[0].length, kind });
    }
  };
  all(IMEI_LABELLED, "imei");
  all(SERIAL_LABELLED, "serial");
  for (const span of luhnSpans(line)) spans.push({ start: span.start, end: span.end, kind: "imei" });
  all(EMAIL, "contact", (m) => m[0].includes("@"));
  all(URL_TEXT, "contact");
  all(PHONE_PLUS, "phone");
  all(MOBILE, "phone");
  all(DASHED_PHONE, "phone");
  all(LONG_NUMBER, "number");
  // A code of 8+ letters and digits — unless the company's vocabulary knows it (a model word, an option).
  all(CODE, "serial", (m) => !/^\d{1,4}(?:gb|tb|mb|w|mm|mah)$/i.test(m[0]) && !/^\p{L}+\d{1,3}\p{L}*$/u.test(m[0])
    && !gluedCurrency(m[0])
    && !lexicon.optionWords.has(fold(m[0]))
    && !lexicon.modelVocabulary.has(fold(m[0])));
  all(DATE, "date", (m) => Number(m[1]) >= 1 && Number(m[1]) <= 31 && Number(m[2]) >= 1 && Number(m[2]) <= 12);
  // The contact word (unless it is the company's own vocabulary) and the rest of the line.
  for (let from = 0; from < line.length;) {
    const m = CONTACT.exec(line.slice(from));
    if (!m) break;
    const start = from + m.index;
    const word = fold(m[1]);
    if (lexicon.optionWords.has(word) || lexicon.modelVocabulary.has(word)) {
      from = start + m[0].length;
      continue;
    }
    spans.push({ start, end: line.length, kind: "contact" });
    break;
  }
  return spans;
}

/** Overlapping spans as one (the earliest start, the farthest end). */
function merge(spans: Span[]): Span[] {
  const sorted = [...spans].sort((a, b) => a.start - b.start || b.end - a.end);
  const out: Span[] = [];
  for (const span of sorted) {
    const last = out[out.length - 1];
    if (last && span.start < last.end) {
      if (span.end > last.end) last.end = span.end;
      if (last.kind === "date" && span.kind !== "date") last.kind = span.kind;
    } else out.push({ ...span });
  }
  return out;
}

/** The line with each span replaced by «•••» (a date stays: it is not personal). */
function shown(line: string, spans: Span[]): string {
  let out = "";
  let at = 0;
  for (const span of spans) {
    out += line.slice(at, span.start) + (span.kind === "date" ? line.slice(span.start, span.end) : "•••");
    at = span.end;
  }
  return (out + line.slice(at)).replace(/\s+/g, " ").trim();
}

// ── 2. Tokens ───────────────────────────────────────────────────────────────────────────────────────────────────

type Kind = "word" | "num" | "punct" | "currency" | "gap";
type Token = { text: string; key: string; start: number; end: number; kind: Kind; currency?: string | null; dollar?: boolean };

/** The marks of a currency (closed technical grammar). Never converted: only said. */
const CURRENCY_SIGN = /^(?:S\/\.?|US\$|\$|€)/i;
/** «$» alone is a dollar of SOME currency: it names none (null). */
const SIGN_CODE: Record<string, string | null> = { "s/": "PEN", "s/.": "PEN", "us$": "USD", $: null, "€": "EUR" };
const CURRENCY_WORDS: Record<string, string> = {
  pen: "PEN", sol: "PEN", soles: "PEN", usd: "USD", dolar: "USD", dolares: "USD", eur: "EUR", euro: "EUR", euros: "EUR",
};
const SCAN = /(\d+(?:[.,]\d+)*)(?![\p{L}\d])|([\p{L}\p{M}\p{N}]+)|(\S)/uy;

/** A word the company's vocabulary knows is never read as grammar (a quantity, a currency, a condition). */
function known(key: string, lexicon: SupplierLexicon): boolean {
  return lexicon.optionWords.has(key) || lexicon.modelVocabulary.has(key);
}

function tokenize(line: string, from: number, to: number, out: Token[], lexicon: SupplierLexicon) {
  let at = from;
  while (at < to) {
    if (/\s/.test(line[at])) { at += 1; continue; }
    const sign = CURRENCY_SIGN.exec(line.slice(at, to));
    if (sign && (sign[0] !== "S/" || !/^\s*n/i.test(line.slice(at + 2, to)))) {
      out.push({ text: sign[0], key: sign[0].toLowerCase(), start: at, end: at + sign[0].length, kind: "currency",
        currency: SIGN_CODE[sign[0].toLowerCase()], dollar: sign[0] === "$" });
      at += sign[0].length;
      continue;
    }
    SCAN.lastIndex = at;
    const m = SCAN.exec(line.slice(0, to));
    if (!m) { at += 1; continue; }
    const text = m[0];
    const end = at + text.length;
    if (m[1] !== undefined) {
      out.push({ text, key: text.replace(/([.,])/g, " $1 "), start: at, end, kind: "num" });
    } else if (m[2] !== undefined) {
      const key = fold(text);
      // «4370usd»: an amount glued to its currency is the amount and the currency, never a word.
      const glued = /^(\d+(?:[.,]\d+)*)(\p{L}+)$/u.exec(text);
      if (CURRENCY_WORDS[key] && !known(key, lexicon)) {
        out.push({ text, key, start: at, end, kind: "currency", currency: CURRENCY_WORDS[key] });
      } else if (glued && CURRENCY_WORDS[fold(glued[2])] && !known(key, lexicon)) {
        const split = at + glued[1].length;
        out.push({ text: glued[1], key: glued[1].replace(/([.,])/g, " $1 "), start: at, end: split, kind: "num" });
        out.push({ text: glued[2], key: fold(glued[2]), start: split, end, kind: "currency", currency: CURRENCY_WORDS[fold(glued[2])] });
      } else {
        out.push({ text, key, start: at, end, kind: /^\d+$/.test(text) ? "num" : "word" });
      }
    } else {
      out.push({ text, key: fold(text) || text, start: at, end, kind: "punct" });
    }
    at = end;
  }
}

// ── 3. Reading the tokens ───────────────────────────────────────────────────────────────────────────────────────

/** Grammar of a list: words that label an amount, state a quantity or join words. Never unknown, never sent. */
const COST_WORDS = new Set(["costo", "costos", "cto", "compra"]);
const SALE_WORDS = new Set(["venta", "publico", "pvp", "final", "cliente"]);
const PRICE_WORDS = new Set(["precio", "precios", "valor", "price"]);
const QUANTITY_UNITS = new Set(["und", "unds", "unid", "unids", "unidad", "unidades", "ud", "uds", "pza", "pzas", "pieza", "piezas", "pcs"]);
const QUANTITY_LABELS = new Set(["cant", "cantidad", "qty"]);
const GRAMMAR = new Set([
  ...COST_WORDS, ...SALE_WORDS, ...PRICE_WORDS, ...QUANTITY_UNITS, ...QUANTITY_LABELS,
  "de", "del", "la", "el", "los", "las", "en", "con", "sin", "a", "al", "para", "por", "o", "u", "x", "proveedor", "c/u",
]);

/** The server's closed condition words (`device_catalog.LINE_WORDS`): what `line` accepts. */
const LINE_WORDS: Record<string, "new" | "used"> = {
  nuevo: "new", nuevos: "new", new: "new", seminuevo: "used", seminuevos: "used", usado: "used", usados: "used", used: "used",
  // Their feminine and plural forms (closed grammar, review N3), sent as the canonical word below.
  nueva: "new", nuevas: "new", seminueva: "used", seminuevas: "used", usada: "used", usadas: "used",
};
const CANONICAL_LINE: Record<string, string> = {
  nuevo: "nuevo", nuevos: "nuevo", nueva: "nuevo", nuevas: "nuevo", new: "new",
  seminuevo: "seminuevo", seminuevos: "seminuevo", seminueva: "seminuevo", seminuevas: "seminuevo",
  usado: "usado", usados: "usado", usada: "usado", usadas: "usado", used: "used",
};
/** Little words that may sit INSIDE a colour («azul de medianoche») but never start, end or make one. */
const FUNCTION_WORDS = new Set(["de", "del", "la", "el", "los", "las", "en", "con", "sin", "a", "al", "para", "por", "o", "u"]);

/** The lengths the server reads per field (`catalog_alias_views.ROW_FIELDS`). */
export const FIELD_LIMITS = { model_text: 160, capacity_text: 60, colour_text: 120, sim_text: 60, region_text: 60 };

const ROLE_WORDS: Record<AttributeRole, string> = {
  capacity: "capacidades", color: "colores", sim: "SIM", region: "regiones", other: "opciones",
};

function unglue(words: string[]): string[] {
  return words.flatMap((word) => {
    const glued = /^(\p{L}+)(\d+)$/u.exec(word);
    return glued ? [glued[1], glued[2]] : [word];
  });
}

/** The model a run of tokens IS for the company (a name, a whole-name alias, or its words rewritten). */
function modelOf(keys: string[], lexicon: SupplierLexicon): number | null {
  const words = keys.join(" ").split(" ");
  for (const candidate of [words, unglue(words)]) {
    const direct = lexicon.models.get(candidate.join(" "));
    if (direct !== undefined) return direct;
    const rewritten = candidate.map((word) => lexicon.modelWords.get(word) ?? word);
    const found = lexicon.models.get(rewritten.join(" "));
    if (found !== undefined) return found;
  }
  return null;
}

const usable = (token: Token | undefined) => Boolean(token) && (token!.kind === "word" || token!.kind === "num" || token!.kind === "punct");

function readLine(raw: string, number: number, lexicon: SupplierLexicon, currencies: Set<string>):
  { line: SupplierLine | null; skipped: SkippedLine | null } {
  const line = cleanLine(raw);
  if (!line) return { line: null, skipped: null };
  const allSpans = spansOf(line, lexicon);
  const spans = merge(allSpans);
  const hidden = [...new Set(allSpans.map((span) => span.kind).filter((kind): kind is HiddenKind => kind !== "date"))];
  const tokens: Token[] = [];
  let at = 0;
  for (const span of spans) {
    tokenize(line, at, span.start, tokens, lexicon);
    tokens.push({ text: "", key: "", start: span.start, end: span.end, kind: "gap" });
    at = span.end;
  }
  tokenize(line, at, line.length, tokens, lexicon);
  const display = shown(line, spans);

  const used = tokens.map((token) => token.kind === "gap");
  const take = (from: number, to: number) => { for (let i = from; i <= to; i++) used[i] = true; };
  const textOf = (from: number, to: number) => line.slice(tokens[from].start, tokens[to].end);
  const conflicts: string[] = [];

  // ORDER: the company's vocabulary first (model, then options), the closed grammar after (review M1). A token
  // that is a model or an option phrase of the company is never a quantity, a currency or a condition.
  // Each token remembers what it became, to tell which unknown words touch what (review H1, rule 4).
  const part: (null | "model" | "capacity" | "option" | "colour" | "quantity" | "amount" | "condition")[] = tokens.map(() => null);
  const mark = (from: number, to: number, what: NonNullable<(typeof part)[number]>) => {
    take(from, to);
    for (let i = from; i <= to; i++) part[i] = what;
  };

  // The model: the longest run of tokens that IS a model of the company (earliest on a tie).
  let model: SupplierLine["model"] = null;
  let best: [number, number] | null = null;
  const reach = lexicon.longestModel * 2 + 2;
  for (let i = 0; i < tokens.length; i++) {
    if (used[i] || (tokens[i].kind !== "word" && tokens[i].kind !== "num")) continue;
    for (let j = i; j < tokens.length && j - i < reach; j++) {
      if (used[j] || !usable(tokens[j])) break;
      if (tokens[j].kind === "punct") continue;
      if (modelOf(tokens.slice(i, j + 1).map((t) => t.key), lexicon) !== null && (!best || j - i > best[1] - best[0])) best = [i, j];
    }
  }
  if (best) {
    model = { text: textOf(best[0], best[1]), known: true };
    mark(best[0], best[1], "model");
  } else {
    // No model of the company IS it: only the company's MODEL WORDS (and a short model number) travel, so the
    // server can say «modelo desconocido». Any other word stays out of `model_text`.
    const start = tokens.findIndex((t, i) => !used[i] && t.kind === "word" && lexicon.modelVocabulary.has(t.key));
    if (start >= 0) {
      let end = start;
      while (end + 1 < tokens.length && !used[end + 1]) {
        const next = tokens[end + 1];
        const word = next.kind === "word" && lexicon.modelVocabulary.has(next.key);
        const shortNumber = next.kind === "num" && /^\d{1,3}$/.test(next.text) && !QUANTITY_UNITS.has(tokens[end + 2]?.key ?? "");
        if (!word && !shortNumber) break;
        end += 1;
      }
      model = { text: textOf(start, end), known: false };
      mark(start, end, "model");
    }
  }

  // Options of one value per role (capacity, SIM, region, other): the longest phrase of the company's labels and
  // aliases. A phrase that names options of two roles is a doubt, never chosen.
  const single: Partial<Record<AttributeRole, { text: string; key: string } | null>> = {};
  const otherTexts: string[] = [];
  const record = (role: AttributeRole, from: number, to: number, key: string) => {
    mark(from, to, role === "capacity" ? "capacity" : "option");
    const text = textOf(from, to);
    if (role === "other") { otherTexts.push(text); return; }
    const before = single[role];
    if (before === null) return;
    if (before && before.key !== key) {
      conflicts.push(`Dos ${ROLE_WORDS[role]} en la línea: «${before.text}» y «${text}». No se envía ninguna.`);
      single[role] = null;
      return;
    }
    single[role] = before ?? { text, key };
  };
  for (let i = 0; i < tokens.length; i++) {
    if (used[i] || tokens[i].kind === "punct" || !usable(tokens[i])) continue;
    for (let size = Math.min(lexicon.longestOption, tokens.length - i); size >= 1; size--) {
      const run = tokens.slice(i, i + size);
      if (run.some((t, k) => used[i + k] || !usable(t)) || run[run.length - 1].kind === "punct") continue;
      const key = run.map((t) => t.key).join(" ");
      const hits = [...(lexicon.options.get(key) ?? []), ...(size <= 2 ? lexicon.capacityCompact.get(compact(key)) ?? [] : [])];
      const roles = [...new Set(hits.map((hit) => hit.role))];
      if (!roles.length || (roles.length === 1 && roles[0] === "color")) continue;
      if (roles.length > 1) {
        mark(i, i + size - 1, "option");
        conflicts.push(`«${textOf(i, i + size - 1)}» es una opción de varios atributos de la empresa: no se elige ninguno.`);
      } else {
        record(roles[0], i, i + size - 1, key);
      }
      i += size - 1;
      break;
    }
  }

  // The condition of the device («seminuevo», «nuevo»…): the server's closed `LINE_WORDS`, sent as `line`.
  let lineText = "";
  tokens.forEach((t, i) => {
    if (used[i] || t.kind !== "word" || !LINE_WORDS[t.key] || lexicon.colourWords.has(t.key)) return;
    mark(i, i, "condition");
    if (lineText && LINE_WORDS[fold(lineText)] !== LINE_WORDS[t.key]) {
      conflicts.push(`La línea dice «${lineText}» y «${t.text}»: no se elige ninguna condición.`);
      lineText = "\u0000";
    } else if (!lineText) lineText = t.text;
  });
  if (lineText === "\u0000") lineText = "";

  // Quantities: «x3», «x 3», «3 und», «cant: 3». Shown as said; never an amount, never confirmed.
  let statedQuantity: SupplierLine["statedQuantity"] = null;
  const stated = (from: number, to: number, value: number) => {
    mark(from, to, "quantity");
    if (statedQuantity && statedQuantity.value !== value) conflicts.push(`Dos cantidades dichas: «${statedQuantity.text}» y «${textOf(from, to)}».`);
    statedQuantity ??= { text: textOf(from, to), value };
  };
  tokens.forEach((token, i) => {
    // A WORD of the company's vocabulary is never «x5»; a free number before «und» is a quantity even if some
    // model name holds that number («iPhone 15 … 15 und»): the model already took its own.
    if (used[i] || (token.kind === "word" && known(token.key, lexicon))) return;
    const next = tokens[i + 1];
    const nextFree = next && !used[i + 1];
    const glued = /^[x×](\d{1,3})$/i.exec(token.text);
    if (token.kind === "word" && glued) stated(i, i, Number(glued[1]));
    else if (token.kind === "word" && /^[x×]$/i.test(token.text) && nextFree && next.kind === "num" && /^\d{1,3}$/.test(next.text)) stated(i, i + 1, Number(next.text));
    else if (token.kind === "num" && /^\d{1,3}$/.test(token.text) && nextFree && next.kind === "word" && QUANTITY_UNITS.has(next.key.replace(/\.$/, ""))) stated(i, i + 1, Number(token.text));
    else if (token.kind === "word" && QUANTITY_LABELS.has(token.key)) {
      const skip = tokens[i + 1]?.kind === "punct" && tokens[i + 1].text === ":" ? 2 : 1;
      const value = tokens[i + skip];
      if (value?.kind === "num" && !used[i + skip] && /^\d{1,3}$/.test(value.text)) stated(i, i + skip, Number(value.text));
    }
  });
  // A bare number in the capacity position: the first number of the line that is a capacity of the company.
  if (single.capacity === undefined) {
    const first = tokens.findIndex((t, i) => !used[i] && t.kind === "num");
    if (first >= 0 && lexicon.capacityNumbers.has(tokens[first].text)) record("capacity", first, first, tokens[first].text);
  }

  // Colour zones: contiguous colour words, separators and unknown words. R-COL decides on the server — but only
  // over text the supplier wrote as a colour: a zone with an unknown word, or two zones apart, is a doubt and no
  // colour is sent for the line (review H1). Never trimmed, never joined with a separator nobody wrote.
  const isUnknown = (t: Token, i: number) => !used[i] && t.kind === "word" && !GRAMMAR.has(t.key) && !lexicon.separatorWords.has(t.key);
  const zones: { from: number; to: number; unknown: boolean }[] = [];
  for (let i = 0; i < tokens.length; i++) {
    const zoneToken = (t: Token, k: number) => !used[k] && ((t.kind === "word" && (lexicon.colourWords.has(t.key)
      || lexicon.separatorWords.has(t.key) || FUNCTION_WORDS.has(t.key))) || (isUnknown(t, k) && !/\d/.test(t.text))
      || t.kind === "punct");
    if (!zoneToken(tokens[i], i)) continue;
    let j = i;
    while (j + 1 < tokens.length && zoneToken(tokens[j + 1], j + 1)) j++;
    const colours = tokens.slice(i, j + 1).map((t, k) => !used[i + k] && t.kind === "word" && lexicon.colourWords.has(t.key));
    if (colours.some(Boolean)) {
      // Separators, little words and loose marks at the edges belong to no colour (a bracket that opens or closes it does).
      let from = i;
      let to = j;
      const edge = (k: number, start: boolean) => (tokens[k].kind === "punct" && !(start ? OPENING : CLOSING).has(tokens[k].text))
        || lexicon.separatorWords.has(tokens[k].key) || FUNCTION_WORDS.has(tokens[k].key);
      while (from < to && edge(from, true)) from++;
      while (to > from && edge(to, false)) to--;
      zones.push({ from, to, unknown: tokens.slice(from, to + 1).some((t, k) => isUnknown(t, from + k) && !lexicon.colourWords.has(t.key)) });
    }
    i = j;
  }
  let colourText = "";
  let colourDoubt: SupplierLine["colourDoubt"] = null;
  const stuck = new Set<number>();
  const withUnknown = zones.find((zone) => zone.unknown);
  if (withUnknown) {
    const base = tokens[withUnknown.from].start;
    const words: ColourWord[] = [];
    for (let k = withUnknown.from; k <= withUnknown.to; k++) {
      if (tokens[k].kind !== "word") continue;
      words.push({ text: tokens[k].text, colour: lexicon.colourWords.has(tokens[k].key),
        start: tokens[k].start - base, end: tokens[k].end - base });
    }
    colourDoubt = { text: textOf(withUnknown.from, withUnknown.to), reason: "unknown_words", words };
  } else if (zones.length > 1) {
    colourDoubt = { text: zones.map((zone) => textOf(zone.from, zone.to)).join(" … "), reason: "separated", words: [] };
  } else if (zones.length === 1) {
    const text = textOf(zones[0].from, zones[0].to);
    if (text.length > FIELD_LIMITS.colour_text) colourDoubt = { text: text.slice(0, 60) + "…", reason: "too_long", words: [] };
    else colourText = text;
  }
  for (const zone of zones) {
    for (let k = zone.from; k <= zone.to; k++) {
      if (isUnknown(tokens[k], k) && !lexicon.colourWords.has(tokens[k].key)) stuck.add(k);
      else { used[k] = true; part[k] = "colour"; }
    }
  }

  // Amounts: every number left, with a currency written next to it and the label before it (a hint).
  const amounts: SupplierAmount[] = [];
  const amountSpans: { start: number; end: number }[] = [];
  const seen = new Map<string, number>();
  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i];
    if (used[i] || t.kind !== "num") continue;
    let to = i;
    let problem: AmountProblem | null = null;
    const joiner = tokens[i + 1];
    if (joiner?.kind === "punct" && ["-", "/", "–"].includes(joiner.text) && tokens[i + 2]?.kind === "num" && !used[i + 2]) {
      to = i + 2;
      problem = "range";
    }
    const raw = textOf(i, to);
    let value: string | null = null;
    if (!problem) {
      const parsed = parseAmountText(raw);
      if (parsed.ok) value = parsed.value;
      else if (parsed.code === "ambiguous_amount") problem = "ambiguous_separator";
      else problem = /^\d+[.,]\d{4,}$/.test(raw) ? "several" : "invalid";
    }
    mark(i, to, "amount");
    let currency: string | null = null;
    let dollarSign = false;
    let first = i;
    let last = to;
    const before = tokens[i - 1];
    const after = tokens[to + 1];
    const sign = before?.kind === "currency" && !used[i - 1] ? before : after?.kind === "currency" && !used[to + 1] ? after : null;
    if (sign) {
      currency = sign.currency ?? null;
      dollarSign = Boolean(sign.dollar);
      if (sign === before) { used[i - 1] = true; first = i - 1; } else { used[to + 1] = true; last = to + 1; }
    }
    let label: SupplierAmount["label"] = null;
    for (let k = i - 1; k >= Math.max(0, i - 4); k--) {
      const word = tokens[k];
      if (word.kind === "gap" || word.kind === "num") break;
      if (word.kind !== "word" || used[k]) continue;
      if (COST_WORDS.has(word.key)) label = "cost";
      else if (SALE_WORDS.has(word.key) && label !== "cost") label = "sale";
      else if (PRICE_WORDS.has(word.key) && !label) label = "price";
    }
    const name = `${raw}${currency ? `@${currency}` : dollarSign ? "@$" : ""}`;
    const occurrence = (seen.get(name) ?? 0) + 1;
    seen.set(name, occurrence);
    amounts.push({ key: `L${number}-A${occurrence}:${name}`, raw, value, currency, dollarSign, problem, label });
    amountSpans.push({ start: tokens[first].start, end: tokens[last].end });
    i = to;
  }
  if (amounts.length > 1) for (const amount of amounts) amount.problem ??= "two_amounts";
  if (statedQuantity && (statedQuantity as { value: number }).value > 1) for (const amount of amounts) amount.problem ??= "per_unit_or_total";

  // What is left: a currency named away from any amount (a hint), grammar, or a word nobody knows. An unknown word
  // (or a run of them) that touches the model, a capacity or a colour cannot be ignored: it may change what they say.
  const unknownWords: string[] = [];
  const ignorableWords: string[] = [];
  const touching = new Set(["model", "capacity", "colour"]);
  const neighbour = (i: number, step: number) => {
    let k = i + step;
    while (tokens[k] && tokens[k].kind === "punct" && isJoiner(tokens[k].text)) k += step;
    return k;
  };
  const unknownAt = (i: number) => stuck.has(i) || isUnknown(tokens[i], i);
  tokens.forEach((t, i) => {
    if (used[i] && !stuck.has(i)) return;
    if (t.kind === "currency") { currencies.add(t.currency ?? "$"); return; }
    if (!unknownAt(i)) return;
    unknownWords.push(t.text);
    if (stuck.has(i)) return;
    // The run of unknown words this one belongs to, and what is on either side of it.
    let from = i;
    let to = i;
    while (tokens[neighbour(from, -1)] && unknownAt(neighbour(from, -1))) from = neighbour(from, -1);
    while (tokens[neighbour(to, 1)] && unknownAt(neighbour(to, 1))) to = neighbour(to, 1);
    const sides = [part[neighbour(from, -1)], part[neighbour(to, 1)]];
    // «OFERTA IPH 15 …»: a word that OPENS the line and touches only the model may be ignored (review N3-d).
    const opensLine = !tokens.slice(0, from).some((t) => t.kind !== "gap") && sides[1] === "model";
    const runStuck = (!opensLine && sides.some((side) => side && touching.has(side)))
      || Array.from({ length: to - from + 1 }, (_, k) => from + k).some((k) => stuck.has(k));
    if (!runStuck) ignorableWords.push(t.text);
  });

  // Field limits of the server (`catalog_alias_views.ROW_FIELDS`): a long text is a doubt of ITS line, never a
  // refused request for the whole list.
  if (model && model.text.length > FIELD_LIMITS.model_text) conflicts.push("El nombre del modelo es demasiado largo para leerlo: la línea no se envía.");
  const fit = (text: string, limit: number, what: string) => {
    if (text.length <= limit) return text;
    conflicts.push(`${what} demasiado largo: no se envía.`);
    return "";
  };

  const hasOptions = Boolean(single.capacity || single.sim || single.region || colourText || colourDoubt || otherTexts.length);
  const figures = (extra: { start: number; end: number }[]) => shown(line, merge([...spans,
    ...extra.map((span) => ({ ...span, kind: "number" as const }))]));
  if (!model && !hasOptions && !amounts.length) {
    // A line with no offer may still hold a figure («23k»): masked for whoever may not see costs.
    const digits = tokens.flatMap((t) => (/\d/.test(t.text) ? [{ start: t.start, end: t.end }] : []));
    return { line: null, skipped: { number, display, displayWithoutAmounts: figures(digits) } };
  }
  const digitWords = tokens.flatMap((t, i) => (unknownAt(i) && /\d/.test(t.text) ? [{ start: t.start, end: t.end }] : []));
  const displayWithoutAmounts = shown(line, merge([...spans,
    ...[...amountSpans, ...digitWords].map((span) => ({ ...span, kind: "number" as const }))]));
  return {
    skipped: null,
    line: {
      key: `L${number}`, number, display, displayWithoutAmounts, hidden, model,
      capacityText: fit(single.capacity?.text ?? "", FIELD_LIMITS.capacity_text, "Capacidad"),
      simText: fit(single.sim?.text ?? "", FIELD_LIMITS.sim_text, "SIM"),
      regionText: fit(single.region?.text ?? "", FIELD_LIMITS.region_text, "Región"),
      colourText, colourDoubt, colourChosen: null, lineText, lineWord: lineText ? CANONICAL_LINE[fold(lineText)] ?? fold(lineText) : "",
      otherTexts, amounts, statedQuantity, unknownWords, ignorableWords, conflicts,
    },
  };
}

/**
 * The pasted list, line by line. Each line is read on its own (one line = one offer); a line with nothing of the
 * company's catalogue and no amount is skipped (counted, never sent).
 */
export function readSupplierList(text: string, lexicon: SupplierLexicon): SupplierListReading {
  if (typeof text !== "string" || text.length > MAX_LIST_CHARS) {
    throw new Error("La lista supera el límite de 60 000 caracteres.");
  }
  const lines: SupplierLine[] = [];
  const skipped: SkippedLine[] = [];
  const currencies = new Set<string>();
  let truncated = false;
  text.replace(/\r\n?/g, "\n").split("\n").forEach((raw, index) => {
    const { line, skipped: left } = readLine(raw, index + 1, lexicon, currencies);
    if (left) skipped.push(left);
    if (!line) return;
    if (lines.length >= MAX_LIST_LINES) { truncated = true; return; }
    lines.push(line);
  });
  return { lines, skipped, truncated, mentionedCurrencies: [...currencies].sort() };
}

/**
 * A PERSON's decision on a colour doubt (review N3-b): which words of the zone are the colour. Only words at the
 * edges may be left out (never a gap in the middle); the result is the zone's own text from the first kept word to
 * the last, exactly as written. The words left out are decided (not sent, not a doubt any more). Null when the
 * choice is not one contiguous run of words, or the result is longer than the server reads.
 */
export function chooseColour(line: SupplierLine, keep: number[]): SupplierLine | null {
  const doubt = line.colourDoubt;
  if (!doubt || doubt.reason !== "unknown_words" || !keep.length) return null;
  const sorted = [...new Set(keep)].sort((a, b) => a - b);
  if (sorted[0] < 0 || sorted[sorted.length - 1] >= doubt.words.length) return null;
  if (sorted.some((index, k) => k > 0 && index !== sorted[k - 1] + 1)) return null;
  const first = doubt.words[sorted[0]];
  const last = doubt.words[sorted[sorted.length - 1]];
  const text = doubt.text.slice(first.start, last.end);
  if (text.length > FIELD_LIMITS.colour_text) return null;
  const zoneWords = doubt.words.map((word) => word.text);
  const left = doubt.words.filter((_, index) => !sorted.includes(index)).map((word) => word.text);
  const without = (words: string[]) => {
    const pool = [...zoneWords];
    return words.filter((word) => {
      const at = pool.indexOf(word);
      if (at < 0) return true;
      pool.splice(at, 1);
      return false;
    });
  };
  return {
    ...line, colourText: text, colourDoubt: null, colourChosen: { text, left },
    unknownWords: without(line.unknownWords), ignorableWords: without(line.ignorableWords),
  };
}

/**
 * What travels to `POST /admin/catalog/resolve/`: per line with a model, ONLY the fields the server reads, each a
 * fragment of the company's vocabulary as the supplier wrote it. No amount, quantity, unknown word or hidden datum.
 */
export function resolvePayload(lines: SupplierLine[]): ResolveRowInput[] {
  return lines.filter((line) => line.model && line.model.text.length <= FIELD_LIMITS.model_text).map((line) => {
    const row: ResolveRowInput = { key: line.key, model_text: line.model!.text };
    if (line.lineWord) row.line = line.lineWord;
    if (line.capacityText) row.capacity_text = line.capacityText;
    if (line.simText) row.sim_text = line.simText;
    if (line.regionText) row.region_text = line.regionText;
    if (line.colourText) row.colour_text = line.colourText;
    return row;
  });
}
