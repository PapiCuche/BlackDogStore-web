import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { StockUnitForm } from '@/app/admin/components/StockUnitForm';
import { StockUnitImport } from '@/app/admin/components/StockUnitImport';
import { forgetBranch } from '@/app/admin/lib/last-branch';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * INV-LOAD-01 · cargar el stock real sin compras ficticias.
 *
 *  - La previsualización de la carga de equipos enseña lo que el servidor simuló: por sucursal × producto, los
 *    equipos disponibles antes → después, y cuántas líneas de Kardex de cada tipo escribirá.
 *  - «Registrar equipo» pide el tipo de ingreso (inventario inicial / compra), y opcionalmente proveedor y fecha.
 *
 * Nada se calcula en el navegador: se muestra lo que respondió el servidor y se envía lo que la persona eligió.
 */

jest.mock('@/app/lib/auth', () => ({ ...jest.requireActual('@/app/lib/auth'), fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

const BRANCHES = [{ id: 1, name: 'Centro' }, { id: 2, name: 'Norte' }];
const IMEI_A = '356938035645002';

function reply(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

type Call = { method: string; url: string; body: Record<string, unknown> | null };
let calls: Call[];
let previewBody: unknown;

const SIMULATION = {
  entry_type: 'initial_stock',
  movements: { initial_stock: 2, purchase_entry: 0 },
  units: { initial_stock: 3, purchase_entry: 0 },
  shelves: [
    { branch_id: 1, branch: 'Centro', product_id: 10, product: 'iPhone 16', adds: 2,
      units_before: 1, units_after: 3, stock_before: 1, stock_after: 3 },
    { branch_id: 2, branch: 'Norte', product_id: 10, product: 'iPhone 16', adds: 1,
      units_before: 0, units_after: 1, stock_before: 0, stock_after: 1 },
  ],
};

function job(overrides: Record<string, unknown> = {}) {
  return {
    id: 41, import_type: 'units', status: 'previewed', original_filename: 'equipos.xlsx',
    counts: { total: 3, create: 3, update: 0, no_change: 0, skip: 0, error: 0 },
    summary: { units: 3, simulation: SIMULATION }, is_applicable: true, rows_truncated: false,
    rows: [
      { sheet: 'Equipos', row: 2, action: 'create', match_key: 'S1', errors: [], warnings: [],
        data: { name: 'iPhone 16', branch: 'Centro', serial_number: 'S1', imei: IMEI_A, condition: 'new',
                supplier: 'Importadora Andina', received_on: '2026-10-01' } },
    ],
    ...overrides,
  };
}

const SUPPLIERS = [{ id: 7, name: 'Importadora Andina' }, { id: 8, name: 'Mayorista Sur' }];
const PRODUCT = {
  id: 10, name: 'iPhone 16', slug: 'p-10', description: '', price: '4500.00', inventory: 0, image_url: '',
  category_id: null, category_name: null, is_active: true, is_published_online: true,
  is_serialized: true, requires_imei: true, can_change_tracking: false, primary_code: '', created_at: '', updated_at: '',
};

beforeEach(() => {
  forgetBranch();
  calls = [];
  previewBody = job();
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const method = (init.method ?? 'GET').toUpperCase();
    const url = String(input);
    calls.push({ method, url, body: typeof init.body === 'string' ? JSON.parse(init.body) : null });
    if (url.includes('/import/preview/')) return reply(201, previewBody);
    if (url.includes('/admin/suppliers/')) return reply(200, { count: SUPPLIERS.length, page: 1, page_size: 25, results: SUPPLIERS });
    if (method === 'GET' && url.includes('/admin/products/')) {
      return reply(200, { count: 1, page: 1, page_size: 8, results: [PRODUCT] });
    }
    if (method === 'GET') return reply(200, { results: [] });
    return reply(201, { results: [{ id: 501 }] });
  });
});

// ---------------------------------------------------------------------------
// The import preview's simulation
// ---------------------------------------------------------------------------

async function previewFile() {
  render(<StockUnitImport branches={BRANCHES} />);
  const file = new File(['x'], 'equipos.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
  fireEvent.change(screen.getByLabelText('Archivo de equipos (.xlsx)'), { target: { files: [file] } });
  fireEvent.click(screen.getByRole('button', { name: 'Previsualizar' }));
}

test('the preview shows the simulated shelves before → after and the Kardex lines by type', async () => {
  await previewFile();

  const table = await screen.findByRole('table', { name: 'Resultado si registras el archivo' });
  const centro = within(table).getByRole('row', { name: /Centro/ });
  expect(centro).toHaveTextContent('iPhone 16');
  expect(centro).toHaveTextContent('1 → 3');
  expect(within(table).getByRole('row', { name: /Norte/ })).toHaveTextContent('0 → 1');
  const kardex = screen.getByRole('note', { name: 'Movimientos en el Kardex' });
  expect(kardex).toHaveTextContent('2 de inventario inicial');
  expect(kardex).toHaveTextContent('0 de compra');
});

test('the rows show the supplier and the entry date the file stated', async () => {
  await previewFile();
  const rows = await screen.findByRole('table', { name: 'Equipos del archivo' });
  expect(within(rows).getByText('Importadora Andina')).toBeInTheDocument();
  expect(within(rows).getByText('2026-10-01')).toBeInTheDocument();
});

test('no simulation is offered while a row is in error', async () => {
  previewBody = job({ counts: { total: 3, create: 2, update: 0, no_change: 0, skip: 0, error: 1 } });
  await previewFile();
  await screen.findByRole('table', { name: 'Equipos del archivo' });
  expect(screen.queryByRole('table', { name: 'Resultado si registras el archivo' })).toBeNull();
});

test('a job previewed before the simulation existed still renders', async () => {
  previewBody = job({ summary: { units: 3 } });
  await previewFile();
  await screen.findByRole('table', { name: 'Equipos del archivo' });
  expect(screen.queryByRole('table', { name: 'Resultado si registras el archivo' })).toBeNull();
});

// ---------------------------------------------------------------------------
// «Registrar equipo»
// ---------------------------------------------------------------------------

function mountForm(props: Partial<Parameters<typeof StockUnitForm>[0]> = {}) {
  const onSaved = jest.fn();
  render(
    <StockUnitForm
      branch={1} branches={BRANCHES} canChangeTracking
      conditions={[{ value: 'new', label: 'Nuevo' }]}
      onSaved={onSaved} onClose={jest.fn()} {...props}
    />,
  );
  return { onSaved };
}

async function pickPhone() {
  const box = screen.getByRole('combobox', { name: 'Producto / modelo' });
  fireEvent.change(box, { target: { value: 'iPhone 16' } });
  await act(async () => { fireEvent.keyDown(box, { key: 'Enter' }); });
  await screen.findByRole('button', { name: 'Cambiar producto' });
}

const posted = () => calls.filter((c) => c.method === 'POST' && /\/admin\/inventory\/units\/$/.test(c.url));

test('the form asks what the entry is, purchase by default', () => {
  mountForm();
  const group = screen.getByRole('group', { name: 'Tipo de ingreso' });
  expect(within(group).getByRole('radio', { name: /Compra/ })).toBeChecked();
  expect(within(group).getByRole('radio', { name: /Inventario inicial/ })).not.toBeChecked();
  expect(screen.getByLabelText(/^Fecha de ingreso/)).toHaveAttribute('type', 'date');
});

test('an opening entry with supplier and date is sent as such', async () => {
  const { onSaved } = mountForm({ canSeeSuppliers: true });
  await pickPhone();
  await screen.findByRole('option', { name: 'Mayorista Sur' });

  fireEvent.click(screen.getByRole('radio', { name: /Inventario inicial/ }));
  fireEvent.change(screen.getByLabelText('Proveedor (opcional)'), { target: { value: '8' } });
  fireEvent.change(screen.getByLabelText(/^Fecha de ingreso/), { target: { value: '2026-10-01' } });
  fireEvent.change(screen.getByLabelText(/^IMEI \*/), { target: { value: IMEI_A } });
  fireEvent.change(screen.getByLabelText(/^Motivo o documento de ingreso/), { target: { value: 'Conteo inicial' } });
  fireEvent.click(screen.getByRole('button', { name: 'Guardar equipo' }));

  await waitFor(() => expect(onSaved).toHaveBeenCalled());
  expect(posted()).toEqual([expect.objectContaining({ body: expect.objectContaining({
    entry_kind: 'opening', supplier: 8, received_on: '2026-10-01',
  }) })]);
});

test('without the supplier permission no supplier list is asked for or shown', async () => {
  mountForm();
  await pickPhone();
  expect(screen.queryByLabelText('Proveedor (opcional)')).toBeNull();
  expect(calls.some((c) => c.url.includes('/admin/suppliers/'))).toBe(false);
});

test('a purchase with no date sends neither entry kind nor date: the server defaults stand', async () => {
  const { onSaved } = mountForm();
  await pickPhone();
  fireEvent.change(screen.getByLabelText(/^IMEI \*/), { target: { value: IMEI_A } });
  fireEvent.change(screen.getByLabelText(/^Motivo o documento de ingreso/), { target: { value: 'Factura F001-1' } });
  fireEvent.click(screen.getByRole('button', { name: 'Guardar equipo' }));
  await waitFor(() => expect(onSaved).toHaveBeenCalled());
  const body = posted()[0].body ?? {};
  expect(body).not.toHaveProperty('entry_kind');
  expect(body).not.toHaveProperty('received_on');
  expect(body).not.toHaveProperty('supplier');
});

test('the latest entry date offered is today in Lima, not in the browser', () => {
  // 03:00 UTC on the 24th is still 22:00 of the 23rd in Lima (UTC-5): a browser in Madrid must not offer the 24th.
  jest.useFakeTimers({ now: new Date('2026-11-24T03:00:00Z'), doNotFake: ['queueMicrotask', 'nextTick'] });
  try {
    mountForm();
    expect(screen.getByLabelText(/^Fecha de ingreso/)).toHaveAttribute('max', '2026-11-23');
  } finally {
    jest.useRealTimers();
  }
});
