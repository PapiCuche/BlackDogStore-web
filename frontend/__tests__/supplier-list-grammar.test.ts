import {
  parseAmountText, phraseKey, readSupplierList, resolvePayload, supplierLexicon,
} from '@/app/admin/lib/supplier-list-grammar';
import type { CatalogVocabulary } from '@/app/admin/lib/internal-api';
import { trainingFixture, trainingVocabulary } from './support/training-vocabulary';

/**
 * B1-1 · la gramática de una lista de proveedor (D-B1-1, D-B1-3 y privacidad).
 *
 * La línea se parte en campos SÓLO con el vocabulario del servidor (palabras y nombres de modelo, etiquetas y alias
 * de opciones por rol) y una gramática técnica cerrada (importes, marcas de moneda, «x3», separadores). Nada de
 * marcas, colores, SIM ni regiones escritos en el código: el color va tal cual en `colour_text` y R-COL lo lee el
 * servidor. Ningún importe se clasifica solo, ninguna cantidad se confirma y ningún dato personal llega a /resolve.
 */

const vocabulary = trainingVocabulary();
const lexicon = supplierLexicon(vocabulary);
const read = (text: string) => readSupplierList(text, lexicon);
const one = (text: string) => {
  const { lines } = read(text);
  expect(lines).toHaveLength(1);
  return lines[0];
};

/** What the backend test hands the resolver for each line: literal fragments of the line (test_configuration_resolver). */
const LINE_FIELDS: Record<number, Record<string, string>> = {
  1: { model_text: 'IPH 15', capacity_text: '128gb', colour_text: 'negro' },
  2: { model_text: 'Iph 17 pro', capacity_text: '256gb', sim_text: 'esim', region_text: 'japan' },
  3: { model_text: 'iph 17 pro max', capacity_text: '256gb', sim_text: 'esim', region_text: 'lla', colour_text: 'blanco azul naranja' },
  4: { model_text: 'iph 17 pro max', sim_text: 'chip', colour_text: 'azul naranja' },
  5: { model_text: 'iph 18 pro', capacity_text: '256gb', sim_text: 'esim', region_text: 'LLA', colour_text: 'borgoña' },
  6: { model_text: 'Iph 18 pro', capacity_text: '256gb', sim_text: 'esim', region_text: 'LLA', colour_text: 'glaciar blanco' },
  7: { model_text: 'iph 18 pro', capacity_text: '512gb', sim_text: 'esim', region_text: 'japan', colour_text: 'negro' },
  8: { model_text: 'iph 18 pro max', capacity_text: '256gb', sim_text: 'esim', region_text: 'LLA', colour_text: 'borgoña' },
  9: { model_text: 'Iph 18 pro max', capacity_text: '256gb', sim_text: 'esim', region_text: 'LLA', colour_text: 'glaciar negro' },
  10: { model_text: 'Iph 18 pro max', capacity_text: '256gb', sim_text: 'chip', colour_text: 'borgoña' },
  11: { model_text: 'Iph 18 pro max', capacity_text: '256gb', sim_text: 'chip', colour_text: 'glaciar negro' },
  12: { model_text: 'iph 18 pro max', capacity_text: '512gb', sim_text: 'esim', region_text: 'LLA', colour_text: 'borgoña' },
  13: { model_text: 'Iph 18 pro max', sim_text: 'esim', colour_text: 'glaciar negro' },
  14: { model_text: 'iph 18 pro max', capacity_text: '512gb', sim_text: 'chip', colour_text: 'borgoña azul y negro' },
};

describe('la clave de una frase es la del servidor', () => {
  it('pliega acentos, mayúsculas y espacios, y separa la puntuación como `phrase_key`', () => {
    for (const option of Object.values(vocabulary.options).flat()) expect(phraseKey(option.label)).toBe(option.key);
    for (const model of vocabulary.models) expect(phraseKey(model.name)).toBe(model.key);
    expect(phraseKey('  BORGOÑA ')).toBe('borgona');
    expect(phraseKey('LL/A')).toBe('ll / a');
    expect(phraseKey('256GB')).toBe('256gb');
  });
});

describe('las 14 líneas del propietario', () => {
  const fixture = trainingFixture();
  const text = fixture.lines.map((line) => line.text).join('\n');
  const reading = read(text);

  it('cada línea da los campos que el servidor espera, tal como los escribió el proveedor', () => {
    expect(reading.lines).toHaveLength(14);
    const payload = resolvePayload(reading.lines);
    expect(payload).toHaveLength(14);
    payload.forEach((row, index) => {
      const { key, ...fields } = row;
      expect(key).toBe(reading.lines[index].key);
      expect(fields).toEqual(LINE_FIELDS[index + 1]);
    });
  });

  it('cada línea tiene su único importe, leído y SIN clasificar; ninguna cantidad', () => {
    reading.lines.forEach((line, index) => {
      const expected = fixture.lines[index].amount;
      expect(line.amounts.map((amount) => [amount.raw, amount.value, amount.currency, amount.problem])).toEqual([
        [String(expected), String(expected), null, null],
      ]);
      for (const amount of line.amounts) {
        expect(Object.keys(amount).sort()).toEqual(['currency', 'key', 'label', 'problem', 'raw', 'value']);
      }
      expect(line.statedQuantity).toBeNull();
      expect(line.unknownWords).toEqual([]);
      expect(line.model?.known).toBe(true);
    });
  });

  it('a /resolve no viaja ningún importe, cantidad, costo ni el texto pegado', () => {
    const sent = JSON.stringify(resolvePayload(reading.lines));
    for (const line of fixture.lines) expect(sent).not.toContain(String(line.amount));
    for (const key of ['amount', 'cost', 'price', 'quantity', '"text"', 'imei']) expect(sent).not.toContain(key);
  });
});

describe('lo dice el vocabulario, nunca una lista del código', () => {
  it('sin vocabulario no se reconoce ni modelo ni color ni capacidad: son palabras sin reconocer', () => {
    const empty: CatalogVocabulary = {
      model_words: [], models: [], separator_words: [],
      options: { capacity: [], color: [], sim: [], region: [], other: [] },
    };
    const reading = readSupplierList('IPH 15 128gb 2295 negro', supplierLexicon(empty));
    expect(reading.lines).toHaveLength(1);
    const [line] = reading.lines;
    expect(line.model).toBeNull();
    expect([line.capacityText, line.colourText, line.simText, line.regionText]).toEqual(['', '', '', '']);
    expect(line.unknownWords).toEqual(['IPH', '128gb', 'negro']);
    expect(resolvePayload(reading.lines)).toEqual([]);
  });

  it('otra empresa con otras palabras se lee con las suyas', () => {
    const other: CatalogVocabulary = {
      model_words: [], separator_words: ['y'],
      models: [{ id: 1, name: 'Tableta Nube 7', key: 'tableta nube 7', aliases: [] }],
      options: {
        capacity: [{ id: 2, label: '64 GB', key: '64 gb', attribute_id: 1, swatch_hex: '', aliases: [], model_ids: [1] }],
        color: [{ id: 3, label: 'Lima', key: 'lima', attribute_id: 2, swatch_hex: '#0f0', aliases: [{ id: 9, alias: 'verdelima' }], model_ids: [1] }],
        sim: [], region: [], other: [],
      },
    };
    const [line] = readSupplierList('tableta nube 7 64gb verdelima 900', supplierLexicon(other)).lines;
    expect(resolvePayload([line])).toEqual([{ key: line.key, model_text: 'tableta nube 7', capacity_text: '64gb', colour_text: 'verdelima' }]);
  });

  it('una palabra desconocida nunca se pega al modelo y queda como duda', () => {
    const line = one('iph 18 pro max japonesa esim LLA 256gb 5500 borgoña');
    expect(line.model?.text).toBe('iph 18 pro max');
    expect(line.unknownWords).toEqual(['japonesa']);
    expect(resolvePayload([line])[0]).not.toHaveProperty('unknown');
    expect(JSON.stringify(resolvePayload([line]))).not.toContain('japonesa');
  });

  it('un modelo que la empresa no tiene viaja sólo con sus palabras de modelo, para que el servidor lo diga', () => {
    const line = one('iph 19 pro 256gb 4800 azul');
    expect(line.model).toEqual({ text: 'iph 19 pro', known: false });
    expect(resolvePayload([line])[0]).toMatchObject({ model_text: 'iph 19 pro', capacity_text: '256gb', colour_text: 'azul' });
  });
});

describe('importes (D-B1-1): se muestran, nunca se clasifican; los dudosos se marcan', () => {
  const amountOf = (text: string) => one(`iph 15 128gb ${text} negro`).amounts;

  it('un número simple, con decimales o con miles inequívocos', () => {
    expect(amountOf('5500').map((a) => [a.value, a.problem])).toEqual([['5500', null]]);
    expect(amountOf('5500.00').map((a) => [a.value, a.problem])).toEqual([['5500.00', null]]);
    expect(amountOf('5,500.00').map((a) => [a.value, a.problem])).toEqual([['5500.00', null]]);
  });

  it('«5,500» y «5.500» son ambiguos: ningún valor', () => {
    for (const raw of ['5,500', '5.500']) {
      expect(amountOf(raw).map((a) => [a.raw, a.value, a.problem])).toEqual([[raw, null, 'ambiguous_separator']]);
    }
  });

  it('un rango o dos importes juntos son uno solo dudoso, nunca un identificador descartado', () => {
    expect(amountOf('5400-5500').map((a) => [a.raw, a.value, a.problem])).toEqual([['5400-5500', null, 'range']]);
    expect(amountOf('5400/5500').map((a) => [a.raw, a.value, a.problem])).toEqual([['5400/5500', null, 'range']]);
    expect(amountOf('1800,2000').map((a) => [a.raw, a.value, a.problem])).toEqual([['1800,2000', null, 'several']]);
  });

  it('dos importes en la línea: los dos se marcan', () => {
    expect(amountOf('5400 5500').map((a) => [a.raw, a.value, a.problem])).toEqual([
      ['5400', '5400', 'two_amounts'], ['5500', '5500', 'two_amounts'],
    ]);
  });

  it('una etiqueta sólo se muestra como pista', () => {
    expect(one('iph 15 128gb costo 2295 negro').amounts.map((a) => [a.value, a.label])).toEqual([['2295', 'cost']]);
    expect(one('iph 15 128gb precio venta 2600 negro').amounts.map((a) => [a.value, a.label])).toEqual([['2600', 'sale']]);
  });

  it('la moneda escrita se lee; sin marca queda sin moneda (la de la lista)', () => {
    expect(amountOf('$4390')[0].currency).toBe('USD');
    expect(amountOf('USD 4390')[0].currency).toBe('USD');
    expect(amountOf('4390 dólares')[0].currency).toBe('USD');
    expect(amountOf('S/ 4390')[0].currency).toBe('PEN');
    expect(amountOf('4390 soles')[0].currency).toBe('PEN');
    expect(amountOf('€4390')[0].currency).toBe('EUR');
    expect(amountOf('4390')[0].currency).toBeNull();
  });

  it('el mismo criterio que B1-0 para un importe escrito a mano', () => {
    expect(parseAmountText('5500')).toEqual({ ok: true, value: '5500' });
    expect(parseAmountText('5500,00')).toEqual({ ok: true, value: '5500.00' });
    expect(parseAmountText('5.500,00')).toEqual({ ok: true, value: '5500.00' });
    expect(parseAmountText('5,500')).toMatchObject({ ok: false, code: 'ambiguous_amount' });
    expect(parseAmountText('1800,2000')).toMatchObject({ ok: false, code: 'invalid' });
    expect(parseAmountText('5400 5500')).toMatchObject({ ok: false });
    expect(parseAmountText('S/ 10')).toMatchObject({ ok: false, code: 'invalid' });
  });
});

describe('cantidades: «x3» se muestra como dicha y nunca se confirma', () => {
  it('«x3» no es un importe ni una cantidad confirmada', () => {
    const line = one('iph 15 128gb 2295 x3 negro');
    expect(line.statedQuantity).toEqual({ text: 'x3', value: 3 });
    expect(line.amounts.map((a) => [a.value, a.problem])).toEqual([['2295', 'per_unit_or_total']]);
    expect(JSON.stringify(resolvePayload([line]))).not.toMatch(/quantity|x3/);
  });

  it('«3 und» también', () => {
    expect(one('iph 15 128gb 2295 negro 3 und').statedQuantity).toEqual({ text: '3 und', value: 3 });
  });
});

describe('privacidad: lo personal se oculta y nunca llega a /resolve', () => {
  const IMEI = '490154203237518';

  it('un teléfono, la palabra de contacto y lo que le sigue', () => {
    const line = one('iph 15 128gb 2295 negro llamar 987654321 Carlos');
    expect(line.hidden).toContain('contact');
    expect(line.display).not.toMatch(/987654321|Carlos|llamar/);
    const sent = JSON.stringify(resolvePayload([line]));
    for (const secret of ['987654321', 'Carlos', 'llamar']) expect(sent).not.toContain(secret);
    expect(line.amounts.map((a) => a.raw)).toEqual(['2295']);
  });

  it('un celular con prefijo, un IMEI, una serie y un correo', () => {
    const line = one(`iph 15 128gb 2295 negro +51 987 654 321 IMEI ${IMEI} serie F2LX12345 ventas@proveedor.pe`);
    expect(line.hidden).toEqual(expect.arrayContaining(['phone', 'imei', 'serial', 'contact']));
    const sent = JSON.stringify(resolvePayload([line]));
    for (const secret of ['987', IMEI, 'F2LX12345', 'ventas@', 'proveedor']) {
      expect(sent).not.toContain(secret);
      expect(line.display).not.toContain(secret);
    }
    expect(line.amounts.map((a) => a.raw)).toEqual(['2295']);
  });

  it('un IMEI sin etiqueta nunca es un importe', () => {
    const line = one(`iph 15 128gb negro ${IMEI}`);
    expect(line.amounts).toEqual([]);
    expect(line.hidden).toContain('imei');
  });

  it('un nombre suelto queda como palabra sin reconocer y no viaja', () => {
    const line = one('iph 15 128gb 2295 negro Carlos');
    expect(line.unknownWords).toEqual(['Carlos']);
    expect(JSON.stringify(resolvePayload([line]))).not.toContain('Carlos');
  });

  it('el prefijo de un chat exportado (con el nombre de quien escribe) no se lee', () => {
    const line = one('[13/11/26, 10:15:03] Carlos Pérez: iph 15 128gb 2295 negro');
    expect(line.display).not.toContain('Carlos');
    expect(line.unknownWords).toEqual([]);
  });

  it('para quien no ve costos, la línea se muestra también sin sus importes ni su moneda', () => {
    const line = one('iph 15 128gb S/ 2295 negro');
    expect(line.display).toBe('iph 15 128gb S/ 2295 negro');
    expect(line.displayWithoutAmounts).toBe('iph 15 128gb ••• negro');
  });

  it('las líneas sin producto no se envían y se cuentan', () => {
    const reading = read('Buenos días Carlos\nLista del 13/11 en dólares\niph 15 128gb 2295 negro');
    expect(reading.lines).toHaveLength(1);
    expect(reading.skipped).toHaveLength(2);
    expect(reading.mentionedCurrencies).toEqual(['USD']);
    expect(JSON.stringify(resolvePayload(reading.lines))).not.toContain('Carlos');
  });
});

describe('conflictos dentro de una línea', () => {
  it('dos capacidades: ninguna se envía y la línea queda en duda', () => {
    const line = one('iph 15 128gb 256gb 2295 negro');
    expect(line.capacityText).toBe('');
    expect(line.conflicts).toHaveLength(1);
    expect(resolvePayload([line])[0]).not.toHaveProperty('capacity_text');
  });
});
