/**
 * INV-COM-01A — el precio propio de un seminuevo y los detalles para el comprador viajan con la recepción.
 * Solo un artículo de línea «seminuevo» lleva precio y detalles; el precio exige autoridad sobre productos
 * (el servidor lo vuelve a pedir); sin precio el equipo se recibe y queda pendiente. Datos ficticios.
 */
import {
  blankUnit, buildReceiptBody, isUsedLine, newLine, priceIsValid, validateLines, type DraftLine, type ReceiptProduct,
} from '@/app/admin/lib/inventory-receipts';
import { commitRows, evaluateRows, type StagedRow } from '@/app/admin/lib/receive-list';

const used: ReceiptProduct = { id: 5, name: 'iPhone 13 128 GB Seminuevo', is_serialized: true, requires_imei: true, condition_line: 'used' };
const fresh: ReceiptProduct = { id: 6, name: 'iPhone 13 128 GB Nuevo', is_serialized: true, requires_imei: true, condition_line: 'new' };
const origin = { branch: 1, supplier: null, document_ref: '', notes: '' } as never;

function lineOf(product: ReceiptProduct, extra: Partial<ReturnType<typeof blankUnit>> = {}): DraftLine {
  const line = newLine(product);
  line.units = [{ ...blankUnit(product.condition_line === 'used' ? 'used' : 'new'), serial_number: 'FAKE000001', imei: '356938035643809', ...extra }];
  return line;
}
const sent = (line: DraftLine, opts: { costs: boolean; prices?: boolean }) => {
  const [body] = buildReceiptBody(origin, [line], opts).body.lines;
  return (body as { units: Record<string, unknown>[] }).units[0];
};

describe('lo que viaja', () => {
  it('un seminuevo con autoridad envía su precio y sus detalles para el comprador', () => {
    const unit = sent(lineOf(used, { price: ' 1899.00 ', publicNotes: ' Incluye cargador ' }), { costs: false, prices: true });
    expect(unit).toMatchObject({ sale_price: '1899.00', public_notes: 'Incluye cargador' });
  });

  it('sin autoridad sobre precios no se envía ningún precio, pero los detalles sí', () => {
    const unit = sent(lineOf(used, { price: '1899.00', publicNotes: 'Caja original' }), { costs: false, prices: false });
    expect(unit).not.toHaveProperty('sale_price');
    expect(unit).toMatchObject({ public_notes: 'Caja original' });
  });

  it('un artículo nuevo nunca lleva precio ni detalles propios', () => {
    const unit = sent(lineOf(fresh, { price: '1899.00', publicNotes: 'x' }), { costs: true, prices: true });
    expect(unit).not.toHaveProperty('sale_price');
    expect(unit).not.toHaveProperty('public_notes');
  });

  it('el costo y el precio son dos cosas: cada uno exige su propia autoridad', () => {
    const line = lineOf(used, { price: '1899.00', cost: '1200.00' });
    expect(sent(line, { costs: true, prices: false })).toMatchObject({ unit_cost: '1200.00' });
    expect(sent(line, { costs: true, prices: false })).not.toHaveProperty('sale_price');
    expect(sent(line, { costs: false, prices: true })).toMatchObject({ sale_price: '1899.00' });
    expect(sent(line, { costs: false, prices: true })).not.toHaveProperty('unit_cost');
  });

  it('solo las líneas seminuevas cuentan como tales', () => {
    expect(isUsedLine(used)).toBe(true);
    expect(isUsedLine(fresh)).toBe(false);
    expect(isUsedLine({ ...fresh, condition_line: undefined })).toBe(false);
  });
});

describe('qué se valida', () => {
  it.each([['1899.00', true], ['1899', true], ['0.01', true], ['0', false], ['0.00', false], ['-5', false], ['12.345', false], ['abc', false], ['1e3', false]])(
    'precio «%s» → %s', (raw, ok) => { expect(priceIsValid(raw)).toBe(ok); },
  );

  it('un precio mal escrito bloquea solo cuando se va a enviar', () => {
    const line = lineOf(used, { price: '0.00' });
    expect(Object.values(validateLines([line], { costs: false, prices: true })[line.key].units)[0]).toHaveProperty('price');
    expect(validateLines([line], { costs: false, prices: false })).toEqual({});
    const blank = lineOf(used, { price: '' });
    expect(validateLines([blank], { costs: false, prices: true })).toEqual({});
  });

  it('detalles para el comprador: hasta 500 caracteres', () => {
    const line = lineOf(used, { publicNotes: 'x'.repeat(501) });
    expect(Object.values(validateLines([line], { costs: false })[line.key].units)[0]).toHaveProperty('publicNotes');
  });
});

describe('de la lista pegada', () => {
  const row = (extra: Partial<StagedRow> = {}): StagedRow => ({
    id: 1, kind: 'device', line: 'used', model: 'iPhone 13', capacityGb: 128, colour: 'azul', ramGb: null, sim: '', region: '',
    serial: 'FAKE000001', imei: '356938035643809', imei2: '', condition: 'used', battery: '90', grade: '', notes: '', cost: '',
    price: '', publicNotes: '', quantity: '', source: '', hints: [], product: used, ...extra,
  });

  it('un precio inválido bloquea la fila; un precio ausente no', () => {
    const bad = evaluateRows({ rows: [row({ price: '0' })], lines: [], costs: false, prices: true })[0];
    expect(bad.bucket).toBe('invalid');
    expect(bad.issues.map((i) => i.field)).toContain('price');
    expect(evaluateRows({ rows: [row({ price: '' })], lines: [], costs: false, prices: true })[0].bucket).toBe('ready');
    // Sin autoridad el precio ni se mira (no se envía).
    expect(evaluateRows({ rows: [row({ price: '0' })], lines: [], costs: false, prices: false })[0].bucket).toBe('ready');
  });

  it('al agregar, el precio pasa a la recepción solo con autoridad', () => {
    const withPrice = row({ price: '1899.00', publicNotes: 'Caja original' });
    const yes = commitRows([withPrice], [], { costs: false, prices: true });
    expect(yes.lines[0].units[0]).toMatchObject({ price: '1899.00', publicNotes: 'Caja original' });
    const no = commitRows([withPrice], [], { costs: false, prices: false });
    expect(no.lines[0].units[0]).toMatchObject({ price: '', publicNotes: 'Caja original' });
  });

  it('un artículo nuevo ignora el precio de la fila', () => {
    const fresh_ = row({ product: fresh, line: 'new', condition: 'new', price: '1899.00', publicNotes: 'x' });
    expect(evaluateRows({ rows: [fresh_], lines: [], costs: false, prices: true })[0].bucket).toBe('ready');
    const done = commitRows([fresh_], [], { costs: false, prices: true });
    const [body] = buildReceiptBody(origin, done.lines, { costs: false, prices: true }).body.lines;
    expect((body as { units: Record<string, unknown>[] }).units[0]).not.toHaveProperty('sale_price');
  });
});
