/**
 * CATALOG-REFERENCE-01 · PR F — la lógica del selector de equipos de la caja, sin React.
 *
 * El servidor manda las combinaciones que EXISTEN (paleta menos exclusiones) y, de cada una, la variante con su
 * stock propio en esta sucursal. Aquí sólo se lee eso: qué preguntar, qué opción es posible con lo ya elegido, qué
 * combinación quedó y si el formulario «Llegó el equipo» se puede enviar. Nada aquí decide un precio ni un permiso.
 */
import type { PosConfigAttribute, PosConfigCombination, PosModelConfigurations } from "./internal-api";
import { imeiPairProblem, imeiProblem } from "./unit-entry";

/**
 * Atributo → opción elegida. `NO_OPTION` dice «sin este atributo»: sólo existe en un modelo sin paleta, cuyas
 * variantes pueden no llevar un rol (re-revisión de #376).
 */
export type Picked = Record<number, number>;

/** «Sin <atributo>»: ningún id de opción es 0. */
export const NO_OPTION = 0;

function optionIdsOf(config: PosModelConfigurations, attributeId: number): Set<number> {
  return new Set(config.attributes.find((attribute) => attribute.id === attributeId)?.options.map((o) => o.id) ?? []);
}

function lacks(combo: PosConfigCombination, own: Set<number>): boolean {
  return !combo.option_ids.some((id) => own.has(id));
}

/** Si alguna combinación no lleva este atributo (sólo pasa sin paleta): entonces el paso ofrece «Sin <atributo>». */
export function isOptional(config: PosModelConfigurations, attributeId: number): boolean {
  const own = optionIdsOf(config, attributeId);
  return own.size > 0 && config.combinations.some((combo) => lacks(combo, own));
}

/** Los pasos que la caja pregunta: los roles que varían, en el orden de lectura que manda el servidor. */
export function stepsToAsk(config: PosModelConfigurations): PosConfigAttribute[] {
  return config.attributes.filter((attribute) => attribute.varies);
}

/** Lo elegido más lo que no se pregunta (un rol con una sola opción se elige solo). */
export function effectivePick(config: PosModelConfigurations, picked: Picked): Picked {
  const out: Picked = { ...picked };
  for (const attribute of config.attributes) {
    if (!attribute.varies && attribute.options.length === 1) out[attribute.id] = attribute.options[0].id;
  }
  return out;
}

function matching(config: PosModelConfigurations, picked: Picked): PosConfigCombination[] {
  const full = effectivePick(config, picked);
  const wanted = Object.values(full).filter((id) => id !== NO_OPTION);
  const absent = Object.keys(full).filter((key) => full[Number(key)] === NO_OPTION)
    .map((key) => optionIdsOf(config, Number(key)));
  return config.combinations.filter((combo) => wanted.every((id) => combo.option_ids.includes(id))
    && absent.every((own) => lacks(combo, own)));
}

/**
 * Si una opción es posible con lo elegido en los pasos ANTERIORES, y cuántos equipos propios hay así. Es jerárquico
 * (modelo → capacidad → color → SIM/región): lo de después nunca cierra una opción de antes, así que cambiar la
 * capacidad siempre se puede.
 */
export function optionState(
  config: PosModelConfigurations,
  picked: Picked,
  attributeId: number,
  optionId: number,
): { possible: boolean; available: number } {
  const steps = stepsToAsk(config).map((attribute) => attribute.id);
  const earlier = steps.slice(0, Math.max(steps.indexOf(attributeId), 0));
  const before: Picked = {};
  for (const id of earlier) if (picked[id] !== undefined) before[id] = picked[id];
  const combos = matching(config, { ...before, [attributeId]: optionId });
  return {
    possible: combos.length > 0,
    available: combos.reduce((sum, combo) => sum + (combo.variant?.sellable ? combo.variant.available : 0), 0),
  };
}

/**
 * Elegir `optionId` en su paso: lo elegido ANTES se queda; lo de después se queda sólo si sigue siendo posible
 * (cambiar la capacidad no obliga a repetir el color, salvo que ese color no exista con ella).
 */
export function pickOption(config: PosModelConfigurations, picked: Picked, attributeId: number, optionId: number): Picked {
  const steps = stepsToAsk(config).map((attribute) => attribute.id);
  const at = steps.indexOf(attributeId);
  const next: Picked = {};
  for (const id of steps.slice(0, at)) if (picked[id] !== undefined) next[id] = picked[id];
  next[attributeId] = optionId;
  for (const id of steps.slice(at + 1)) {
    if (picked[id] === undefined) continue;
    if (matching(config, { ...next, [id]: picked[id] }).length) next[id] = picked[id];
  }
  return next;
}

/**
 * Los pasos aún sin elegir que, con lo elegido, tienen UNA sola opción posible, elegidos solos (256 GB · Azul que
 * sólo existe en eSIM no pregunta la SIM). Repite hasta que nada cambia; nunca toca lo que la persona eligió.
 */
export function autoFill(config: PosModelConfigurations, picked: Picked): Picked {
  const next: Picked = { ...picked };
  let changed = true;
  while (changed) {
    changed = false;
    for (const attribute of stepsToAsk(config)) {
      if (next[attribute.id] !== undefined) continue;
      // A combination still possible WITHOUT this attribute (no palette): choosing its one option would hide it.
      if (isOptional(config, attribute.id)
          && optionState(config, next, attribute.id, NO_OPTION).possible) continue;
      const possible = attribute.options.filter((option) => optionState(config, next, attribute.id, option.id).possible);
      if (possible.length === 1 && Object.keys(next).length > 0) {
        next[attribute.id] = possible[0].id;
        changed = true;
      }
    }
  }
  return next;
}

/**
 * La combinación elegida: la que tiene EXACTAMENTE las opciones elegidas (más las que no se preguntan); si no hay
 * ninguna, `null`. Con paleta toda combinación lleva todos los roles, así que exige elegirlos todos; sin paleta una
 * variante puede no llevar un atributo (revisión de #375) y se alcanza sin elegirlo.
 */
export function chosenCombination(config: PosModelConfigurations, picked: Picked): PosConfigCombination | null {
  const wanted = new Set(Object.values(effectivePick(config, picked)).filter((id) => id !== NO_OPTION));
  if (!wanted.size) return null;
  return config.combinations.find(
    (combo) => combo.option_ids.length === wanted.size && combo.option_ids.every((id) => wanted.has(id)),
  ) ?? null;
}

export type ReceiveForm = { imei: string; imei2: string; serial: string; price: string; cost: string };
export type ReceiveField = "imei" | "imei2" | "serial" | "price" | "cost";

const AMOUNT = /^\d{1,9}(\.\d{1,2})?$/;

/** Qué le falta al formulario, por campo. Vacío: se puede enviar (el servidor vuelve a decidirlo todo). */
export function receiveProblems(
  form: ReceiveForm,
  { needsPrice }: { needsPrice: boolean },
): Partial<Record<ReceiveField, string>> {
  const problems: Partial<Record<ReceiveField, string>> = {};
  const imei = form.imei.trim();
  const imei2 = form.imei2.trim();
  if (imei) {
    const problem = imeiProblem(imei);
    if (problem) problems.imei = problem;
  } else if (!form.serial.trim()) {
    problems.imei = "Indica el IMEI 1 del equipo (o su serie, si no tiene IMEI).";
  }
  if (imei2) {
    const pair = imeiPairProblem(imei, imei2);
    const problem = pair ?? imeiProblem(imei2);
    if (problem) problems.imei2 = problem;
  }
  const price = form.price.trim();
  if (price) {
    if (!AMOUNT.test(price)) problems.price = "Escribe el precio con punto decimal, p. ej. 3899.00.";
    else if (Number(price) <= 0) problems.price = "El precio de venta debe ser mayor que cero.";
  } else if (needsPrice) {
    problems.price = "Indica el precio de venta de esta variante nueva.";
  }
  const cost = form.cost.trim();
  if (cost && !AMOUNT.test(cost)) problems.cost = "Escribe el costo con punto decimal, p. ej. 3100.00.";
  return problems;
}
