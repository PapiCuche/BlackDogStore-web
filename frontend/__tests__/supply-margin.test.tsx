/**
 * SUPPLIER-MARGIN (decisión D2) — el realizado de lo vendido bajo pedido es al
 * costo real de la recepción; sin él la línea es «costo real pendiente», se
 * muestra aparte y nunca como margen realizado.
 */
import { act, render, screen } from '@testing-library/react';

import { OrderSupplyPanel } from '@/app/admin/components/OrderSupplyPanel';
import { SalesMarginSection } from '@/app/admin/components/SalesMarginSection';
import * as api from '@/app/admin/lib/internal-api';
import type { AdminOrderDetail } from '@/app/lib/admin';

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/admin/lib/internal-api', () => ({
  ...jest.requireActual('@/app/admin/lib/internal-api'),
  fetchSalesMargin: jest.fn(),
}));
const mocked = api as jest.Mocked<typeof api>;

const totals = (cost: string) => ({ lines: 1, net_sales: '100.00', cost, margin: '10.00', margin_percent: '10.00' });

const REPORT = {
  window_days: 30, sales: { orders: 2, gross: '236.00', net: '200.00' }, collected: { orders: 2, amount: '236.00' },
  net_sales_with_cost: '200.00', cost_of_goods_sold: '140.00', margin: '60.00', margin_percent: '30.00',
  pending_cost: { lines: 0, orders: 0, amount: '0.00' }, refunds: { orders: 0, amount: '0.00', net: '0.00' },
  estimated: totals('140.00'), realised: totals('60.00'),
  estimated_only: { lines: 1, net_sales: '100.00', reference_cost_lines: 0, on_order_not_received_lines: 1 },
  unknown_cost: { lines: 0, orders: 0, net_sales: '0.00' },
  real_cost_pending: { lines: 2, orders: 2, net_sales: '93.22', not_received_lines: 1, received_without_real_cost_lines: 1 },
  by_product: [], by_branch: [], by_channel: [],
} as unknown as api.SalesMargin;

function line(extra: Record<string, unknown>): AdminOrderDetail {
  return {
    id: 900, supply_status: 'received', can_manage_supply: false, can_receive_supply: false,
    items: [{ id: 2, product_id: 44, product_name: 'Cargador', quantity: 1, price: '110.00', subtotal: '110.00',
      supply_origin: 'supplier', supply_status: 'received', supplier_name: 'Mayorista', lead_time_days: 4,
      agreed_unit_cost: '70.0000', ...extra }],
  } as unknown as AdminOrderDetail;
}

it('el margen lista aparte el costo real pendiente', async () => {
  mocked.fetchSalesMargin.mockResolvedValue(REPORT);
  await act(async () => { render(<SalesMarginSection companyId={7} branchId={null} canSeeCosts />); });
  const box = screen.getByTestId('margin-real-cost-pending');
  expect(box).toHaveTextContent('Costo real pendiente');
  expect(box).toHaveTextContent('2 líneas');
  expect(box).toHaveTextContent('1 sin recibir · 1 recibidas sin costo real registrado');
  expect(box).toHaveTextContent('fuera del realizado');
});

it('sin líneas pendientes no aparece', async () => {
  mocked.fetchSalesMargin.mockResolvedValue({ ...REPORT, real_cost_pending: { ...REPORT.real_cost_pending!, lines: 0 } });
  await act(async () => { render(<SalesMarginSection companyId={7} branchId={null} canSeeCosts />); });
  expect(screen.queryByTestId('margin-real-cost-pending')).toBeNull();
});

it('una línea recibida dice su costo real, su origen o que está pendiente', () => {
  const { unmount } = render(<OrderSupplyPanel order={line({ received_unit_cost: '70.0000', received_cost_source: 'agreed',
    unit_cost_difference: '0.0000' })} onChanged={jest.fn()} />);
  expect(screen.getByLabelText('Costos de la línea')).toHaveTextContent('Costo real S/ 70.00 (al costo pactado)');
  unmount();
  render(<OrderSupplyPanel order={line({ received_unit_cost: null, received_cost_source: '' })} onChanged={jest.fn()} />);
  expect(screen.getByLabelText('Costos de la línea')).toHaveTextContent('Costo real pendiente');
});
