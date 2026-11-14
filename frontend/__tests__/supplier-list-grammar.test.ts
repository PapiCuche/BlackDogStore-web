import {
  chooseColour, parseAmountText, phraseKey, readSupplierList, resolvePayload, supplierLexicon,
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
        expect(Object.keys(amount).sort()).toEqual(['currency', 'dollarSign', 'key', 'label', 'problem', 'raw', 'value']);
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
    // «$» is a dollar of SOME currency: by itself it says nothing (review M-LOW); «US$»/«USD» do say it.
    expect(amountOf('$4390').map((a) => [a.currency, a.dollarSign])).toEqual([[null, true]]);
    expect(amountOf('US$ 4390').map((a) => [a.currency, a.dollarSign])).toEqual([['USD', false]]);
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
    const line = one('iph 15 128gb 2295 Carlos esim negro');
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

describe('revisión H1: un color nunca se recorta ni se parte en silencio', () => {
  const noCompounds = supplierLexicon(trainingVocabulary({ compounds: false }));

  it('con una paleta sin colores compuestos, «glaciar X» es una duda que bloquea: nunca «Blanco» ni «Negro»', () => {
    const fixture = trainingFixture();
    const reading = readSupplierList(fixture.lines.map((line) => line.text).join('\n'), noCompounds);
    const glaciar = reading.lines.filter((line) => /glaciar/i.test(fixture.lines[line.number - 1].text));
    expect(glaciar.map((line) => line.number)).toEqual([6, 9, 11, 13]);
    for (const line of glaciar) {
      expect(line.colourText).toBe('');
      expect(line.colourDoubt).toMatchObject({ text: expect.stringMatching(/^glaciar (blanco|negro)$/), reason: 'unknown_words' });
      expect(line.ignorableWords).toEqual([]);
    }
    const sent = resolvePayload(reading.lines);
    for (const line of glaciar) {
      const row = sent.find((r) => r.key === line.key)!;
      expect(row).not.toHaveProperty('colour_text');
    }
    expect(JSON.stringify(sent)).not.toContain('glaciar');
  });

  it('«negro mate azul»: ni «negro, azul» ni un color recortado', () => {
    const line = one('iph 18 pro max esim LLA 256gb 5500 negro mate azul');
    expect(line.colourText).toBe('');
    expect(line.colourDoubt).toMatchObject({ text: 'negro mate azul', reason: 'unknown_words' });
    expect(line.ignorableWords).toEqual([]);
    expect(resolvePayload([line])[0]).not.toHaveProperty('colour_text');
  });

  it('dos colores separados por otra cosa: duda, nunca se unen con una coma inventada', () => {
    const line = one('iph 18 pro max esim LLA 256gb negro 5500 azul');
    expect(line.colourText).toBe('');
    expect(line.colourDoubt).toMatchObject({ text: 'negro … azul', reason: 'separated' });
  });

  it('el separador que escribió el proveedor viaja tal cual', () => {
    expect(one('iph 18 pro max esim LLA 256gb 5500 borgoña, azul').colourText).toBe('borgoña, azul');
    expect(one('iph 18 pro max esim LLA 256gb 5500 borgoña / azul').colourText).toBe('borgoña / azul');
  });

  it('«Ignorar» sólo para palabras que no tocan el modelo, la capacidad ni el color', () => {
    expect(one('iph 18 pro max japonesa esim LLA 256gb 5500 borgoña').ignorableWords).toEqual([]);
    expect(one('iph 18 pro max esim LLA 256gb oferta 5500 borgoña').ignorableWords).toEqual([]);
    const loose = one('iph 18 pro max esim oferta LLA 256gb 5500 borgoña');
    expect(loose.unknownWords).toEqual(['oferta']);
    expect(loose.ignorableWords).toEqual(['oferta']);
  });

  it('un color demasiado largo para el servidor es una duda, no un error de toda la lista', () => {
    const many = Array.from({ length: 30 }, () => 'azul').join(' y ');
    const line = one(`iph 18 pro max esim LLA 256gb 5500 ${many}`);
    expect(line.colourText).toBe('');
    expect(line.colourDoubt?.reason).toBe('too_long');
    expect(resolvePayload([line])[0]).not.toHaveProperty('colour_text');
  });
});

describe('revisión M1: el vocabulario de la empresa gana a la gramática', () => {
  const vocabulary: CatalogVocabulary = {
    model_words: [], separator_words: ['y'],
    models: [
      { id: 1, name: 'Poco X5 Pro', key: 'poco x5 pro', aliases: [] },
      { id: 2, name: 'iPhone X', key: 'iphone x', aliases: [] },
      { id: 3, name: 'Tableta Nube 7', key: 'tableta nube 7', aliases: [] },
    ],
    options: {
      capacity: [{ id: 10, label: '64 GB', key: '64 gb', attribute_id: 1, swatch_hex: '', aliases: [], model_ids: [1, 2] }],
      color: [{ id: 11, label: 'Sol', key: 'sol', attribute_id: 2, swatch_hex: '#ff0', aliases: [], model_ids: [3] }],
      sim: [], region: [], other: [],
    },
  };
  const lexicon = supplierLexicon(vocabulary);
  const only = (text: string) => readSupplierList(text, lexicon).lines[0];

  it('«Poco X5 Pro» es un modelo, no «x5» unidades', () => {
    const line = only('poco x5 pro 64gb 900');
    expect(line.model?.text).toBe('poco x5 pro');
    expect(line.statedQuantity).toBeNull();
  });

  it('«iPhone X 64» es el modelo y la capacidad, no «x 64» unidades', () => {
    const line = only('iphone x 64 900');
    expect([line.model?.text, line.capacityText, line.statedQuantity]).toEqual(['iphone x', '64', null]);
  });

  it('un color «Sol» es el color, no la moneda', () => {
    const line = only('tableta nube 7 sol 900');
    expect(line.colourText).toBe('sol');
    expect(line.amounts.map((a) => [a.raw, a.currency])).toEqual([['900', null]]);
  });
});

describe('revisión M2 y M4: importes por su contenido, también pegados a la moneda', () => {
  it('la clave de un importe dice cuál es, no sólo su posición', () => {
    const first = one('iph 15 128gb 15 negro').amounts[0];
    const second = one('iph 15 128gb 2300 negro').amounts[0];
    expect(first.key).not.toBe(second.key);
  });

  it('«4370usd» es un importe en dólares, nunca una palabra sin reconocer', () => {
    const line = one('iph 15 128gb 4370usd negro');
    expect(line.amounts.map((a) => [a.raw, a.currency])).toEqual([['4370', 'USD']]);
    expect(line.unknownWords).toEqual([]);
    expect(line.displayWithoutAmounts).toBe('iph 15 128gb ••• negro');
  });
});

describe('revisión: la condición del equipo es la línea del catálogo', () => {
  it('«seminuevo» viaja en `line`, no se ignora como palabra', () => {
    const line = one('iph 15 128gb 2295 negro seminuevo');
    expect(line.lineText).toBe('seminuevo');
    expect(line.unknownWords).toEqual([]);
    expect(resolvePayload([line])[0]).toMatchObject({ line: 'seminuevo' });
  });
});

describe('segunda revisión N2: una palabra gramatical nunca es un color', () => {
  it('«de» de «Azul de medianoche» no crea una zona de color', () => {
    const vocabulary = trainingVocabulary();
    vocabulary.options.color.push({ id: 9999, label: 'Azul de medianoche', key: 'azul de medianoche', attribute_id: 2, swatch_hex: '#002', aliases: [], model_ids: [] });
    const line = readSupplierList('IPH 15 128gb negro precio de venta 2300', supplierLexicon(vocabulary)).lines[0];
    expect(line.colourDoubt).toBeNull();
    expect(line.colourText).toBe('negro');
    expect(line.amounts.map((a) => [a.raw, a.label])).toEqual([['2300', 'sale']]);
    const midnight = readSupplierList('IPH 15 128gb 2300 azul de medianoche', supplierLexicon(vocabulary)).lines[0];
    expect(midnight.colourText).toBe('azul de medianoche');
  });
});

describe('segunda revisión N3: relleno y condición', () => {
  it('«nueva», «usadas», «seminueva» van como la palabra canónica del servidor', () => {
    expect(resolvePayload([one('iph 15 128gb 2295 negro nueva')])[0]).toMatchObject({ line: 'nuevo', colour_text: 'negro' });
    expect(resolvePayload([one('iph 15 128gb 2295 negro usadas')])[0]).toMatchObject({ line: 'usado' });
    expect(resolvePayload([one('iph 15 128gb 2295 negro seminueva')])[0]).toMatchObject({ line: 'seminuevo' });
  });

  it('una palabra al principio de la línea, pegada sólo al modelo, se puede ignorar', () => {
    expect(one('OFERTA IPH 15 128gb 2295 negro').ignorableWords).toEqual(['OFERTA']);
  });

  it('el color se puede acotar por una persona, sólo por los extremos', () => {
    const line = one('IPH 15 128gb 2295 negro sellado');
    expect(line.colourDoubt?.words.map((w) => [w.text, w.colour])).toEqual([['negro', true], ['sellado', false]]);
    const chosen = chooseColour(line, [0])!;
    expect(chosen.colourText).toBe('negro');
    expect(chosen.colourDoubt).toBeNull();
    expect(resolvePayload([chosen])[0]).toEqual({ key: line.key, model_text: 'IPH 15', capacity_text: '128gb', colour_text: 'negro' });
    const gap = one('iph 18 pro max esim LLA 256gb 5500 negro mate azul');
    expect(chooseColour(gap, [0, 2])).toBeNull();
  });
});

describe('segunda revisión: menores', () => {
  it('«15 und» después del número del modelo es una cantidad dicha', () => {
    expect(one('IPH 15 128gb 2295 negro 15 und').statedQuantity).toEqual({ text: '15 und', value: 15 });
    expect(one('iph 19 pro 15 und 256gb 2295 azul').statedQuantity).toEqual({ text: '15 und', value: 15 });
  });

  it('los paréntesis unen las palabras de un color, como en el servidor', () => {
    const vocabulary = trainingVocabulary();
    vocabulary.options.color.push({ id: 9998, label: '(PRODUCT)RED', key: '( product ) red', attribute_id: 2, swatch_hex: '#f00', aliases: [], model_ids: [] });
    const line = readSupplierList('IPH 15 128gb 2300 (PRODUCT)RED', supplierLexicon(vocabulary)).lines[0];
    expect(line.colourDoubt).toBeNull();
    expect(line.colourText).toBe('(PRODUCT)RED');
  });

  it('un importe pegado a una moneda larga es un importe con esa moneda', () => {
    expect(one('iph 15 128gb 2300soles negro').amounts.map((a) => [a.raw, a.currency])).toEqual([['2300', 'PEN']]);
    expect(one('iph 15 128gb 4370dolares negro').amounts.map((a) => [a.raw, a.currency])).toEqual([['4370', 'USD']]);
    expect(one('iph 15 128gb 4370dolares negro').hidden).toEqual([]);
  });

  it('una línea sin oferta también se puede mostrar sin cifras', () => {
    const reading = read('Hola, precios 23k\nIPH 15 128gb 2295 negro');
    expect(reading.skipped[0].displayWithoutAmounts).not.toContain('23');
  });
});
