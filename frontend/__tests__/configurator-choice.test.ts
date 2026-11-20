import {
  chooseOption,
  choiceOutcome,
  matchVariant,
  roleOrderedAttributes,
  type ModelAttribute,
  type Selection,
  type Variant,
} from '@/app/lib/catalog-models';

import { modelFixture } from './support/device-commerce-fixtures';

/**
 * CATALOG-REFERENCE-01 · PR D — choosing one option of the configurator.
 *
 * Any option a NEW variant has can be pressed, whatever was chosen before (a colour that only exists in another
 * capacity included): the chosen option stays, every other one is kept while a variant still has it together with
 * what is decided, and otherwise moves to the nearest one that does. What moved is returned so the page can say it.
 * The result is always an existing variant: an impossible combination (a SIM × region pair no variant has, PR B's
 * exclusions) is never reached.
 */

const CAP = 3, COL = 4;
const C128 = 11, C256 = 12, C512 = 13;
const AZUL = 21, BLANCO = 22, ROJO = 23;

function fixture() {
  const model = modelFixture();
  const attributes = roleOrderedAttributes(model.attributes as ModelAttribute[]);
  return { attributes, variants: model.variants as Variant[] };
}

const sel = (cap: number, col: number): Selection => ({ [CAP]: cap, [COL]: col });
const colourIndex = 1;
const capacityIndex = 0;

describe('cambiar de color', () => {
  it('conserva la capacidad cuando el color existe en ella', () => {
    const { attributes, variants } = fixture();
    const { selection, changed } = chooseOption(variants, attributes, sel(C128, AZUL), colourIndex, BLANCO);
    expect(selection).toEqual(sel(C128, BLANCO));
    expect(changed).toEqual([]);
  });

  it('la conserva aunque en ese color esté agotada: compatible no es disponible', () => {
    const { attributes, variants } = fixture();
    const { selection, changed } = chooseOption(variants, attributes, sel(C256, AZUL), colourIndex, BLANCO);
    expect(selection).toEqual(sel(C256, BLANCO));
    expect(changed).toEqual([]);
  });

  it('un color que no existe en la capacidad elegida se puede elegir y mueve la capacidad, y lo dice', () => {
    const { attributes, variants } = fixture();
    const { selection, changed } = chooseOption(variants, attributes, sel(C128, AZUL), colourIndex, ROJO);
    expect(selection).toEqual(sel(C512, ROJO));
    expect(changed).toEqual([{ attributeId: CAP, from: C128, to: C512, reason: 'unavailable' }]);
    expect(matchVariant(variants, attributes, selection)?.product_id).toBe(45);
  });

  it('la capacidad va a la más cercana con ese color; a igual distancia, la menor', () => {
    const { attributes, variants } = fixture();
    // Verde at 128 and 512 only, both in stock: from 256 both are one step away.
    const verde = 24;
    attributes[colourIndex].options.push({ id: verde, label: 'Verde', swatch_hex: '#0F0', sort_order: 3 });
    const more = [
      ...variants,
      { ...variants[0], product_id: 46, slug: 'v-128', options: { [CAP]: C128, [COL]: verde } },
      { ...variants[0], product_id: 47, slug: 'v-512', options: { [CAP]: C512, [COL]: verde } },
    ];
    expect(chooseOption(more, attributes, sel(C256, AZUL), colourIndex, verde).selection).toEqual(sel(C128, verde));
  });

  it('prefiere una capacidad que se puede comprar antes que una agotada más cercana', () => {
    const { attributes, variants } = fixture();
    const verde = 24;
    attributes[colourIndex].options.push({ id: verde, label: 'Verde', swatch_hex: '#0F0', sort_order: 3 });
    const more = [
      ...variants,
      { ...variants[0], product_id: 46, slug: 'v-256', options: { [CAP]: C256, [COL]: verde }, available: false, availability: 'none' },
      { ...variants[0], product_id: 47, slug: 'v-512', options: { [CAP]: C512, [COL]: verde } },
    ];
    expect(chooseOption(more, attributes, sel(C128, AZUL), colourIndex, verde).selection).toEqual(sel(C512, verde));
  });
});

describe('la elección del comprador se recuerda (revisión de #373)', () => {
  it('volver a un color que existe en la capacidad que eligió la devuelve, y lo dice', () => {
    const { attributes, variants } = fixture();
    // The buyer chose 128 GB; Rojo moved it to 512 GB; Azul exists in 128 GB again.
    const { selection, changed } = chooseOption(
      variants, attributes, sel(C512, ROJO), colourIndex, AZUL, { preferred: sel(C128, AZUL) });
    expect(selection).toEqual(sel(C128, AZUL));
    expect(changed).toEqual([{ attributeId: CAP, from: C512, to: C128, reason: 'restored' }]);
  });

  it('si la elegida no existe con ese color, la más cercana como siempre', () => {
    const { attributes, variants } = fixture();
    const { selection } = chooseOption(variants, attributes, sel(C128, AZUL), colourIndex, ROJO, { preferred: sel(C128, AZUL) });
    expect(selection).toEqual(sel(C512, ROJO));
  });

  it('sin preferencia distinta de lo elegido, nada cambia', () => {
    const { attributes, variants } = fixture();
    expect(chooseOption(variants, attributes, sel(C128, AZUL), colourIndex, BLANCO, { preferred: sel(C128, AZUL) }))
      .toEqual({ selection: sel(C128, BLANCO), changed: [] });
  });
});

describe('cambiar de capacidad', () => {
  it('conserva el color si se puede comprar en ella', () => {
    const { attributes, variants } = fixture();
    expect(chooseOption(variants, attributes, sel(C128, AZUL), capacityIndex, C256)).toEqual({
      selection: sel(C256, AZUL), changed: [],
    });
  });

  it('si el color no existe en ella, pasa al más cercano que se pueda comprar y lo dice', () => {
    const { attributes, variants } = fixture();
    const { selection, changed } = chooseOption(variants, attributes, sel(C128, AZUL), capacityIndex, C512);
    expect(selection).toEqual(sel(C512, ROJO));
    expect(changed).toEqual([{ attributeId: COL, from: AZUL, to: ROJO, reason: 'unavailable' }]);
  });
});

describe('qué pasa si se pulsa una opción', () => {
  it('disponible, agotada o inexistente, según la variante a la que lleva', () => {
    const { attributes, variants } = fixture();
    expect(choiceOutcome(variants, attributes, sel(C128, AZUL), colourIndex, ROJO)).toBe('available');
    expect(choiceOutcome(variants, attributes, sel(C256, AZUL), colourIndex, BLANCO)).toBe('sold-out');
    // An option only a used device has (the public attributes include theirs): no new variant, nothing to choose.
    expect(choiceOutcome(variants, attributes, sel(C128, AZUL), colourIndex, 99)).toBe('unavailable');
  });
});

describe('combinaciones imposibles (pares SIM × región excluidos)', () => {
  const SIM = 5, REG = 6;
  const FISICA = 31, ESIM = 32;
  const INTL = 41, US = 42;
  const attributes = roleOrderedAttributes([
    { id: CAP, name: 'Capacidad', kind: 'text', role: 'capacity', sort_order: 0,
      options: [{ id: C128, label: '128 GB', swatch_hex: '', sort_order: 0 }] },
    { id: SIM, name: 'SIM', kind: 'text', role: 'sim', sort_order: 1,
      options: [{ id: FISICA, label: 'nano-SIM y eSIM', swatch_hex: '', sort_order: 0 },
        { id: ESIM, label: 'Solo eSIM', swatch_hex: '', sort_order: 1 }] },
    { id: REG, name: 'Región', kind: 'text', role: 'region', sort_order: 2,
      options: [{ id: INTL, label: 'Internacional', swatch_hex: '', sort_order: 0 },
        { id: US, label: 'EE. UU.', swatch_hex: '', sort_order: 1 }] },
  ]);
  const base = modelFixture().variants[0] as Variant;
  // Only (nano-SIM, Internacional) and (Solo eSIM, EE. UU.) exist: the other two pairs are excluded.
  const variants: Variant[] = [
    { ...base, product_id: 1, slug: 'a', options: { [CAP]: C128, [SIM]: FISICA, [REG]: INTL } },
    { ...base, product_id: 2, slug: 'b', options: { [CAP]: C128, [SIM]: ESIM, [REG]: US } },
  ];

  it('elegir la región de la otra pareja mueve la SIM con ella', () => {
    const start = { [CAP]: C128, [SIM]: FISICA, [REG]: INTL };
    const { selection, changed } = chooseOption(variants, attributes, start, 2, US);
    expect(selection).toEqual({ [CAP]: C128, [SIM]: ESIM, [REG]: US });
    expect(changed).toEqual([{ attributeId: SIM, from: FISICA, to: ESIM, reason: 'unavailable' }]);
  });

  it('ninguna secuencia de pulsaciones llega a un par excluido', () => {
    let selection: Selection = { [CAP]: C128, [SIM]: FISICA, [REG]: INTL };
    const presses: [number, number][] = [[1, ESIM], [2, INTL], [1, FISICA], [2, US], [1, ESIM], [2, US], [1, FISICA]];
    for (const [index, option] of presses) {
      selection = chooseOption(variants, attributes, selection, index, option).selection;
      expect(matchVariant(variants, attributes, selection)).not.toBeNull();
    }
  });
});
