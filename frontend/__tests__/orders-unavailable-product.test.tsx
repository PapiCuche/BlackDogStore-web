/**
 * INVENTORY-UNIFIED-01, revisión O2 · «Mis pedidos» y un producto que ya no se
 * vende en la web.
 *
 * El servidor ya no manda el producto entero en esa línea: manda su nombre y su
 * imagen, sin ficha ni precio de hoy. La línea del pedido —cantidad y precio
 * pagado— es del comprador y se sigue viendo igual; lo que no hay es un enlace
 * a una ficha que ya no existe para el público.
 */
import { act, fireEvent, render, screen, within } from '@testing-library/react';

import OrdersPage from '@/app/orders/page';
import { fetchWithAuth } from '@/app/lib/auth';

// El MISMO objeto en cada render: la página lo tiene entre las dependencias de su carga.
jest.mock('next/navigation', () => {
  const router = { push: jest.fn() };
  return { useRouter: () => router };
});
jest.mock('@/app/lib/auth', () => ({
  fetchWithAuth: jest.fn(),
  getCurrentUser: jest.fn(() => Promise.resolve({ username: 'compradora' })),
}));

const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

const ORDER = {
  id: 41, customer_name: 'Ana Torres', customer_email: 'ana@correo.test', total: '90.00', discount_amount: '0.00',
  coupon_code: '', paid: true, created_at: '2026-10-01T15:00:00Z', delivery: null,
  items: [
    // Sigue en venta: el producto entero, como siempre.
    { id: 1, quantity: 1, price: '50.00', product: { id: 3, name: 'Funda', slug: 'funda', price: '55.00', image_url: '' } },
    // Dejó de venderse: sólo nombre e imagen.
    { id: 2, quantity: 2, price: '20.00', product: { id: 4, name: 'Flex de carga', image_url: '' } },
  ],
};

async function open() {
  send.mockResolvedValue({ ok: true, status: 200, json: async () => [ORDER] } as Response);
  await act(async () => { render(<OrdersPage />); });
  await act(async () => { fireEvent.click(await screen.findByRole('button', { name: /Pedido #41/ })); });
}

const line = (name: string) => screen.getByText(name).closest('div.flex')! as HTMLElement;

describe('la línea de un producto que ya no se vende', () => {
  it('conserva su nombre, su cantidad y lo que se pagó, y no enlaza a ninguna ficha', async () => {
    await open();

    const gone = line('Flex de carga');
    expect(within(gone).queryByRole('link')).toBeNull();
    expect(gone).toHaveTextContent('2 × S/ 20.00');
    expect(gone).toHaveTextContent('S/ 40.00');
    expect(gone).toHaveTextContent('Ya no se vende en la tienda');
    expect(document.body.innerHTML).not.toContain('/product/undefined');
    expect(document.body.textContent).not.toMatch(/NaN|undefined/);
  });

  it('la de un producto que sigue en venta enlaza a su ficha como siempre', async () => {
    await open();

    const sold = line('Funda');
    expect(within(sold).getByRole('link', { name: 'Funda' })).toHaveAttribute('href', '/product/funda');
    // El precio de la línea es el que se pagó, no el de hoy.
    expect(sold).toHaveTextContent('1 × S/ 50.00');
    expect(sold).not.toHaveTextContent('Ya no se vende');
  });
});
