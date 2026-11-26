/**
 * CATALOG-REFERENCE-01 · PR F — la lógica del selector de equipos de la caja, sin React.
 *
 * El servidor manda las combinaciones que EXISTEN (paleta menos exclusiones) y, de cada una, la variante con su
 * stock propio en esta sucursal. Aquí sólo se lee eso: qué preguntar, qué opción es posible con lo ya elegido, qué
 * combinación quedó y si el formulario «Llegó el equipo» se puede enviar. Nada aquí decide un precio ni un permiso.
 */
import type { PosConfigCombination, PosModelConfigurations } from "./internal-api";
import {
  NO_OPTION,
  autoFill,
  chosenCombination,
  effectivePick,
  isOptional,
  optionState as configuratorOptionState,
  pickOption,
  stepsToAsk,
  type Picked,
} from "./device-configurator";
import { imeiPairProblem, imeiProblem } from "./unit-entry";

/*
 * SELECTOR-UX-01: the steps, the hierarchy and the combinations are THE device configurator's
 * (`device-configurator`), shared with the supplier-list assistant and «Registrar artículo». The till keeps its own
 * contract here: the same names, and each option counts the own devices of this branch.
 */
export { NO_OPTION, autoFill, chosenCombination, effectivePick, isOptional, pickOption, stepsToAsk, type Picked };

/** The till counts what it can sell here: the own devices of a sellable variant. Never a supplier's stock. */
export function ownAvailable(combo: PosConfigCombination): number {
  return combo.variant?.sellable ? combo.variant.available : 0;
}

/**
 * Si una opción es posible con lo elegido en los pasos ANTERIORES, y cuántos equipos propios hay así. Es jerárquico
 * (modelo → capacidad → color → SIM/región): lo de después nunca cierra una opción de antes.
 */
export function optionState(
  config: PosModelConfigurations,
  picked: Picked,
  attributeId: number,
  optionId: number,
): { possible: boolean; available: number } {
  return configuratorOptionState(config, picked, attributeId, optionId, ownAvailable);
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
