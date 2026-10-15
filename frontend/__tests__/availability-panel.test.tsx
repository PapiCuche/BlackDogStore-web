import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { AvailabilityPanel } from '@/app/admin/components/AvailabilityPanel';
import { INTERNAL_MODULES } from '@/app/admin/lib/internal-modules';
import { fetchWithAuth } from '@/app/lib/auth';
import { notifyStockChanged } from '@/app/lib/stock-events';

/**
 * COMMERCE-ADMIN-UX-01 · Ventas › Consultar disponibilidad.
 *
 * La pantalla no suma nada: el stock propio y lo que un proveedor puede traer
 * llegan por separado y se muestran por separado. Se vuelve a preguntar al
 * servidor al cambiar la búsqueda, al pulsar «Actualizar», cada cierto tiempo
 * mientras la página se ve, y cuando otra pantalla avisa que el stock cambió.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

function reply(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

const counts = (o: Partial<Record<string, number>> = {}) => ({
  available: 0, reserved_web: 0, reserved_staff: 0, sold: 0, written_off: 0, ...o,
});

function product(overrides: Record<string, unknown> = {}) {
  return {
    id: 10, name: 'iPhone 16 128 GB Negro', barcode: '7750001112223',
    model: { id: 5, name: 'iPhone 16' }, category: { id: 3, name: 'Celulares' },
    line: 'new', line_label: 'Nuevo',
    options: [{ attribute: 'Capacidad', kind: 'text', label: '128 GB', swatch_hex: '' }],
    is_serialized: true, price: '4500.00', unit_price_range: null,
    sale_mode: 'stock_or_order', sells_on_order: true, is_published_online: true,
    stock: {
      by_branch: [
        { branch_id: 1, branch_name: 'Centro', ...counts({ available: 3, reserved_web: 1, sold: 2 }) },
        { branch_id: 2, branch_name: 'Norte', ...counts({ available: 1, reserved_staff: 1 }) },
      ],
      total: counts({ available: 4, reserved_web: 1, reserved_staff: 1, sold: 2 }),
    },
    supplier_offers: [
      { supplier_name: 'Mayorista Lima', commercial_price: '4400.00', remaining: 6, valid_until: '2026-10-21', lead_time_days: 3 },
    ],
    ...overrides,
  };
}

const USED = product({
  id: 11, name: 'iPhone 15 seminuevo', line: 'used', line_label: 'Seminuevo', model: { id: 6, name: 'iPhone 15' },
  unit_price_range: { min: '2400.00', max: '2500.00' }, sells_on_order: false, supplier_offers: [],
  stock: { by_branch: [{ branch_id: 1, branch_name: 'Centro', ...counts() }, { branch_id: 2, branch_name: 'Norte', ...counts() }], total: counts() },
});

const FACETS = {
  categories: [{ id: 3, name: 'Celulares' }],
  models: [{ id: 5, name: 'iPhone 16' }, { id: 6, name: 'iPhone 15' }],
  attributes: [
    { id: 1, name: 'Capacidad', kind: 'text', options: [{ id: 12, label: '128 GB', swatch_hex: '' }] },
    { id: 2, name: 'Color', kind: 'color', options: [{ id: 21, label: 'Negro', swatch_hex: '#111111' }] },
  ],
};

const UNITS = {
  product: product(), count: 2, truncated: false,
  results: [
    { id: 1, branch_id: 1, branch_name: 'Centro', status: 'reserved', status_label: 'Apartado', hold_kind: 'web',
      hold_until: '2026-10-15T15:30:00Z', imei_last4: '1234', serial_last4: 'ABC1', condition: 'new', condition_label: 'Nuevo',
      battery_health: null, cosmetic_grade_label: '', public_notes: '', sale_price: null, received_at: '2026-10-01T10:00:00Z', sold_at: null },
    { id: 2, branch_id: 2, branch_name: 'Norte', status: 'available', status_label: 'Disponible', hold_kind: null,
      hold_until: null, imei_last4: '5678', serial_last4: 'ABC2', condition: 'new', condition_label: 'Nuevo',
      battery_health: null, cosmetic_grade_label: '', public_notes: '', sale_price: null, received_at: '2026-10-02T10:00:00Z', sold_at: null },
  ],
};

let urls: string[];
let listReply: { status: number; body: unknown };

beforeEach(() => {
  urls = [];
  listReply = {
    status: 200,
    body: {
      count: 2, page: 1, page_size: 25, can_see_suppliers: true,
      scope: { branch: null, branches: [{ id: 1, name: 'Centro' }, { id: 2, name: 'Norte' }], is_aggregate: true },
      results: [product(), USED],
    },
  };
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    urls.push(url);
    if (url.includes('units=1')) return reply(200, UNITS);
    if (listReply.status !== 200) return reply(listReply.status, listReply.body);
    const body = listReply.body as Record<string, unknown>;
    return reply(200, url.includes('facets=1') ? { ...body, facets: FACETS } : body);
  });
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
});

const listCalls = () => urls.filter((u) => u.includes('/admin/availability/') && !u.includes('units=1'));
const lastList = () => listCalls()[listCalls().length - 1];

test('own stock and supplier offers are separate columns, and the supplier is never own stock', async () => {
  render(<AvailabilityPanel pollMs={60_000} />);
  const row = await screen.findByTestId('availability-row-10');

  expect(within(row).getByText('Nuevo')).toBeInTheDocument();
  expect(within(row).getByText('4 disponibles')).toBeInTheDocument();
  expect(within(row).getByText('Centro: 3')).toBeInTheDocument();
  expect(within(row).getByText('Norte: 1')).toBeInTheDocument();
  expect(within(row).getByText(/Apartados 2 \(web 1 · personal 1\)/)).toBeInTheDocument();
  expect(within(row).getByText(/6 por pedir/)).toBeInTheDocument();
  expect(within(row).getByText('Mayorista Lima')).toBeInTheDocument();
  expect(screen.getByText('Proveedor (bajo pedido)')).toBeInTheDocument();
  expect(screen.getByText('no es stock propio')).toBeInTheDocument();

  const used = screen.getByTestId('availability-row-11');
  expect(within(used).getByText('Seminuevo')).toBeInTheDocument();
  expect(within(used).getByText('Sin stock propio')).toBeInTheDocument();
  expect(within(used).getByText(/2[.,]?400\.00 – S\/ 2[.,]?500\.00/)).toBeInTheDocument();
  expect(within(used).getByText('Sin ofertas vigentes')).toBeInTheDocument();

  // The filter lists are asked for once, with the first query.
  expect(listCalls()[0]).toContain('facets=1');
  expect(listCalls()[0]).toContain('branch=all');
});

test('a product with stock 0 and a supplier offer still says it has no own stock', async () => {
  listReply.body = { ...(listReply.body as object), results: [product({ stock: { by_branch: [], total: counts() } })] };
  render(<AvailabilityPanel pollMs={60_000} />);
  const row = await screen.findByTestId('availability-row-10');
  expect(within(row).getByText('Sin stock propio')).toBeInTheDocument();
  expect(within(row).getByText(/6 por pedir/)).toBeInTheDocument();
});

test('the search waits for the typing to stop, then asks the server', async () => {
  render(<AvailabilityPanel pollMs={60_000} />);
  await screen.findByTestId('availability-row-10');
  const before = listCalls().length;

  fireEvent.change(screen.getByLabelText('Buscar'), { target: { value: 'iph' } });
  fireEvent.change(screen.getByLabelText('Buscar'), { target: { value: 'iphone 256' } });
  expect(listCalls().length).toBe(before);

  await waitFor(() => expect(lastList()).toContain('q=iphone+256'));
  expect(listCalls().length).toBe(before + 1);
  expect(lastList()).not.toContain('facets=1');
});

test('filters are sent to the server', async () => {
  render(<AvailabilityPanel pollMs={60_000} />);
  await screen.findByRole('option', { name: '128 GB' });

  fireEvent.change(screen.getByLabelText('Línea'), { target: { value: 'used' } });
  fireEvent.change(screen.getByLabelText('Sucursal'), { target: { value: '2' } });
  fireEvent.change(screen.getByLabelText('Categoría'), { target: { value: '3' } });
  fireEvent.change(screen.getByLabelText('Modelo'), { target: { value: '6' } });
  fireEvent.change(screen.getByLabelText('Capacidad'), { target: { value: '12' } });
  fireEvent.change(screen.getByLabelText('Color'), { target: { value: '21' } });

  await waitFor(() => expect(lastList()).toContain('option=21'));
  for (const part of ['line=used', 'branch=2', 'category=3', 'model=6', 'option=12']) {
    expect(lastList()).toContain(part);
  }
});

test('the refresh button and a «stock changed» notice ask again', async () => {
  render(<AvailabilityPanel pollMs={60_000} />);
  await screen.findByTestId('availability-row-10');
  const before = listCalls().length;

  fireEvent.click(screen.getByRole('button', { name: 'Actualizar' }));
  await waitFor(() => expect(listCalls().length).toBe(before + 1));

  act(() => { notifyStockChanged(); });
  await waitFor(() => expect(listCalls().length).toBe(before + 2));
});

test('it polls while visible and stops while the page is hidden', async () => {
  render(<AvailabilityPanel pollMs={40} />);
  await screen.findByTestId('availability-row-10');
  await waitFor(() => expect(listCalls().length).toBeGreaterThanOrEqual(3));

  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
  await new Promise((r) => setTimeout(r, 60));
  const hidden = listCalls().length;
  await new Promise((r) => setTimeout(r, 200));
  expect(listCalls().length).toBe(hidden);
});

test('coming back to the tab asks again', async () => {
  render(<AvailabilityPanel pollMs={60_000} />);
  await screen.findByTestId('availability-row-10');
  const before = listCalls().length;
  await new Promise((r) => setTimeout(r, 3_100));

  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  act(() => { document.dispatchEvent(new Event('visibilitychange')); });
  await waitFor(() => expect(listCalls().length).toBe(before + 1));
}, 10_000);

test('the devices of a product: masked IMEI, where, and what each one is doing', async () => {
  render(<AvailabilityPanel pollMs={60_000} />);
  const row = await screen.findByTestId('availability-row-10');

  fireEvent.click(within(row).getByRole('button', { name: 'Ver equipos' }));
  const drawer = await screen.findByLabelText('Equipos de iPhone 16 128 GB Negro');
  expect(await within(drawer).findByText('IMEI ••••1234')).toBeInTheDocument();
  expect(within(drawer).getByText(/Apartado web hasta/)).toBeInTheDocument();
  expect(within(drawer).getByText('Disponible')).toBeInTheDocument();
  expect(urls.find((u) => u.includes('units=1'))).toContain('product=10');

  fireEvent.change(within(drawer).getByLabelText('Estado del equipo'), { target: { value: 'sold' } });
  await waitFor(() => expect(urls[urls.length - 1]).toContain('unit_status=sold'));
});

test('a used product shows battery and grade per device; no button for a product sold by quantity', async () => {
  listReply.body = { ...(listReply.body as object), results: [product({ is_serialized: false, id: 12, name: 'Cargador' })] };
  render(<AvailabilityPanel pollMs={60_000} />);
  const row = await screen.findByTestId('availability-row-12');
  expect(within(row).queryByRole('button', { name: 'Ver equipos' })).toBeNull();
  expect(within(row).queryByText(/Vendidos/)).toBeNull();
});

test('a refusal is shown in the server\'s words', async () => {
  listReply = { status: 403, body: { detail: 'No tienes permisos para esta operación.' } };
  render(<AvailabilityPanel pollMs={60_000} />);
  expect(await screen.findByText('No tienes permisos para esta operación.')).toBeInTheDocument();
});

test('an empty answer says so', async () => {
  listReply.body = { ...(listReply.body as object), count: 0, results: [] };
  render(<AvailabilityPanel pollMs={60_000} />);
  expect(await screen.findByText(/No hay productos que coincidan/)).toBeInTheDocument();
});

test('without the supplier capability the offer has no supplier name', async () => {
  listReply.body = {
    ...(listReply.body as object), can_see_suppliers: false,
    results: [product({ supplier_offers: [{ commercial_price: '4400.00', remaining: 6, valid_until: '2026-10-21', lead_time_days: 3 }] })],
  };
  render(<AvailabilityPanel pollMs={60_000} />);
  const row = await screen.findByTestId('availability-row-10');
  expect(within(row).getByText(/6 por pedir/)).toBeInTheDocument();
  expect(within(row).queryByText('Mayorista Lima')).toBeNull();
});

test('the menu entry sits under Ventas and needs the till capability', () => {
  const entry = INTERNAL_MODULES.find((m) => m.id === 'sales.availability');
  expect(entry).toMatchObject({ group: 'sales', href: '/admin/sales/availability', requiredCapabilities: ['sales.pos.use'] });
  expect(entry?.legacyRoles).toBeUndefined();
});
