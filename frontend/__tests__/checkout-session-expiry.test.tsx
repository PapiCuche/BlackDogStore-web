/**
 * AUTH-C (review P2-1 of #181) · the session closes while buying.
 *
 * A buyer who opened the checkout signed in must never have the order placed
 * as a stranger's because the session closed meanwhile (a guest read can end
 * it with no sign but `X-Session-Expired`). The page asks the server before
 * sending, stops, and lets the buyer choose; it never sends the order by itself.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

import CheckoutPage from '@/app/checkout/page';
import { fetchWithAuth, getCurrentUser } from '@/app/lib/auth';
import { __resetSessionStateForTests } from '@/app/lib/session-state';

jest.mock('next/navigation', () => {
  const router = { push: jest.fn(), replace: jest.fn() };
  return { useRouter: () => router };
});
jest.mock('@/app/lib/auth', () => ({
  fetchWithAuth: jest.fn(),
  getCurrentUser: jest.fn(),
  logout: jest.fn(),
}));
jest.mock('@/app/lib/cart', () => ({
  ...jest.requireActual('@/app/lib/cart'),
  getSessionKey: () => 'sesion-de-prueba',
  readCart: jest.fn(),
}));

/* eslint-disable-next-line @typescript-eslint/no-require-imports */
const { readCart } = require('@/app/lib/cart') as { readCart: jest.Mock };
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;
const who = getCurrentUser as jest.MockedFunction<typeof getCurrentUser>;

const LINE = { id: 7, quantity: 1, available: true, product: { id: 3, name: 'Funda', price: '50.00', slug: 'funda' } };
const answer = (status: number, body: unknown) =>
  Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) } as Response);

let sessionOpen: boolean;
const orders = () => send.mock.calls.filter(([url]) => String(url).includes('create-checkout-session'));

beforeEach(() => {
  __resetSessionStateForTests();
  sessionOpen = true;
  send.mockReset();
  send.mockImplementation((url) => {
    const target = String(url);
    if (target.includes('/auth/session/')) {
      return sessionOpen
        ? answer(200, { idle_remaining_s: 1700, absolute_remaining_s: 600000, idle_timeout_s: 1800, warn_before_s: 300 })
        : answer(401, { code: 'session_expired' });
    }
    if (target.includes('create-checkout-session')) return answer(400, { detail: 'Datos inválidos.' });
    return answer(400, { detail: 'sin cotización' });
  });
  who.mockResolvedValue({ id: 1, username: 'ana', email: 'ana@example.test', first_name: 'Ana', last_name: '', role: 'customer', is_staff: false });
  readCart.mockResolvedValue({ status: 'ok', items: [LINE] });
  global.fetch = jest.fn(() => answer(400, {})) as unknown as typeof fetch;
});

async function open() {
  await act(async () => { render(<CheckoutPage />); });
  await screen.findByText('Funda');
}

const submit = () => act(async () => {
  fireEvent.submit(screen.getByRole('button', { name: /Continuar al pago/ }).closest('form')!);
});

it('signed in and still open: the order goes out', async () => {
  await open();
  await submit();
  await waitFor(() => expect(orders()).toHaveLength(1));
});

it('signed in but the session closed: nothing is sent, and the buyer is asked', async () => {
  await open();
  sessionOpen = false;

  await submit();

  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Tu sesión se cerró antes de enviar el pedido'));
  expect(orders()).toHaveLength(0);
  expect(screen.getByRole('link', { name: 'Iniciar sesión' })).toHaveAttribute('href', expect.stringContaining('/auth'));
});

it('choosing to buy as a guest does not send by itself: the buyer presses again', async () => {
  await open();
  sessionOpen = false;
  await submit();
  await screen.findByRole('button', { name: 'Comprar como invitado' });

  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Comprar como invitado' })); });
  expect(orders()).toHaveLength(0);

  await submit();
  await waitFor(() => expect(orders()).toHaveLength(1));
});

it('a 401 on the order itself stops and asks too, without resending', async () => {
  await open();
  send.mockImplementation((url) => {
    const target = String(url);
    if (target.includes('/auth/session/')) {
      return answer(200, { idle_remaining_s: 1700, absolute_remaining_s: 600000, idle_timeout_s: 1800, warn_before_s: 300 });
    }
    if (target.includes('create-checkout-session')) return answer(401, { code: 'session_expired' });
    return answer(400, { detail: 'sin cotización' });
  });

  await submit();

  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Tu sesión se cerró'));
  expect(orders()).toHaveLength(1);
});
