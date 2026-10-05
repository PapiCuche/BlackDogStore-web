import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { StockUnitsPanel } from '@/app/admin/components/StockUnitsPanel';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * SERIALIZED-STOCK · Inventario › Equipos.
 *
 * Un equipo es stock: entra, se aparta, se da de baja o vuelve por operaciones
 * que el servidor valida y anota en el Kardex. La pantalla no cuenta nada ni
 * decide qué IMEI vale: lista lo que hay, filtra en el servidor y envía.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

const BRANCHES = [{ id: 1, name: 'Centro' }, { id: 2, name: 'Norte' }];
const PRODUCTS = [
  { id: 10, name: 'iPhone 16', requires_imei: true, price: '4500.00' },
  { id: 11, name: 'MacBook Air', requires_imei: false, price: '5200.00' },
];

function unit(overrides: Record<string, unknown> = {}) {
  return {
    id: 1, product_id: 10, product_name: 'iPhone 16', branch_id: 1, branch_name: 'Centro',
    serial_number: 'F2LXK1ABC1', imei: '356938035643809', imei2: null,
    condition: 'new', condition_label: 'Nuevo', status: 'available', status_label: 'Disponible',
    cost: '3800.00', price_override: null, catalogue_price: '4500.00',
    received_at: '2026-10-05T15:00:00Z', sold_at: null, order_id: null, notes: '',
    ...overrides,
  };
}

type Call = { method: string; path: string; body: unknown };
let calls: Call[];
let rows: ReturnType<typeof unit>[];
let receiveReply: { status: number; body: unknown };
let actionReply: { status: number; body: unknown };

function reply(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

beforeEach(() => {
  calls = [];
  rows = [unit(), unit({ id: 2, serial_number: 'F2LXK1ABC2', imei: '356938035643817', status: 'sold', status_label: 'Vendido', order_id: 77 })];
  receiveReply = { status: 201, body: { results: [unit({ id: 3, serial_number: 'NUEVO00001' })] } };
  actionReply = { status: 200, body: unit({ status: 'reserved', status_label: 'Apartado' }) };
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const method = (init.method ?? 'GET').toUpperCase();
    const path = String(input).replace(/^.*\/admin\/inventory\/units/, '');
    calls.push({ method, path, body: typeof init.body === 'string' ? JSON.parse(init.body) : null });
    if (method === 'GET' && path.startsWith('/products/')) {
      return reply(200, { results: PRODUCTS.map((p) => ({ ...p, is_serialized: true, stock: 1, can_change_tracking: false })) });
    }
    if (method === 'GET') {
      return reply(200, {
        results: rows, count: rows.length, page: 1, page_size: 25,
        statuses: [{ value: 'available', label: 'Disponible' }, { value: 'reserved', label: 'Apartado' }, { value: 'sold', label: 'Vendido' }, { value: 'written_off', label: 'Dado de baja' }],
        conditions: [{ value: 'new', label: 'Nuevo' }, { value: 'used', label: 'Usado' }],
      });
    }
    if (path === '/') return reply(receiveReply.status, receiveReply.body);
    return reply(actionReply.status, actionReply.body);
  });
});

function mount(props: Partial<Parameters<typeof StockUnitsPanel>[0]> = {}) {
  render(<StockUnitsPanel branch={1} branches={BRANCHES} canAdjust {...props} />);
}

const gets = () => calls.filter((c) => c.method === 'GET' && !c.path.startsWith('/products/'));
const writes = () => calls.filter((c) => c.method !== 'GET');

test('the table lists each device with its identifiers, where it is and its state', async () => {
  mount();

  const row = (await screen.findByText('F2LXK1ABC1')).closest('tr') as HTMLElement;
  expect(within(row).getByText('356938035643809')).toBeInTheDocument();
  expect(within(row).getByText('iPhone 16')).toBeInTheDocument();
  expect(within(row).getByText('Centro')).toBeInTheDocument();
  expect(within(row).getByText('Disponible')).toBeInTheDocument();
  expect(gets()[0].path).toContain('branch=1');
});

test('filters are answered by the server, not by hiding rows', async () => {
  mount({ branch: 'all' });
  await screen.findByText('F2LXK1ABC1');

  fireEvent.change(screen.getByLabelText('Estado'), { target: { value: 'reserved' } });
  fireEvent.change(screen.getByLabelText('Condición'), { target: { value: 'used' } });
  fireEvent.change(screen.getByLabelText('Producto'), { target: { value: '11' } });
  fireEvent.change(screen.getByLabelText('Serie o IMEI'), { target: { value: '3809' } });
  fireEvent.click(screen.getByRole('button', { name: 'Filtrar' }));

  await waitFor(() => expect(gets().length).toBeGreaterThan(1));
  const last = gets()[gets().length - 1].path;
  for (const part of ['branch=all', 'status=reserved', 'condition=used', 'product=11', 'search=3809']) {
    expect(last).toContain(part);
  }
});

test('"+ Registrar equipo" is the way in, and saving one refreshes the table', async () => {
  mount();
  await screen.findByText('F2LXK1ABC1');
  const before = gets().length;

  fireEvent.click(screen.getByRole('button', { name: '+ Registrar equipo' }));
  // El formulario pide la identidad del equipo desde el primer momento.
  expect(await screen.findByLabelText(/^Número de serie/)).toBeInTheDocument();
  expect(screen.getByLabelText(/^IMEI \*/)).toBeInTheDocument();
  expect(screen.getByLabelText(/^IMEI 2/)).toBeInTheDocument();

  await screen.findByRole('option', { name: /iPhone 16/ });
  fireEvent.change(screen.getByLabelText('Producto / modelo'), { target: { value: '10' } });
  fireEvent.change(screen.getByLabelText(/^Número de serie/), { target: { value: 'NUEVO00001' } });
  fireEvent.change(screen.getByLabelText(/^IMEI \*/), { target: { value: '356938035643825' } });
  fireEvent.change(screen.getByLabelText(/^Motivo o documento de ingreso/), { target: { value: 'Factura F001-1' } });
  fireEvent.click(screen.getByRole('button', { name: 'Guardar equipo' }));

  expect(await screen.findByText(/Equipo registrado: iPhone 16 · Serie NUEVO00001 · IMEI 356938035643825/)).toBeInTheDocument();
  await waitFor(() => expect(gets().length).toBeGreaterThan(before));
  expect(writes()[0].path).toBe('/');
  expect(screen.queryByLabelText(/^Número de serie/)).toBeNull();
});

test('an available device can be set aside or written off, each with its reason', async () => {
  mount();
  const row = (await screen.findByText('F2LXK1ABC1')).closest('tr') as HTMLElement;

  fireEvent.click(within(row).getByRole('button', { name: 'Apartar' }));
  expect(screen.getByRole('button', { name: 'Confirmar' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Motivo de la operación'), { target: { value: 'Apartado para Ana' } });
  fireEvent.click(screen.getByRole('button', { name: 'Confirmar' }));

  await waitFor(() => expect(writes()).toHaveLength(1));
  expect(writes()[0]).toEqual({ method: 'POST', path: '/1/reserve/', body: { reason: 'Apartado para Ana' } });
});

test('a sold device can only come back, and an available one cannot', async () => {
  mount();
  const sold = (await screen.findByText('F2LXK1ABC2')).closest('tr') as HTMLElement;
  const available = screen.getByText('F2LXK1ABC1').closest('tr') as HTMLElement;

  expect(within(sold).getByRole('button', { name: 'Registrar devolución' })).toBeInTheDocument();
  expect(within(sold).queryByRole('button', { name: 'Apartar' })).toBeNull();
  expect(within(sold).queryByRole('button', { name: 'Dar de baja' })).toBeNull();
  expect(within(available).queryByRole('button', { name: 'Registrar devolución' })).toBeNull();
});

test('a refusal on a device is shown as the server said it', async () => {
  actionReply = { status: 400, body: { detail: 'El equipo F2LXK1ABC1 está vendido: no admite esta operación.' } };
  mount();
  const row = (await screen.findByText('F2LXK1ABC1')).closest('tr') as HTMLElement;

  fireEvent.click(within(row).getByRole('button', { name: 'Dar de baja' }));
  fireEvent.change(screen.getByLabelText('Motivo de la operación'), { target: { value: 'Roto' } });
  fireEvent.click(screen.getByRole('button', { name: 'Confirmar' }));

  expect(await screen.findByText(/no admite esta operación/)).toBeInTheDocument();
});

test('someone who can only look gets the table and nothing to press', async () => {
  mount({ canAdjust: false });
  await screen.findByText('F2LXK1ABC1');

  expect(screen.queryByRole('button', { name: '+ Registrar equipo' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Apartar' })).toBeNull();
});

test('an empty shelf says so', async () => {
  rows = [];
  mount();
  expect(await screen.findByText(/No hay equipos/)).toBeInTheDocument();
});
