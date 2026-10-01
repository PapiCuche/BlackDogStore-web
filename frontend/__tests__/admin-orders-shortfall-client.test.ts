/**
 * INV-04 · el cliente de órdenes: filtro de faltante y reproceso de salida.
 *
 * Se afirma QUÉ sale hacia el servidor y cómo se mapea cada respuesta, contra
 * un `fetchWithAuth` falso.
 */

const mockFetch = jest.fn();
jest.mock('@/app/lib/auth', () => ({
  fetchWithAuth: (...a: unknown[]) => mockFetch(...a),
}));

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { reprocessOrderStockExit, fetchAdminOrders } = require('@/app/lib/admin');

function reply(status: number, body: unknown = {}): Response {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

beforeEach(() => mockFetch.mockReset());

describe('reprocessOrderStockExit · mapeo de respuestas', () => {
  it('200 devuelve el detalle recomputado', async () => {
    mockFetch.mockResolvedValue(
      reply(200, { id: 7, stock_shortfall: [], can_reprocess_stock_exit: false, created_movements: 2 }),
    );
    const res = await reprocessOrderStockExit(7);
    expect(res.created_movements).toBe(2);
    const [url, init] = mockFetch.mock.calls[0];
    expect(String(url)).toContain('/admin/orders/7/reprocess-stock-exit/');
    expect(init.method).toBe('POST');
  });

  it('403 → error de permisos', async () => {
    mockFetch.mockResolvedValue(reply(403));
    await expect(reprocessOrderStockExit(7)).rejects.toThrow(/permisos/i);
  });

  it('404 → orden no encontrada', async () => {
    mockFetch.mockResolvedValue(reply(404));
    await expect(reprocessOrderStockExit(7)).rejects.toThrow(/no encontrada/i);
  });

  it('409 → conflicto de estado, con el detalle del servidor', async () => {
    mockFetch.mockResolvedValue(reply(409, { detail: 'Solo un pedido pagado puede reprocesar su salida de stock.' }));
    await expect(reprocessOrderStockExit(7)).rejects.toThrow(/Solo un pedido pagado/);
  });
});

describe('fetchAdminOrders · filtro de faltante', () => {
  it('propaga shortfall=true al backend', async () => {
    mockFetch.mockResolvedValue(reply(200, { results: [], count: 0, page: 1, page_size: 25 }));
    await fetchAdminOrders({ shortfall: 'true' });
    expect(String(mockFetch.mock.calls[0][0])).toContain('shortfall=true');
  });

  it('no envía shortfall cuando no se pide', async () => {
    mockFetch.mockResolvedValue(reply(200, { results: [], count: 0, page: 1, page_size: 25 }));
    await fetchAdminOrders({});
    expect(String(mockFetch.mock.calls[0][0])).not.toContain('shortfall');
  });
});
