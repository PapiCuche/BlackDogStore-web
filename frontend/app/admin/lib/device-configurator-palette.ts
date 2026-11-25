/**
 * SELECTOR-UX-01 — a model's palette (`GET /admin/catalog/models/<id>/palette/`) as THE device configurator's steps.
 *
 * Used by the screens that are not the till (the till reads its own `/pos/models/<id>/configurations/`, with the
 * branch's stock): the supplier-list assistant (multiple mode) and «Registrar artículo» (single mode). Only the options
 * IN the palette are steps; the combinations are the palette minus its exclusions, as the server lists them; each one
 * carries the variant of the requested catalogue line that IS it (an archived one never counts as existing).
 *
 * Pure: no network, no storage.
 */
import type { ConfiguratorCombination, ConfiguratorSpace } from "./device-configurator";
import type { AdminModelPalette, CatalogLine } from "./internal-api";

/** A combination of the palette with the variant of the line that IS it (never an archived one). */
export type PaletteCombination = ConfiguratorCombination & {
  variant: { product_id: number; name: string } | null;
  /** Only archived variants hold it (VARIANT-SAFE-01): it is neither an existing variant nor offered to be created. */
  archived: boolean;
};

export type PaletteSpace = ConfiguratorSpace<PaletteCombination> & { modelId: number; modelName: string };

/**
 * The palette as the configurator's steps, for one catalogue line (`new` for a supplier's offer). Null when the model
 * has no palette: nothing can be validated, so no selector is offered.
 */
export function spaceFromPalette(palette: AdminModelPalette, line: CatalogLine): PaletteSpace | null {
  if (!palette.has_palette) return null;
  const attributes = palette.attributes
    .map((attribute) => {
      const options = attribute.options.filter((option) => option.in_palette).map((option) => ({
        id: option.id,
        label: option.label,
        // FASE 1.2 · Y: what is drawn for this model (its own swatch, else the company's).
        swatch_hex: option.shown_swatch_hex ?? option.swatch_hex ?? "",
        swatch_is_approximate: option.shown_swatch_is_approximate ?? option.swatch_is_approximate,
        thumbnail: option.thumbnail ?? null,
      }));
      return { id: attribute.id, name: attribute.name, role: attribute.role, kind: attribute.kind, varies: options.length > 1, options };
    })
    .filter((attribute) => attribute.options.length > 0);
  const combinations = palette.combinations.map((combo) => {
    const product = combo.products.find((p) => p.condition_line === line && !p.archived) ?? null;
    return {
      key: combo.key, option_ids: combo.option_ids,
      variant: product ? { product_id: product.product_id, name: product.name } : null,
      archived: !product && combo.products.some((p) => p.condition_line === line && p.archived),
    };
  });
  return { modelId: palette.model_id, modelName: palette.model_name, attributes, combinations };
}

