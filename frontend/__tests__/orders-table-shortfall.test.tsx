import { render, screen } from '@testing-library/react';
import { OrdersTable } from '@/app/admin/components/OrdersTable';
import type { AdminOrder } from '@/app/lib/admin';

/**
 * INV-04 · el listado hace visible el faltante de stock.
 *
 * La etiqueta es TEXTO ("Faltante de stock"), no sólo color: un operador que no
 * distingue el matiz igual la lee.
 */

function order(overrides: Partial<AdminOrder>): AdminOrder {
  return {
    id: 1,
    customer_name: 'Cliente',
    customer_email: 'c@example.com',
    user_id: null,
    username: null,
    total: '100.00',
    status: 'paid',
    fulfillment_status: 'pending',
    paid: true,
    created_at: '2026-01-01T00:00:00Z',
    paid_at: null,
    item_count: 1,
    has_stock_shortfall: false,
    ...overrides,
  };
}

describe('OrdersTable · indicador de faltante', () => {
  it('no muestra la etiqueta cuando no hay faltante', () => {
    render(<OrdersTable orders={[order({ id: 1, has_stock_shortfall: false })]} />);
    expect(screen.queryByTestId('order-shortfall-badge')).toBeNull();
  });

  it('muestra "Faltante de stock" como texto cuando lo hay', () => {
    render(<OrdersTable orders={[order({ id: 2, has_stock_shortfall: true })]} />);
    expect(screen.getByTestId('order-shortfall-badge')).toHaveTextContent('Faltante de stock');
  });

  it('marca sólo los pedidos con faltante', () => {
    render(
      <OrdersTable
        orders={[
          order({ id: 1, has_stock_shortfall: false }),
          order({ id: 2, has_stock_shortfall: true }),
        ]}
      />,
    );
    expect(screen.getAllByTestId('order-shortfall-badge')).toHaveLength(1);
  });
});
