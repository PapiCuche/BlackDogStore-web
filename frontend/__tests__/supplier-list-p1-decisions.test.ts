/**
 * P1-02 · decisiones del propietario sobre las listas de WhatsApp (2026-11-16):
 *
 *   1. «sellado» es una PISTA de «nuevo»: se propone con su motivo y una persona la confirma; junto a una palabra de
 *      «no nuevo» sigue siendo una duda; si es palabra de un color de la empresa, gana el color (D-B1-5).
 *   2. «3,999» / «5.500»: la persona elige la lectura (miles o decimales). Nunca se adivina ni se muestra sin costos.
 *   3. Una línea que sólo nombra un modelo conocido es una CABECERA: las líneas siguientes sin modelo lo heredan, a la
 *      vista y confirmable. Se corta en una línea en blanco, en otra cabecera, en una línea con modelo propio o en una
 *      línea detenida; nunca viene de una cabecera desconocida o ambigua.
 *   4. «15PM» donde se espera un modelo: la gramática lo señala para ofrecer «Guardar como alias de…»; nunca se crea solo.
 */
import { MAX_LIST_LINES, readSupplierList, resolvePayload, supplierLexicon } from '@/app/admin/lib/supplier-list-grammar';
import { correctionsAgainst } from '@/app/admin/lib/supplier-list-corrections';
import type { ResolveModel } from '@/app/admin/lib/internal-api';
import {
  ambiguousReadings, amountIssues, conditionConfirmKey, lineCost, lineDoubts, modelConfirmKey, type AmountContext,
} from '@/app/admin/lib/supplier-list-plan';
import { modelId, trainingVocabulary } from './support/training-vocabulary';

/** What the server answers for a model text it reads as ONE model of the company (or not). */
const serverModel = (change: Partial<ResolveModel> = {}): ResolveModel => ({
  text: 'IPH 15', status: 'model_word', via: 'model_word', model_id: 101, model_name: 'iPhone 15', candidates: [], extracted: [],
  ...change,
});

const lexicon = supplierLexicon(trainingVocabulary());
const read = (text: string, lex = lexicon) => readSupplierList(text, lex);
const one = (text: string, lex = lexicon) => {
  const { lines } = read(text, lex);
  expect(lines).toHaveLength(1);
  return lines[0];
};

describe('1 · «sellado» es una pista de «nuevo», nunca una equivalencia', () => {
  it('propone «nuevo» con su motivo; no es palabra desconocida ni toca el color; no viaja como `line`', () => {
    const line = one('IPH 15 128gb negro sellado 3500');
    expect(line.conditionHint).toEqual({ word: 'sellado', line: 'nuevo' });
    expect(line.colourText).toBe('negro');
    expect(line.colourDoubt).toBeNull();
    expect(line.unknownWords).toEqual([]);
    expect(line.conflicts).toEqual([]);
    expect(line.lineWord).toBe('');
    expect(resolvePayload([line])[0]).not.toHaveProperty('line');
  });

  it.each(['SELLADOS', 'sellada', 'Selladas'])('«%s» también es la pista', (word) => {
    expect(one(`IPH 15 128gb negro ${word} 3500`).conditionHint).toEqual({ word, line: 'nuevo' });
  });

  it('una persona debe confirmarla: hasta entonces es una duda de la línea', () => {
    const line = one('IPH 15 128gb negro sellado 3500');
    expect(lineDoubts(line, { ignoredWords: {} }).join(' ')).toMatch(/Confirma el estado «nuevo» \(por «sellado»\)/);
    expect(lineDoubts(line, { ignoredWords: {}, confirmed: { [conditionConfirmKey(line)!]: true } })).toEqual([]);
  });

  it.each(['CPO', 'caja abierta', 'reacondicionado', 'seminuevo', 'usado', 'batería 89%'])(
    'junto a «%s» sigue siendo una duda: ninguna condición se propone', (word) => {
      const line = one(`IPH 15 128gb negro sellado ${word} 3500`);
      expect(line.conditionHint).toBeNull();
      expect(line.lineWord).toBe('');
      expect(line.conflicts.join(' ')).toMatch(/«sellado»/);
    });

  it('con «nuevo» escrito no hace falta confirmar nada: lo dice la línea', () => {
    const line = one('IPH 15 128gb negro nuevo sellado 3500');
    expect(line.conditionHint).toBeNull();
    expect(line.lineWord).toBe('nuevo');
    expect(line.conflicts).toEqual([]);
  });

  it('si «sellado» es palabra de un color de la empresa, gana el color (D-B1-5): nunca se recorta', () => {
    const vocabulary = trainingVocabulary();
    vocabulary.options.color.find((option) => option.label === 'Negro')!.aliases.push({ id: 9901, alias: 'Negro Sellado' });
    const line = one('IPH 15 128gb negro sellado 3500', supplierLexicon(vocabulary));
    expect(line.conditionHint).toBeNull();
    expect(line.colourText).toBe('negro sellado');
  });
});

describe('2 · un importe ambiguo: la persona elige la lectura', () => {
  const ctx: AmountContext = { canSeeCosts: true, canViewCompany: true, companyCurrency: 'PEN', listCurrency: 'PEN' };

  it('las dos lecturas, y si la de decimales puede ser un costo (a lo sumo dos decimales)', () => {
    expect(ambiguousReadings('3,999')).toEqual({ thousands: '3999', decimal: '3.999', decimalCost: null });
    expect(ambiguousReadings('5.500')).toEqual({ thousands: '5500', decimal: '5.500', decimalCost: '5.50' });
    expect(ambiguousReadings('5500')).toBeNull();
    expect(ambiguousReadings('5,500.00')).toBeNull();
  });

  it('sin elegir no hay costo; elegida «miles» el costo es ese; nunca se adivina', () => {
    const line = one('IPH 15 128gb negro 3,999');
    const [amount] = line.amounts;
    expect(amount.problem).toBe('ambiguous_separator');
    expect(amountIssues(line, { [amount.key]: { kind: 'cost' } }, ctx).join(' ')).toMatch(/elige cómo se lee/);
    expect(lineCost(line, { [amount.key]: { kind: 'cost' } }, ctx)).toBeNull();
    expect(amountIssues(line, { [amount.key]: { kind: 'cost', reading: 'thousands' } }, ctx)).toEqual([]);
    expect(lineCost(line, { [amount.key]: { kind: 'cost', reading: 'thousands' } }, ctx)).toBe('3999');
  });

  it('elegida «decimales»: 5.500 es 5.50; 3,999 no puede ser un costo (tres decimales)', () => {
    const five = one('IPH 15 128gb negro 5.500');
    const fiveKey = five.amounts[0].key;
    expect(lineCost(five, { [fiveKey]: { kind: 'cost', reading: 'decimal' } }, ctx)).toBe('5.50');
    const three = one('IPH 15 128gb negro 3,999');
    const threeKey = three.amounts[0].key;
    expect(lineCost(three, { [threeKey]: { kind: 'cost', reading: 'decimal' } }, ctx)).toBeNull();
    expect(amountIssues(three, { [threeKey]: { kind: 'cost', reading: 'decimal' } }, ctx).join(' ')).toMatch(/dos decimales/);
  });

  it('revisión M1: la lectura y el costo escrito son excluyentes; con los dos no hay costo', () => {
    const line = one('IPH 15 128gb negro 3,999');
    const key = line.amounts[0].key;
    const both = { [key]: { kind: 'cost' as const, reading: 'thousands' as const, exact: '4100' } };
    expect(lineCost(line, both, ctx)).toBeNull();
    expect(amountIssues(line, both, ctx).join(' ')).toMatch(/sólo una/);
    expect(lineCost(line, { [key]: { kind: 'cost', exact: '4100' } }, ctx)).toBe('4100');
  });

  it('sin costs.view ningún mensaje repite el importe', () => {
    const line = one('IPH 15 128gb negro 3,999');
    const hidden = { ...ctx, canSeeCosts: false };
    const said = amountIssues(line, { [line.amounts[0].key]: { kind: 'ignore' } }, hidden).join(' ')
      + amountIssues(line, {}, hidden).join(' ');
    expect(said).not.toMatch(/3[.,]?999/);
  });
});

describe('3 · el modelo de una cabecera', () => {
  it('una línea que sólo nombra un modelo es una cabecera: no es una oferta y las siguientes lo heredan', () => {
    const reading = read('*IPH 15*\n128gb negro 2295\n128gb azul 2300');
    expect(reading.headers).toEqual([expect.objectContaining({ number: 1, known: true, text: 'IPH 15', followers: [2, 3] })]);
    expect(reading.lines.map((line) => line.number)).toEqual([2, 3]);
    for (const line of reading.lines) {
      expect(line.model).toEqual({ text: 'IPH 15', known: true });
      expect(line.inherited).toEqual({ from: 1, text: 'IPH 15' });
    }
    expect(resolvePayload(reading.lines).map((row) => row.model_text)).toEqual(['IPH 15', 'IPH 15']);
  });

  it('se confirma: hasta entonces es una duda de la línea', () => {
    const [line] = read('iPhone 15\n128gb negro 2295').lines;
    expect(lineDoubts(line, { ignoredWords: {} }).join(' ')).toMatch(/Confirma el modelo de la cabecera/);
    expect(lineDoubts(line, { ignoredWords: {}, confirmed: { [modelConfirmKey(line)!]: true } }, serverModel())).toEqual([]);
  });

  it('revisión L3: una confirmación sólo vale si el servidor lee UN modelo', () => {
    const [line] = read('iPhone 15\n128gb negro 2295').lines;
    const confirmed = { [modelConfirmKey(line)!]: true };
    expect(lineDoubts(line, { ignoredWords: {}, confirmed }).join(' ')).toMatch(/cabecera/);
    expect(lineDoubts(line, { ignoredWords: {}, confirmed }, null).join(' ')).toMatch(/cabecera/);
    for (const status of ['ambiguous', 'unknown', 'name_with_options'] as const) {
      expect(lineDoubts(line, { ignoredWords: {}, confirmed }, serverModel({ status })).join(' ')).toMatch(/cabecera/);
    }
    expect(lineDoubts(line, { ignoredWords: {}, confirmed }, serverModel({ model_id: null })).join(' ')).toMatch(/cabecera/);
  });

  it('revisión L1: la cabecera sólo nombra las líneas que se leen (tope de líneas)', () => {
    const many = Array.from({ length: MAX_LIST_LINES + 5 }, () => '128gb negro 2295').join('\n');
    const reading = read(`iPhone 15\n${many}`);
    expect(reading.truncated).toBe(true);
    const shown = new Set(reading.lines.map((line) => line.number));
    expect(reading.headers[0].followers).toHaveLength(MAX_LIST_LINES);
    expect(reading.headers[0].followers.every((number) => shown.has(number))).toBe(true);
  });

  it('revisión L2: una cabecera corregida a mano marca las líneas que heredan su modelo', () => {
    const corrected = correctionsAgainst('iph 18 pro max\n256gb 5500 borgoña', 'iph 18 pro\n256gb 5500 borgoña', lexicon);
    expect(corrected.L2).toEqual(expect.objectContaining({
      blocked: null, headerOnly: true,
      header: { received: { display: 'iph 18 pro max', displayWithoutAmounts: 'iph 18 pro max' },
        current: { display: 'iph 18 pro', displayWithoutAmounts: 'iph 18 pro' } },
    }));
    // A header received as it is: nothing to mark.
    expect(correctionsAgainst('iph 18 pro\n256gb 5500 borgoña', 'iph 18 pro\n256gb 5500 borgoña\n', lexicon)).toEqual({});
  });

  it('revisión L2: una línea corregida bajo una cabecera corregida lleva las dos marcas', () => {
    const corrected = correctionsAgainst('iph 18 pro max\n256gb 5500 borgona', 'iph 18 pro\n256gb 5500 borgoña', lexicon);
    expect(corrected.L2.headerOnly).toBeFalsy();
    expect(corrected.L2.header?.current.display).toBe('iph 18 pro');
  });

  it('la clave de la confirmación nombra la cabecera: otra cabecera pide confirmar otra vez', () => {
    const [a] = read('iPhone 15\n128gb negro 2295').lines;
    const [b] = read('iph 18 pro\n128gb negro 2295').lines;
    expect(modelConfirmKey(a)).not.toBe(modelConfirmKey(b));
    expect(modelConfirmKey(one('IPH 15 128gb negro 2295'))).toBeNull();
  });

  it('se corta en una línea en blanco', () => {
    const reading = read('iPhone 15\n128gb negro 2295\n\n128gb azul 2300');
    expect(reading.lines.map((line) => line.inherited?.from ?? null)).toEqual([1, null]);
    expect(reading.lines[1].model).toBeNull();
  });

  it('se corta en la siguiente cabecera, que toma el relevo', () => {
    const reading = read('iPhone 15\n128gb negro 2295\niph 18 pro\n256gb borgoña 4650');
    expect(reading.headers.map((header) => [header.number, header.text])).toEqual([[1, 'iPhone 15'], [3, 'iph 18 pro']]);
    expect(reading.lines.map((line) => [line.number, line.model?.text])).toEqual([[2, 'iPhone 15'], [4, 'iph 18 pro']]);
  });

  it('se corta en una línea con modelo propio', () => {
    const reading = read('iPhone 15\n128gb negro 2295\niph 18 pro 256gb borgoña 4650\n512gb negro 5290');
    expect(reading.lines.map((line) => [line.number, line.model?.text ?? null, line.inherited?.from ?? null])).toEqual([
      [2, 'iPhone 15', 1], [3, 'iph 18 pro', null], [4, null, null],
    ]);
  });

  it('nunca cruza una línea que la gramática detuvo', () => {
    const reading = read('iPhone 15\n128gb negro 2295 agotado\n128gb azul 2300');
    expect(reading.lines.map((line) => line.inherited)).toEqual([null, null]);
    expect(reading.lines.map((line) => line.model)).toEqual([null, null]);
  });

  it('nunca viene de una cabecera desconocida', () => {
    const reading = read('15PM\n256gb negro 3500');
    expect(reading.headers).toEqual([expect.objectContaining({ number: 1, known: false, text: '15PM', followers: [] })]);
    expect(reading.lines[0].model).toBeNull();
    expect(reading.lines[0].inherited).toBeNull();
  });

  it('nunca viene de una cabecera ambigua (dos modelos de la empresa con el mismo nombre leído)', () => {
    const vocabulary = trainingVocabulary();
    vocabulary.models[0].aliases.push({ id: 9911, alias: 'X15' });
    vocabulary.models[1].aliases.push({ id: 9912, alias: 'X15' });
    const reading = read('X15\n256gb negro 3500', supplierLexicon(vocabulary));
    expect(reading.headers).toEqual([expect.objectContaining({ known: false })]);
    expect(reading.lines[0].inherited).toBeNull();
  });

  it('una palabra que no conoce nadie donde iría el modelo corta la herencia: podría ser otro modelo', () => {
    const reading = read('iPhone 15\n15PM 256gb negro 3500\n128gb azul 2300');
    expect(reading.lines.map((line) => [line.number, line.inherited, line.modelSlot])).toEqual([
      [2, null, '15PM'], [3, null, null],
    ]);
  });

  it('un saludo o un texto sin oferta no es una cabecera', () => {
    const reading = read('Buenos días, les paso la lista\nIPH 15 128gb negro 2295');
    expect(reading.headers).toEqual([]);
    expect(reading.lines[0].inherited).toBeNull();
  });
});

describe('4 · «15PM» donde se espera un modelo', () => {
  it('se señala la palabra (nunca se lee como modelo, nunca se crea un alias)', () => {
    const line = one('15PM 256gb negro 3500');
    expect(line.model).toBeNull();
    expect(line.modelSlot).toBe('15PM');
    expect(resolvePayload([line])).toEqual([]);
  });

  it('una línea con su modelo no tiene hueco de modelo', () => {
    expect(one('IPH 15 128gb negro 2295').modelSlot).toBeNull();
  });

  it('con el alias de la empresa, «15PM» es el modelo (y una cabecera)', () => {
    const vocabulary = trainingVocabulary();
    vocabulary.models.find((model) => model.name === 'iPhone 15')!.aliases.push({ id: 9921, alias: '15PM' });
    const withAlias = supplierLexicon(vocabulary);
    expect(one('15PM 256gb negro 3500', withAlias).model).toEqual({ text: '15PM', known: true });
    const reading = read('15PM\n256gb negro 3500', withAlias);
    expect(reading.headers[0]).toEqual(expect.objectContaining({ known: true, text: '15PM' }));
    expect(reading.lines[0].inherited).toEqual({ from: 1, text: '15PM' });
    expect(modelId(vocabulary, 'iPhone 15')).toBeGreaterThan(0);
  });
});
