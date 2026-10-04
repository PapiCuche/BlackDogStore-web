import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

import CartPage from '@/app/cart/page';
import ProductDetail from '@/app/components/ProductDetail';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * CART-CSRF · el carrito de quien inició sesión.
 *
 * El carrito es de la sesión del navegador (`session_key`), no de la cuenta, y
 * por eso sus peticiones salían con `fetch` a secas. Pero la cookie de acceso
 * viaja sola en cada petición al mismo origen, y con ella el servidor exige el
 * token CSRF: un cliente con sesión iniciada recibía «CSRF token missing» al
 * agregar, cambiar la cantidad, quitar una línea o validar un cupón.
 *
 * Lo que se fija: todo lo que ESCRIBE en el carrito sale por `fetchWithAuth`,
 * que es quien pone el token.
 */

jest.mock('@/app/lib/auth', () => ({
  fetchWithAuth: jest.fn(),
  getCurrentUser: jest.fn(() => Promise.resolve({ username: 'cliente' })),
}));

const withSession = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;
const plain = jest.fn();

const LINE = {
  id: 7, quantity: 1,
  product: { id: 3, name: 'Producto 3', price: '300.00', slug: 'producto-3', image_url: '' },
};

function answer(body: unknown, status = 200) {
  return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) } as Response);
}

beforeEach(() => {
  withSession.mockReset();
  withSession.mockImplementation(() => answer({ code: 'OFERTA', discount_percent: 10 }));
  plain.mockReset();
  plain.mockImplementation((input: RequestInfo | URL) => answer(String(input).includes('/cart/') ? [LINE] : []));
  global.fetch = plain as unknown as typeof fetch;
});

/** Lo que salió por `fetchWithAuth`: [método, ruta sin la base de la API]. */
function written(): Array<[string, string]> {
  return withSession.mock.calls.map(([url, options]) => [
    String(options?.method ?? 'GET'), String(url).replace(/^.*\/api/, ''),
  ]);
}

/** Ninguna escritura puede salir sin el token. */
function writesWithoutToken(): string[] {
  return plain.mock.calls
    .filter(([, options]) => options?.method && options.method !== 'GET')
    .map(([url, options]) => `${options.method} ${String(url).replace(/^.*\/api/, '')}`);
}

test('agregar al carrito desde la ficha sale con el token de la sesión', async () => {
  await act(async () => {
    render(<ProductDetail product={{ id: 3, slug: 'producto-3', name: 'Producto 3', price: 300, inventory: 5 }} />);
  });

  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Agregar al carrito' }));
  });

  expect(written()).toContainEqual(['POST', '/cart/add/']);
  expect(writesWithoutToken()).toEqual([]);
  expect(await screen.findByText('Producto agregado al carrito.')).toBeInTheDocument();
});

test('cambiar la cantidad, quitar una línea y validar un cupón salen con el token', async () => {
  await act(async () => { render(<CartPage />); });
  const quantity = await screen.findByLabelText('Cantidad de Producto 3');

  await act(async () => { fireEvent.change(quantity, { target: { value: '2' } }); });
  await waitFor(() => expect(written().some(([method]) => method === 'PATCH')).toBe(true));

  await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Eliminar/ })); });
  await waitFor(() => expect(written().some(([method]) => method === 'DELETE')).toBe(true));

  fireEvent.change(await screen.findByPlaceholderText('Código'), { target: { value: 'OFERTA' } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Aplicar' })); });

  const calls = written();
  expect(calls.find(([method]) => method === 'PATCH')?.[1]).toMatch(/^\/cart\/7\/\?session_key=/);
  expect(calls.find(([method]) => method === 'DELETE')?.[1]).toMatch(/^\/cart\/7\/\?session_key=/);
  expect(calls).toContainEqual(['POST', '/coupons/validate/']);
  expect(writesWithoutToken()).toEqual([]);
});
