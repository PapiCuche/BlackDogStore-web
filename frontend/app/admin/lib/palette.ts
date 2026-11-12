/**
 * CATALOG-COMMERCIAL-01 · A1 — the palette of a model, as the panel reads it.
 *
 * Pure helpers over the payload of `GET /admin/catalog/models/<id>/palette/`.
 * The words here are the platform's names for the ROLES and the STATES (closed
 * technical vocabularies); the values (labels, swatches) are each company's.
 */

import type { AdminModelPalette, AttributeRole, PaletteStatus } from "./internal-api";

/** What a role is called on screen. Its attribute keeps the company's own name. */
export const ROLE_LABELS: Record<AttributeRole, string> = {
  capacity: "Capacidad",
  color: "Color",
  sim: "SIM",
  region: "Región",
  other: "Otro",
};

/** The reading order of the roles (ADR §9): capacity → colour → SIM → region → other. */
export const ROLE_ORDER: AttributeRole[] = ["capacity", "color", "sim", "region", "other"];

/** The state of a configuration (ADR §8.3), in the operator's words. */
export const PALETTE_STATUS_LABELS: Record<PaletteStatus, string> = {
  variante_existente: "Con variante",
  combinacion_valida_sin_variante: "Sin variante",
  fuera_de_paleta: "Fuera de la paleta",
  incompleta: "Incompleta",
};

/** The option ids the palette holds now. */
export function paletteSelection(palette: Pick<AdminModelPalette, "attributes">): Set<number> {
  const chosen = new Set<number>();
  for (const attribute of palette.attributes) {
    for (const option of attribute.options) if (option.in_palette) chosen.add(option.id);
  }
  return chosen;
}

export function sameSelection(a: Set<number>, b: Set<number>): boolean {
  if (a.size !== b.size) return false;
  for (const id of a) if (!b.has(id)) return false;
  return true;
}

/** Only the attributes with something in the palette, each with only its palette options: what a variant may pick. */
export function paletteAttributes(palette: Pick<AdminModelPalette, "attributes">) {
  return palette.attributes
    .map((attribute) => ({ ...attribute, options: attribute.options.filter((option) => option.in_palette) }))
    .filter((attribute) => attribute.options.length > 0);
}
