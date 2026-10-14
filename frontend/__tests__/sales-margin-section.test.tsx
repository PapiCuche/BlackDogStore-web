/**
 * DEVICE-COMMERCE-01 · DC1 — «Margen comercial» en el resumen de ventas.
 *
 * Lo que se fija aquí: la sección se llama «margen comercial» y nunca
 * «utilidad»; avisa cuando hay ventas con costo pendiente; y sólo se pide al
 * servidor cuando quien mira tiene `costs.view` (las cifras las decide el
 * servidor, `test_margin_analytics.py`).
 */
import { act, render, screen, within } from '@testing-library/react';

import { SalesMarginSection } from '@/app/admin/components/SalesMarginSection';
import * as api from '@/app/admin/lib/internal-api';

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/admin/lib/internal-api', () => ({
  ...jest.requireActual('@/app/admin/lib/internal-api'),
  fetchSalesMargin: jest.fn(),
}));

const mocked = api as jest.Mocked<typeof api>;

const REPORT: api.SalesMargin = {
  window_days: 30,
  sales: { orders: 3, gross: '354.00', net: '300.00' },
  collected: { orders: 3, amount: '354.00' },
  net_sales_with_cost: '250.00',
  cost_of_goods_sold: '150.00',
  margin: '100.00',
  margin_percent: '40.00',
  pending_cost: { lines: 1, orders: 1, amount: '50.00' },
  refunds: { orders: 1, amount: '118.00' },
  by_product: [
    { key: 4, name: 'Funda', units: 5, net_sales: '250.00', cost: '150.00', margin: '100.00', margin_percent: '40.00', pending_lines: 0 },
  ],
  by_branch: [
    { key: 1, name: 'Centro', units: 5, net_sales: '250.00', cost: '150.00', margin: '100.00', margin_percent: '40.00', pending_lines: 1 },
  ],
  by_channel: [
    { key: 'pos', name: 'Punto de venta', units: 5, net_sales: '250.00', cost: '150.00', margin: '100.00', margin_percent: '40.00', pending_lines: 1 },
  ],
};

async function show(props: Partial<React.ComponentProps<typeof SalesMarginSection>> = {}) {
  await act(async () => {
    render(<SalesMarginSection companyId={7} branchId={null} canSeeCosts {...props} />);
  });
}

beforeEach(() => mocked.fetchSalesMargin.mockReset().mockResolvedValue(REPORT));

describe('Margen comercial', () => {
  it('sin `costs.view` no aparece ni se pide', async () => {
    await show({ canSeeCosts: false });
    expect(screen.queryByRole('region', { name: 'Margen comercial' })).toBeNull();
    expect(mocked.fetchSalesMargin).not.toHaveBeenCalled();
  });

  it('con `costs.view` enseña ventas netas, costo y margen, y nunca dice «utilidad»', async () => {
    await show();
    expect(mocked.fetchSalesMargin).toHaveBeenCalledWith(7, null);
    const region = screen.getByRole('region', { name: 'Margen comercial' });
    expect(within(region).getByText('Margen comercial estimado')).toBeInTheDocument();
    expect(within(region).getByTestId('margin-percent')).toHaveTextContent('40.00 %');
    expect(within(region).getByText('Ventas netas (sin IGV)')).toBeInTheDocument();
    expect(within(region).getByText('Costo de lo vendido')).toBeInTheDocument();
    expect(within(region).getByText('Cobros confirmados')).toBeInTheDocument();
    expect(within(region).getByText('Reembolsos')).toBeInTheDocument();
    expect(region.textContent).not.toMatch(/utilidad/i);
    expect(within(region).getByRole('cell', { name: 'Funda' })).toBeInTheDocument();
  });

  it('avisa del costo pendiente', async () => {
    await show();
    expect(screen.getByRole('status')).toHaveTextContent(/1 línea.*costo pendiente/i);
  });

  it('sin costo pendiente no hay aviso', async () => {
    mocked.fetchSalesMargin.mockResolvedValue({ ...REPORT, pending_cost: { lines: 0, orders: 0, amount: '0.00' } });
    await show();
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('un margen sin ventas con costo se muestra como «—», no como 0 %', async () => {
    mocked.fetchSalesMargin.mockResolvedValue({ ...REPORT, margin_percent: null });
    await show();
    expect(screen.queryByText('0.00 %')).toBeNull();
    expect(screen.getByTestId('margin-percent')).toHaveTextContent('—');
  });

  it('si el servidor lo niega, la sección se oculta sin romper la página', async () => {
    mocked.fetchSalesMargin.mockRejectedValue(new Error('403'));
    await show();
    expect(screen.queryByRole('region', { name: 'Margen comercial' })).toBeNull();
  });
});
