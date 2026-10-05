import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import RepairsPage from '@/app/repairs/page';
import { fetchWithAuth, getCurrentUser } from '@/app/lib/auth';

/**
 * TRACKING · «Mis reparaciones», desde la cuenta.
 *
 * La lista sale del servidor (las órdenes del cliente que esta cuenta ES). Una
 * orden dejada en el mostrador sin cuenta se suma presentando su enlace: nada
 * se vincula por correo ni por nombre.
 */

const push = jest.fn();
jest.mock('next/navigation', () => ({ useRouter: () => ({ push }) }));
jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn(), getCurrentUser: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;
const who = getCurrentUser as jest.MockedFunction<typeof getCurrentUser>;

const TOKEN = 'kQ3xV9aZ0bC1dE2fG3hI4jK5lM6nO7pQ8rS9tU0vW1x';
const ROW = {
  number: 'SRV-000042', status: 'in_repair', status_label: 'En reparación', device_summary: 'Genérica X200',
  received_at: '2026-10-04T10:00:00Z', closed_at: null, updated_at: '2026-10-05T14:00:00Z',
  tracking_path: `/seguimiento/${TOKEN}`,
};

let rows: typeof ROW[];
let claim: { status: number; body: unknown };
let posted: unknown[];

function reply(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

beforeEach(() => {
  rows = [ROW];
  claim = { status: 200, body: { linked: true } };
  posted = [];
  push.mockReset();
  who.mockReset();
  who.mockResolvedValue({ id: 1, username: 'ana' } as never);
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    if ((init.method ?? 'GET') === 'POST') {
      posted.push(JSON.parse(String(init.body)));
      if (claim.status === 200) rows = [ROW, { ...ROW, number: 'SRV-000050', tracking_path: '/seguimiento/OTRO' }];
      return reply(claim.status, claim.body);
    }
    return reply(200, { count: rows.length, results: rows });
  });
});

test('my repairs are listed with their state and a way into each one', async () => {
  render(<RepairsPage />);

  const link = await screen.findByRole('link', { name: /SRV-000042/ });
  expect(link).toHaveAttribute('href', `/seguimiento/${TOKEN}`);
  expect(screen.getByText('En reparación')).toBeInTheDocument();
  expect(screen.getByText('Genérica X200')).toBeInTheDocument();
});

test('someone who is not signed in is sent to sign in', async () => {
  who.mockResolvedValue(null);
  render(<RepairsPage />);
  await waitFor(() => expect(push).toHaveBeenCalledWith('/auth'));
  expect(send).not.toHaveBeenCalled();
});

test('with nothing yet, the page says how an order gets here', async () => {
  rows = [];
  render(<RepairsPage />);
  expect(await screen.findByText(/Todavía no hay reparaciones en tu cuenta/)).toBeInTheDocument();
});

test('pasting the whole link adds that order to the account', async () => {
  render(<RepairsPage />);
  await screen.findByRole('link', { name: /SRV-000042/ });

  fireEvent.change(screen.getByLabelText('Enlace de seguimiento'), {
    target: { value: ` https://tienda.example/seguimiento/${TOKEN}?x=1 ` },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Agregar a mi cuenta' }));

  expect(await screen.findByRole('link', { name: /SRV-000050/ })).toBeInTheDocument();
  expect(posted).toEqual([{ token: TOKEN }]);
});

test('an order that belongs to another account is refused with the reason', async () => {
  claim = { status: 409, body: { detail: 'Esta orden ya pertenece a otra cuenta.' } };
  render(<RepairsPage />);
  await screen.findByRole('link', { name: /SRV-000042/ });

  fireEvent.change(screen.getByLabelText('Enlace de seguimiento'), { target: { value: TOKEN } });
  fireEvent.click(screen.getByRole('button', { name: 'Agregar a mi cuenta' }));

  expect(await screen.findByText('Esta orden ya pertenece a otra cuenta.')).toBeInTheDocument();
});
