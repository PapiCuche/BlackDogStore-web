/**
 * SELECTOR-UX-01 — the device configurator inside «Actualizar lista de proveedor», in multiple mode.
 *
 * A line of a supplier's list is ONE offer: one model, one capacity, SIM and region, one amount — and one or several
 * colours. Its selector is THE configurator (`device-configurator`) over the model's real palette
 * (`GET /admin/catalog/models/<id>/palette/`, the palette minus its exclusions):
 *
 *   * what the line says is marked «leído», what stays in doubt is marked «dudoso» and is never chosen by itself;
 *   * what the TEXT already says of a common step (capacity, SIM, region) is fixed here — changing it is correcting the
 *     text, with the corrections' rules; a step the text does not say is chosen by hand, and says so;
 *   * colours are marked and unmarked by hand: one configuration per colour marked, never a product of roles;
 *   * the choice travels to `POST /admin/catalog/resolve/` as the company's own LABELS, one row per colour — the
 *     server decides again what each configuration is (an existing variant by its real id, valid without a variant,
 *     outside the palette, excluded…). No amount, quantity or identifier travels.
 *
 * Pure: no network, no storage.
 */
export { spaceFromPalette, type PaletteCombination as SupplierCombination, type PaletteSpace as SupplierSpace } from "./device-configurator-palette";
import type { MultiPick, Picked } from "./device-configurator";
import { colourAttributeOf, isColourAttribute } from "./device-configurator";
import type { PaletteSpace as SupplierSpace } from "./device-configurator-palette";
import type { CatalogVocabulary, ResolveRowInput, ResolveRowResult } from "./internal-api";
import type { SupplierLine } from "./supplier-list-grammar";

/** What the server read in the line, as a starting choice; and which options it read or left in doubt. */
export type LineReading = { pick: MultiPick; recognised: Set<number>; doubtful: Set<number> };

function attributeOfOption(space: SupplierSpace, optionId: number) {
  return space.attributes.find((attribute) => attribute.options.some((option) => option.id === optionId)) ?? null;
}

/**
 * The line's reading on the selector. A colour or a common option counts as read only when the server READ it as that
 * option (never from a doubt). An option a doubt names as a possible reading is «dudoso»: shown, never chosen.
 */
export function readingOf(space: SupplierSpace, result: ResolveRowResult | undefined): LineReading {
  const recognised = new Set<number>();
  const doubtful = new Set<number>();
  const picked: Picked = {};
  const colours: number[] = [];
  const colour = colourAttributeOf(space);
  const read = [
    ...(result?.readings ?? []),
    ...(result?.configurations ?? []).flatMap((configuration) => configuration.options),
  ];
  for (const option of read) {
    const attribute = attributeOfOption(space, option.option_id);
    if (!attribute) continue;
    recognised.add(option.option_id);
    if (colour && attribute.id === colour.id) {
      if (!colours.includes(option.option_id)) colours.push(option.option_id);
    } else if (picked[attribute.id] === undefined) {
      picked[attribute.id] = option.option_id;
    }
  }
  for (const configuration of result?.configurations ?? []) {
    for (const doubt of configuration.doubts) {
      for (const reading of doubt.readings) {
        for (const part of reading) {
          if (part.option_id && attributeOfOption(space, part.option_id) && !recognised.has(part.option_id)) doubtful.add(part.option_id);
        }
      }
    }
  }
  return { pick: { picked, colours }, recognised, doubtful };
}

const ROLE_TEXT: Record<string, (line: SupplierLine) => string> = {
  capacity: (line) => line.capacityText,
  sim: (line) => line.simText,
  region: (line) => line.regionText,
};

/**
 * The common steps the TEXT already says: attribute id → why it is fixed here. A colour is never fixed (marking colours
 * is what the selector is for); a step the text does not say is free.
 */
export function lockedSteps(space: SupplierSpace, line: SupplierLine, result: ResolveRowResult | undefined): Record<number, string> {
  const out: Record<number, string> = {};
  const read = result?.readings ?? [];
  for (const attribute of space.attributes) {
    if (isColourAttribute(attribute)) continue;
    const said = ROLE_TEXT[attribute.role]?.(line) ?? "";
    const readHere = read.find((option) => attribute.options.some((o) => o.id === option.option_id));
    const text = said || readHere?.text || "";
    if (text) out[attribute.id] = `La lista dice «${text}»: para cambiarlo, corrige el texto de la línea.`;
  }
  return out;
}

/** The key of a selector row and of its configuration card: the line and the colour (≤ 40 characters). */
export function selectionKey(lineKey: string, colourId: number | null): string {
  return `${lineKey}-S${colourId ?? 0}`;
}

const FIELD_OF_ROLE: Record<string, keyof ResolveRowInput> = { capacity: "capacity_text", sim: "sim_text", region: "region_text" };
const SPLITTING_MARKS = /[,;/+&|]/;

/** A colour label the server would read as two colours (a separator inside it): never sent as one. */
function splitsOnServer(label: string, vocabulary: CatalogVocabulary): boolean {
  if (SPLITTING_MARKS.test(label)) return true;
  const words = label.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase().split(/\s+/);
  return words.some((word) => vocabulary.separator_words.includes(word));
}

/**
 * The rows a choice sends to `/resolve`: one per colour marked, in the palette's order, each with the company's labels
 * of every step (and the line's own condition word). `problems`: what cannot be sent as text, said in words.
 */
export function selectionRows(space: SupplierSpace, line: SupplierLine, pick: MultiPick, vocabulary: CatalogVocabulary):
  { rows: ResolveRowInput[]; problems: string[] } {
  const problems: string[] = [];
  const colour = colourAttributeOf(space);
  const common: Partial<ResolveRowInput> = {};
  for (const attribute of space.attributes) {
    if (colour && attribute.id === colour.id) continue;
    const chosen = pick.picked[attribute.id] ?? (!attribute.varies && attribute.options.length === 1 ? attribute.options[0].id : undefined);
    if (chosen === undefined) continue;
    const field = FIELD_OF_ROLE[attribute.role];
    const label = attribute.options.find((option) => option.id === chosen)?.label ?? "";
    if (!field) {
      problems.push(`«${attribute.name}» no se puede leer desde una lista de proveedor: la configuración queda incompleta.`);
      continue;
    }
    (common as Record<string, string>)[field] = label;
  }
  const base: ResolveRowInput = { key: "", model_text: space.modelName, ...common };
  if (line.lineWord) base.line = line.lineWord;
  if (!colour) return { rows: [{ ...base, key: selectionKey(line.key, null) }], problems };
  const rows: ResolveRowInput[] = [];
  for (const option of colour.options) {
    if (!pick.colours.includes(option.id)) continue;
    if (splitsOnServer(option.label, vocabulary)) {
      problems.push(`«${option.label}» lleva un separador en su nombre y se leería como dos colores: escríbelo en la línea.`);
      continue;
    }
    rows.push({ ...base, key: selectionKey(line.key, option.id), colour_text: option.label });
  }
  return { rows, problems };
}

/** Two choices are the same: the same common steps and the same colours, in any order. */
export function sameSelection(a: MultiPick, b: MultiPick): boolean {
  const keys = (p: Picked) => Object.entries(p).map(([k, v]) => `${k}:${v}`).sort().join("|");
  return keys(a.picked) === keys(b.picked)
    && a.colours.length === b.colours.length && a.colours.every((id) => b.colours.includes(id));
}
