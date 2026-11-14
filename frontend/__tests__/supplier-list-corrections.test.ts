import { correctionsAgainst, withCorrections } from '@/app/admin/lib/supplier-list-corrections';
import { readSupplierList, supplierLexicon } from '@/app/admin/lib/supplier-list-grammar';
import { lineDoubts, mayCreateVariant } from '@/app/admin/lib/supplier-list-plan';
import { trainingVocabulary } from './support/training-vocabulary';

/**
 * B1-1 · regla del propietario (2026-11-14) sobre colores compuestos: una palabra desconocida que puede formar parte
 * de un color comercial («Glaciar Blanco») nunca se quita para dejar el resto listo para guardar. Corregir el texto
 * vale para errores de escritura; borrar la palabra deja la línea bloqueada, se mueva, copie o reescriba donde sea.
 * Sólo la paleta o un alias (A2) resuelven la duda, y entonces el texto recibido deja de tenerla.
 */

const plain = supplierLexicon(trainingVocabulary({ compounds: false }));
const GLACIAR = 'Iph 18 pro esim LLA 256gb 4620 glaciar blanco';
const OTHER = 'IPH 15 128gb 2295 negro';

const lineOf = (text: string, number: number) => readSupplierList(text, plain).lines.find((line) => line.number === number)!;
const blocked = (received: string, current: string, number: number, lexicon = plain) => {
  const corrections = correctionsAgainst(received, current, lexicon);
  const line = readSupplierList(current, lexicon).lines.find((candidate) => candidate.number === number)!;
  return { correction: corrections[line.key] ?? null, line: withCorrections([line], corrections)[0] };
};

describe('lo recibido y lo corregido', () => {
  it('sin cambios no hay corrección', () => {
    expect(correctionsAgainst(`${OTHER}\n${GLACIAR}`, `${OTHER}\n${GLACIAR}`, plain)).toEqual({});
    // Spaces alone are not a correction.
    expect(correctionsAgainst(`${OTHER}\n${GLACIAR}`, `${OTHER}  \n  ${GLACIAR}`, plain)).toEqual({});
  });

  it('«glaciar blanco» corregido a «blanco»: bloqueada, con el texto recibido a la vista', () => {
    const { correction, line } = blocked(`${OTHER}\n${GLACIAR}`, `${OTHER}\nIph 18 pro esim LLA 256gb 4620 blanco`, 2);
    expect(line.colourText).toBe('blanco');
    expect(correction?.received?.display).toBe(GLACIAR);
    expect(correction?.blocked).toMatch(/quitó palabras del color recibido «glaciar blanco» y dejó «blanco»/);
    expect(lineDoubts(line, { ignoredWords: {} })).toContain(correction!.blocked);
  });

  it('un error de escritura corregido no bloquea («blanco azull» → «blanco azul»)', () => {
    const received = 'iph 17 pro max 256gb esim lla 5500 blanco azull';
    expect(lineOf(received, 1).colourDoubt).toMatchObject({ text: 'blanco azull', reason: 'unknown_words' });
    const { correction } = blocked(received, 'iph 17 pro max 256gb esim lla 5500 blanco azul', 1);
    expect(correction?.received?.display).toBe(received);
    expect(correction?.blocked).toBeNull();
    // A typo outside the colour neither.
    expect(blocked(OTHER, 'IPH 15 128gb 2299 negro', 1).correction?.blocked).toBeNull();
  });

  it('borrar la línea y escribirla en otro lugar sin «glaciar» sigue bloqueada', () => {
    const { correction } = blocked(`${GLACIAR}\n${OTHER}`, `${OTHER}\nIPH 18 PRO 256gb esim LLA 4620 Blanco`, 2);
    expect(correction?.received).toBeNull();
    expect(correction?.blocked).toMatch(/«glaciar blanco» y dejó «Blanco»/);
  });

  it('copiar la línea sin «glaciar» junto a la original sigue bloqueada', () => {
    const { correction } = blocked(GLACIAR, `${GLACIAR}\nIph 18 pro esim LLA 256gb 4620 blanco`, 2);
    expect(correction?.blocked).toMatch(/«glaciar blanco»/);
  });

  it('otro modelo con el mismo color no se bloquea por la duda de otra línea', () => {
    const { correction } = blocked(`${OTHER}\n${GLACIAR}`, `IPH 15 128gb 2295 blanco\n${GLACIAR}`, 1);
    expect(correction?.blocked).toBeNull();
  });

  it('una línea bloqueada no ofrece «Crear variante»', () => {
    const { line } = blocked(GLACIAR, 'Iph 18 pro esim LLA 256gb 4620 blanco', 1);
    const entry = {
      key: `${line.key}-C1`, line, condition: 'new' as const,
      configuration: { status: 'combinacion_valida_sin_variante', product_id: null } as never,
    };
    expect(mayCreateVariant(entry as never, { ignoredWords: {} })).toBe(false);
  });

  it('resuelta la duda en la paleta (color compuesto), la corrección ya no se bloquea', () => {
    const withPalette = supplierLexicon(trainingVocabulary({ compounds: true }));
    expect(readSupplierList(GLACIAR, withPalette).lines[0].colourDoubt).toBeNull();
    const { correction } = blocked(GLACIAR, 'Iph 18 pro esim LLA 256gb 4620 blanco', 1, withPalette);
    expect(correction?.received?.display).toBe(GLACIAR);
    expect(correction?.blocked).toBeNull();
  });

  it('lo recibido se muestra sin datos personales y, sin costos, sin importes', () => {
    const received = 'IPH 15 128gb 2295 negro llamar al 987654321';
    const { correction } = blocked(received, 'IPH 15 128gb 2296 negro', 1);
    expect(correction?.received?.display).not.toContain('987654321');
    expect(correction?.received?.displayWithoutAmounts).not.toContain('2295');
  });

  it('un texto enorme no se alinea pero la regla por modelo sigue', () => {
    const filler = Array.from({ length: 1600 }, () => '').join('\n');
    const { correction } = blocked(`${GLACIAR}\n${filler}x`, `${filler}y\nIph 18 pro esim LLA 256gb 4620 blanco`, 1601);
    expect(correction?.received).toBeNull();
    expect(correction?.blocked).toMatch(/«glaciar blanco»/);
  });
});
