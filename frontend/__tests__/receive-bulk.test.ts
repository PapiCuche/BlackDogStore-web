/**
 * INV-INTAKE-BULK-01 — reglas puras del ingreso rápido en «Recibir mercadería»: lector de lo pegado en una línea por
 * equipo (Luhn, repetidos, límite, enmascarado), «Aplicar a todos», +1 al volver a escanear, el atajo de teclado y la
 * sugerencia de la «S» inicial de la caja de Apple.
 */
import { blankUnit, newLine, type DraftLine, type DraftUnit } from '@/app/admin/lib/inventory-receipts';
import {
  addOneMore, appleBoxSerialFix, applyProblems, applyToAllUnits, isReviewShortcut, maskIdentifier, mergePastedUnits,
  parseUnitPaste, PASTE_MAX_TOKENS,
} from '@/app/admin/lib/receive-bulk';

// Valid IMEIs (Luhn).
const IMEI_A = '356789012345672';
const IMEI_B = '490154203237518';
const IMEI_C = '352099001761481';
const BAD_IMEI = '356789012345679'; // 15 digits, fails the check digit

const unit = (over: Partial<DraftUnit> = {}): DraftUnit => ({ ...blankUnit('new'), ...over });

describe('parseUnitPaste · Luhn decide el campo', () => {
  it('un valor de 15 dígitos que supera Luhn va al IMEI; cualquier otro, a la serie', () => {
    const preview = parseUnitPaste(`${IMEI_A}\nF2LXK1ABCD12\n${IMEI_B}`, []);
    expect(preview.tooMany).toBe(false);
    expect(preview.tokens).toBe(3);
    expect(preview.units).toEqual([
      { serial: '', imei: IMEI_A, imei2: '' },
      { serial: 'F2LXK1ABCD12', imei: '', imei2: '' },
      { serial: '', imei: IMEI_B, imei2: '' },
    ]);
    expect(preview.imeis).toBe(2);
    expect(preview.serials).toBe(1);
    expect(preview.withoutSerial).toBe(2);
  });

  it('separa por saltos de línea, espacios, tabuladores, comas y punto y coma', () => {
    const preview = parseUnitPaste(`AAAA1111, BBBB2222;CCCC3333\tDDDD4444  EEEE5555`, []);
    expect(preview.units.map((u) => u.serial)).toEqual(['AAAA1111', 'BBBB2222', 'CCCC3333', 'DDDD4444', 'EEEE5555']);
  });

  it('una línea «serie IMEI» (o «serie IMEI IMEI2») es UN equipo', () => {
    const preview = parseUnitPaste(`F2LXK1ABCD12\t${IMEI_A}\nG7QXK1ABCD99 ${IMEI_B} ${IMEI_C}`, []);
    expect(preview.units).toEqual([
      { serial: 'F2LXK1ABCD12', imei: IMEI_A, imei2: '' },
      { serial: 'G7QXK1ABCD99', imei: IMEI_B, imei2: IMEI_C },
    ]);
    expect(preview.withoutSerial).toBe(0);
  });

  it('un IMEI escrito en grupos («35 678901 234567 2» o con guiones) se lee como uno', () => {
    const preview = parseUnitPaste('35 678901 234567 2\n49-015420-323751-8', []);
    expect(preview.units).toEqual([
      { serial: '', imei: IMEI_A, imei2: '' },
      { serial: '', imei: IMEI_B, imei2: '' },
    ]);
  });

  it('la serie se normaliza como el servidor: mayúsculas', () => {
    expect(parseUnitPaste('f2lxk1abcd12', []).units[0].serial).toBe('F2LXK1ABCD12');
  });

  it('marca como no válidos el IMEI que falla Luhn y la serie con formato imposible, enmascarados', () => {
    const preview = parseUnitPaste(`${BAD_IMEI}\nAB1\nSERIE#ÑAND99\n${IMEI_A}`, []);
    expect(preview.units).toEqual([{ serial: '', imei: IMEI_A, imei2: '' }]);
    expect(preview.invalid).toHaveLength(3);
    expect(preview.invalid[0]).toEqual({ masked: '••••5679', reason: expect.stringMatching(/dígito de control/) });
    expect(preview.invalid[1].reason).toMatch(/entre 4 y 40/);
    expect(preview.invalid[2].reason).toMatch(/sólo lleva letras/);
    // Nothing echoed in full.
    expect(JSON.stringify(preview.invalid)).not.toContain(BAD_IMEI);
  });
});

describe('parseUnitPaste · repetidos', () => {
  it('lo repetido dentro de lo pegado entra una sola vez y se informa enmascarado', () => {
    const preview = parseUnitPaste(`${IMEI_A}\n${IMEI_A}\nAAAA1111\naaaa1111`, []);
    expect(preview.units).toHaveLength(2);
    expect(preview.duplicates).toEqual(['••••5672', '••••1111']);
    expect(preview.duplicates.join(' ')).not.toContain(IMEI_A);
  });

  it('lo que ya está en la línea (serie, IMEI o IMEI 2) no se vuelve a agregar', () => {
    const existing = [unit({ serial_number: 'aaaa1111' }), unit({ imei: IMEI_A }), unit({ imei2: IMEI_B })];
    const preview = parseUnitPaste(`AAAA1111\n${IMEI_A}\n${IMEI_B}\nZZZZ9999`, existing);
    expect(preview.units).toEqual([{ serial: 'ZZZZ9999', imei: '', imei2: '' }]);
    expect(preview.already).toEqual(['••••1111', '••••5672', '••••7518']);
  });
});

describe('parseUnitPaste · límite', () => {
  it(`acepta hasta ${PASTE_MAX_TOKENS} códigos`, () => {
    const text = Array.from({ length: PASTE_MAX_TOKENS }, (_v, i) => `SER${String(i).padStart(5, '0')}`).join('\n');
    const preview = parseUnitPaste(text, []);
    expect(preview.tooMany).toBe(false);
    expect(preview.units).toHaveLength(PASTE_MAX_TOKENS);
  });

  it('con más de 500 no agrega nada: pide pegar una parte', () => {
    const text = Array.from({ length: PASTE_MAX_TOKENS + 1 }, (_v, i) => `SER${String(i).padStart(5, '0')}`).join('\n');
    const preview = parseUnitPaste(text, []);
    expect(preview.tooMany).toBe(true);
    expect(preview.tokens).toBe(PASTE_MAX_TOKENS + 1);
    expect(preview.units).toEqual([]);
  });
});

describe('parseUnitPaste · «S» inicial de la caja de Apple', () => {
  it('cuenta las series de 13 caracteres con S + serie Apple de 12, y sólo las quita si se pide', () => {
    const text = `SF2LXK1ABCD12\nSG7QXK1ABCD99\nSHORT1234`;
    const kept = parseUnitPaste(text, []);
    expect(kept.sPrefixed).toBe(2);
    expect(kept.units.map((u) => u.serial)).toEqual(['SF2LXK1ABCD12', 'SG7QXK1ABCD99', 'SHORT1234']);
    const stripped = parseUnitPaste(text, [], { stripS: true });
    expect(stripped.units.map((u) => u.serial)).toEqual(['F2LXK1ABCD12', 'G7QXK1ABCD99', 'SHORT1234']);
  });
});

describe('maskIdentifier', () => {
  it('como la revisión: •••• y los 4 últimos', () => {
    expect(maskIdentifier(IMEI_A)).toBe('••••5672');
    expect(maskIdentifier('  F2LXK1ABCD12 ')).toBe('••••CD12');
    expect(maskIdentifier('')).toBe('—');
  });
});

describe('mergePastedUnits', () => {
  it('agrega al final, con el estado base de la línea, y quita sólo las filas totalmente vacías', () => {
    const line: DraftLine = {
      ...newLine({ id: 1, name: 'iPhone 13 usado', is_serialized: true, requires_imei: true, condition_line: 'used' }),
    };
    const blank = line.units[0];
    const kept = unit({ condition: 'used', cost: '100' }); // has something typed: stays
    const next = mergePastedUnits({ ...line, units: [blank, kept] }, [{ serial: 'F2LXK1ABCD12', imei: IMEI_A, imei2: '' }]);
    expect(next).toHaveLength(2);
    expect(next[0]).toBe(kept);
    expect(next[1]).toMatchObject({ serial_number: 'F2LXK1ABCD12', imei: IMEI_A, condition: 'used', cost: '' });
  });
});

describe('applyToAllUnits', () => {
  const ALL = { costs: true, prices: true, used: true, grades: true };
  const filled = (units: DraftUnit[]) => units.map((u, i) => ({ ...u, serial_number: u.serial_number || `S${i}XYZ` }));

  it('por defecto sólo completa lo vacío', () => {
    const units = filled([unit({ condition: 'used', cost: '900' }), unit({ condition: 'used' })]);
    const out = applyToAllUnits(units, { cost: '1000', battery: '88', grade: '3', price: '1899' }, ALL, { overwrite: false });
    expect(out.units[0]).toMatchObject({ cost: '900', battery: '88', grade: '3', price: '1899' });
    expect(out.units[1]).toMatchObject({ cost: '1000', battery: '88', grade: '3', price: '1899' });
    expect(out.changed).toBe(2);
    expect(out.kept).toBe(1);
  });

  it('con «reemplazar» escribe en todos', () => {
    const units = filled([unit({ condition: 'used', cost: '900' }), unit({ condition: 'used' })]);
    const out = applyToAllUnits(units, { cost: '1000' }, ALL, { overwrite: true });
    expect(out.units.map((u) => u.cost)).toEqual(['1000', '1000']);
    expect(out.kept).toBe(0);
  });

  it('sin costs.view nunca toca el costo', () => {
    const units = filled([unit({ condition: 'used' })]);
    const out = applyToAllUnits(units, { cost: '1000', battery: '90' }, { ...ALL, costs: false }, { overwrite: true });
    expect(out.units[0].cost).toBe('');
    expect(out.units[0].battery).toBe('90');
  });

  it('sin products.manage nunca toca el precio', () => {
    const units = filled([unit({ condition: 'used' })]);
    const out = applyToAllUnits(units, { price: '1899' }, { ...ALL, prices: false }, { overwrite: true });
    expect(out.units[0].price).toBe('');
    expect(out.changed).toBe(0);
  });

  it('en una línea que no es de seminuevos sólo aplica el costo', () => {
    const units = filled([unit({ condition: 'open_box' })]);
    const out = applyToAllUnits(units, { cost: '50', battery: '90', grade: '2', price: '99' }, { ...ALL, used: false }, { overwrite: true });
    expect(out.units[0]).toMatchObject({ cost: '50', battery: '', grade: '', price: '' });
  });

  it('grado y batería no se escriben en un equipo marcado «Nuevo» (no se envían)', () => {
    const units = filled([unit({ condition: 'new' }), unit({ condition: 'used' })]);
    const out = applyToAllUnits(units, { battery: '90', grade: '2' }, ALL, { overwrite: false });
    expect(out.units[0]).toMatchObject({ battery: '', grade: '' });
    expect(out.units[1]).toMatchObject({ battery: '90', grade: '2' });
  });

  it('sólo los equipos con serie o IMEI: una fila vacía queda vacía', () => {
    const units = [unit({ condition: 'used', serial_number: 'ABCD1234' }), unit({ condition: 'used' })];
    const out = applyToAllUnits(units, { cost: '10' }, ALL, { overwrite: false });
    expect(out.units.map((u) => u.cost)).toEqual(['10', '']);
  });
});

describe('applyProblems', () => {
  it('valida como la recepción: costo con 2 decimales, precio mayor que cero, batería 0–100', () => {
    expect(applyProblems({ cost: '2,500' })).toEqual({ cost: expect.stringMatching(/2 decimales/) });
    expect(applyProblems({ price: '0' })).toEqual({ price: expect.stringMatching(/mayor que cero/) });
    expect(applyProblems({ battery: '120' })).toEqual({ battery: expect.stringMatching(/0 a 100/) });
    expect(applyProblems({ cost: '2500.00', price: '1899', battery: '93%' })).toEqual({});
  });
});

describe('addOneMore', () => {
  it('suma uno a la cantidad', () => {
    expect(addOneMore({ quantity: '3' })).toEqual({ quantity: '4' });
  });
  it('revisión H1: sólo suma sobre un entero de 1 a MAX_QUANTITY-1; nunca recorta al máximo', () => {
    expect(addOneMore({ quantity: '99999' })).toEqual({ quantity: '100000' });
    expect(addOneMore({ quantity: '100000' })).toBeNull();
    expect(addOneMore({ quantity: '7501234567890' })).toBeNull(); // a scan typed into «Cantidad»
    expect(addOneMore({ quantity: '' })).toBeNull();
    expect(addOneMore({ quantity: '0' })).toBeNull();
    expect(addOneMore({ quantity: '2a' })).toBeNull();
  });
});

describe('isReviewShortcut', () => {
  const key = (over: Partial<KeyboardEvent>) => ({ key: 'Enter', ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, ...over });
  it('Ctrl+Enter y ⌘+Enter sí; Enter solo, Alt o Mayús no', () => {
    expect(isReviewShortcut(key({ ctrlKey: true }))).toBe(true);
    expect(isReviewShortcut(key({ metaKey: true }))).toBe(true);
    expect(isReviewShortcut(key({}))).toBe(false);
    expect(isReviewShortcut(key({ ctrlKey: true, shiftKey: true }))).toBe(false);
    expect(isReviewShortcut(key({ ctrlKey: true, altKey: true }))).toBe(false);
    expect(isReviewShortcut(key({ ctrlKey: true, key: 'a' }))).toBe(false);
  });
});

describe('appleBoxSerialFix', () => {
  it('13 caracteres, «S» y una serie Apple de 12: propone quitar la S', () => {
    expect(appleBoxSerialFix('SF2LXK1ABCD12')).toBe('F2LXK1ABCD12');
    expect(appleBoxSerialFix(' sc02xg0fdjg5j ')).toBe('C02XG0FDJG5J');
  });
  it('en cualquier otro caso, nada', () => {
    expect(appleBoxSerialFix('F2LXK1ABCD12')).toBeNull(); // 12, sin S
    expect(appleBoxSerialFix('SF2LXK1ABCD1')).toBeNull(); // 12 con S
    expect(appleBoxSerialFix('XF2LXK1ABCD12')).toBeNull(); // 13 sin S
    expect(appleBoxSerialFix('S123456789012')).toBeNull(); // sólo números: no es una serie Apple
    expect(appleBoxSerialFix('SF2LXK1ABCD-2')).toBeNull(); // signos
    expect(appleBoxSerialFix('')).toBeNull();
  });
});

describe('revisión M2 · una línea con varios códigos', () => {
  const units = (text: string) => parseUnitPaste(text, []).units;
  const rejected = (text: string) => parseUnitPaste(text, []).rejected;

  it.each([
    ['serie IMEI', `F2LXK1ABCD12 ${IMEI_A}`, [{ serial: 'F2LXK1ABCD12', imei: IMEI_A, imei2: '' }]],
    ['serie IMEI IMEI2', `F2LXK1ABCD12\t${IMEI_A}\t${IMEI_B}`, [{ serial: 'F2LXK1ABCD12', imei: IMEI_A, imei2: IMEI_B }]],
    ['IMEI IMEI (sin serie) es UN equipo con IMEI 2', `${IMEI_A} ${IMEI_B}`, [{ serial: '', imei: IMEI_A, imei2: IMEI_B }]],
    ['sólo series: una por código', 'AAAA1111 BBBB2222', [{ serial: 'AAAA1111', imei: '', imei2: '' }, { serial: 'BBBB2222', imei: '', imei2: '' }]],
    ['tres IMEI: uno por código', `${IMEI_A} ${IMEI_B} ${IMEI_C}`, [IMEI_A, IMEI_B, IMEI_C].map((imei) => ({ serial: '', imei, imei2: '' }))],
  ])('%s', (_name, text, expected) => {
    expect(units(text)).toEqual(expected);
    expect(rejected(text)).toEqual([]);
  });

  it.each([
    ['modelo, color e IMEI', `iPhone 13 NEGRO ${IMEI_A}`],
    ['código pegado, color e IMEI', `IPHONE13 NEGRO ${IMEI_A}`],
    ['dos series y un IMEI', `F2LXK1ABCD12 G7QXK1ABCD99 ${IMEI_A}`],
    ['serie e IMEI que falla Luhn', `F2LXK1ABCD12 ${BAD_IMEI}`],
    ['IMEI en grupos que falla Luhn (no crea «678901»)', '35 678901 234567 9'],
    ['serie y un código imposible', 'F2LXK1ABCD12 AB1'],
    ['serie, IMEI, IMEI 2 y algo más', `F2LXK1ABCD12 ${IMEI_A} ${IMEI_B} ${IMEI_C}`],
  ])('se rechaza ENTERA y enmascarada: %s', (_name, text) => {
    expect(units(text)).toEqual([]);
    const out = rejected(text);
    expect(out).toHaveLength(1);
    expect(out[0]).not.toContain(IMEI_A);
    expect(out[0]).not.toContain(BAD_IMEI);
    expect(out[0]).not.toContain('F2LXK1ABCD12');
    expect(out[0]).not.toContain('678901');
  });

  it('las demás líneas de lo pegado entran igual', () => {
    const preview = parseUnitPaste(`iPhone 13 NEGRO ${IMEI_A}\nAAAA1111`, []);
    expect(preview.units).toEqual([{ serial: 'AAAA1111', imei: '', imei2: '' }]);
    expect(preview.rejected).toHaveLength(1);
  });
});

describe('revisión L1 · sólo una agrupación real de IMEI', () => {
  it('2-6-6-1 y 3-3-3-3-3 se leen como un IMEI', () => {
    expect(parseUnitPaste('35 678901 234567 2', []).units).toEqual([{ serial: '', imei: IMEI_A, imei2: '' }]);
    expect(parseUnitPaste('356 789 012 345 672', []).units).toEqual([{ serial: '', imei: IMEI_A, imei2: '' }]);
  });

  it('dos series numéricas no se juntan en un IMEI aunque sumen 15 dígitos y pasen Luhn', () => {
    expect(parseUnitPaste('12345678 1234568', []).units).toEqual([
      { serial: '12345678', imei: '', imei2: '' },
      { serial: '1234568', imei: '', imei2: '' },
    ]);
  });
});

describe('revisión L2 · una serie repetida nunca pierde su IMEI en silencio', () => {
  it('la serie ya está en la línea sin IMEI: conflicto, con el IMEI enmascarado', () => {
    const preview = parseUnitPaste(`F2LXK1ABCD12 ${IMEI_A}`, [unit({ serial_number: 'F2LXK1ABCD12' })]);
    expect(preview.units).toEqual([]);
    expect(preview.already).toEqual([]);
    expect(preview.conflicts).toEqual(['serie ••••CD12 · IMEI ••••5672']);
  });

  it('la serie se repite en lo pegado con otro IMEI: entra la primera, la segunda es conflicto', () => {
    const preview = parseUnitPaste(`F2LXK1ABCD12 ${IMEI_A}\nF2LXK1ABCD12 ${IMEI_B}`, []);
    expect(preview.units).toEqual([{ serial: 'F2LXK1ABCD12', imei: IMEI_A, imei2: '' }]);
    expect(preview.duplicates).toEqual([]);
    expect(preview.conflicts).toEqual(['serie ••••CD12 · IMEI ••••7518']);
  });

  it('el mismo equipo, igual que en la línea: «ya está», sin conflicto', () => {
    const preview = parseUnitPaste(`F2LXK1ABCD12 ${IMEI_A}`, [unit({ serial_number: 'F2LXK1ABCD12', imei: IMEI_A })]);
    expect(preview.already).toEqual(['••••CD12']);
    expect(preview.conflicts).toEqual([]);
  });

  it('una serie y un IMEI de dos equipos distintos de la línea: conflicto', () => {
    const preview = parseUnitPaste(`F2LXK1ABCD12 ${IMEI_A}`, [unit({ serial_number: 'F2LXK1ABCD12', imei: IMEI_B }), unit({ serial_number: 'ZZZZ9999', imei: IMEI_A })]);
    expect(preview.units).toEqual([]);
    expect(preview.conflicts).toHaveLength(1);
  });
});
