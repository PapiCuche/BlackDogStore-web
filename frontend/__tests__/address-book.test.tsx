import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { AddressBook } from '@/app/components/AddressBook';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * DELIVERY-01 · «Mis direcciones».
 *
 * The list is the server's. An entry is a door or an agency, with who receives
 * there; one is the default. After every change the list is asked for again.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

const HOME = {
  id: 1, label: 'Casa', kind: 'address', recipient_name: 'Ana Torres', recipient_document_type: '', recipient_document_number: '',
  recipient_phone: '987654321', address_line: 'Av. Siempre Viva 123', district: 'Cercado', city: 'Arequipa', department: 'Arequipa',
  reference: '', carrier: null as number | null, carrier_name: '', agency_name: '', agency_locality: '', is_default: true,
};
const OFFICE = {
  ...HOME, id: 2, label: '', kind: 'agency', recipient_name: 'Rosa Quispe', address_line: '', district: '', city: '',
  carrier: 7, carrier_name: 'Agencia Uno', agency_name: 'Agencia Centro', agency_locality: 'Juliaca, Puno', is_default: false,
};

let entries: (typeof HOME)[];
let limit: number;
let refusal: { status: number; body: unknown } | null;
let calls: { method: string; path: string; body: unknown }[];

function reply(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

beforeEach(() => {
  entries = [HOME, OFFICE];
  limit = 10;
  refusal = null;
  calls = [];
  jest.spyOn(window, 'confirm').mockReturnValue(true);
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const method = init.method ?? 'GET';
    const path = String(input).replace(/^.*\/account\/addresses\//, '/');
    if (method === 'GET') return reply(200, { results: entries, limit, agency_carriers: [{ id: 7, name: 'Agencia Uno' }] });
    calls.push({ method, path, body: init.body ? JSON.parse(String(init.body)) : null });
    if (refusal) return reply(refusal.status, refusal.body);
    if (method === 'DELETE') { entries = entries.filter((e) => `/${e.id}/` !== path); return reply(204, null); }
    if (path.endsWith('/default/')) { entries = entries.map((e) => ({ ...e, is_default: `/${e.id}/default/` === path })); return reply(200, entries[0]); }
    if (method === 'POST') { entries = [...entries, { ...HOME, ...(calls[calls.length - 1].body as object), id: 3, is_default: false }]; return reply(201, entries[2]); }
    entries = entries.map((e) => (`/${e.id}/` === path ? { ...e, ...(calls[calls.length - 1].body as object) } : e));
    return reply(200, entries[0]);
  });
});

afterEach(() => jest.restoreAllMocks());

async function opened(props = {}) {
  render(<AddressBook {...props} />);
  await screen.findAllByTestId('saved-address');
}

test('the book lists each entry by what a person recognises it by', async () => {
  await opened();
  const [home, office] = screen.getAllByTestId('saved-address');
  expect(within(home).getByText('Casa')).toBeInTheDocument();
  expect(within(home).getByText('Predeterminada')).toBeInTheDocument();
  expect(within(home).getByText('Av. Siempre Viva 123, Cercado, Arequipa')).toBeInTheDocument();
  expect(within(office).getByText('Agencia Uno · Agencia Centro · Juliaca, Puno')).toBeInTheDocument();
  expect(within(office).getByText('Recibe: Rosa Quispe')).toBeInTheDocument();
  expect(within(office).queryByText('Predeterminada')).not.toBeInTheDocument();
});

test('an empty book says so', async () => {
  entries = [];
  render(<AddressBook />);
  expect(await screen.findByText('Todavía no guardaste ninguna dirección.')).toBeInTheDocument();
});

test('a new door starts from the buyer\'s own name and is sent trimmed', async () => {
  await opened({ defaultRecipient: 'Ana Torres' });
  fireEvent.click(screen.getByRole('button', { name: 'Agregar dirección' }));
  expect(screen.getByLabelText('Quién recibe')).toHaveValue('Ana Torres');
  fireEvent.change(screen.getByLabelText('Dirección'), { target: { value: '  Calle Dos 45 ' } });
  fireEvent.change(screen.getByLabelText('Distrito'), { target: { value: 'Yanahuara' } });
  fireEvent.change(screen.getByLabelText('Ciudad'), { target: { value: 'Arequipa' } });
  fireEvent.click(screen.getByRole('button', { name: 'Guardar dirección' }));

  await waitFor(() => expect(screen.getAllByTestId('saved-address')).toHaveLength(3));
  expect(calls).toEqual([{ method: 'POST', path: '/', body: expect.objectContaining({
    kind: 'address', recipient_name: 'Ana Torres', address_line: 'Calle Dos 45', district: 'Yanahuara', city: 'Arequipa', carrier: null,
  }) }]);
  expect(screen.queryByRole('form', { name: 'Formulario de dirección' })).not.toBeInTheDocument();
});

test('an agency asks for a carrier of the shop, its office and its place', async () => {
  await opened();
  fireEvent.click(screen.getByRole('button', { name: 'Agregar dirección' }));
  fireEvent.change(screen.getByLabelText('Tipo'), { target: { value: 'agency' } });
  expect(screen.queryByLabelText('Dirección')).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Quién recibe'), { target: { value: 'Rosa Quispe' } });
  fireEvent.change(screen.getByLabelText('Agencia de transporte'), { target: { value: '7' } });
  fireEvent.change(screen.getByLabelText('Sede de la agencia'), { target: { value: 'Agencia Sur' } });
  fireEvent.change(screen.getByLabelText('Localidad'), { target: { value: 'Tacna' } });
  fireEvent.change(screen.getByLabelText('Documento de quien recibe'), { target: { value: 'dni' } });
  fireEvent.change(screen.getByLabelText('Número de documento'), { target: { value: ' 70000002 ' } });
  fireEvent.click(screen.getByRole('button', { name: 'Guardar dirección' }));

  await waitFor(() => expect(calls).toHaveLength(1));
  expect(calls[0].body).toEqual(expect.objectContaining({
    kind: 'agency', carrier: 7, agency_name: 'Agencia Sur', agency_locality: 'Tacna',
    recipient_name: 'Rosa Quispe', recipient_document_type: 'dni', recipient_document_number: '70000002',
  }));
});

test('without a document type no number travels', async () => {
  await opened();
  fireEvent.click(screen.getByRole('button', { name: 'Editar Casa' }));
  fireEvent.change(screen.getByLabelText('Documento de quien recibe'), { target: { value: 'dni' } });
  fireEvent.change(screen.getByLabelText('Número de documento'), { target: { value: '70000002' } });
  fireEvent.change(screen.getByLabelText('Documento de quien recibe'), { target: { value: '' } });
  expect(screen.queryByLabelText('Número de documento')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Guardar dirección' }));
  await waitFor(() => expect(calls).toHaveLength(1));
  expect(calls[0]).toEqual({ method: 'PATCH', path: '/1/', body: expect.objectContaining({ recipient_document_type: '', recipient_document_number: '' }) });
});

test('what the server refuses is said next to its field and the form stays open', async () => {
  refusal = { status: 400, body: { city: ['Este dato es necesario.'] } };
  await opened();
  fireEvent.click(screen.getByRole('button', { name: 'Editar Casa' }));
  fireEvent.change(screen.getByLabelText('Ciudad'), { target: { value: '' } });
  fireEvent.click(screen.getByRole('button', { name: 'Guardar dirección' }));

  expect(await screen.findByText('Este dato es necesario.')).toBeInTheDocument();
  expect(screen.getByLabelText('Ciudad')).toHaveAttribute('aria-invalid', 'true');
  expect(screen.getByText('Revisa los datos marcados.')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Guardar dirección' })).toBeInTheDocument();
});

test('another entry is chosen as the default', async () => {
  await opened();
  fireEvent.click(screen.getByRole('button', { name: /Usar Agencia Uno · Agencia Centro · Juliaca, Puno como predeterminada/ }));
  await waitFor(() => expect(calls).toEqual([{ method: 'POST', path: '/2/default/', body: {} }]));
  await waitFor(() => expect(within(screen.getAllByTestId('saved-address')[1]).getByText('Predeterminada')).toBeInTheDocument());
});

test('taking an entry out asks first, and does nothing on «no»', async () => {
  (window.confirm as jest.Mock).mockReturnValueOnce(false);
  await opened();
  fireEvent.click(screen.getByRole('button', { name: 'Quitar Casa' }));
  expect(calls).toEqual([]);

  fireEvent.click(screen.getByRole('button', { name: 'Quitar Casa' }));
  await waitFor(() => expect(screen.getAllByTestId('saved-address')).toHaveLength(1));
  expect(calls).toEqual([{ method: 'DELETE', path: '/1/', body: null }]);
});

test('a full book does not offer one more', async () => {
  limit = 2;
  await opened();
  expect(screen.getByRole('button', { name: 'Agregar dirección' })).toBeDisabled();
  expect(screen.getByText(/Llegaste al máximo de 2 direcciones/)).toBeInTheDocument();
});

test('a shop with no agencies says so instead of an empty list', async () => {
  send.mockImplementation(async () => reply(200, { results: [], limit: 10, agency_carriers: [] }));
  render(<AddressBook />);
  fireEvent.click(await screen.findByRole('button', { name: 'Agregar dirección' }));
  fireEvent.change(screen.getByLabelText('Tipo'), { target: { value: 'agency' } });
  expect(screen.getByText('Esta tienda todavía no tiene agencias configuradas.')).toBeInTheDocument();
});

test('a book that cannot be loaded says so', async () => {
  send.mockImplementation(async () => reply(404, { detail: 'Dirección no encontrada.' }));
  render(<AddressBook />);
  expect(await screen.findByRole('alert')).toHaveTextContent('Dirección no encontrada.');
  expect(screen.queryByRole('button', { name: 'Agregar dirección' })).not.toBeInTheDocument();
});
