import fs from 'fs';
import path from 'path';

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import CartPage from '@/app/cart/page';
import { CartItemCard } from '@/app/components/CartItemCard';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * INVENTORY-UNIFIED-01 · un producto que dejó de venderse en la web y seguía
 * en un carrito.
 *
 * El servidor ya no manda su precio ni su stock: manda la línea marcada
 * `available: false`, con el nombre y la imagen. Lo que se fija aquí es que el
 * carrito lo diga —«Ya no está disponible»—, que no lo cuente en el total, que
 * deje quitarlo y que no ofrezca seguir al pago con él dentro (el pago lo
 * rechazaría de todos modos).
 */

jest.mock('@/app/lib/auth', () => ({
  fetchWithAuth: jest.fn(),
  getCurrentUser: jest.fn(() => Promise.resolve(null)),
}));

const withSession = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;
const plain = jest.fn();

const ON_SALE = {
  id: 7, quantity: 2, available: true,
  product: { id: 3, name: 'Funda', price: '50.00', slug: 'funda', image_url: '' },
};
const GONE = {
  id: 8, quantity: 3, available: false,
  product: { id: 4, name: 'Flex de carga', image_url: '' },
};

function answer(body: unknown, status = 200) {
  return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) } as Response);
}

function cartIs(lines: unknown[]) {
  plain.mockImplementation((input: RequestInfo | URL) => answer(String(input).includes('/cart/') ? lines : []));
}

beforeEach(() => {
  withSession.mockReset();
  withSession.mockImplementation(() => answer({}, 204));
  plain.mockReset();
  global.fetch = plain as unknown as typeof fetch;
});

describe('la línea de un producto que ya no se vende', () => {
  it('lo dice, sin precio, sin cantidad editable y sin enlace a una ficha que no existe', () => {
    render(<CartItemCard {...GONE} onRemove={jest.fn()} />);

    expect(screen.getByText('Flex de carga')).toBeInTheDocument();
    expect(screen.getByText('Ya no está disponible')).toBeInTheDocument();
    expect(screen.queryByRole('link')).toBeNull();
    expect(screen.queryByRole('spinbutton')).toBeNull();
    expect(screen.queryByText(/S\//)).toBeNull();
    expect(screen.queryByText(/NaN/)).toBeNull();
  });

  it('se puede quitar, y el botón dice qué quita', () => {
    const onRemove = jest.fn();
    render(<CartItemCard {...GONE} onRemove={onRemove} />);
    fireEvent.click(screen.getByRole('button', { name: 'Eliminar Flex de carga' }));
    expect(onRemove).toHaveBeenCalledTimes(1);
  });

  it('una línea que sí se vende sigue como siempre', () => {
    render(<CartItemCard {...ON_SALE} onRemove={jest.fn()} />);
    expect(screen.getByRole('link', { name: 'Funda' })).toHaveAttribute('href', '/product/funda');
    expect(screen.getByRole('spinbutton', { name: 'Cantidad de Funda' })).toBeInTheDocument();
    expect(screen.queryByText('Ya no está disponible')).toBeNull();
  });

  it('una respuesta de antes, sin la marca, es una línea en venta', () => {
    const { available: _available, ...legacy } = ON_SALE;
    render(<CartItemCard {...legacy} />);
    expect(screen.queryByText('Ya no está disponible')).toBeNull();
    expect(screen.getByRole('spinbutton')).toBeInTheDocument();
  });
});

describe('el carrito que la contiene', () => {
  it('abre, no la suma al total ni a las unidades, y no deja seguir al pago', async () => {
    cartIs([ON_SALE, GONE]);
    await act(async () => { render(<CartPage />); });
    await screen.findByText('Flex de carga');

    expect(screen.getByText('2 unidades')).toBeInTheDocument();
    const summary = screen.getByRole('heading', { name: 'Resumen' }).closest('aside')!;
    expect(within(summary).getAllByText('S/ 100.00').length).toBeGreaterThan(0);
    expect(document.body.textContent).not.toMatch(/NaN/);

    expect(screen.getByRole('alert')).toHaveTextContent(/ya no está disponible/i);
    expect(screen.queryByRole('link', { name: 'Continuar al checkout' })).toBeNull();
    expect(within(summary).getByText(/Quita lo que ya no está disponible para continuar/)).toBeInTheDocument();
  });

  it('al quitarla se pide al servidor y el pago vuelve a ofrecerse', async () => {
    cartIs([ON_SALE, GONE]);
    await act(async () => { render(<CartPage />); });
    await screen.findByText('Flex de carga');

    cartIs([ON_SALE]);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Eliminar Flex de carga' })); });

    await waitFor(() => expect(screen.queryByText('Flex de carga')).toBeNull());
    const deleted = withSession.mock.calls.map(([url, options]) => [options?.method, String(url).replace(/^.*\/api/, '')]);
    expect(deleted).toContainEqual(['DELETE', expect.stringMatching(/^\/cart\/8\//)]);
    expect(screen.getByRole('link', { name: 'Continuar al checkout' })).toBeInTheDocument();
  });

  it('con todo en venta no hay aviso y el pago se ofrece', async () => {
    cartIs([ON_SALE]);
    await act(async () => { render(<CartPage />); });
    await screen.findByText('Funda');

    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByRole('link', { name: 'Continuar al checkout' })).toBeInTheDocument();
  });

  it('si sólo queda lo que ya no se vende, no es un carrito vacío: se puede quitar', async () => {
    cartIs([GONE]);
    await act(async () => { render(<CartPage />); });

    expect(await screen.findByText('Ya no está disponible')).toBeInTheDocument();
    expect(screen.queryByText('Tu carrito está vacío')).toBeNull();
    expect(screen.getByRole('button', { name: 'Eliminar Flex de carga' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Continuar al checkout' })).toBeNull();
  });
});

describe('el resumen del pago', () => {
  // La página de pago no se monta en esta suite (pide el SDK de la pasarela);
  // como en el resto de sus pruebas, se fija sobre su código.
  const checkout = fs.readFileSync(path.join(process.cwd(), 'app/checkout/page.tsx'), 'utf8');

  it('no suma lo que ya no se vende ni le pone precio', () => {
    expect(checkout).toMatch(/item\.available === false \? 0 : Number\(item\.product\.price \?\? 0\) \* item\.quantity/);
    expect(checkout).not.toMatch(/Number\(item\.product\.price\)/);
  });

  it('lo dice y manda al carrito a quitarlo', () => {
    expect(checkout).toMatch(/Ya no está disponible\.[\s\S]{0,200}href="\/cart"/);
  });
});
