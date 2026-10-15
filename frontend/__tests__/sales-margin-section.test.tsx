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
  refunds: { orders: 1, amount: '118.00', net: '100.00' },
  estimated: { lines: 4, net_sales: '250.00', cost: '150.00', margin: '100.00', margin_percent: '40.00' },
  realised: { lines: 2, net_sales: '120.00', cost: '90.00', margin: '30.00', margin_percent: '25.00' },
  estimated_only: { lines: 2, net_sales: '130.00', reference_cost_lines: 1, on_order_not_received_lines: 1 },
  unknown_cost: { lines: 1, orders: 1, net_sales: '50.00' },
  by_product: [
    { key: 4, name: 'Funda', units: 5, net_sales: '250.00', cost: '150.00', margin: '100.00', margin_percent: '40.00', pending_lines: 0 },
    { key: 9, name: 'Cargador', units: 1, net_sales: '200.00', cost: '0.00', margin: '0.00', margin_percent: null, pending_lines: 1, net_estimated: true },
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
    mocked.fetchSalesMargin.mockResolvedValue({
      ...REPORT,
      margin_percent: null,
      estimated: { ...REPORT.estimated, margin_percent: null },
      realised: { lines: 0, net_sales: '0.00', cost: '0.00', margin: '0.00', margin_percent: null },
    });
    await show();
    expect(screen.queryByText('0.00 %')).toBeNull();
    expect(screen.getByTestId('margin-percent')).toHaveTextContent('—');
    expect(screen.getByTestId('realised-percent')).toHaveTextContent('—');
  });

  it('si el servidor lo niega, la sección dice por qué en vez de ocultarse (A1)', async () => {
    mocked.fetchSalesMargin.mockRejectedValue(new Error('Se requiere permiso para ver costos y márgenes.'));
    await show();
    expect(screen.getByRole('region', { name: 'Margen comercial' }))
      .toHaveTextContent('Se requiere permiso para ver costos y márgenes.');
    expect(screen.queryByTestId('margin-percent')).toBeNull();
  });

  it('una venta anterior a los costos dice que su neto es estimado', async () => {
    await show();
    const row = screen.getByText('Cargador').closest('tr') as HTMLElement;
    expect(within(row).getByText(/estimado/i)).toBeInTheDocument();
    const funda = screen.getAllByText('Funda')[0].closest('tr') as HTMLElement;
    expect(within(funda).queryByText(/estimado/i)).toBeNull();
  });

  it('separa el margen estimado del realizado, con importe y %', async () => {
    await show();
    const estimated = screen.getByTestId('margin-estimated');
    expect(estimated).toHaveTextContent('Margen comercial estimado');
    expect(estimated.textContent).toMatch(/100\.00/);
    expect(screen.getByTestId('margin-percent')).toHaveTextContent('40.00 %');
    const realised = screen.getByTestId('margin-realised');
    expect(realised).toHaveTextContent('Margen comercial realizado');
    expect(realised.textContent).toMatch(/30\.00/);
    expect(screen.getByTestId('realised-percent')).toHaveTextContent('25.00 %');
  });

  it('dice qué es sólo estimado: costo de referencia y bajo pedido sin recibir', async () => {
    await show();
    const only = screen.getByTestId('margin-estimated-only');
    expect(only.textContent).toMatch(/130\.00/);
    expect(only).toHaveTextContent('1 con costo de referencia');
    expect(only).toHaveTextContent('1 bajo pedido sin recibir');
  });

  it('el costo desconocido se cuenta aparte y queda fuera de ambos márgenes', async () => {
    await show();
    const unknown = screen.getByTestId('margin-unknown-cost');
    expect(unknown).toHaveTextContent('Costo desconocido');
    expect(unknown.textContent).toMatch(/50\.00/);
    expect(unknown).toHaveTextContent('1 línea');
    expect(unknown).toHaveTextContent(/fuera de ambos márgenes/);
  });

  it('los reembolsos dicen pedidos, importe con IGV y neto sin IGV', async () => {
    await show();
    const refunds = screen.getByTestId('margin-refunds');
    expect(refunds).toHaveTextContent('Reembolsos');
    expect(refunds.textContent).toMatch(/118\.00/);
    expect(refunds.textContent).toMatch(/100\.00 sin IGV/);
    expect(refunds).toHaveTextContent('1 pedido');
  });

  it('dice la fórmula en una línea', async () => {
    await show();
    expect(screen.getByText(
      'Margen = (venta neta sin IGV − costo neto comparable) ÷ venta neta × 100, sólo sobre líneas con costo conocido.',
    )).toBeInTheDocument();
  });

  it('nunca dice «utilidad», en ningún texto de la sección', async () => {
    await show();
    const region = screen.getByRole('region', { name: 'Margen comercial' });
    expect(region.textContent?.toLowerCase()).not.toContain('utilidad');
    expect(document.body.textContent?.toLowerCase()).not.toContain('utilidad');
  });
});
