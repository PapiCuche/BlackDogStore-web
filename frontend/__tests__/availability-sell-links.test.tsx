import { render, screen, within } from '@testing-library/react';

import { AvailabilityPanel, POS_HREF } from '@/app/admin/components/AvailabilityPanel';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * SUPPLIER-DEVICE-FLOW-02 · B — «Vender en caja» desde la disponibilidad.
 *
 * Stock propio: vender en caja. Sin stock propio pero con una oferta vigente de
 * un producto «Bajo pedido»: vender bajo pedido en caja. Nada de eso: nada que
 * vender. Ningún costo en la pantalla.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

const counts = (o: Partial<Record<string, number>> = {}) => ({
  available: 0, reserved_web: 0, reserved_staff: 0, sold: 0, written_off: 0, ...o,
});

const OFFER = { supplier_name: 'Mayorista Lima', commercial_price: '4400.00', remaining: 6, valid_until: '2026-10-21', lead_time_days: 3 };

function product(id: number, name: string, overrides: Record<string, unknown> = {}) {
  return {
    id, name, barcode: '', model: null, category: null, line: 'new', line_label: 'Nuevo', options: [],
    is_serialized: false, price: '100.00', unit_price_range: null, sale_mode: 'stock_or_order', sells_on_order: true,
    is_published_online: true, stock: { by_branch: [], total: counts() }, supplier_offers: [], ...overrides,
  };
}

beforeEach(() => {
  send.mockReset();
  send.mockImplementation(async () => ({
    ok: true, status: 200,
    json: async () => ({
      count: 3, page: 1, page_size: 25, can_see_suppliers: true,
      scope: { branch: null, branches: [{ id: 1, name: 'Centro' }], is_aggregate: true },
      results: [
        product(1, 'Con stock', { stock: { by_branch: [], total: counts({ available: 2 }) }, supplier_offers: [OFFER] }),
        product(2, 'Bajo pedido', { supplier_offers: [OFFER] }),
        product(3, 'Sólo stock, sin stock', { sells_on_order: false, supplier_offers: [OFFER] }),
      ],
    }),
  }) as Response);
});

test('own stock sells at the till; an on-order offer sells on order there; nothing else does', async () => {
  render(<AvailabilityPanel pollMs={60_000} />);
  const own = await screen.findByTestId('availability-row-1');
  expect(within(own).getByRole('link', { name: 'Vender en caja: Con stock' })).toHaveAttribute('href', POS_HREF);
  expect(POS_HREF).toBe('/admin/sales/pos');
  expect(within(screen.getByTestId('availability-row-2')).getByRole('link', { name: 'Vender bajo pedido en caja: Bajo pedido' }))
    .toHaveAttribute('href', '/admin/sales/pos');
  expect(within(screen.getByTestId('availability-row-3')).queryByRole('link')).toBeNull();
  expect(document.body.textContent).not.toMatch(/costo/i);
});
