/**
 * SELECTOR-UX-01 — the logic of THE device configurator, without React.
 *
 * One way to choose a model's configuration everywhere (owner's decision, 2026-11-25): the till's «Vender equipo por
 * modelo» (CATALOG-REFERENCE-01 · PR F) is the standard; the supplier-list assistant and «Registrar artículo» use the
 * same steps, chips and rules. The data is always the model's real catalogue as the server sends it — the palette
 * minus its exclusions (`combinations`) — never a list written here.
 *
 * Two modes:
 *   * single   one capacity, one colour, one SIM, one region → ONE configuration (a device being registered or sold);
 *   * multiple one capacity, SIM and region in common and SEVERAL colours → one configuration per colour chosen (a
 *              supplier's offer). Nothing is multiplied across roles: only the colours are many, and only the ones a
 *              person marks (never a cartesian product nobody declared).
 *
 * The hierarchy is the till's: capacity → colour → SIM → region. A later step never closes an option of an earlier
 * one, so changing the capacity is always possible. Nothing here decides a price, a permission or a status: each
 * screen asks the server, which decides again.
 */

/** Attribute → chosen option. `NO_OPTION` says «without this attribute» (a model without a palette only). */
export type Picked = Record<number, number>;

/** «Sin <atributo>»: no option id is 0. */
export const NO_OPTION = 0;

export type ConfiguratorOption = {
  id: number;
  label: string;
  swatch_hex: string;
  thumbnail?: string | null;
  swatch_is_approximate?: boolean;
};

export type ConfiguratorAttribute = {
  id: number;
  name: string;
  role: string;
  kind: string;
  /** With a single option the step is not asked. */
  varies: boolean;
  options: ConfiguratorOption[];
};

export type ConfiguratorCombination = { key: string; option_ids: number[] };

/** What a configurator reads: the steps and the combinations that EXIST (a combination absent here does not exist). */
export type ConfiguratorSpace<C extends ConfiguratorCombination = ConfiguratorCombination> = {
  attributes: ConfiguratorAttribute[];
  combinations: C[];
};

type Space = ConfiguratorSpace<ConfiguratorCombination>;

function optionIdsOf(config: Space, attributeId: number): Set<number> {
  return new Set(config.attributes.find((attribute) => attribute.id === attributeId)?.options.map((o) => o.id) ?? []);
}

function lacks(combo: ConfiguratorCombination, own: Set<number>): boolean {
  return !combo.option_ids.some((id) => own.has(id));
}

/** Whether a step is about colour (its role, or — with no role assigned — its kind). */
export function isColourAttribute(attribute: Pick<ConfiguratorAttribute, "role" | "kind">): boolean {
  return attribute.role === "color" || attribute.kind === "color";
}

/** Si alguna combinación no lleva este atributo (sólo pasa sin paleta): entonces el paso ofrece «Sin <atributo>». */
export function isOptional(config: Space, attributeId: number): boolean {
  const own = optionIdsOf(config, attributeId);
  return own.size > 0 && config.combinations.some((combo) => lacks(combo, own));
}

/** Los pasos que se preguntan: los roles que varían, en el orden de lectura que manda el servidor. */
export function stepsToAsk<A extends ConfiguratorAttribute>(config: { attributes: A[] }): A[] {
  return config.attributes.filter((attribute) => attribute.varies);
}

/** Lo elegido más lo que no se pregunta (un rol con una sola opción se elige solo). */
export function effectivePick(config: Space, picked: Picked): Picked {
  const out: Picked = { ...picked };
  for (const attribute of config.attributes) {
    if (!attribute.varies && attribute.options.length === 1) out[attribute.id] = attribute.options[0].id;
  }
  return out;
}

/** The combinations that hold everything chosen (and lack what was chosen «without»). */
export function matchingCombinations<C extends ConfiguratorCombination>(config: ConfiguratorSpace<C>, picked: Picked): C[] {
  const full = effectivePick(config, picked);
  const wanted = Object.values(full).filter((id) => id !== NO_OPTION);
  const absent = Object.keys(full).filter((key) => full[Number(key)] === NO_OPTION)
    .map((key) => optionIdsOf(config, Number(key)));
  return config.combinations.filter((combo) => wanted.every((id) => combo.option_ids.includes(id))
    && absent.every((own) => lacks(combo, own)));
}

/**
 * Si una opción es posible con lo elegido en los pasos ANTERIORES y, con `count`, cuánto suma lo que queda posible
 * (la caja cuenta sus equipos propios). Es jerárquico: lo de después nunca cierra una opción de antes.
 */
export function optionState<C extends ConfiguratorCombination>(
  config: ConfiguratorSpace<C>,
  picked: Picked,
  attributeId: number,
  optionId: number,
  count?: (combo: C) => number,
): { possible: boolean; available: number } {
  const steps = stepsToAsk(config).map((attribute) => attribute.id);
  const earlier = steps.slice(0, Math.max(steps.indexOf(attributeId), 0));
  const before: Picked = {};
  for (const id of earlier) if (picked[id] !== undefined) before[id] = picked[id];
  const combos = matchingCombinations(config, { ...before, [attributeId]: optionId });
  return {
    possible: combos.length > 0,
    available: count ? combos.reduce((sum, combo) => sum + count(combo), 0) : 0,
  };
}

/**
 * Elegir `optionId` en su paso: lo elegido ANTES se queda; lo de después se queda sólo si sigue siendo posible
 * (cambiar la capacidad no obliga a repetir el color, salvo que ese color no exista con ella).
 */
export function pickOption(config: Space, picked: Picked, attributeId: number, optionId: number): Picked {
  const steps = stepsToAsk(config).map((attribute) => attribute.id);
  const at = steps.indexOf(attributeId);
  const next: Picked = {};
  for (const id of steps.slice(0, at)) if (picked[id] !== undefined) next[id] = picked[id];
  next[attributeId] = optionId;
  for (const id of steps.slice(at + 1)) {
    if (picked[id] === undefined) continue;
    if (matchingCombinations(config, { ...next, [id]: picked[id] }).length) next[id] = picked[id];
  }
  return next;
}

/**
 * Los pasos aún sin elegir que, con lo elegido, tienen UNA sola opción posible, elegidos solos. Repite hasta que nada
 * cambia; nunca toca lo que la persona eligió.
 */
export function autoFill(config: Space, picked: Picked): Picked {
  const next: Picked = { ...picked };
  let changed = true;
  while (changed) {
    changed = false;
    for (const attribute of stepsToAsk(config)) {
      if (next[attribute.id] !== undefined) continue;
      // An optional role (no palette) counts «Sin …» as one more way out: with a combination still possible
      // WITHOUT it and another WITH it, nothing is chosen on its own; with only one way out, that one is.
      const ways = [...attribute.options.map((option) => option.id),
        ...(isOptional(config, attribute.id) ? [NO_OPTION] : [])]
        .filter((id) => optionState(config, next, attribute.id, id).possible);
      if (ways.length === 1 && Object.keys(next).length > 0) {
        next[attribute.id] = ways[0];
        changed = true;
      }
    }
  }
  return next;
}

/**
 * La combinación elegida: la que tiene EXACTAMENTE las opciones elegidas (más las que no se preguntan); si no hay
 * ninguna, `null`. Un paso opcional sin responder es «sin decidir», nunca «sin él».
 */
export function chosenCombination<C extends ConfiguratorCombination>(config: ConfiguratorSpace<C>, picked: Picked): C | null {
  if (stepsToAsk(config).some((attribute) => picked[attribute.id] === undefined && isOptional(config, attribute.id))) {
    return null;
  }
  const wanted = new Set(Object.values(effectivePick(config, picked)).filter((id) => id !== NO_OPTION));
  if (!wanted.size) return null;
  return config.combinations.find(
    (combo) => combo.option_ids.length === wanted.size && combo.option_ids.every((id) => wanted.has(id)),
  ) ?? null;
}

/** The words of a choice, in reading order («128 GB · Negro · eSIM»). */
export function pickedWords(config: Space, picked: Picked): string {
  const full = effectivePick(config, picked);
  return config.attributes
    .map((attribute) => attribute.options.find((option) => option.id === full[attribute.id])?.label)
    .filter(Boolean)
    .join(" · ");
}

// ── Multiple mode: one capacity, SIM and region in common, several colours ─────────────────────────────────────────

/** A choice in multiple mode: the common steps (`picked`, colour excluded) and the colours marked, in no order. */
export type MultiPick = { picked: Picked; colours: number[] };

/** The colour step of a space, or null when the model has no colour (then multiple mode is one configuration). */
export function colourAttributeOf<A extends ConfiguratorAttribute>(config: { attributes: A[] }): A | null {
  return config.attributes.find((attribute) => attribute.role === "color")
    ?? config.attributes.find((attribute) => isColourAttribute(attribute)) ?? null;
}

/**
 * Whether an option is possible in multiple mode, and why not. A common step follows the till's hierarchy over the
 * other common steps; a colour is possible when it exists with the common steps BEFORE it (the capacity). A colour
 * that a later step (SIM, region) leaves out is not closed: its own card says it is incompatible, nothing is dropped
 * in silence.
 */
export function multiOptionState(config: Space, pick: MultiPick, attributeId: number, optionId: number): { possible: boolean } {
  const colour = colourAttributeOf(config);
  if (colour && attributeId === colour.id) {
    const steps = stepsToAsk(config).map((attribute) => attribute.id);
    const at = steps.indexOf(colour.id);
    const before: Picked = {};
    for (const id of steps.slice(0, Math.max(at, 0))) if (pick.picked[id] !== undefined) before[id] = pick.picked[id];
    return { possible: matchingCombinations(config, { ...before, [colour.id]: optionId }).length > 0 };
  }
  const common: Space = colour ? { ...config, attributes: config.attributes.filter((a) => a.id !== colour.id) } : config;
  return { possible: optionState(common, pick.picked, attributeId, optionId).possible };
}

/** Choose a common step (single choice): later common steps stay while still possible; the colours stay marked. */
export function pickCommon(config: Space, pick: MultiPick, attributeId: number, optionId: number): MultiPick {
  const colour = colourAttributeOf(config);
  const common: Space = colour ? { ...config, attributes: config.attributes.filter((a) => a.id !== colour.id) } : config;
  return { ...pick, picked: pickOption(common, pick.picked, attributeId, optionId) };
}

/** Mark or unmark a colour. */
export function toggleColour(pick: MultiPick, colourId: number): MultiPick {
  return pick.colours.includes(colourId)
    ? { ...pick, colours: pick.colours.filter((id) => id !== colourId) }
    : { ...pick, colours: [...pick.colours, colourId] };
}

export type MultiProblem = "incomplete" | "incompatible";

/** One configuration a multiple choice gives: one per colour marked (or one, when the model has no colour step). */
export type MultiResult<C extends ConfiguratorCombination> = {
  /** The colour of this configuration; null when the model has no colour step. */
  colourId: number | null;
  /** Every option of the configuration (common steps, the ones not asked, and the colour). */
  optionIds: number[];
  /** The combination of the catalogue it is; null when it is incomplete or does not exist (an excluded pair). */
  combination: C | null;
  /** incomplete: a common step is still unanswered; incompatible: every step answered and no such combination. */
  problem: MultiProblem | null;
  /** The steps still unanswered, by name. */
  missing: string[];
};

/** The configurations of a multiple choice, colours in the catalogue's order. Never more than the colours marked. */
export function multiResults<C extends ConfiguratorCombination>(config: ConfiguratorSpace<C>, pick: MultiPick): MultiResult<C>[] {
  const colour = colourAttributeOf(config);
  const steps = stepsToAsk(config);
  const colourIds = colour ? colour.options.map((o) => o.id).filter((id) => pick.colours.includes(id)) : [null];
  return colourIds.map((colourId) => {
    const picked: Picked = { ...pick.picked };
    if (colour && colourId !== null) picked[colour.id] = colourId;
    const missing = steps.filter((attribute) => picked[attribute.id] === undefined).map((attribute) => attribute.name);
    const full = effectivePick(config, picked);
    const optionIds = Object.values(full).filter((id) => id !== NO_OPTION);
    if (missing.length) return { colourId, optionIds, combination: null, problem: "incomplete" as const, missing };
    const combination = chosenCombination(config, picked);
    return { colourId, optionIds, combination, problem: combination ? null : "incompatible" as const, missing };
  });
}
