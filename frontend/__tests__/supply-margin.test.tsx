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
  estimated_only: { lines: 2, net_sales: '100.00', reference_cost_lines: 0, on_order_not_received_lines: 1,
    on_order_without_real_cost_lines: 1 },
  unknown_cost: { lines: 0, orders: 0, net_sales: '0.00' },
  real_cost_pending: { lines: 3, orders: 3, net_sales: '93.22', not_received_lines: 1, to_confirm_lines: 1,
    received_without_real_cost_lines: 1 },
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
  expect(box).toHaveTextContent('3 líneas');
  expect(box).toHaveTextContent('1 sin recibir · 1 por confirmar · 1 recibidas sin costo real registrado');
  // «Sólo estimado» says the two apart: not received, and received without a real cost.
  expect(screen.getByTestId('margin-estimated-only')).toHaveTextContent(
    '1 bajo pedido sin recibir · 1 recibidas sin costo real');
  expect(box).toHaveTextContent('fuera del realizado');
});

it('sin líneas pendientes no aparece', async () => {
  mocked.fetchSalesMargin.mockResolvedValue({ ...REPORT, real_cost_pending: { ...REPORT.real_cost_pending!, lines: 0 } });
  await act(async () => { render(<SalesMarginSection companyId={7} branchId={null} canSeeCosts />); });
  expect(screen.queryByTestId('margin-real-cost-pending')).toBeNull();
});

it('una línea recibida dice su costo real, su origen o que está pendiente', () => {
  const { unmount } = render(<OrderSupplyPanel order={line({ received_unit_cost: '70.0000', received_cost_source: 'real',
    unit_cost_difference: '0.0000' })} onChanged={jest.fn()} />);
  expect(screen.getByLabelText('Costos de la línea')).toHaveTextContent('Costo real S/ 70.00 · Sin diferencia');
  unmount();
  render(<OrderSupplyPanel order={line({ received_unit_cost: null, received_cost_source: 'agreed' })} onChanged={jest.fn()} />);
  expect(screen.getByLabelText('Costos de la línea')).toHaveTextContent('Costo real pendiente');
});

it('al recibir, el costo real vacío queda pendiente y así se dice', () => {
  const { fireEvent } = jest.requireActual('@testing-library/react');
  render(<OrderSupplyPanel order={{ ...line({ supply_status: 'pending', received_unit_cost: null }),
    supply_status: 'pending', can_receive_supply: true } as AdminOrderDetail} onChanged={jest.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: 'Recibir del proveedor' }));
  expect(screen.getByText(/costo real queda pendiente hasta registrarlo/)).toBeInTheDocument();
});

describe('registrar costo real', () => {
  const send = jest.requireMock('@/app/lib/auth').fetchWithAuth as jest.Mock;
  let writes: { path: string; body: unknown }[];
  let answer: { status: number; body: unknown };

  beforeEach(() => {
    writes = [];
    answer = { status: 200, body: { id: 2, supply_status: 'received', received_unit_cost: '64.0000', received_cost_source: 'real' } };
    send.mockReset();
    send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = String(input);
      if ((init.method ?? 'GET') !== 'GET') {
        writes.push({ path: url.replace(/^.*\/api/, ''), body: JSON.parse(String(init.body ?? '{}')) });
        return { ok: answer.status < 300, status: answer.status, json: async () => answer.body } as Response;
      }
      return { ok: true, status: 200, json: async () => line({ received_unit_cost: '64.0000', received_cost_source: 'real' }) } as Response;
    });
  });

  const pending = (flags = {}) => line({ received_unit_cost: null, received_cost_source: 'agreed', ...flags });

  it('con costs.view y permiso de recibir, se registra en una línea pendiente', async () => {
    const onChanged = jest.fn();
    render(<OrderSupplyPanel order={{ ...pending(), can_receive_supply: true } as AdminOrderDetail} onChanged={onChanged} />);
    const { fireEvent } = jest.requireActual('@testing-library/react');
    fireEvent.change(screen.getByLabelText('Costo real por unidad'), { target: { value: '64.00' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Registrar costo real' })); });
    expect(writes).toEqual([{ path: '/admin/orders/900/supply/2/real-cost/', body: { unit_cost: '64.00' } }]);
    expect(onChanged).toHaveBeenCalled();
  });

  it('el rechazo del servidor se dice (ya registrado)', async () => {
    answer = { status: 409, body: { detail: 'Esta línea ya tiene su costo real registrado; no se sobrescribe.', code: 'already_real' } };
    render(<OrderSupplyPanel order={{ ...pending(), can_receive_supply: true } as AdminOrderDetail} onChanged={jest.fn()} />);
    const { fireEvent } = jest.requireActual('@testing-library/react');
    fireEvent.change(screen.getByLabelText('Costo real por unidad'), { target: { value: '60' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Registrar costo real' })); });
    expect(screen.getByRole('alert')).toHaveTextContent('no se sobrescribe');
  });

  it('sin costs.view, sin permiso de recibir o con el costo ya registrado no aparece', () => {
    const { unmount } = render(<OrderSupplyPanel order={{ ...line({ agreed_unit_cost: undefined, received_unit_cost: undefined }), can_receive_supply: true } as AdminOrderDetail} onChanged={jest.fn()} />);
    expect(screen.queryByRole('button', { name: 'Registrar costo real' })).toBeNull();
    unmount();
    const second = render(<OrderSupplyPanel order={pending()} onChanged={jest.fn()} />);
    expect(screen.queryByRole('button', { name: 'Registrar costo real' })).toBeNull();
    second.unmount();
    render(<OrderSupplyPanel order={{ ...line({ received_unit_cost: '64.0000', received_cost_source: 'real' }), can_receive_supply: true } as AdminOrderDetail} onChanged={jest.fn()} />);
    expect(screen.queryByRole('button', { name: 'Registrar costo real' })).toBeNull();
  });
});
