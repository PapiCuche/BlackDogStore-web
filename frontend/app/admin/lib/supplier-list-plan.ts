/**
 * CATALOG-COMMERCIAL-01 · B1-1 — what a person decided about a supplier's list, and what may be saved.
 *
 * Owner's decisions of 2026-11-14 (D-B1-1 to D-B1-4), as rules of this screen. The server remains the authority on
 * every one of them (`/catalog/resolve/`, `/text-preview/offer-draft/`, the official offer importer with B0):
 *
 *   * D-B1-1 — no amount is classified by itself. Each one is «Costo», «Referencia» or «Ignorar», chosen by a person.
 *     «Costo» needs `costs.view`; without it the supplier's amounts are not even shown (DEC-INV-TEXT-03A) and can only
 *     be ignored. Without the list's currency nothing can be classified; an amount in another currency than the
 *     company's is never converted and can only be a reference or be ignored. An ambiguous amount needs its exact
 *     value typed before it is a cost.
 *   * D-B1-2 — the cost goes to `agreed_cost`; a reference is only shown, never sent; the public price
 *     (`commercial_price`) is typed by a person and never filled from a supplier amount. No margin, tax or price is
 *     computed here.
 *   * D-B1-3 — only the clean configurations a person selects are sent, and every selected one is listed before; a
 *     doubt stays on screen. The list is always PARTIAL (`complete: false`): an offer it does not name never changes.
 *     A quantity is sent only when a person typed it. The same variant twice is caught before the dry run.
 *   * D-B1-4 — «Crear variante» and «Es un alias de…» are separate, confirmed actions (see the screen).
 *
 * Owner's decisions of 2026-11-16 (P1-02): an ambiguous amount («3,999») is a cost only once a person chooses how it
 * reads (thousands or decimals) or types it; a model inherited from a header and «nuevo» proposed by «sellado» keep
 * the line from being saved until a person confirms each one (`confirmed`).
 *
 * Pure: no network, no storage.
 */
import type { OfferDraftAnswer, OfferDraftAnswerRow, OfferDraftBody, OfferDraftRow } from "./inventory-text-draft";
import type { CatalogLine, ResolveConfiguration, ResolveModel, ResolveRowResult, ResolverStatus } from "./internal-api";
import { RESOLVER_STATUS_LABELS } from "./palette";
import { validityFromMessage } from "./supplier-import-api";
import { parseAmountText, type SupplierAmount, type SupplierLine } from "./supplier-list-grammar";

// ── Amounts ─────────────────────────────────────────────────────────────────────────────────────────────────────

export type AmountClass = "cost" | "reference" | "ignore";

export const AMOUNT_CLASS_LABELS: Record<AmountClass, string> = {
  cost: "Costo del proveedor",
  reference: "Referencia",
  ignore: "Ignorar",
};

/**
 * A person's decision on one amount. `exact`: the cost typed when the amount as written is not one clear number.
 * `reading`: how a person said an ambiguous amount reads («3,999»: thousands → 3999, decimals → 3.999). Never guessed.
 * The two are exclusive (review M1): the screen clears one when the other is chosen; with both there is no cost.
 */
export type AmountDecision = { kind: AmountClass; exact?: string; reading?: AmountReading };

export type AmountReading = "thousands" | "decimal";

/**
 * The two readings of an ambiguous amount («3,999», «5.500»), or null when it is not one. `decimalCost`: the decimal
 * reading as a cost (at most two decimals, as the server takes it: «5.500» → «5.50»), or null when it cannot be one.
 */
export function ambiguousReadings(raw: string): { thousands: string; decimal: string; decimalCost: string | null } | null {
  const parsed = parseAmountText(raw);
  if (parsed.ok || parsed.code !== "ambiguous_amount") return null;
  const [head, tail] = raw.split(/[.,]/);
  return { thousands: `${head}${tail}`, decimal: `${head}.${tail}`, decimalCost: tail.endsWith("0") ? `${head}.${tail.slice(0, 2)}` : null };
}

export type AmountContext = {
  canSeeCosts: boolean;
  /** `company.view`: without it the company's currency cannot be read, so no amount can be its cost. */
  canViewCompany: boolean;
  /** The company's currency (its configuration); null when it could not be read. */
  companyCurrency: string | null;
  /** The currency the person says the list is in; "" until chosen. */
  listCurrency: string;
};

/** ISO codes a list may be declared in (closed technical list; nothing is ever converted). */
export const LIST_CURRENCY_CODES = ["PEN", "USD", "EUR"];

export const AMOUNT_PROBLEM_TEXT: Record<NonNullable<SupplierAmount["problem"]>, string> = {
  ambiguous_separator: "Separador ambiguo: puede ser un importe con miles o con decimales.",
  range: "Es un rango o dos importes: no se elige ninguno.",
  several: "Son dos importes escritos juntos.",
  two_amounts: "La línea tiene más de un importe: decide qué es cada uno.",
  per_unit_or_total: "Va junto a una cantidad: puede ser el precio de cada una o el total.",
  invalid: "No es un importe que el servidor lea.",
};

export const AMOUNT_LABEL_HINT: Record<NonNullable<SupplierAmount["label"]>, string> = {
  cost: "el texto lo llama costo",
  sale: "el texto lo llama precio de venta",
  price: "el texto dice «precio» sin más",
};

/**
 * Whether a currency is written with «$» — USD, the pesos… — derived from its ISO code by the platform's own
 * currency data (`Intl`), never from a list of countries or brands.
 */
export function usesDollarSign(code: string): boolean {
  try {
    const symbol = new Intl.NumberFormat("es", { style: "currency", currency: code, currencyDisplay: "narrowSymbol" })
      .formatToParts(1).find((part) => part.type === "currency")?.value;
    return symbol === "$";
  } catch {
    return false;
  }
}

/**
 * Whether this person may SEE the amount. DEC-SUPPLIER-LIST-01: a supplier's amounts are not shown to whoever cannot
 * see costs — whatever a label calls them («venta», «pvp»…): in a supplier's list it is still the supplier's figure.
 */
export function amountVisible(_amount: SupplierAmount, ctx: AmountContext): boolean {
  return ctx.canSeeCosts;
}

export function amountCurrency(amount: SupplierAmount, ctx: AmountContext): string | null {
  return amount.currency ?? (ctx.listCurrency || null);
}

/** The classes a person may choose for this amount now, and why the others are not offered. */
export function amountChoices(amount: SupplierAmount, ctx: AmountContext): { allowed: AmountClass[]; why: string | null } {
  const currency = amountCurrency(amount, ctx);
  if (!currency) return { allowed: [], why: "Elige primero la moneda de la lista: sin ella ningún importe se puede clasificar." };
  if (!amountVisible(amount, ctx)) {
    return { allowed: ["ignore"], why: "Sin permiso para ver costos el importe no se muestra: sólo puede ignorarse." };
  }
  if (!ctx.canSeeCosts) return { allowed: ["reference", "ignore"], why: "Marcar un costo requiere permiso para ver costos." };
  // Review N1: «$2295» in a list in soles is NOT 2295 soles — it would be a silent 1:1 conversion.
  if (amount.dollarSign && !usesDollarSign(currency)) {
    return {
      allowed: ["reference", "ignore"],
      why: `Escrito con «$», pero la lista está en ${currency}, que no se escribe con «$»: nunca se convierte; sólo puede ser referencia o ignorarse.`,
    };
  }
  if (!ctx.companyCurrency) {
    return {
      allowed: ["reference", "ignore"],
      why: ctx.canViewCompany
        ? "No se pudo leer la moneda de la empresa: ningún importe puede ser costo."
        : "Marcar un costo requiere también permiso para ver la configuración de la empresa (su moneda): sin ella no se sabe si el importe está en la moneda de la empresa.",
    };
  }
  if (currency !== ctx.companyCurrency) {
    return {
      allowed: ["reference", "ignore"],
      why: `Está en ${currency} y la empresa trabaja en ${ctx.companyCurrency}: nunca se convierte; sólo puede ser referencia o ignorarse.`,
    };
  }
  return { allowed: ["cost", "reference", "ignore"], why: null };
}

/** The exact cost of a decision: the amount as read when it is one clear number, otherwise what the person typed. */
function costValue(amount: SupplierAmount, decision: AmountDecision): string | null {
  const typed = decision.exact?.trim() ?? "";
  if (typed && decision.reading) return null;
  if (typed) {
    const parsed = parseAmountText(typed);
    return parsed.ok ? parsed.value : null;
  }
  const readings = amount.problem === "ambiguous_separator" ? ambiguousReadings(amount.raw) : null;
  if (readings && decision.reading) return decision.reading === "thousands" ? readings.thousands : readings.decimalCost;
  return amount.problem === "ambiguous_separator" || amount.problem === "range" || amount.problem === "several"
    || amount.problem === "invalid" ? null : amount.value;
}

/** Why the amounts of a line keep it from being saved. Empty: every amount is decided. */
export function amountIssues(line: SupplierLine, decisions: Record<string, AmountDecision | undefined>, ctx: AmountContext): string[] {
  const issues = new Set<string>();
  let costs = 0;
  for (const amount of line.amounts) {
    const named = amountVisible(amount, ctx) ? `«${amount.raw}»` : "oculto";
    const { allowed, why } = amountChoices(amount, ctx);
    const decision = decisions[amount.key];
    if (!allowed.length) { issues.add(why ?? "Elige la moneda de la lista."); continue; }
    if (!decision) { issues.add(`Clasifica el importe ${named}: nunca se clasifica solo.`); continue; }
    if (!allowed.includes(decision.kind)) {
      issues.add(`La clasificación del importe ${named} ya no vale (cambió la moneda o el permiso): clasifícalo otra vez.`);
      continue;
    }
    if (decision.kind === "cost") {
      costs += 1;
      if (costValue(amount, decision) === null) {
        const ambiguous = amount.problem === "ambiguous_separator" && !decision.exact?.trim();
        issues.add(decision.exact?.trim() && decision.reading
          ? `Elige sólo una: la lectura del importe ${named} o el costo escrito.`
          : decision.exact?.trim()
          ? `El costo escrito para ${named} no es un importe claro.`
          : ambiguous && decision.reading === "decimal"
            ? `Leído con decimales, el importe ${named} lleva más de dos decimales: no puede ser un costo. Elige la otra lectura o escribe el costo exacto.`
            : ambiguous
              ? `El importe ${named} es ambiguo: elige cómo se lee (miles o decimales) o escribe el costo exacto.`
              : `El importe ${named} no es un número claro: escribe el costo exacto.`);
      }
    }
  }
  if (costs > 1) issues.add("Sólo un importe de la línea puede ser el costo.");
  return [...issues];
}

/** The line's cost, as it will travel in `agreed_cost` (only with `costs.view`); null when there is none. */
export function lineCost(line: SupplierLine, decisions: Record<string, AmountDecision | undefined>, ctx: AmountContext): string | null {
  if (!ctx.canSeeCosts) return null;
  const costs = line.amounts.filter((amount) => decisions[amount.key]?.kind === "cost"
    && amountChoices(amount, ctx).allowed.includes("cost"));
  if (costs.length !== 1) return null;
  return costValue(costs[0], decisions[costs[0].key]!);
}

/** The amounts the person called a reference and may see: shown next to the price, never copied, never sent. */
export function lineReferences(line: SupplierLine, decisions: Record<string, AmountDecision | undefined>, ctx: AmountContext): SupplierAmount[] {
  return line.amounts.filter((amount) => decisions[amount.key]?.kind === "reference" && amountVisible(amount, ctx));
}

// ── Configurations ──────────────────────────────────────────────────────────────────────────────────────────────

export type ConfigEntry = {
  /** `L<line>-C<n>`: the key of the draft row (≤ 40 characters). */
  key: string;
  line: SupplierLine;
  index: number;
  configuration: ResolveConfiguration;
  /** The catalogue line the server read for the row (`new` unless the text said otherwise). */
  condition: CatalogLine | null;
  /** The model the server read for the line (review L3: a header's model is confirmed only against ONE model). */
  model: ResolveModel | null;
  /**
   * SELECTOR-UX-01 — the configuration comes from the visual selector (a person marked it), not from the text as read:
   * the server resolved the company's labels of what was marked. `handPicked`: the options a person chose that the
   * line does not say (shown on its card).
   */
  bySelector?: boolean;
  handPicked?: string[];
};

/**
 * The configurations of the reading: per line, the server's reading of its text — or, when a person used the visual
 * selector on the line (`chosen`), the server's reading of each row the selector sent (one per colour marked). The
 * line itself (its amounts, its doubts, its model) is the same either way.
 */
export function configurationEntries(lines: SupplierLine[], results: Record<string, ResolveRowResult | undefined>,
  chosen: Record<string, ResolveRowResult[] | undefined> = {}): ConfigEntry[] {
  return lines.flatMap((line) => {
    const model = results[line.key]?.model ?? null;
    const picked = chosen[line.key];
    if (picked) {
      return picked.flatMap((row) => row.configurations.map((configuration, part) => ({ row, configuration, part })))
        .map(({ row, configuration, part }, index) => ({
          key: part ? `${row.key}-${part + 1}` : row.key, line, index, configuration, condition: row.line ?? null, model,
          bySelector: true,
        }));
    }
    return (results[line.key]?.configurations ?? []).map((configuration, index) => ({
      key: `${line.key}-C${index + 1}`, line, index, configuration, condition: results[line.key]?.line ?? null, model,
    }));
  });
}

export type EntryInputs = {
  decisions: Record<string, AmountDecision | undefined>;
  /** Commercial price per configuration: typed, empty by default. */
  prices: Record<string, string | undefined>;
  /** Confirmed quantity per configuration: typed, empty = «sin confirmar». */
  quantities: Record<string, string | undefined>;
  /** Lines whose unknown words a person chose to ignore. */
  ignoredWords: Record<string, boolean | undefined>;
  /** P1-02 — what a person confirmed of a line's proposals, by `modelConfirmKey` / `conditionConfirmKey`. */
  confirmed?: Record<string, boolean | undefined>;
};

/**
 * The key of «use the header's model» for a line, or null when it inherits none. It names the header and its text:
 * a confirmation never carries over to another header.
 */
export function modelConfirmKey(line: SupplierLine): string | null {
  return line.inherited ? `${line.key}|model|H${line.inherited.from}|${line.inherited.text}` : null;
}

/** Model readings that are not ONE model of the company: a header's model is never confirmed with them. */
const UNCLEAR_MODEL = new Set(["ambiguous", "unknown", "name_with_options"]);

/** Whether the server read exactly one model of the company for a line. */
export function serverReadsOneModel(model: ResolveModel | null | undefined): boolean {
  return Boolean(model?.model_id) && !UNCLEAR_MODEL.has(model?.status ?? "");
}

/**
 * The model a line's catalogue writes may use (option, palette, variant): null while the line takes it from a header
 * and a person has not confirmed it, or the server does not read exactly one model (review M2).
 */
export function writableModelId(line: SupplierLine, model: ResolveModel | null | undefined,
  confirmed: Record<string, boolean | undefined> | undefined): number | null {
  const key = modelConfirmKey(line);
  if (key && !(confirmed?.[key] && serverReadsOneModel(model))) return null;
  return model?.model_id ?? null;
}

/** The key of «the line is «nuevo»» proposed by «sellado», or null when nothing is proposed. */
export function conditionConfirmKey(line: SupplierLine): string | null {
  return line.conditionHint ? `${line.key}|condition|${line.conditionHint.line}|${line.conditionHint.word}` : null;
}

/** Why this configuration cannot be saved. Empty: it is clean and may be selected. */
export function entryIssues(entry: ConfigEntry, inputs: EntryInputs, ctx: AmountContext): string[] {
  const issues: string[] = [];
  const { configuration, line } = entry;
  if (configuration.status !== "variante_existente" || !configuration.product_id) {
    issues.push(configuration.status === "combinacion_valida_sin_variante"
      ? "No hay variante con esta configuración: créala (nace como borrador) para poder guardar la oferta."
      : `${RESOLVER_STATUS_LABELS[configuration.status as ResolverStatus] ?? configuration.status}: queda pendiente, no se guarda.`);
  }
  if (entry.condition === "used") {
    issues.push("La línea dice seminuevo: una oferta de proveedor no admite seminuevos; cada equipo se recibe por unidad.");
  }
  issues.push(...line.conflicts);
  issues.push(...lineDoubts(line, inputs, entry.model));
  issues.push(...amountIssues(line, inputs.decisions, ctx));
  const price = (inputs.prices[entry.key] ?? "").trim();
  if (!price) issues.push("Escribe el precio comercial: nunca se llena solo.");
  else {
    const parsed = parseAmountText(price);
    if (!parsed.ok) issues.push(`Precio comercial: ${parsed.detail}`);
    else if (Number(parsed.value) <= 0) issues.push("El precio comercial debe ser mayor que cero.");
  }
  const quantity = (inputs.quantities[entry.key] ?? "").trim();
  if (quantity && !/^\d{1,6}$/.test(quantity)) issues.push("La cantidad es un número entero, o se deja vacía (sin confirmar).");
  return issues;
}

/**
 * What the line itself leaves in doubt: unknown words, a colour that could not be sent, a correction that cut a colour,
 * and a proposal not yet confirmed (a header's model, «nuevo» by «sellado»).
 */
export function lineDoubts(line: SupplierLine, inputs: Pick<EntryInputs, "ignoredWords" | "confirmed">,
  serverModel?: ResolveModel | null): string[] {
  const out: string[] = [];
  const modelKey = modelConfirmKey(line);
  // Review L3: a confirmation counts only while the server reads exactly one model for the line.
  if (modelKey && !(inputs.confirmed?.[modelKey] && serverReadsOneModel(serverModel))) {
    out.push(`Confirma el modelo de la cabecera («${line.inherited!.text}», línea ${line.inherited!.from}): esta línea no lo dice.`);
  }
  const conditionKey = conditionConfirmKey(line);
  if (conditionKey && !inputs.confirmed?.[conditionKey]) {
    out.push(`Confirma el estado «${line.conditionHint!.line}» (por «${line.conditionHint!.word}»): la línea no dice «nuevo».`);
  }
  // SELECTOR-UX-01: several offers in one line — an amount never sticks to another offer's model or colour.
  if (line.offers) {
    out.push(line.offers.parts
      ? `La línea tiene ${line.offers.parts.length} ofertas: sepárala (una oferta por línea) para que cada importe quede con su modelo y su color.`
      : "La línea parece tener varias ofertas y se puede leer de más de una manera: escribe una oferta por línea.");
  }
  if (line.colourDoubt) out.push(COLOUR_DOUBT_TEXT[line.colourDoubt.reason](line.colourDoubt.text));
  if (line.correction?.blocked) out.push(line.correction.blocked);
  const stuck = line.unknownWords.length > line.ignorableWords.length;
  if (stuck) {
    out.push("Hay palabras sin reconocer pegadas al modelo, la capacidad o el color: regístralas como alias o deja la línea pendiente.");
  } else if (line.unknownWords.length && !inputs.ignoredWords[line.key]) {
    out.push("La línea tiene palabras sin reconocer: regístralas como alias o márcalas para ignorarlas.");
  }
  return out;
}

export const COLOUR_DOUBT_TEXT: Record<NonNullable<SupplierLine["colourDoubt"]>["reason"], (text: string) => string> = {
  unknown_words: (text) => `Color con palabras sin reconocer: «${text}». No se envía ningún color de esta línea.`,
  separated: (text) => `Colores en lugares distintos de la línea: «${text}». No se unen solos: no se envía ningún color.`,
  too_long: (text) => `El color escrito es demasiado largo para leerlo («${text}»): no se envía.`,
};

/**
 * «Crear variante» (D-B1-4 and review H1): only for a complete valid configuration of a NEW line whose text has no
 * doubt left — no unknown word that is not ignored, no conflict, no colour doubt.
 */
export function mayCreateVariant(entry: ConfigEntry, inputs: Pick<EntryInputs, "ignoredWords" | "confirmed">): boolean {
  return entry.configuration.status === "combinacion_valida_sin_variante" && entry.condition !== "used"
    && !entry.line.conflicts.length && !lineDoubts(entry.line, inputs, entry.model).length;
}

/** The selected configuration that already offers the same variant, if any (the same variant twice is refused). */
export function repeatedIn(entry: ConfigEntry, selected: ConfigEntry[]): ConfigEntry | null {
  const product = entry.configuration.product_id;
  if (!product) return null;
  return selected.find((other) => other.key !== entry.key && other.configuration.product_id === product) ?? null;
}

// ── The draft ───────────────────────────────────────────────────────────────────────────────────────────────────

export type SaveSettings = { supplierId: number | null; messageDate: string; leadTimeDays: string };

/** The rows of the dry run: EXACTLY the selected configurations, with what the person typed. */
export function draftRows(selected: ConfigEntry[], inputs: EntryInputs, ctx: AmountContext): OfferDraftRow[] {
  return selected.map((entry) => {
    const price = parseAmountText(inputs.prices[entry.key] ?? "");
    const quantity = (inputs.quantities[entry.key] ?? "").trim();
    const cost = lineCost(entry.line, inputs.decisions, ctx);
    return {
      key: entry.key,
      product_id: entry.configuration.product_id as number,
      commercial_price: price.ok ? price.value : (inputs.prices[entry.key] ?? "").trim(),
      confirmed_quantity: quantity === "" ? null : Number(quantity),
      ...(ctx.canSeeCosts && cost ? { agreed_cost: cost } : {}),
    };
  });
}

/** The body of `/text-preview/offer-draft/`: a PARTIAL list, its validity from the message date. */
export function draftBody(rows: OfferDraftRow[], settings: SaveSettings): OfferDraftBody {
  const validity = validityFromMessage(settings.messageDate);
  return {
    supplier_id: settings.supplierId, message_date: settings.messageDate,
    valid_from: validity?.validFrom ?? "", valid_until: validity?.validUntil ?? "",
    lead_time_days: settings.leadTimeDays, complete: false, rows,
  };
}

/** The dry run's answer per configuration key: every error and warning goes back to its own row, never hidden. */
export function dryRunByKey(answer: OfferDraftAnswer): Record<string, OfferDraftAnswerRow> {
  const out: Record<string, OfferDraftAnswerRow> = {};
  for (const row of answer.rows) if (row.key) out[row.key] = row;
  return out;
}

/** «Hoy» as the server counts it (`settings.TIME_ZONE`, America/Lima for the installation), never the browser's. */
export const SERVER_TIME_ZONE = "America/Lima";

export function serverToday(now: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: SERVER_TIME_ZONE, year: "numeric", month: "2-digit", day: "2-digit" })
    .formatToParts(now);
  const part = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")}`;
}

/** B0 — the message date the server accepts: a real day, not after the server's today. The message when it is not. */
export function messageDateIssue(messageDate: string, now: Date = new Date()): string | null {
  if (!messageDate) return "Indica la fecha del mensaje del proveedor: con ella se calcula la vigencia y se reconoce una lista ya cargada.";
  if (!validityFromMessage(messageDate)) return "La fecha del mensaje no es válida (AAAA-MM-DD).";
  if (messageDate > serverToday(now)) return "La fecha del mensaje no puede ser posterior a hoy (hora de Lima, como el servidor).";
  return null;
}
