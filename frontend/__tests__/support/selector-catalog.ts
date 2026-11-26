/**
 * SELECTOR-UX-01 — a small invented catalogue for the shared configurator's tests: one company, one model «Equipo Z»
 * with a palette (two capacities, three colours, two SIM types, one region) and one EXCLUDED pair (Verde · Chip), as
 * `GET /admin/catalog/models/<id>/palette/` and `GET /admin/catalog/vocabulary/` answer it. Nothing of a real tenant.
 */
import type { AdminModelPalette, CatalogVocabulary, PaletteAttribute } from '@/app/admin/lib/internal-api';

export const MODEL = 501;
export const CAP = 1, COL = 2, SIM = 3, REG = 4;
export const G128 = 11, G256 = 12, BLACK = 21, BLUE = 22, GREEN = 23, PINK = 24, ESIM = 31, CHIP = 32, JA = 41;

const option = (id: number, label: string, swatch = '', inPalette = true) => ({
  id, label, swatch_hex: swatch, sort_order: id, in_palette: inPalette, shown_swatch_hex: swatch,
});

export function selectorPalette(over: Partial<AdminModelPalette> = {}): AdminModelPalette {
  const attributes: PaletteAttribute[] = [
    { id: CAP, name: 'Capacidad', kind: 'text', role: 'capacity', sort_order: 0, options: [option(G128, '128 GB'), option(G256, '256 GB')] },
    { id: COL, name: 'Color', kind: 'color', role: 'color', sort_order: 1,
      options: [option(BLACK, 'Negro', '#111111'), option(BLUE, 'Azul', '#1d4ed8'), option(GREEN, 'Verde', '#15803d'),
        option(PINK, 'Rosa', '#f9a8d4', false)] },
    { id: SIM, name: 'Tipo de SIM', kind: 'text', role: 'sim', sort_order: 2, options: [option(ESIM, 'eSIM'), option(CHIP, 'Chip')] },
    { id: REG, name: 'Región', kind: 'text', role: 'region', sort_order: 3, options: [option(JA, 'J/A')] },
  ];
  const combinations: AdminModelPalette['combinations'] = [];
  for (const cap of [G128, G256]) {
    for (const colour of [BLACK, BLUE, GREEN]) {
      for (const sim of [ESIM, CHIP]) {
        if (colour === GREEN && sim === CHIP) continue; // the excluded pair
        const ids = [cap, colour, sim, JA];
        const existing = cap === G128 && colour === BLACK && sim === ESIM;
        combinations.push({
          key: ids.join('-'), option_ids: ids, labels: {}, status: existing ? 'variante_existente' : 'combinacion_valida_sin_variante',
          products: existing ? [{ product_id: 900, name: 'Equipo Z 128 GB Negro eSIM J/A', condition_line: 'new' }] : [],
        });
      }
    }
  }
  return {
    model_id: MODEL, model_name: 'Equipo Z', has_palette: true, attributes, combinations, variants: [],
    excluded_pairs: [[GREEN, CHIP]],
    summary: { combinations: combinations.length, existing: 1, valid_without_variant: combinations.length - 1, outside: 0, incomplete: 0 },
    truncated: false, ...over,
  };
}

export function selectorVocabulary(): CatalogVocabulary {
  const opt = (id: number, attribute: number, label: string, swatch = '', models = [MODEL]) => ({
    id, label, key: label.toLowerCase().replace(/\//g, ' / '), attribute_id: attribute, swatch_hex: swatch, aliases: [], model_ids: models,
  });
  return {
    model_words: [],
    models: [{ id: MODEL, name: 'Equipo Z', key: 'equipo z', aliases: [] }],
    options: {
      capacity: [opt(G128, CAP, '128 GB'), opt(G256, CAP, '256 GB')],
      color: [opt(BLACK, COL, 'Negro', '#111111'), opt(BLUE, COL, 'Azul', '#1d4ed8'), opt(GREEN, COL, 'Verde', '#15803d'),
        opt(PINK, COL, 'Rosa', '#f9a8d4', [])],
      sim: [opt(ESIM, SIM, 'eSIM'), opt(CHIP, SIM, 'Chip')],
      region: [opt(JA, REG, 'J/A')],
      other: [],
    },
    separator_words: ['and', 'e', 'y'],
  };
}
