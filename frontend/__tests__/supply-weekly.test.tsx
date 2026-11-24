import { act, fireEvent, render, screen, within } from '@testing-library/react';

import { SupplierOffersManager } from '@/app/admin/components/SupplierOffersManager';
import { deltaDetail, shiftDay, variantOptions } from '@/app/admin/components/SupplierWeeklyPanel';
import type { SupplierWeeklyRow } from '@/app/admin/lib/internal-api';
import { fetchWithAuth } from '@/app/lib/auth';

import { reply } from './support/device-commerce-fixtures';

/**
 * SUPPLY-WEEKLY-01 — la pestaña «Semana» (una fila por proveedor × variante, con el cambio frente a la lista
 * anterior y el stock propio aparte), las ofertas que una lista parcial no menciona (sólo informativo) y «Crear
 * variante» para una fila que es una combinación válida sin variante.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

type Call = { method: string; path: string; body: unknown };
let writes: Call[];
let weeklyAsked: string[];
let weekly: Record<string, unknown>;
let previews: unknown[];

const SUPPLIER = {
  id: 5, name: 'Distribuidora Andina', document_type: 'ruc', document_number: '20123456789', contact_name: 'Rosa',
  phone: '999888777', email: 'ventas@andina.pe', notes: '', is_active: true, offer_count: 1,
};

const weekRow = (extra: Partial<SupplierWeeklyRow> = {}): SupplierWeeklyRow => ({
  supplier_id: 5, supplier_name: 'Distribuidora Andina', supplier_active: true, product_id: 44,
  product_name: 'Equipo X 256 Blanco', offer_id: 31, model: 'Equipo X', capacity: '256 GB', color: 'Blanco',
  sim: 'eSIM', region: null, condition_line: 'new', commercial_price: '105.00', variant_price: '120.00',
  confirmed_quantity: 8, committed_quantity: 0, remaining: 8, lead_time_days: 4, valid_from: '2026-11-23',
  valid_until: '2026-11-29', state: 'active', own_stock: 3, message_date: '2026-11-24', listed_price: '105.00',
  declared_quantity: 8, previous: { message_date: '2026-11-17', job_id: 9, commercial_price: '100.00', declared_quantity: 5 },
  delta: 'precio_cambio', changes: ['price', 'quantity'], ...extra,
});

const COUNTS = { nuevo: 1, precio_cambio: 1, cantidad_cambio: 0, retirado: 0, no_mencionado: 1, sin_cambio: 0 };

const weeklyOf = (results: SupplierWeeklyRow[], extra: Record<string, unknown> = {}) => ({
  week: '2026-W48', week_start: '2026-11-23', week_end: '2026-11-29', previous_week_start: '2026-11-16',
  today: '2026-11-24', with_cost: false, counts: COUNTS, total: results.length, truncated: false, results, ...extra,
});

const SUMMARY = { new: 0, updated: 0, unchanged: 0, sold_out: 0, errors: 1, skipped: 0, price_changes: 0, mappings: 0 };

const previewOf = (rows: unknown[], extra: Record<string, unknown> = {}) => ({
  id: 61, status: 'previewed', rows_total: rows.length, rows_create: 0, rows_update: 0, rows_no_change: 0,
  rows_error: 1, cost_column_ignored: false, message_date: '2026-11-24', summary: SUMMARY, rows, ...extra,
});

const PROPOSAL = {
  status: 'combinacion_valida_sin_variante', model_id: 12, model_name: 'Tableta Serie X', condition_line: 'new',
  option_ids: [3, 7], labels: ['128 GB', 'Grafito'],
};

beforeEach(() => {
  writes = [];
  weeklyAsked = [];
  previews = [];
  weekly = weeklyOf([
    weekRow(),
    weekRow({ product_id: 45, product_name: 'Funda Z', model: null, capacity: null, color: 'Azul', sim: null,
      offer_id: 32, delta: 'no_mencionado', changes: [], previous: null, message_date: null, listed_price: null,
      declared_quantity: null, own_stock: 0, remaining: null }),
    weekRow({ product_id: 46, product_name: 'Equipo X 128 Negro', capacity: '128 GB', color: 'Negro', offer_id: 33,
      delta: 'nuevo', changes: [], previous: null }),
  ]);
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = (init.method ?? 'GET').toUpperCase();
    if (method !== 'GET') {
      writes.push({ method, path: url.replace(/^.*\/api/, ''), body: init.body instanceof FormData ? init.body : JSON.parse(String(init.body ?? '{}')) });
      if (url.includes('/import/preview/')) return reply(previews.shift());
      if (url.includes('/variants/')) return reply({ id: 90, name: 'Tableta Serie X 128 GB Grafito' }, 201);
      return reply({}, 404);
    }
    if (url.includes('/admin/suppliers/')) return reply({ count: 1, page: 1, page_size: 25, results: [SUPPLIER] });
    if (url.includes('/admin/supplier-offers/weekly/')) {
      weeklyAsked.push(url);
      return reply(weekly);
    }
    if (url.includes('/admin/supplier-offers/')) return reply({ count: 0, page: 1, page_size: 25, results: [] });
    return reply({}, 404);
  });
});

async function openWeek(canSeeCosts = false) {
  render(<SupplierOffersManager canManage canSeeCosts={canSeeCosts} companyId={null} />);
  const tab = screen.getByRole('tab', { name: 'Semana' });
  expect(tab).toHaveAttribute('aria-selected', 'false');
  await act(async () => { fireEvent.click(tab); });
  expect(tab).toHaveAttribute('aria-selected', 'true');
  return screen.findByTestId('supplier-week-table');
}

describe('pestaña «Semana»', () => {
  it('muestra una fila por proveedor y variante con sus opciones, el cambio y el stock propio aparte', async () => {
    const table = await openWeek();
    const row = within(table).getByTestId('week-row-5-44');
    expect(row).toHaveTextContent('Equipo X');
    expect(row).toHaveTextContent('256 GB · Blanco · eSIM');
    expect(row).toHaveTextContent('Precio cambió');
    expect(row).toHaveTextContent('Precio: S/ 100.00 → S/ 105.00');
    expect(row).toHaveTextContent('Cantidad: 5 → 8');
    const headers = within(table).getAllByRole('columnheader').map((cell) => cell.textContent);
    expect(headers).toEqual(expect.arrayContaining(['Stock propio', 'Quedan (proveedor)', 'Precio oferta', 'Precio variante']));
    expect(headers).not.toContain('Costo pactado');
    const cells = within(row).getAllByRole('cell').map((cell) => cell.textContent);
    expect(cells[headers.indexOf('Stock propio')]).toBe('3');
    expect(cells[headers.indexOf('Quedan (proveedor)')]).toBe('8');
    expect(within(table).getByTestId('week-row-5-45')).toHaveTextContent('No mencionado');
    expect(weeklyAsked[0]).toContain('/admin/supplier-offers/weekly/');
  });

  it('el costo sólo aparece cuando el servidor lo manda', async () => {
    weekly = weeklyOf([weekRow({ agreed_cost: '80.00' })], { with_cost: true });
    const table = await openWeek(true);
    expect(within(table).getAllByRole('columnheader').map((cell) => cell.textContent)).toContain('Costo pactado');
    expect(within(table).getByTestId('week-row-5-44')).toHaveTextContent('S/ 80.00');
  });

  it('filtra por cambio y por texto sin volver a pedir al servidor', async () => {
    const table = await openWeek();
    const asked = weeklyAsked.length;
    fireEvent.click(screen.getByRole('button', { name: 'Nuevo (1)' }));
    expect(within(table).queryByTestId('week-row-5-44')).toBeNull();
    expect(within(table).getByTestId('week-row-5-46')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Todos (3)' }));
    fireEvent.change(screen.getByLabelText('Buscar'), { target: { value: 'azul' } });
    expect(within(table).getByTestId('week-row-5-45')).toBeInTheDocument();
    expect(within(table).queryByTestId('week-row-5-44')).toBeNull();
    expect(weeklyAsked.length).toBe(asked);
  });

  it('el proveedor y la semana anterior se piden al servidor', async () => {
    await openWeek();
    await act(async () => {
      fireEvent.change(within(screen.getByRole('group', { name: 'Filtrar la semana' })).getByLabelText('Proveedor'), { target: { value: '5' } });
    });
    expect(weeklyAsked.at(-1)).toContain('supplier=5');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '← Semana anterior' })); });
    expect(weeklyAsked.at(-1)).toContain('week=2026-11-16');
    expect(screen.getByRole('button', { name: 'Esta semana' })).toBeDisabled();
  });

  it('en el teléfono cada fila es una tarjeta con el stock propio separado', async () => {
    await openWeek();
    const cards = screen.getByTestId('supplier-week-cards');
    const first = within(cards).getAllByRole('listitem')[0];
    expect(first).toHaveTextContent('Stock propio');
    expect(first).toHaveTextContent('Quedan (proveedor)');
  });

  it('ayudantes: opciones, detalle del cambio y desplazamiento de días', () => {
    expect(variantOptions({ capacity: '256 GB', color: null, sim: 'eSIM', region: 'Japón' })).toBe('256 GB · eSIM · Japón');
    expect(deltaDetail(weekRow({ changes: ['quantity'] }))).toEqual(['Cantidad: 5 → 8']);
    expect(deltaDetail(weekRow({ previous: null }))).toEqual([]);
    expect(shiftDay('2026-11-23', -7)).toBe('2026-11-16');
    expect(shiftDay('2026-12-28', 7)).toBe('2027-01-04');
  });
});

describe('vista previa del Excel', () => {
  async function upload() {
    render(<SupplierOffersManager canManage canSeeCosts={false} companyId={null} />);
    await screen.findByText('No hay ofertas que coincidan.');
    fireEvent.change(screen.getByLabelText('Archivo de ofertas (.xlsx)'), { target: { files: [new File(['x'], 'ofertas.xlsx')] } });
    fireEvent.change(screen.getByLabelText('Fecha del mensaje del proveedor'), { target: { value: '2026-11-24' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Previsualizar' })); });
    return screen.findByTestId('supplier-import-preview');
  }

  const errorRow = {
    row_number: 2, action: 'error', warnings: [],
    errors: ['«Tableta Serie X» con «128 GB», «Grafito» es una combinación válida de su paleta, pero aún no existe la variante.'],
    data: { supplier: 'Distribuidora Andina', product_name: 'Tableta Serie X', commercial_price: '1000.00',
      error_code: 'combinacion_valida_sin_variante', variant_proposal: PROPOSAL },
  };

  it('una lista parcial nombra las ofertas vigentes que no menciona, sólo como información', async () => {
    previews = [previewOf([{ ...errorRow, action: 'no_change', errors: [], data: { supplier: 'Distribuidora Andina', product_name: 'Cargador' } }], {
      rows_error: 0,
      unmentioned: [{ offer_id: 70, supplier_id: 5, supplier_name: 'Distribuidora Andina', product_id: 50,
        product_name: 'Funda Z', commercial_price: '35.00', available: 4, valid_until: '2026-11-29' }],
      unmentioned_count: 1,
    })];
    const shown = await upload();
    const box = within(shown).getByTestId('unmentioned-offers');
    expect(box).toHaveTextContent('Ofertas vigentes que la lista no menciona (1)');
    expect(box).toHaveTextContent('Funda Z');
    expect(box).toHaveTextContent('quedan 4');
    expect(box).toHaveTextContent('no desactiva nada');
    expect(within(box).queryByRole('checkbox')).toBeNull();
  });

  it('una combinación válida sin variante ofrece «Crear variante», confirmado, y vuelve a previsualizar', async () => {
    previews = [previewOf([errorRow]), previewOf([{ ...errorRow, action: 'create', errors: [], data: { ...errorRow.data, variant_proposal: undefined, error_code: undefined } }], { rows_error: 0 })];
    const shown = await upload();
    const row = within(shown).getByTestId('import-row-2');
    expect(row).toHaveTextContent('combinación válida');
    fireEvent.click(within(row).getByRole('button', { name: 'Crear variante de la fila 2' }));
    expect(writes.filter((call) => call.path.includes('/variants/'))).toHaveLength(0);
    const ask = within(row).getByRole('group', { name: 'Crear variante de la fila 2' });
    expect(ask).toHaveTextContent('borrador');
    await act(async () => { fireEvent.click(within(ask).getByRole('button', { name: 'Confirmar: crear borrador' })); });
    const created = writes.filter((call) => call.path.includes('/variants/'));
    expect(created).toHaveLength(1);
    expect(created[0].path).toContain('/admin/catalog/models/12/variants/');
    expect(created[0].body).toEqual({ condition_line: 'new', option_ids: [3, 7] });
    expect(writes.filter((call) => call.path.includes('/import/preview/'))).toHaveLength(2);
    expect(await screen.findByText(/creada como borrador pendiente/)).toBeInTheDocument();
  });
});
