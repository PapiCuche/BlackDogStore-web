import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { SupplierOffersManager, rowDiff } from '@/app/admin/components/SupplierOffersManager';
import type { WeeklyImportRow } from '@/app/admin/lib/supplier-import-api';
import { fetchWithAuth } from '@/app/lib/auth';

import { reply } from './support/device-commerce-fixtures';

/**
 * SUPPLIER-DEVICE-FLOW-02 · A — el Excel semanal del proveedor en el panel:
 * qué cambia cada fila (antes → después), «agotada», la decisión explícita
 * sobre precios nuevos, la vista previa vieja y el código del proveedor.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

type Call = { method: string; path: string; body: unknown };
let writes: Call[];
let previews: number;
let applyAnswer: () => Response;
let preview: unknown;

const BEFORE = {
  commercial_price: '110.00', confirmed_quantity: 5, committed_quantity: 2, valid_from: '2026-10-19',
  valid_until: '2026-10-25', lead_time_days: 4, is_active: true,
};

const updateRow = (row_number: number, data: Record<string, unknown>, warnings: string[] = []) => ({
  row_number, action: 'update', errors: [], warnings,
  data: {
    supplier: 'Distribuidora Andina', product_name: 'Cargador 20W', commercial_price: '110.00', confirmed_quantity: 5,
    valid_from: '2026-10-19', valid_until: '2026-10-25', lead_time_days: 4, offer_id: 31, before: BEFORE, ...data,
  },
});

const SUMMARY = { new: 0, updated: 1, unchanged: 0, sold_out: 1, errors: 0, skipped: 0, price_changes: 0, mappings: 0 };

function previewOf(rows: unknown[], summary: Record<string, number> = {}) {
  const errors = rows.filter((r) => (r as { action: string }).action === 'error').length;
  return {
    id: 61, status: 'previewed', rows_total: rows.length, rows_create: 0, rows_update: rows.length - errors,
    rows_no_change: 0, rows_error: errors, cost_column_ignored: false,
    summary: { ...SUMMARY, errors, ...summary }, rows,
  };
}

beforeEach(() => {
  writes = [];
  previews = 0;
  applyAnswer = () => reply({ status: 'applied', created: 0, updated: 1, unchanged: 0, sold_out: 1 });
  preview = previewOf([
    updateRow(2, { confirmed_quantity: 9, lead_time_days: 6, changes: ['confirmed_quantity', 'lead_time_days'] }),
    updateRow(3, { confirmed_quantity: 0, sold_out: true, changes: ['confirmed_quantity'] }, ['Agotada: no se vende más.']),
  ]);
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = (init.method ?? 'GET').toUpperCase();
    if (method !== 'GET') {
      writes.push({ method, path: url.replace(/^.*\/api/, ''), body: init.body instanceof FormData ? 'form' : JSON.parse(String(init.body ?? '{}')) });
      if (url.includes('/import/preview/')) { previews += 1; return reply(preview); }
      if (url.includes('/import/61/apply/')) return applyAnswer();
      if (url.includes('/product-refs/')) return reply({ id: 1, supplier_id: 5, supplier_code: 'XYZ-9', product_id: 44, product_name: 'Cable USB-C' }, 201);
      return reply({}, 404);
    }
    if (url.includes('/admin/suppliers/')) return reply({ count: 0, page: 1, page_size: 25, results: [] });
    if (url.includes('/admin/supplier-offers/')) return reply({ count: 0, page: 1, page_size: 25, results: [] });
    if (url.includes('/admin/products/')) return reply({ count: 1, results: [{ id: 44, name: 'Cable USB-C' }] });
    return reply({}, 404);
  });
});

async function upload() {
  render(<SupplierOffersManager canManage canSeeCosts={false} companyId={null} />);
  await screen.findByText('No hay ofertas que coincidan.');
  fireEvent.change(screen.getByLabelText('Archivo de ofertas (.xlsx)'), { target: { files: [new File(['x'], 'ofertas.xlsx')] } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Previsualizar' })); });
  return screen.findByTestId('supplier-import-preview');
}

const applied = () => writes.filter((w) => w.path.includes('/import/61/apply/'));

describe('vista previa: antes → después', () => {
  it('cada fila dice qué era y qué será, y el resumen cuenta las agotadas', async () => {
    const shown = await upload();
    const changes = within(shown).getByRole('list', { name: 'Cambios de la fila 2' });
    expect(changes).toHaveTextContent('Cantidad confirmada: 5 → 9');
    expect(changes).toHaveTextContent('Entrega (días): 4 → 6');
    const soldOut = screen.getByTestId('import-row-3');
    expect(soldOut).toHaveTextContent('Agotada');
    // Sold out: what remains confirmed is what was committed.
    expect(within(soldOut).getByRole('list', { name: 'Cambios de la fila 3' })).toHaveTextContent('Cantidad confirmada: 5 → 2');
    const agotadas = within(shown).getByText('Agotadas').closest('div')!;
    expect(agotadas).toHaveTextContent('1');
  });

  it('el costo sólo aparece si el servidor lo mandó en «before»', () => {
    const row = updateRow(2, { agreed_cost: '65.00', changes: ['agreed_cost', 'confirmed_quantity'], confirmed_quantity: 9 }) as unknown as WeeklyImportRow;
    expect(rowDiff(row).map((c) => c.field)).toEqual(['confirmed_quantity']);
    const withCost = { ...row, data: { ...row.data, before: { ...BEFORE, agreed_cost: '70.00' } } } as WeeklyImportRow;
    expect(rowDiff(withCost).map((c) => c.field)).toEqual(['agreed_cost', 'confirmed_quantity']);
  });

  it('una respuesta sin `summary` (servidor anterior) sigue contando con `rows_*`', async () => {
    preview = { ...previewOf([updateRow(2, { changes: ['confirmed_quantity'], confirmed_quantity: 7 })]), summary: undefined };
    const shown = await upload();
    expect(within(shown).getByText('Cambian').closest('div')!).toHaveTextContent('1');
  });
});

describe('precio comercial nuevo: decisión explícita', () => {
  beforeEach(() => {
    preview = previewOf([
      updateRow(2, { commercial_price: '120.00', changes: ['commercial_price'], price_change: { before: '110.00', after: '120.00' } }),
    ], { price_changes: 1, sold_out: 0 });
  });

  it('no aplica hasta marcar la casilla, que lista las filas', async () => {
    const shown = await upload();
    const group = within(shown).getByRole('group', { name: 'Precios comerciales que cambian' });
    expect(group).toHaveTextContent('Fila 2');
    expect(group).toHaveTextContent('110.00');
    expect(group).toHaveTextContent('120.00');
    const applyButton = within(shown).getByRole('button', { name: 'Aplicar cambios' });
    expect(applyButton).toBeDisabled();
    fireEvent.click(within(group).getByRole('checkbox', { name: /Acepto los precios nuevos de esta oferta/ }));
    expect(applyButton).toBeEnabled();
    await act(async () => { fireEvent.click(applyButton); });
    expect(applied()[0].body).toEqual({ accept_price_changes: true });
  });

  it('se puede aplicar lo demás manteniendo los precios actuales', async () => {
    applyAnswer = () => reply({ status: 'applied', created: 0, updated: 0, unchanged: 1, prices_kept: 1 });
    const shown = await upload();
    await act(async () => { fireEvent.click(within(shown).getByRole('button', { name: 'Aplicar sin cambiar precios' })); });
    expect(applied()[0].body).toEqual({ keep_prices: true });
    expect(await screen.findByRole('status')).toHaveTextContent('Se mantuvo el precio actual de 1 ofertas.');
  });

  it('sin precios nuevos se aplica sin decisión', async () => {
    preview = previewOf([updateRow(2, { changes: ['confirmed_quantity'], confirmed_quantity: 7 })], { sold_out: 0 });
    const shown = await upload();
    expect(within(shown).queryByRole('group', { name: 'Precios comerciales que cambian' })).toBeNull();
    expect(within(shown).queryByRole('button', { name: 'Aplicar sin cambiar precios' })).toBeNull();
    await act(async () => { fireEvent.click(within(shown).getByRole('button', { name: 'Aplicar cambios' })); });
    expect(applied()[0].body).toEqual({});
  });
});

describe('vista previa vieja', () => {
  it('el servidor la rechaza: no se aplicó nada y se puede volver a previsualizar el mismo archivo', async () => {
    applyAnswer = () => reply({ detail: 'El archivo cambió: vuelve a previsualizarlo.', code: 'stale' }, 400);
    preview = previewOf([updateRow(2, { changes: ['confirmed_quantity'], confirmed_quantity: 7 })], { sold_out: 0 });
    const shown = await upload();
    await act(async () => { fireEvent.click(within(shown).getByRole('button', { name: 'Aplicar cambios' })); });
    expect(await screen.findByText(/El archivo cambió: vuelve a previsualizarlo\. No se aplicó nada/)).toBeInTheDocument();
    expect(screen.queryByTestId('supplier-import-preview')).toBeNull();
    const again = screen.getByRole('button', { name: 'Previsualizar' });
    expect(again).toBeEnabled();
    await act(async () => { fireEvent.click(again); });
    expect(previews).toBe(2);
  });
});

describe('código del proveedor', () => {
  it('una fila sin variante se asocia eligiéndola, se guarda el código y se vuelve a previsualizar', async () => {
    preview = previewOf([{
      row_number: 4, action: 'error', warnings: [],
      errors: ['No existe un producto con el código «XYZ-9». Si es un código del proveedor, elige la variante en la vista previa.'],
      data: { supplier: 'Distribuidora Andina', product_name: 'XYZ-9', mappable: { supplier_id: 5, supplier_code: 'XYZ-9' } },
    }], { sold_out: 0, updated: 0 });
    const shown = await upload();
    expect(within(shown).getByRole('button', { name: 'Aplicar cambios' })).toBeDisabled();
    fireEvent.click(within(shown).getByRole('button', { name: 'Elegir variante' }));
    const group = within(shown).getByRole('group', { name: 'Variante para el código XYZ-9' });
    expect(within(group).getByRole('button', { name: 'Guardar código' })).toBeDisabled();
    fireEvent.change(within(group).getByLabelText('Variante para «XYZ-9»'), { target: { value: 'Cable' } });
    fireEvent.click(await within(group).findByRole('button', { name: 'Cable USB-C' }));
    await act(async () => { fireEvent.click(within(group).getByRole('button', { name: 'Guardar código' })); });
    const saved = writes.find((w) => w.path.includes('/admin/suppliers/5/product-refs/'));
    expect(saved).toMatchObject({ method: 'POST', body: { supplier_code: 'XYZ-9', product_id: 44 } });
    await waitFor(() => expect(previews).toBe(2));
    expect(screen.getByText(/queda asociado a «Cable USB-C»/)).toBeInTheDocument();
  });

  it('sin `products.manage` no se ofrece elegir variante', async () => {
    render(<SupplierOffersManager canManage={false} canSeeCosts={false} companyId={null} />);
    await screen.findByText('No hay ofertas que coincidan.');
    expect(screen.queryByRole('button', { name: 'Elegir variante' })).toBeNull();
    expect(screen.queryByLabelText('Archivo de ofertas (.xlsx)')).toBeNull();
  });
});
