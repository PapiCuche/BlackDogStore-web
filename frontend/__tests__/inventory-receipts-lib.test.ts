/**
 * INV-OPS-01 — reglas y cliente del asistente «Recibir mercadería».
 */
import {
  limitProblem, MAX_DEVICES, MAX_LINES,
  blankUnit, buildReceiptBody, confirmReceipt, fingerprint, forgetStashedDraft, newLine, newReceiptKey,
  previewReceipt, ReceiptApiError, stashDraft, takeStashedDraft, validateLines,
  type DraftLine, type ReceiptOrigin, type ReceiptProduct,
} from '@/app/admin/lib/inventory-receipts';
import { fetchWithAuth } from '@/app/lib/auth';

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const mockFetch = fetchWithAuth as jest.Mock;

const phone: ReceiptProduct = { id: 5, name: 'iPhone 15', is_serialized: true, requires_imei: true };
const cable: ReceiptProduct = { id: 9, name: 'Cable USB-C', is_serialized: false, requires_imei: false };
const origin: ReceiptOrigin = { branch: 2, supplier: null, document_ref: ' F001-23 ', notes: '' };

function phoneLine(units: Partial<ReturnType<typeof blankUnit>>[]): DraftLine {
  const line = newLine(phone);
  return { ...line, units: units.map((u) => ({ ...blankUnit(), ...u })) };
}

describe('validateLines', () => {
  it('pide serie, y IMEI cuando el producto lo exige, fila por fila', () => {
    const line = phoneLine([{ serial_number: 'A1' }, { serial_number: '', imei: '356789012345678' }]);
    const problems = validateLines([line], { costs: false })[line.key];
    expect(problems.units[line.units[0].key].imei).toMatch(/Falta el IMEI/);
    expect(problems.units[line.units[1].key].serial_number).toMatch(/Falta el número de serie/);
  });

  it('ignora filas en blanco y pide al menos un equipo', () => {
    const line = phoneLine([{}]);
    expect(validateLines([line], { costs: false })[line.key].line).toMatch(/al menos un equipo/);
  });

  it('marca series e IMEI repetidos dentro de toda la recepción, aunque sea en otra línea', () => {
    const a = phoneLine([{ serial_number: 'A1', imei: '111' }]);
    const b = phoneLine([{ serial_number: 'a1', imei: '222' }]);
    const out = validateLines([a, b], { costs: false });
    expect(out[a.key].units[a.units[0].key].serial_number).toMatch(/repetido/);
    expect(out[b.key].units[b.units[0].key].serial_number).toMatch(/repetido/);
  });

  it('cantidad entera mínima 1 y costo con dos decimales sólo si se ven costos', () => {
    const line = { ...newLine(cable), quantity: '0', unitCost: '1.234' };
    expect(validateLines([line], { costs: true })[line.key]).toMatchObject({
      quantity: expect.stringMatching(/entero/), unitCost: expect.stringMatching(/2 decimales/),
    });
    expect(validateLines([{ ...line, quantity: '3' }], { costs: false })).toEqual({});
    expect(validateLines([{ ...line, quantity: '2.5' }], { costs: false })[line.key].quantity).toBeDefined();
  });
});

describe('contrato real del servidor', () => {
  it('rechaza identificadores con tildes o caracteres fuera de ASCII', () => {
    const line = phoneLine([{ serial_number: 'Ñ123', imei: '356789012345678' }]);
    expect(validateLines([line], { costs: false })[line.key].units[line.units[0].key].serial_number).toMatch(/sin tilde/);
  });

  it('el costo admite hasta 8 enteros y 2 decimales', () => {
    const ok = { ...newLine(cable), quantity: '1', unitCost: '12345678.50' };
    const bad = { ...newLine(cable), quantity: '1', unitCost: '123456789' };
    expect(validateLines([ok], { costs: true })).toEqual({});
    expect(validateLines([bad], { costs: true })[bad.key].unitCost).toBeDefined();
  });

  it('avisa de los topes por petición: 50 líneas y 200 equipos', () => {
    const many = Array.from({ length: MAX_LINES + 1 }, () => ({ ...newLine(cable), quantity: '1' }));
    expect(limitProblem(many)).toMatch(/hasta 50 artículos/);
    const units = phoneLine(Array.from({ length: MAX_DEVICES + 1 }, (_, i) => ({ serial_number: `S${i}` })));
    expect(limitProblem([units])).toMatch(/hasta 200 equipos/);
    expect(limitProblem([{ ...newLine(cable), quantity: '1' }])).toBeNull();
  });

  it('dice qué equipo de cada línea se mandó, para ubicar lo que el servidor señale', () => {
    const a = phoneLine([{ serial_number: 'A' }, {}, { serial_number: 'B' }]);
    const { unitKeys } = buildReceiptBody(origin, [a, { ...newLine(cable), quantity: '2' }], { costs: false });
    expect(unitKeys).toEqual([[a.units[0].key, a.units[2].key], []]);
  });

  it('conserva código, línea y la revisión completa de un 400 invalid_lines', async () => {
    const review = { ok: false, lines: [{ index: 0 }], detail: 'Hay líneas que no se pueden recibir. No se recibió nada.', code: 'invalid_lines' };
    mockFetch.mockResolvedValue({ ok: false, status: 400, json: async () => review });
    const err = await confirmReceipt(buildReceiptBody(origin, [{ ...newLine(cable), quantity: '1' }], { costs: false }).body, 'k').catch((e) => e);
    expect(err.code).toBe('invalid_lines');
    expect(err.preview).toEqual(review);
    mockFetch.mockResolvedValue({ ok: false, status: 400, json: async () => ({ detail: 'Cantidad no válida.', code: 'invalid', line: 2 }) });
    const other = await previewReceipt(buildReceiptBody(origin, [{ ...newLine(cable), quantity: '1' }], { costs: false }).body).catch((e) => e);
    expect(other).toMatchObject({ code: 'invalid', line: 2, preview: null, ambiguous: false });
  });
});

describe('buildReceiptBody', () => {
  it('arma líneas por tipo, recorta textos y no manda costos sin permiso', () => {
    const units = phoneLine([{ serial_number: ' S1 ', imei: '356', cost: '10.50' }, {}]);
    const qty = { ...newLine(cable), quantity: '4', unitCost: '2.5' };
    const { body, lineKeys } = buildReceiptBody(origin, [units, qty], { costs: false });
    expect(body).toEqual({
      branch: 2, supplier: null, document_ref: 'F001-23', notes: '',
      lines: [
        { kind: 'units', product: 5, units: [{ serial: 'S1', imei: '356', condition: 'new' }] },
        { kind: 'quantity', product: 9, quantity: 4 },
      ],
    });
    expect(lineKeys).toEqual([units.key, qty.key]);
    const withCosts = buildReceiptBody(origin, [units, qty], { costs: true }).body;
    expect(withCosts.lines[0]).toMatchObject({ units: [{ unit_cost: '10.50' }] });
    expect(withCosts.lines[1]).toMatchObject({ unit_cost: '2.5' });
  });

  it('un equipo seminuevo abre en «Usado»', () => {
    expect(newLine({ ...phone, condition_line: 'used' }).units[0].condition).toBe('used');
  });

  it('la huella cambia con cualquier cambio del cuerpo', () => {
    const a = buildReceiptBody(origin, [{ ...newLine(cable), quantity: '1' }], { costs: false }).body;
    const b = buildReceiptBody(origin, [{ ...newLine(cable), quantity: '2' }], { costs: false }).body;
    expect(fingerprint(a)).not.toEqual(fingerprint(b));
    expect(fingerprint(a)).toEqual(fingerprint(JSON.parse(JSON.stringify(a))));
  });
});

describe('cliente', () => {
  const body = buildReceiptBody(origin, [{ ...newLine(cable), quantity: '1' }], { costs: false }).body;
  beforeEach(() => mockFetch.mockReset());

  it('previsualiza sin Idempotency-Key y confirma con ella', async () => {
    mockFetch.mockResolvedValue({ ok: true, status: 200, json: async () => ({ ok: true }) });
    await previewReceipt(body);
    await confirmReceipt(body, 'rcv-abc');
    expect(mockFetch.mock.calls[0][0]).toMatch(/\/admin\/inventory\/receipts\/preview\/$/);
    expect(mockFetch.mock.calls[0][1].headers).toEqual({});
    expect(mockFetch.mock.calls[1][0]).toMatch(/\/receipts\/confirm\/$/);
    expect(mockFetch.mock.calls[1][1].headers).toEqual({ 'Idempotency-Key': 'rcv-abc' });
  });

  it('muestra el detalle del servidor en 400/409', async () => {
    mockFetch.mockResolvedValue({ ok: false, status: 409, json: async () => ({ detail: 'La sucursal ya no existe.' }) });
    await expect(previewReceipt(body)).rejects.toMatchObject({ message: 'La sucursal ya no existe.', status: 409, ambiguous: false });
  });

  it('una caída al confirmar es ambigua; al previsualizar no', async () => {
    mockFetch.mockRejectedValue(new TypeError('network'));
    await expect(confirmReceipt(body, 'k')).rejects.toMatchObject({ ambiguous: true });
    await expect(previewReceipt(body)).rejects.toMatchObject({ ambiguous: false });
    mockFetch.mockResolvedValue({ ok: false, status: 502, json: async () => null });
    const err = await confirmReceipt(body, 'k').catch((e) => e);
    expect(err).toBeInstanceOf(ReceiptApiError);
    expect(err.ambiguous).toBe(true);
  });
});

describe('newReceiptKey', () => {
  it('usa randomUUID, cabe en 64 y nunca Math.random', () => {
    const spy = jest.spyOn(Math, 'random');
    const key = newReceiptKey();
    expect(key).toMatch(/^rcv-/);
    expect(key!.length).toBeLessThanOrEqual(64);
    expect(newReceiptKey()).not.toEqual(key);
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });

  it('sin fuente segura devuelve null', () => {
    const original = Object.getOwnPropertyDescriptor(globalThis, 'crypto');
    Object.defineProperty(globalThis, 'crypto', { value: {}, configurable: true });
    expect(newReceiptKey()).toBeNull();
    if (original) Object.defineProperty(globalThis, 'crypto', original);
  });
});

describe('borrador en memoria', () => {
  afterEach(forgetStashedDraft);
  it('se entrega una sola vez y caduca', () => {
    const line = newLine(cable);
    stashDraft(origin, [line]);
    expect(takeStashedDraft()?.lines).toEqual([line]);
    expect(takeStashedDraft()).toBeNull();
    stashDraft(origin, [line]);
    expect(takeStashedDraft(Date.now() + 31 * 60 * 1000)).toBeNull();
  });
});
