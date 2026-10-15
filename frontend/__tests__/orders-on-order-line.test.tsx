/**
 * DEVICE-COMMERCE-01 · DC3 (revisión) · «Mis pedidos» y una línea bajo pedido.
 *
 * La línea pública conserva la forma de INVENTORY-UNIFIED-01 (`id`, `product`,
 * `quantity`, `price`). Una línea bajo pedido añade SÓLO `lead_time_days`: la
 * página lo lee para decir «Bajo pedido». El estado interno del abastecimiento
 * y el origen no llegan al comprador.
 */
import { act, fireEvent, render, screen } from '@testing-library/react';

import OrdersPage from '@/app/orders/page';
import { fetchWithAuth } from '@/app/lib/auth';

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
  id: 52, customer_name: 'Ana Torres', customer_email: 'ana@correo.test', total: '330.00', discount_amount: '0.00',
  coupon_code: '', paid: true, created_at: '2026-10-14T15:00:00Z', delivery: null,
  items: [
    { id: 1, quantity: 2, price: '110.00', lead_time_days: 4,
      product: { id: 3, name: 'Cargador 20W', slug: 'cargador', price: '120.00', image_url: '' } },
    { id: 2, quantity: 1, price: '110.00', product: { id: 4, name: 'Funda', slug: 'funda', price: '110.00', image_url: '' } },
  ],
};

async function open() {
  send.mockResolvedValue({ ok: true, status: 200, json: async () => [ORDER] } as Response);
  await act(async () => { render(<OrdersPage />); });
  await act(async () => { fireEvent.click(await screen.findByRole('button', { name: /Pedido #52/ })); });
}

describe('una línea bajo pedido en «Mis pedidos»', () => {
  it('dice «Bajo pedido» con su plazo sólo en la línea que trae `lead_time_days`', async () => {
    await open();

    const supply = screen.getAllByTestId('order-item-supply');
    expect(supply).toHaveLength(1);
    expect(supply[0]).toHaveTextContent('Bajo pedido');
    expect(supply[0]).toHaveTextContent('4');
    expect(screen.getByText('Funda').closest('div.flex')).not.toHaveTextContent('Bajo pedido');
    expect(document.body.textContent).not.toMatch(/NaN|undefined/);
  });
});
