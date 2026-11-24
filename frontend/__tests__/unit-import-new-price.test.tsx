import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { StockUnitImport } from '@/app/admin/components/StockUnitImport';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * NEW-TEMPLATE-PRICE · «Precio de venta» de la plantilla de equipos nuevos.
 *
 * El precio del archivo nunca se aplica solo. La pantalla enseña lo que el servidor comparó (variante, precio actual,
 * precio del archivo, diferencia, equipos que entran y ya en stock) y, sólo a quien administra productos, una casilla
 * por variante. Registrar con una casilla marcada pide una confirmación antes de llamar al servidor.
 */

jest.mock('@/app/lib/auth', () => ({ ...jest.requireActual('@/app/lib/auth'), fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

const BRANCHES = [{ id: 1, name: 'Centro' }];

const ENTRY = {
  variant_id: 7, name: 'iPhone 16 128 GB Negro', current_price: '3000.00', has_price: true,
  file_prices: ['3199.90'], conflict: false, proposed_price: '3199.90', difference: '199.90',
  units_in_file: 3, rows_with_price: 2, units_in_stock: 5, is_published_online: true, is_sold_online: true,
  pending_draft: false,
};

function job(overrides: Record<string, unknown> = {}, review: unknown[] = [ENTRY]) {
  return {
    id: 41, import_type: 'units', status: 'previewed', original_filename: 'stock-equipos-nuevos.xlsx',
    counts: { total: 1, create: 1, update: 0, no_change: 0, skip: 0, error: 0 },
    summary: { units: 1, price_review: review }, is_applicable: true, rows_truncated: false,
    options: { line: 'new', entry_kind: 'opening' }, can_update_prices: true,
    rows: [
      { sheet: 'Equipos nuevos', row: 3, action: 'create', match_key: 'NP1', errors: [],
        warnings: ['Precio del archivo S/ 3199.90 distinto del de la variante «iPhone 16 128 GB Negro» (S/ 3000.00; diferencia +199.90). No se aplica solo: revisa la propuesta antes de registrar.'],
        data: { name: 'iPhone 16 128 GB Negro', branch: 'Centro', serial_number: 'NP1', imei: '356938035643809', condition: 'new', file_price: '3199.90' } },
    ],
    ...overrides,
  };
}

type Call = { method: string; path: string; body: unknown };
let calls: Call[];
let previewBody: unknown;
let applyBody: unknown;

function reply(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

beforeEach(() => {
  calls = [];
  previewBody = job();
  applyBody = job({
    status: 'applied', is_applicable: false,
    summary: { units: 1, price_review: [ENTRY], applied: { units: 1, movements: 1,
      price_updates: [{ variant_id: 7, old_price: '3000.00', new_price: '3199.90' }] } },
  });
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const path = String(input).replace(/^.*\/admin\/inventory\/units/, '');
    const body = typeof init.body === 'string' ? JSON.parse(init.body) : null;
    calls.push({ method: (init.method ?? 'GET').toUpperCase(), path, body });
    if (path.startsWith('/import/preview/')) return reply(201, previewBody);
    return reply(200, applyBody);
  });
});

async function previewed() {
  render(<StockUnitImport branches={BRANCHES} />);
  const file = new File(['x'], 'stock-equipos-nuevos.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
  fireEvent.change(screen.getByLabelText('Archivo de equipos (.xlsx)'), { target: { files: [file] } });
  fireEvent.click(screen.getByRole('radio', { name: 'Nuevos' }));
  fireEvent.click(screen.getByRole('button', { name: 'Previsualizar' }));
  return screen.findByRole('table', { name: 'Precios de venta del archivo' });
}

test('the difference, its reach and the proposal are shown as the server compared them', async () => {
  const table = await previewed();
  const row = within(table).getByText('iPhone 16 128 GB Negro').closest('tr') as HTMLElement;
  for (const said of ['S/ 3000.00', 'S/ 3199.90', '+S/ 199.90', '3', '5', 'Publicada en la tienda']) {
    expect(within(row).getByText(said)).toBeInTheDocument();
  }
  expect(screen.getByRole('note', { name: 'Precio de las variantes' })).toHaveTextContent('no se aplica solo');
  expect(within(row).getByRole('checkbox', { name: 'Actualizar el precio de la variante a S/ 3199.90' })).not.toBeChecked();
});

test('registering without ticking a proposal sends no price update and asks nothing more', async () => {
  await previewed();
  fireEvent.click(screen.getByRole('button', { name: 'Registrar 1 equipo' }));
  await waitFor(() => expect(calls).toHaveLength(2));
  expect(calls[1]).toMatchObject({ method: 'POST', path: '/import/41/apply/', body: {} });
});

test('a ticked proposal is confirmed before registering, and the confirmation travels', async () => {
  await previewed();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Actualizar el precio de la variante a S/ 3199.90' }));
  fireEvent.click(screen.getByRole('button', { name: 'Registrar 1 equipo' }));

  const confirm = screen.getByRole('group', { name: 'Confirma el cambio de precio de 1 variante' });
  expect(confirm).toHaveTextContent('iPhone 16 128 GB Negro: S/ 3000.00 → S/ 3199.90');
  expect(calls).toHaveLength(1);                         // nothing registered yet

  fireEvent.click(within(confirm).getByRole('button', { name: 'Volver' }));
  expect(screen.queryByRole('group', { name: /Confirma el cambio de precio/ })).toBeNull();
  expect(calls).toHaveLength(1);

  fireEvent.click(screen.getByRole('button', { name: 'Registrar 1 equipo' }));
  fireEvent.click(screen.getByRole('button', { name: 'Confirmar: registrar y actualizar precios' }));
  await waitFor(() => expect(calls).toHaveLength(2));
  expect(calls[1]).toMatchObject({
    method: 'POST', path: '/import/41/apply/',
    body: { price_updates: [{ variant_id: 7, price: '3199.90', confirm: true }] },
  });
  expect(await screen.findByRole('note', { name: 'Precios actualizados' }))
    .toHaveTextContent('Se actualizó el precio de 1 variante: iPhone 16 128 GB Negro (S/ 3000.00 → S/ 3199.90)');
});

test('without products.manage the proposal is read-only and registering sends none', async () => {
  previewBody = job({ can_update_prices: false });
  const table = await previewed();
  expect(within(table).queryByRole('checkbox')).toBeNull();
  expect(within(table).getByText(/Sólo quien administra productos puede aplicarla/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Registrar 1 equipo' }));
  await waitFor(() => expect(calls).toHaveLength(2));
  expect(calls[1].body).toEqual({});
});

test('two prices for one variant are a conflict: nothing is proposed', async () => {
  previewBody = job({}, [{ ...ENTRY, file_prices: ['3100.00', '3199.90'], conflict: true, proposed_price: null, difference: null }]);
  const table = await previewed();
  expect(within(table).queryByRole('checkbox')).toBeNull();
  expect(within(table).getByText(/no se propone ninguno/)).toBeInTheDocument();
  expect(within(table).getByText('S/ 3100.00 · S/ 3199.90')).toBeInTheDocument();
});

test('a variant without a price, still a draft, says so', async () => {
  previewBody = job({}, [{ ...ENTRY, current_price: '0.00', has_price: false, difference: '3199.90',
    pending_draft: true, is_published_online: false, is_sold_online: false }]);
  const table = await previewed();
  const row = within(table).getByText('iPhone 16 128 GB Negro').closest('tr') as HTMLElement;
  expect(within(row).getByText('Sin precio')).toBeInTheDocument();
  expect(within(row).getByText(/Borrador sin habilitar/)).toBeInTheDocument();
});

test('a file without a review shows no price section', async () => {
  previewBody = job({}, []);
  render(<StockUnitImport branches={BRANCHES} />);
  const file = new File(['x'], 'a.xlsx');
  fireEvent.change(screen.getByLabelText('Archivo de equipos (.xlsx)'), { target: { files: [file] } });
  fireEvent.click(screen.getByRole('radio', { name: 'Nuevos' }));
  fireEvent.click(screen.getByRole('button', { name: 'Previsualizar' }));
  await screen.findByRole('table', { name: 'Equipos del archivo' });
  expect(screen.queryByRole('table', { name: 'Precios de venta del archivo' })).toBeNull();
});
