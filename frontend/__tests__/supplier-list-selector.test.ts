/**
 * SELECTOR-UX-01 — el selector visual dentro del asistente de listas de proveedor.
 *
 * La paleta real del modelo (`/admin/catalog/models/<id>/palette/`) da los pasos; lo que la línea dice se marca como
 * «leído» y lo dudoso como «dudoso»; lo que el texto ya dice (capacidad, SIM, región) no se cambia aquí; lo que una
 * persona marca a mano se dice. Lo elegido viaja a `/resolve` como texto de las etiquetas, UNA fila por color: el
 * servidor sigue siendo quien dice si cada configuración es una variante, válida sin variante o no existe.
 */
import type { ResolveRowResult } from '@/app/admin/lib/internal-api';
import { readSupplierList, supplierLexicon } from '@/app/admin/lib/supplier-list-grammar';
import {
  lockedSteps, readingOf, selectionKey, selectionRows, spaceFromPalette, sameSelection,
} from '@/app/admin/lib/supplier-list-selector';
import {
  BLACK, BLUE, CAP, CHIP, COL, ESIM, G128, G256, GREEN, JA, MODEL, REG, SIM, selectorPalette, selectorVocabulary,
} from './support/selector-catalog';

const vocabulary = selectorVocabulary();
const lexicon = supplierLexicon(vocabulary);
const lineOf = (text: string) => readSupplierList(text, lexicon).lines[0];

function result(over: Partial<ResolveRowResult> = {}): ResolveRowResult {
  return {
    key: 'L1', line: 'new', error: '',
    model: { text: 'equipo z', status: 'exact', via: 'exact', model_id: MODEL, model_name: 'Equipo Z', candidates: [], extracted: [] },
    readings: [
      { role: 'capacity', text: '128gb', option_id: G128, label: '128 GB', via: 'capacity', alias_id: null },
      { role: 'color', text: 'negro', option_id: BLACK, label: 'Negro', via: 'label', alias_id: null },
    ],
    configurations: [{
      status: 'incompleta', complete: false, colour_text: 'negro', option_ids: [G128, BLACK],
      options: [{ role: 'color', text: 'negro', option_id: BLACK, label: 'Negro', via: 'label', alias_id: null }],
      product_id: null, product_name: null, draft_pending: false, missing: ['sim'], candidates: [],
      doubts: [],
    }, {
      status: 'ambigua', complete: false, colour_text: 'verdoso', option_ids: [], options: [], product_id: null,
      product_name: null, draft_pending: false, missing: [], candidates: [],
      doubts: [{ role: 'color', text: 'verdoso', reason: 'ambiguous', actions: ['alias_of'],
        readings: [[{ text: 'verdoso', option_id: GREEN, label: 'Verde' }]] }],
    }],
    ...over,
  };
}

describe('la paleta como pasos del selector', () => {
  it('sólo las opciones de la paleta, cada combinación con su variante de la línea pedida', () => {
    const space = spaceFromPalette(selectorPalette(), 'new')!;
    expect(space.attributes.map((a) => [a.id, a.varies, a.options.map((o) => o.id)])).toEqual([
      [CAP, true, [G128, G256]], [COL, true, [BLACK, BLUE, GREEN]], [SIM, true, [ESIM, CHIP]], [REG, false, [JA]],
    ]);
    expect(space.combinations.find((c) => c.variant)?.variant).toEqual({ product_id: 900, name: 'Equipo Z 128 GB Negro eSIM J/A' });
    expect(spaceFromPalette(selectorPalette(), 'used')!.combinations.some((c) => c.variant)).toBe(false);
  });

  it('un modelo sin paleta no tiene selector (nada se puede validar)', () => {
    expect(spaceFromPalette(selectorPalette({ has_palette: false }), 'new')).toBeNull();
  });
});

describe('lo que la línea dice, marcado', () => {
  it('lo leído se marca y lo que está en duda también; nada se elige de una duda', () => {
    const space = spaceFromPalette(selectorPalette(), 'new')!;
    const reading = readingOf(space, result());
    expect(reading.pick).toEqual({ picked: { [CAP]: G128 }, colours: [BLACK] });
    expect([...reading.recognised].sort()).toEqual([G128, BLACK].sort());
    expect([...reading.doubtful]).toEqual([GREEN]);
  });

  it('la capacidad que el texto dice queda fija; la SIM que no dice se elige a mano', () => {
    const space = spaceFromPalette(selectorPalette(), 'new')!;
    const locked = lockedSteps(space, lineOf('equipo z 128gb negro 2500'), result());
    expect(locked[CAP]).toMatch(/128gb/);
    expect(locked[SIM]).toBeUndefined();
  });
});

describe('lo elegido viaja como filas de /resolve, una por color', () => {
  const space = spaceFromPalette(selectorPalette(), 'new')!;
  const line = lineOf('equipo z 128gb negro 2500');

  it('etiquetas de la empresa, nunca importes; la clave nombra la línea y el color', () => {
    const { rows, problems } = selectionRows(space, line, { picked: { [CAP]: G128, [SIM]: ESIM }, colours: [BLUE, BLACK] }, vocabulary);
    expect(problems).toEqual([]);
    expect(rows).toEqual([
      { key: selectionKey(line.key, BLACK), model_text: 'Equipo Z', capacity_text: '128 GB', colour_text: 'Negro', sim_text: 'eSIM', region_text: 'J/A' },
      { key: selectionKey(line.key, BLUE), model_text: 'Equipo Z', capacity_text: '128 GB', colour_text: 'Azul', sim_text: 'eSIM', region_text: 'J/A' },
    ]);
    expect(JSON.stringify(rows)).not.toContain('2500');
  });

  it('un color cuyo nombre el servidor partiría («Negro y Blanco») no se envía: se dice', () => {
    const palette = selectorPalette();
    palette.attributes[1].options[1] = { ...palette.attributes[1].options[1], label: 'Azul y Blanco' };
    const odd = spaceFromPalette(palette, 'new')!;
    const { rows, problems } = selectionRows(odd, line, { picked: { [CAP]: G128, [SIM]: ESIM }, colours: [BLUE] }, vocabulary);
    expect(rows).toEqual([]);
    expect(problems.join(' ')).toMatch(/Azul y Blanco/);
  });

  it('la misma selección en otro orden es la misma', () => {
    expect(sameSelection({ picked: { 1: 2 }, colours: [3, 4] }, { picked: { 1: 2 }, colours: [4, 3] })).toBe(true);
    expect(sameSelection({ picked: { 1: 2 }, colours: [3] }, { picked: { 1: 2 }, colours: [3, 4] })).toBe(false);
  });
});
