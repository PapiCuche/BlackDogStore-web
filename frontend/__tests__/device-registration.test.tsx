import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

import { DeviceRegistration } from '@/app/admin/service/components/DeviceRegistration';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * DEVICE-IDENTITY · registrar un equipo en la recepción.
 *
 * Qué identificadores pide cada tipo de equipo lo decide el servidor; aquí se
 * fija lo que el formulario ofrece, lo que envía y lo que hace cuando el
 * servidor dice «este equipo ya estuvo aquí».
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

const TYPES = [
  { value: 'phone', label: 'Teléfono' }, { value: 'tablet', label: 'Tablet' },
  { value: 'laptop', label: 'Laptop' }, { value: 'other', label: 'Otro' },
];
const IMEI = '356938035643809';

type Sent = { method: string; path: string; body: Record<string, unknown> | null };
let sent: Sent[];
let lookup: unknown[];
let createReply: { status: number; body: unknown };

function reply(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

beforeEach(() => {
  sent = [];
  lookup = [];
  createReply = { status: 201, body: { id: 77, display_name: 'Genérica X200', possible_duplicates: [] } };
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const method = (init.method ?? 'GET').toUpperCase();
    const path = String(input).replace(/^.*\/service/, '');
    sent.push({ method, path, body: typeof init.body === 'string' ? JSON.parse(init.body) : null });
    if (path.startsWith('/devices/lookup/')) return reply(200, { results: lookup });
    return reply(createReply.status, createReply.body);
  });
});

function mount(onRegistered = jest.fn()) {
  render(<DeviceRegistration slug="taller" customerId={5} deviceTypes={TYPES} onRegistered={onRegistered} />);
  return onRegistered;
}

function fill(label: string | RegExp, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

function choose(type: string) {
  fireEvent.change(screen.getByLabelText('Tipo de equipo'), { target: { value: type } });
}

const posts = () => sent.filter((call) => call.method === 'POST');

describe('qué pide cada tipo de equipo', () => {
  it('un teléfono pide serie e IMEI, y ofrece un segundo IMEI', () => {
    mount();
    choose('phone');
    expect(screen.getByLabelText(/Número de serie/)).toBeRequired();
    expect(screen.getByLabelText(/^IMEI$/)).toBeRequired();
    expect(screen.getByLabelText(/Segundo IMEI/)).not.toBeRequired();
  });

  it('una laptop pide serie y no muestra IMEI', () => {
    mount();
    choose('laptop');
    expect(screen.getByLabelText(/Número de serie/)).toBeRequired();
    expect(screen.queryByLabelText(/^IMEI$/)).not.toBeInTheDocument();
  });

  it('una tablet puede llevar IMEI o no', () => {
    mount();
    choose('tablet');
    expect(screen.getByLabelText(/^IMEI/)).not.toBeRequired();
  });

  it('«otro» no exige ningún identificador', () => {
    mount();
    choose('other');
    expect(screen.getByLabelText(/Número de serie/)).not.toBeRequired();
  });
});

describe('guardar', () => {
  it('envía los identificadores tal como se escribieron: el servidor los normaliza', async () => {
    const onRegistered = mount();
    choose('phone');
    fill('Marca', 'Genérica'); fill('Modelo', 'X200');
    fill(/Número de serie/, 'f2lxk1abc9'); fill(/^IMEI$/, IMEI);

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Guardar equipo' })); });

    expect(posts()).toEqual([expect.objectContaining({ path: '/devices/', body: {
      customer_id: 5, device_type: 'phone', brand: 'Genérica', model: 'X200',
      serial_number: 'f2lxk1abc9', imei: IMEI, imei2: '', identifiers_pending_reason: '',
    } })]);
    expect(onRegistered).toHaveBeenCalledWith(expect.objectContaining({ id: 77 }));
  });

  it('lo que no se pudo leer se guarda con su motivo', async () => {
    mount();
    choose('phone');
    fill('Marca', 'Genérica'); fill('Modelo', 'X200');
    fireEvent.click(screen.getByLabelText(/No se puede leer la serie o el IMEI/));
    fill('Motivo por el que falta', 'No enciende y no tiene bandeja SIM');

    expect(screen.getByLabelText(/^IMEI$/)).not.toBeRequired();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Guardar equipo' })); });

    expect(posts()[0].body).toMatchObject({ imei: '', identifiers_pending_reason: 'No enciende y no tiene bandeja SIM' });
  });

  it('un rechazo del servidor se muestra junto al campo que falla', async () => {
    createReply = { status: 400, body: { imei: ['El IMEI no supera su dígito de control: revisa que esté bien copiado.'] } };
    const onRegistered = mount();
    choose('phone');
    fill('Marca', 'Genérica'); fill('Modelo', 'X200');
    fill(/Número de serie/, 'F2LXK1ABC9'); fill(/^IMEI$/, '356938035643800');

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Guardar equipo' })); });

    const field = screen.getByLabelText(/^IMEI$/);
    expect(field).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText(/no supera su dígito de control/)).toBeInTheDocument();
    expect(onRegistered).not.toHaveBeenCalled();
  });

  it('si el cliente ya tenía ese equipo, se usa el que existe', async () => {
    createReply = { status: 409, body: {
      detail: 'Este cliente ya tiene registrado este equipo. Úsalo en lugar de crear otro.',
      existing_device: { id: 31, display_name: 'Genérica X100' },
    } };
    const onRegistered = mount();
    choose('phone');
    fill('Marca', 'Genérica'); fill('Modelo', 'X200');
    fill(/Número de serie/, 'F2LXK1ABC9'); fill(/^IMEI$/, IMEI);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Guardar equipo' })); });

    expect(screen.getByText(/ya tiene registrado este equipo/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Usar Genérica X100' }));
    expect(onRegistered).toHaveBeenCalledWith({ id: 31, display_name: 'Genérica X100' });
  });

  it('avisa cuando el equipo figuraba a nombre de otro cliente', async () => {
    createReply = { status: 201, body: { id: 77, display_name: 'Genérica X200', possible_duplicates: [
      { id: 12, display_name: 'Genérica X200', customer_name: 'Bruno Segundo', repair_orders_count: 2,
        last_repair_order: { id: 3, number: 'SRV-000003', status: 'delivered', received_at: '2026-08-01T10:00:00Z' } },
    ] } };
    const onRegistered = mount();
    choose('phone');
    fill('Marca', 'Genérica'); fill('Modelo', 'X200');
    fill(/Número de serie/, 'F2LXK1ABC9'); fill(/^IMEI$/, IMEI);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Guardar equipo' })); });

    expect(onRegistered).toHaveBeenCalledWith(expect.objectContaining({ id: 77 }));
    const note = screen.getByRole('status');
    expect(note).toHaveTextContent('Bruno Segundo');
    expect(note).toHaveTextContent('2 órdenes anteriores');
  });
});

describe('«este equipo ya estuvo aquí»', () => {
  const known = (customer: number, name: string) => ({
    id: 31, customer, customer_name: name, display_name: 'Genérica X100',
    serial_number: 'F2LXK1ABH1', imei: IMEI, repair_orders_count: 3,
    last_repair_order: { id: 9, number: 'SRV-000009', status: 'delivered', received_at: '2026-08-01T10:00:00Z' },
  });

  it('al salir del IMEI busca el equipo y muestra su historial', async () => {
    lookup = [known(5, 'Ana Cliente')];
    mount();
    choose('phone');
    fill(/^IMEI$/, IMEI);
    await act(async () => { fireEvent.blur(screen.getByLabelText(/^IMEI$/)); });

    await waitFor(() => expect(sent.some((call) => call.path.startsWith('/devices/lookup/'))).toBe(true));
    expect(sent.find((call) => call.path.startsWith('/devices/lookup/'))!.path).toContain(`imei=${IMEI}`);
    const note = await screen.findByRole('status');
    expect(note).toHaveTextContent('Este equipo ya estuvo aquí');
    expect(note).toHaveTextContent('3 órdenes anteriores');
    expect(note).toHaveTextContent('SRV-000009');
  });

  it('si es del mismo cliente, se reutiliza sin crear otro', async () => {
    lookup = [known(5, 'Ana Cliente')];
    const onRegistered = mount();
    choose('phone');
    fill(/^IMEI$/, IMEI);
    await act(async () => { fireEvent.blur(screen.getByLabelText(/^IMEI$/)); });

    fireEvent.click(await screen.findByRole('button', { name: 'Usar Genérica X100' }));

    expect(onRegistered).toHaveBeenCalledWith({ id: 31, display_name: 'Genérica X100' });
    expect(posts()).toEqual([]);
  });

  it('si figura a nombre de otro cliente, lo dice y deja registrarlo', async () => {
    lookup = [known(8, 'Bruno Segundo')];
    mount();
    choose('phone');
    fill(/^IMEI$/, IMEI);
    await act(async () => { fireEvent.blur(screen.getByLabelText(/^IMEI$/)); });

    const note = await screen.findByRole('status');
    expect(note).toHaveTextContent('Bruno Segundo');
    expect(screen.queryByRole('button', { name: /^Usar / })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Guardar equipo' })).toBeInTheDocument();
  });

  it('un número a medio escribir no se busca', async () => {
    mount();
    choose('phone');
    fill(/^IMEI$/, '35693803');
    await act(async () => { fireEvent.blur(screen.getByLabelText(/^IMEI$/)); });
    expect(sent.filter((call) => call.path.startsWith('/devices/lookup/'))).toEqual([]);
  });
});
