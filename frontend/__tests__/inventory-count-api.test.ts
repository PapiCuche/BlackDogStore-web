/**
 * MIGRATION-D — `counts-api`: lo que viaja y cómo se leen los rechazos.
 */
import * as api from '@/app/admin/lib/counts-api';
import { fetchWithAuth } from '@/app/lib/auth';

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const mockFetch = fetchWithAuth as jest.Mock;

const reply = (status: number, body: unknown) => ({ ok: status < 400, status, json: async () => body });

beforeEach(() => mockFetch.mockReset());

describe('counts-api', () => {
  it('el incremento viaja con el valor mostrado y un 409 trae la línea actual sin reintentar', async () => {
    mockFetch.mockResolvedValueOnce(reply(409, {
      code: 'expected_mismatch', detail: 'El valor cambió.', line: { product: 5, physical_quantity: 3 },
    }));
    const err = await api.scanCount(9, { code: '7750001', mode: 'increment', expected: 2 }).catch((e) => e);
    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [url, init] = mockFetch.mock.calls[0];
    expect(String(url)).toMatch(/\/admin\/inventory\/counts\/9\/scan\/$/);
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({ code: '7750001', mode: 'increment', expected: 2 });
    expect(err).toBeInstanceOf(api.CountApiError);
    expect(err.status).toBe(409);
    expect(err.code).toBe('expected_mismatch');
    expect(err.line.physical_quantity).toBe(3);
  });

  it('en el servidor de hoy el total no lleva campos nuevos', async () => {
    mockFetch.mockResolvedValueOnce(reply(200, { id: 9, items: [] }));
    await api.setCountLines(9, [{ product: 5, physical_quantity: 0, entry: 'correction', entry_note: 'x' }], false);
    const [url, init] = mockFetch.mock.calls[0];
    expect(String(url)).toMatch(/\/counts\/9\/items\/$/);
    expect(init.method).toBe('PUT');
    expect(JSON.parse(init.body)).toEqual([{ product: 5, physical_quantity: 0 }]);
  });

  it('con el contrato de C el total lleva `entry`', async () => {
    mockFetch.mockResolvedValueOnce(reply(200, { id: 9, items: [] }));
    await api.setCountLines(9, [{ product: 5, physical_quantity: 4, entry: 'correction', entry_note: 'tecleo' }], true);
    expect(JSON.parse(mockFetch.mock.calls[0][1].body)).toEqual([
      { product: 5, physical_quantity: 4, entry: 'correction', entry_note: 'tecleo' },
    ]);
  });

  it('un serializado se reconoce por su código', async () => {
    mockFetch.mockResolvedValueOnce(reply(400, {
      code: 'serialized_product', detail: 'Es un equipo.', product: { id: 40, name: 'iPhone 15' },
    }));
    const err = await api.scanCount(9, { code: '3567', mode: 'lookup' }).catch((e) => e);
    expect(err.code).toBe('serialized_product');
    expect(err.product).toEqual({ id: 40, name: 'iPhone 15' });
  });

  it('la búsqueda del recuento usa su propia ruta (nombre o código)', async () => {
    mockFetch.mockResolvedValueOnce(reply(200, { results: [{ id: 5, name: 'Cable', is_serialized: false }] }));
    const hits = await api.searchCountProducts(9, '7750 01');
    expect(String(mockFetch.mock.calls[0][0])).toMatch(/\/counts\/9\/products\/\?search=7750(\+|%20)01$/);
    expect(hits).toEqual([{ id: 5, name: 'Cable', is_serialized: false }]);
  });

  it('el contrato se detecta por `kind`', () => {
    expect(api.supportsCountContract({ kind: 'regular' })).toBe(true);
    expect(api.supportsCountContract({})).toBe(false);
    expect(api.supportsCountContract(null)).toBe(false);
  });

  it('no hay ninguna función de aprobación en este módulo', () => {
    expect(Object.keys(api).some((k) => /approv/i.test(k))).toBe(false);
  });
});
