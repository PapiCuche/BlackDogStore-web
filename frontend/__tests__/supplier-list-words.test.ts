/**
 * P1 · palabras reales de proveedores por WhatsApp: estado, disponibilidad, porcentajes e importes con «k».
 * Ninguna se ignora en silencio ni se lee como importe: la línea se detiene para una persona. «sellado» no se
 * interpreta: junto a un color es una duda (D-B1-5) hasta que el propietario decida.
 */
import { readSupplierList, supplierLexicon } from '@/app/admin/lib/supplier-list-grammar';
import { trainingVocabulary } from './support/training-vocabulary';

const lexicon = supplierLexicon(trainingVocabulary());
const one = (text: string) => {
  const { lines } = readSupplierList(text, lexicon);
  expect(lines).toHaveLength(1);
  return lines[0];
};

describe('estado del equipo', () => {
  it('«sellado» no se decide solo: junto al color sigue siendo una duda (regla D-B1-5, pendiente del propietario)', () => {
    const line = one('IPH 15 128gb negro sellado 3500');
    expect(line.colourDoubt?.words.map((w) => [w.text, w.kind])).toEqual([['negro', 'colour'], ['sellado', 'unknown']]);
  });

  it.each(['caja abierta', 'open box', 'CPO', 'reacondicionado', 'exhibición'])('«%s» detiene la línea: nunca se guarda como nuevo', (word) => {
    const line = one(`IPH 15 128gb negro ${word} 3500`);
    expect(line.conflicts.join(' ')).toMatch(/no es nuevo de caja/);
    expect(line.ignorableWords).toEqual([]);
  });

  it('«batería 89%» nunca es un importe', () => {
    const line = one('IPH 15 128gb negro batería 89% 2800');
    expect(line.amounts.map((a) => a.raw)).toEqual(['2800']);
    expect(line.conflicts.join(' ')).toMatch(/porcentaje/);
  });
});

describe('disponibilidad', () => {
  it.each(['agotado', 'AGOTADOS', 'sin stock'])('«%s» detiene la línea: no es una oferta disponible', (word) => {
    const line = one(`IPH 15 128gb negro 3500 ${word}`);
    expect(line.conflicts.join(' ')).toMatch(/no se guarda como oferta disponible/);
    expect(line.ignorableWords).toEqual([]);
  });

  it('«disponible» y «stock» siguen siendo palabras que una persona decide (como en master)', () => {
    expect(one('IPH 15 128gb negro disponible 3500').colourDoubt).not.toBeNull();
    expect(one('IPH 15 128gb negro 3500 stock').unknownWords).toEqual(['stock']);
  });
});

describe('importes con «k»', () => {
  it.each(['2.5k', '4K', '3,2k'])('«%s» se rechaza entero y nunca deja un «2» suelto', (text) => {
    const line = one(`IPH 15 128gb negro ${text}`);
    expect(line.amounts).toEqual([]);
    expect(line.conflicts.join(' ')).toMatch(/sin «k»/);
  });

  it('el importe con «k» se enmascara como cualquier importe y el mensaje no lo repite', () => {
    const line = one('IPH 15 128gb negro 2.5k');
    expect(line.displayWithoutAmounts).not.toMatch(/2\.5k/i);
    expect(line.conflicts.join(' ')).not.toMatch(/2\.5/);
  });

  it('una palabra del vocabulario de la empresa («4K» de «Apple TV 4K») nunca es un importe con «k»', () => {
    const vocabulary = trainingVocabulary();
    vocabulary.models.push({ id: 999, name: 'Apple TV 4K', key: 'apple tv 4k', aliases: [] });
    const withTv = supplierLexicon(vocabulary);
    const [line] = readSupplierList('Apple TV 4K 650', withTv).lines;
    expect(line.conflicts).toEqual([]);
    expect(line.model?.text).toMatch(/Apple TV 4K/i);
  });
});
