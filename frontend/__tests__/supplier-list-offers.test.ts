/**
 * SELECTOR-UX-01 — dos ofertas en una línea de proveedor.
 *
 * Una línea es UNA oferta: un modelo, una capacidad, una SIM, una región y un importe, con uno o varios colores. Si
 * el proveedor escribe dos en la misma línea («… negro 2500 azul 2600», «iph 15 … 2500 iph 17 pro … 3900»), el
 * importe de una nunca se pega al modelo o al color de la otra: la línea queda en duda (no se guarda) y, si hay UNA
 * sola manera limpia de partirla, se propone partirla en líneas, cada una con su importe. Nunca se parte sola y nunca
 * se combinan opciones que el proveedor no escribió juntas.
 */
import { OFFER_READS_MAX, readSupplierList, splitOffers, supplierLexicon } from '@/app/admin/lib/supplier-list-grammar';
import { lineDoubts } from '@/app/admin/lib/supplier-list-plan';
import { trainingVocabulary } from './support/training-vocabulary';

const lexicon = supplierLexicon(trainingVocabulary());
const one = (text: string) => {
  const { lines } = readSupplierList(text, lexicon);
  expect(lines).toHaveLength(1);
  return lines[0];
};
const NONE = { ignoredWords: {}, confirmed: {} };

describe('una línea con dos ofertas', () => {
  it('distinto precio por color: se propone una línea por color con su importe, capacidad y modelo en común', () => {
    const line = one('iph 15 128gb negro 2500 azul 2600');
    expect(line.offers?.parts?.map((part) => part.text)).toEqual(['iph 15 128gb negro 2500', 'iph 15 128gb azul 2600']);
    expect(lineDoubts(line, NONE).join(' ')).toMatch(/2 ofertas/);
  });

  it('dos modelos en la línea: el importe nunca se pega al otro modelo', () => {
    const line = one('iph 15 negro 2500 iph 17 pro esim japan 3900');
    expect(line.offers?.parts?.map((part) => part.text)).toEqual(['iph 15 negro 2500', 'iph 17 pro esim japan 3900']);
    expect(lineDoubts(line, NONE).length).toBeGreaterThan(0);
  });

  it('dos capacidades con su precio: cada una en su línea, el color no se inventa', () => {
    const line = one('iph 15 128gb negro 2500 256gb 2700');
    expect(line.offers?.parts?.map((part) => part.text)).toEqual(['iph 15 128gb negro 2500', 'iph 15 256gb 2700']);
  });

  it('con el importe antes del color, como escriben algunos proveedores', () => {
    const line = one('iph 18 pro esim LLA 256gb 4650 borgoña 512gb 5290 negro');
    expect(line.offers?.parts?.map((part) => part.text)).toEqual([
      'iph 18 pro esim LLA 256gb 4650 borgoña', 'iph 18 pro esim LLA 512gb 5290 negro',
    ]);
  });

  it('cada parte propuesta se muestra sin importes a quien no ve costos', () => {
    const line = one('iph 15 128gb negro 2500 azul 2600');
    for (const part of line.offers!.parts!) {
      expect(part.displayWithoutAmounts).not.toMatch(/2[56]00/);
      expect(part.display).toMatch(/2[56]00/);
    }
  });

  it('un costo y un precio de venta de la MISMA oferta no son dos ofertas', () => {
    const line = one('iph 15 128gb negro costo 2500 pvp 2900');
    expect(line.offers ?? null).toBeNull();
    expect(lineDoubts(line, NONE)).toEqual([]);
  });

  it('una oferta de varios colores con un importe es una sola oferta', () => {
    const line = one('iph 15 128gb negro, azul 2500');
    expect(line.offers ?? null).toBeNull();
  });

  it('si hay más de una manera de partirla, no se propone ninguna: se pide revisarla', () => {
    const line = one('iph 15 negro 2500 128gb azul 2600');
    expect(line.offers?.parts ?? null).toBeNull();
    expect(line.offers).not.toBeUndefined();
    expect(lineDoubts(line, NONE).join(' ')).toMatch(/varias ofertas/);
  });

  it('las líneas de una oferta no cambian', () => {
    const line = one('iph 18 pro esim LLA 256gb 4650 borgoña');
    expect(line.offers ?? null).toBeNull();
  });
});

describe('rendimiento de la búsqueda de cortes', () => {
  it('cada lectura de una parte se hace una sola vez (memoria) y hay un tope de lecturas', () => {
    const stats = { reads: 0 };
    const split = splitOffers('iph 15 128gb negro 2500 azul 2600 256gb 2700 negro 2800 512gb 2900', 1, lexicon, stats);
    expect(split).not.toBeUndefined();
    expect(stats.reads).toBeGreaterThan(0);
    expect(stats.reads).toBeLessThanOrEqual(OFFER_READS_MAX);
    const again = { reads: 0 };
    splitOffers('iph 15 128gb negro 2500 azul 2600', 1, lexicon, again);
    // 6 words: at most one reading per distinct (piece, what it inherits).
    expect(again.reads).toBeLessThanOrEqual(30);
  });

  it('una línea larga no se parte (más de 16 palabras): ni una lectura, y queda como siempre', () => {
    const stats = { reads: 0 };
    const long = 'iph 15 negro 2500 iph 17 pro esim japan 3900 iph 18 pro 256gb esim LLA borgoña 4650 iph 18 pro max chip 256gb 5650';
    expect(splitOffers(long, 1, lexicon, stats)).toBeNull();
    expect(stats.reads).toBe(0);
    const t0 = Date.now();
    one(long);
    expect(Date.now() - t0).toBeLessThan(500);
  });
});
