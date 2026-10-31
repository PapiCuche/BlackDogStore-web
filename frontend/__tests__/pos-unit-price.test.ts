/**
 * INV-COM-01C — un seminuevo se cobra en caja a su precio de ficha.
 *
 * Lo que fijan estas pruebas del lado del panel (el precio lo decide el servidor,
 * `test_pos_unit_price.py`): la cesta suma el precio de cada equipo, un equipo sin
 * precio no entra ni se cobra, la vista previa nombra los equipos, y un 409
 * `price_changed` llega como error propio con el total nuevo.
 */
import { PosPriceChangedError, posSale } from '@/app/admin/lib/internal-api';
import {
  lineTotal, previewItems, saleItems, UNPRICED_UNIT_MESSAGE, unitPriceValid, unpricedUnits, withProduct, withUnit,
  type LineProduct,
} from '@/app/admin/lib/unit-entry';

const USED: LineProduct = {
  id: 5, name: 'iPhone 15 128 GB Seminuevo', price: '0.00', available: 3, is_serialized: true, requires_imei: true, priced_by: 'unit',
};
const NEW_PHONE: LineProduct = {
  id: 6, name: 'iPhone 16 128 GB', price: '3500.00', available: 3, is_serialized: true, requires_imei: true, priced_by: 'product',
};
const found = (id: number, sale_price: string | null | undefined, extra = {}) => ({
  unit_id: id, serial_number: `S${id}`, imei: `35693803564${id}`, priced_by: 'unit' as const, sale_price, ...extra,
});

describe('unitPriceValid', () => {
  it.each(['1800.00', '1', '0.50'])('%s sirve', (price) => expect(unitPriceValid(price)).toBe(true));
  it.each([null, undefined, '', '0', '0.00', '-5', 'abc', '10.999'])('%s no sirve', (price) => expect(unitPriceValid(price)).toBe(false));
});

describe('dos seminuevos de una variante con precios distintos', () => {
  const lines = withUnit(withUnit([], found(1, '2100.00'), USED, 'scanner'), found(2, '2450.00', { battery_health: 91 }), USED, 'scanner');

  it('se quedan en una línea, cada uno con su precio y su ficha', () => {
    expect(lines).toHaveLength(1);
    expect(lines[0].units.map((u) => u.price)).toEqual(['2100.00', '2450.00']);
    expect(lines[0].units[1].battery_health).toBe(91);
  });

  it('la cesta suma los precios de los equipos, no el de la variante', () => {
    expect(lineTotal(lines[0])).toBe(4550);
  });

  it('la vista previa y la venta nombran los equipos', () => {
    const named = [{ id: 1, method: 'scanner' }, { id: 2, method: 'scanner' }];
    expect(previewItems(lines)).toEqual([{ product: 5, quantity: 2, units: named }]);
    expect(saleItems(lines)).toEqual([{ product: 5, quantity: 2, units: named }]);
  });
});

describe('un equipo sin precio', () => {
  it.each([null, undefined, '0.00'])('%s: no entra en la venta y dice qué hacer', (price) => {
    expect(() => withUnit([], found(3, price), USED, 'scanner')).toThrow(UNPRICED_UNIT_MESSAGE);
  });

  it('una línea que ya lo traía lo cuenta como sin precio y no lo suma', () => {
    const line = withProduct([], USED)[0];
    const broken = { ...line, units: [{ id: 9, method: 'scanner' as const, serial_number: 'X', imei: '', price: null }], quantity: 1 };
    expect(unpricedUnits([broken])).toBe(1);
    expect(lineTotal(broken)).toBe(0);
  });
});

describe('un equipo nuevo y un accesorio siguen al precio del producto', () => {
  it('el equipo nuevo se suma por precio × cantidad y no se pide precio de equipo', () => {
    const lines = withUnit([], { unit_id: 20, serial_number: 'N20', imei: '356938035643809', priced_by: 'product' }, NEW_PHONE, 'scanner');
    expect(lineTotal(lines[0])).toBe(3500);
    expect(unpricedUnits(lines)).toBe(0);
    expect(saleItems(lines)).toEqual([{ product: 6, quantity: 1, units: [{ id: 20, method: 'scanner' }] }]);
  });

  it('un accesorio no lleva equipos en la vista previa', () => {
    const lines = withProduct([], { id: 8, name: 'Funda', price: '49.90', available: 10 });
    expect(previewItems(lines)).toEqual([{ product: 8, quantity: 1 }]);
    expect(lineTotal(lines[0])).toBeCloseTo(49.9);
  });
});

describe('409 price_changed', () => {
  afterEach(() => { jest.restoreAllMocks(); });

  function answer(status: number, body: unknown) {
    document.cookie = 'csrftoken=csrf';
    global.fetch = jest.fn(async () => ({
      status, ok: status < 400, headers: { get: () => null }, clone() { return this; }, json: () => Promise.resolve(body),
    })) as unknown as typeof fetch;
  }

  it('llega como error propio con los dos totales y la vista previa nueva', async () => {
    const preview = { total: '1900.00', lines: [] };
    answer(409, { detail: 'El precio cambió.', code: 'price_changed', expected_total: '1800.00', current_total: '1900.00', preview });
    const error = await posSale(3, { idempotency_key: 'k' } as never).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(PosPriceChangedError);
    const changed = error as PosPriceChangedError;
    expect([changed.expected, changed.current]).toEqual(['1800.00', '1900.00']);
    expect(changed.preview).toEqual(preview);
  });

  it('otro 409 no se confunde con él', async () => {
    answer(409, { detail: 'Ya existe.', existing_order: 4 });
    const error = await posSale(3, { idempotency_key: 'k' } as never).catch((e: unknown) => e);
    expect(error).not.toBeInstanceOf(PosPriceChangedError);
  });
});
