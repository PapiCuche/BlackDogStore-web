import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { StockUnitForm } from '@/app/admin/components/StockUnitForm';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * SERIALIZED-STOCK · «Registrar equipo».
 *
 * CADA EQUIPO FÍSICO ES UNA UNIDAD. El formulario pide, a la vista y desde el
 * principio, lo que identifica a UN equipo: su número de serie y su IMEI. No
 * hay una «cantidad» que sustituya a esas identidades, y los campos no se
 * esconden a la espera de que alguien adivine qué producto elegir.
 *
 * Qué vale como serie o como IMEI lo decide el servidor; aquí se fija que el
 * formulario lo pide, lo envía tal cual y dice el error junto al campo.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

const BRANCHES = [{ id: 1, name: 'Centro' }, { id: 2, name: 'Norte' }];
type Product = { id: number; name: string; price: string; is_serialized: boolean; requires_imei: boolean; stock: number; can_change_tracking: boolean };
let products: Product[];
let calls: { method: string; path: string; body: Record<string, unknown> | null }[];
let receive: { status: number; body: unknown };

const IMEI_A = '356938035643809';
const IMEI_B = '356938035643817';

function reply(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

beforeEach(() => {
  products = [
    { id: 10, name: 'iPhone 16 Pro Max', price: '4500.00', is_serialized: true, requires_imei: true, stock: 2, can_change_tracking: false },
    { id: 11, name: 'MacBook Air', price: '5200.00', is_serialized: true, requires_imei: false, stock: 0, can_change_tracking: true },
    { id: 12, name: 'Cable USB-C', price: '49.00', is_serialized: false, requires_imei: false, stock: 30, can_change_tracking: false },
    { id: 13, name: 'iPad Air', price: '2900.00', is_serialized: false, requires_imei: false, stock: 0, can_change_tracking: true },
  ];
  calls = [];
  receive = { status: 201, body: { results: [{ id: 501 }] } };
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const method = (init.method ?? 'GET').toUpperCase();
    const path = String(input).replace(/^.*\/admin\/inventory\/units/, '');
    const body = typeof init.body === 'string' ? JSON.parse(init.body) : null;
    calls.push({ method, path, body });
    if (method === 'GET') return reply(200, { results: products });
    if (path.endsWith('/serialization/')) {
      const id = Number(path.split('/')[2]);
      products = products.map((p) => (p.id === id ? { ...p, is_serialized: body.is_serialized, requires_imei: body.requires_imei } : p));
      return reply(200, products.find((p) => p.id === id));
    }
    return reply(receive.status, receive.body);
  });
});

function mount(props: Partial<Parameters<typeof StockUnitForm>[0]> = {}) {
  const onSaved = jest.fn();
  const onClose = jest.fn();
  render(
    <StockUnitForm
      branch={1} branches={BRANCHES} canChangeTracking
      conditions={[{ value: 'new', label: 'Nuevo' }, { value: 'used', label: 'Usado' }]}
      onSaved={onSaved} onClose={onClose} {...props}
    />,
  );
  return { onSaved, onClose };
}

const type = (label: string | RegExp, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
const posts = () => calls.filter((c) => c.method === 'POST' && c.path === '/');

async function pick(product: string) {
  await screen.findByRole('option', { name: new RegExp(product) });
  type('Producto / modelo', String(products.find((p) => p.name === product)!.id));
}

test('the form asks for what identifies ONE device, in sight from the start', async () => {
  mount();
  await screen.findByRole('option', { name: /iPhone 16 Pro Max/ });

  // Antes de elegir nada: los campos del equipo ya están ahí.
  for (const label of ['Sucursal', 'Producto / modelo', 'Condición', /^Número de serie/, /^IMEI/, /^IMEI 2/,
    /^Costo/, /^Precio de este equipo/, /^Motivo o documento de ingreso/]) {
    expect(screen.getAllByLabelText(label).length).toBeGreaterThan(0);
  }
  // Y ninguna «cantidad»: un equipo es uno.
  expect(screen.queryByLabelText(/cantidad/i)).toBeNull();
  expect((screen.getByLabelText('Sucursal') as HTMLSelectElement).value).toBe('1');
});

test('saving one device sends that device with its own serial and IMEI', async () => {
  const { onSaved, onClose } = mount();
  await pick('iPhone 16 Pro Max');
  expect(screen.getByLabelText(/^IMEI \*/)).toBeRequired();

  type(/^Número de serie/, 'F2LXK1AAA1');
  type(/^IMEI \*/, IMEI_A);
  type(/^IMEI 2/, IMEI_B);
  type(/^Costo/, '3800');
  type(/^Precio de este equipo/, '4400');
  type(/^Motivo o documento de ingreso/, 'Factura F001-234');
  fireEvent.click(screen.getByRole('button', { name: 'Guardar equipo' }));

  await waitFor(() => expect(onSaved).toHaveBeenCalled());
  expect(posts()).toEqual([expect.objectContaining({ body: {
    product_id: 10, branch: 1, reason: 'Factura F001-234',
    units: [{ serial_number: 'F2LXK1AAA1', imei: IMEI_A, imei2: IMEI_B, condition: 'new', cost: '3800', price_override: '4400' }],
  } })]);
  expect(onClose).toHaveBeenCalled();
});

test('"save and add another" keeps the model and asks for the next device', async () => {
  const { onSaved, onClose } = mount();
  await pick('iPhone 16 Pro Max');
  type(/^Número de serie/, 'F2LXK1AAA1');
  type(/^IMEI \*/, IMEI_A);
  type(/^Motivo o documento de ingreso/, 'Factura F001-234');
  fireEvent.click(screen.getByRole('button', { name: 'Guardar y añadir otro' }));

  const done = await screen.findByRole('list', { name: 'Equipos registrados ahora' });
  expect(within(done).getByText(/#1/)).toHaveTextContent('F2LXK1AAA1');
  expect(within(done).getByText(/#1/)).toHaveTextContent(IMEI_A);
  expect(onSaved).toHaveBeenCalledTimes(1);
  expect(onClose).not.toHaveBeenCalled();
  // El modelo y el motivo siguen; lo que es de UN equipo se vacía.
  expect((screen.getByLabelText('Producto / modelo') as HTMLSelectElement).value).toBe('10');
  expect((screen.getByLabelText(/^Motivo o documento de ingreso/) as HTMLInputElement).value).toBe('Factura F001-234');
  expect((screen.getByLabelText(/^Número de serie/) as HTMLInputElement).value).toBe('');
  expect((screen.getByLabelText(/^IMEI \*/) as HTMLInputElement).value).toBe('');

  type(/^Número de serie/, 'F2LXK1BBB2');
  type(/^IMEI \*/, IMEI_B);
  fireEvent.click(screen.getByRole('button', { name: 'Guardar y añadir otro' }));

  await waitFor(() => expect(within(screen.getByRole('list', { name: 'Equipos registrados ahora' })).getAllByRole('listitem')).toHaveLength(2));
  expect(posts()).toHaveLength(2);
  expect((posts()[1].body!.units as unknown[])).toHaveLength(1);
});

test('what the server refuses is said next to the field, and nothing typed is lost', async () => {
  receive = { status: 400, body: { detail: 'El IMEI no supera su dígito de control: revisa que esté bien copiado.', line: 1, field: 'imei' } };
  const { onSaved } = mount();
  await pick('iPhone 16 Pro Max');
  type(/^Número de serie/, 'F2LXK1AAA1');
  type(/^IMEI \*/, '356938035643800');
  type(/^Motivo o documento de ingreso/, 'Compra');
  fireEvent.click(screen.getByRole('button', { name: 'Guardar equipo' }));

  const imei = await screen.findByLabelText(/^IMEI \*/);
  await waitFor(() => expect(imei).toHaveAttribute('aria-invalid', 'true'));
  expect(imei).toHaveAccessibleDescription(/no supera su dígito de control/);
  expect(screen.getByLabelText(/^Número de serie/)).not.toHaveAttribute('aria-invalid', 'true');
  expect((imei as HTMLInputElement).value).toBe('356938035643800');
  expect(onSaved).not.toHaveBeenCalled();
});

test('a device without a cellular line is told its IMEI is optional, not hidden', async () => {
  mount();
  await pick('MacBook Air');

  expect(screen.getByLabelText(/^IMEI \(opcional/)).not.toBeRequired();
  type(/^Número de serie/, 'C02LAPTOP01');
  type(/^Motivo o documento de ingreso/, 'Compra');
  fireEvent.click(screen.getByRole('button', { name: 'Guardar equipo' }));

  await waitFor(() => expect(posts()).toHaveLength(1));
  expect((posts()[0].body!.units as { imei: string }[])[0].imei).toBe('');
});

test('a product counted by quantity explains itself instead of going quiet', async () => {
  mount();
  await pick('Cable USB-C');

  const note = screen.getByRole('note');
  expect(note).toHaveTextContent(/se controla por cantidad/);
  expect(note).toHaveTextContent(/30 unidades/);
  expect(screen.queryByRole('button', { name: 'Activar seguimiento por serie' })).toBeNull();
  expect(screen.getByRole('button', { name: 'Guardar equipo' })).toBeDisabled();
  // Los campos del equipo siguen a la vista.
  expect(screen.getByLabelText(/^Número de serie/)).toBeInTheDocument();
});

test('with an empty shelf and the authority, serial tracking is turned on from here', async () => {
  mount();
  await pick('iPad Air');
  expect(screen.getByRole('note')).toHaveTextContent(/se controla por cantidad/);

  fireEvent.click(screen.getByLabelText('Es un equipo con línea celular (lleva IMEI)'));
  fireEvent.click(screen.getByRole('button', { name: 'Activar seguimiento por serie' }));

  await waitFor(() => expect(screen.queryByRole('note')).toBeNull());
  expect(calls.find((c) => c.path === '/products/13/serialization/')?.body).toEqual({ is_serialized: true, requires_imei: true });
  expect(screen.getByLabelText(/^IMEI \*/)).toBeRequired();
  type(/^Número de serie/, 'DMPXK1AAA1');
  type(/^IMEI \*/, IMEI_A);
  type(/^Motivo o documento de ingreso/, 'Compra');
  expect(screen.getByRole('button', { name: 'Guardar equipo' })).toBeEnabled();
});

test('without the authority the explanation says who can change it', async () => {
  mount({ canChangeTracking: false });
  await pick('iPad Air');

  expect(screen.getByRole('note')).toHaveTextContent(/administra el inventario/);
  expect(screen.queryByRole('button', { name: 'Activar seguimiento por serie' })).toBeNull();
});

test('with every branch in view, the branch the device enters has to be chosen', async () => {
  mount({ branch: 'all' });
  await pick('MacBook Air');
  type(/^Número de serie/, 'C02LAPTOP01');
  type(/^Motivo o documento de ingreso/, 'Compra');
  expect(screen.getByRole('button', { name: 'Guardar equipo' })).toBeDisabled();

  type('Sucursal', '2');
  fireEvent.click(screen.getByRole('button', { name: 'Guardar equipo' }));

  await waitFor(() => expect(posts()).toHaveLength(1));
  expect(posts()[0].body!.branch).toBe(2);
});

test('several devices at once are still one row per device, each with its own identifiers', async () => {
  receive = { status: 400, body: { detail: 'Ese IMEI ya está registrado en otro equipo de la empresa.', line: 2, field: 'imei' } };
  mount();
  await pick('iPhone 16 Pro Max');
  type(/^Motivo o documento de ingreso/, 'Compra');

  fireEvent.click(screen.getByRole('button', { name: 'Registrar varios equipos a la vez' }));
  const first = screen.getByRole('group', { name: 'Equipo 1' });
  type(/^Número de serie/, 'F2LXK1AAA1');
  fireEvent.change(within(first).getByLabelText(/^IMEI \*/), { target: { value: IMEI_A } });
  fireEvent.click(screen.getByRole('button', { name: 'Añadir otro equipo' }));
  const second = screen.getByRole('group', { name: 'Equipo 2' });
  fireEvent.change(within(second).getByLabelText(/^Número de serie/), { target: { value: 'F2LXK1BBB2' } });
  fireEvent.change(within(second).getByLabelText(/^IMEI \*/), { target: { value: IMEI_A } });
  fireEvent.click(screen.getByRole('button', { name: 'Guardar 2 equipos' }));

  await waitFor(() => expect(posts()).toHaveLength(1));
  expect((posts()[0].body!.units as { serial_number: string }[]).map((u) => u.serial_number)).toEqual(['F2LXK1AAA1', 'F2LXK1BBB2']);
  // El error es del segundo equipo, y de su IMEI.
  await waitFor(() => expect(within(second).getByLabelText(/^IMEI \*/)).toHaveAttribute('aria-invalid', 'true'));
  expect(within(first).getByLabelText(/^IMEI \*/)).not.toHaveAttribute('aria-invalid', 'true');
});
