/**
 * INVENTORY-UNIFIED-01, revisión (g) · el pago con una línea que ya no se vende.
 *
 * El servidor rechaza ese carrito entero. La página no debe dejar llegar hasta
 * ahí a ciegas: lo dice, no suma esa línea, no deja enviar el formulario, y si
 * el servidor rechaza por cualquier otro producto enseña QUÉ producto fue.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

import CheckoutPage from '@/app/checkout/page';
import { fetchWithAuth } from '@/app/lib/auth';

jest.mock('next/navigation', () => {
  const router = { push: jest.fn(), replace: jest.fn() };
  return { useRouter: () => router };
});
jest.mock('@/app/lib/auth', () => ({
  fetchWithAuth: jest.fn(),
  getCurrentUser: jest.fn(() => Promise.resolve(null)),
}));
jest.mock('@/app/lib/cart', () => ({
  ...jest.requireActual('@/app/lib/cart'),
  getSessionKey: () => 'sesion-de-prueba',
  readCart: jest.fn(),
}));

/* eslint-disable-next-line @typescript-eslint/no-require-imports */
const { readCart } = require('@/app/lib/cart') as { readCart: jest.Mock };
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

const ON_SALE = { id: 7, quantity: 2, available: true, product: { id: 3, name: 'Funda', price: '50.00', slug: 'funda' } };
const GONE = { id: 8, quantity: 3, available: false, product: { id: 4, name: 'Flex de carga' } };

const answer = (status: number, body: unknown) =>
  Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) } as Response);

async function open(items: unknown[]) {
  readCart.mockResolvedValue({ status: 'ok', items });
  await act(async () => { render(<CheckoutPage />); });
  await screen.findByText('Funda');
}

const submit = () => screen.getByRole('button', { name: /Continuar al pago/ });

beforeEach(() => {
  send.mockReset();
  // La cotización y cualquier otra consulta: sin respuesta útil, la página sigue con su cuenta.
  send.mockImplementation(() => answer(400, { detail: 'sin cotización' }));
  global.fetch = jest.fn(() => answer(400, {})) as unknown as typeof fetch;
});

it('con una línea que ya no se vende: lo dice, no la suma y no deja enviar', async () => {
  await open([ON_SALE, GONE]);

  expect(screen.getByText('Flex de carga')).toBeInTheDocument();
  expect(screen.getByText(/Ya no está disponible\./)).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Quítalo en el carrito' })).toHaveAttribute('href', '/cart');
  expect(document.body.textContent).not.toMatch(/NaN/);
  expect(document.body.textContent).toContain('100.00');          // 2 × 50, sin la otra línea
  expect(submit()).toBeDisabled();
  expect(screen.getByText(/Quita del carrito lo que ya no está disponible para continuar/)).toBeInTheDocument();
});

it('aunque una respuesta antigua trajera precio en esa línea, no se suma', async () => {
  await open([ON_SALE, { ...GONE, product: { ...GONE.product, price: '999.00' } }]);
  expect(document.body.textContent).toContain('100.00');
  expect(document.body.textContent).not.toContain('999.00');
  expect(document.body.textContent).not.toContain('3097.00');     // 100 + 3 × 999
});

it('con todo en venta, se puede enviar', async () => {
  await open([ON_SALE]);
  expect(submit()).toBeEnabled();
  expect(screen.queryByText(/Ya no está disponible/)).toBeNull();
});

it('si el servidor rechaza el carrito, enseña qué producto fue', async () => {
  await open([ON_SALE]);
  send.mockImplementation((url) => String(url).includes('create-checkout-session')
    ? answer(400, { detail: 'No se pudo procesar el carrito.', errors: ['Funda ya no está disponible.', 'Stock insuficiente para Cable.'] })
    : answer(400, { detail: 'sin cotización' }));

  await act(async () => { fireEvent.submit(submit().closest('form')!); });

  await waitFor(() => expect(document.body.textContent).toContain('Funda ya no está disponible.'));
  expect(document.body.textContent).toContain('Stock insuficiente para Cable.');
  expect(document.body.textContent).toContain('No se pudo procesar el carrito.');
});
