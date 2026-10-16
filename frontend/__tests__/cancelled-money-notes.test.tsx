import { render, screen } from '@testing-library/react';

import { CustomerCancelledNote, DashboardCancelledMoney } from '@/app/admin/components/CancelledMoneyNotes';

/**
 * Cierre financiero (propietario, 2026-10-16) · los tableros secundarios dicen
 * aparte el dinero de los pedidos cancelados: no es venta, pero lo cobrado
 * sigue cobrado hasta que un reembolso se CONFIRMA.
 */

it('el tablero interno dice lo cobrado de los cancelados, lo devuelto y lo que aún tiene la tienda', () => {
  render(<DashboardCancelledMoney collections={{ cancelled_paid: { orders: 4, amount: '350.00' },
    refunds_confirmed: '60.00', refunds_pending: '50.00', cancelled_held: '290.00' }} />);
  const note = screen.getByTestId('dashboard-cancelled-money');
  expect(note).toHaveTextContent('4 pedidos cancelados con pago');
  expect(note).toHaveTextContent('S/ 350.00');
  expect(note).toHaveTextContent('S/ 60.00 devueltos');
  expect(note).toHaveTextContent('S/ 50.00 por devolver');
  expect(note).toHaveTextContent('S/ 290.00 aún en la tienda');
  expect(note).toHaveTextContent('No cuentan como ventas');
});

it('sin cancelados con pago, o con una respuesta anterior, no dice nada', () => {
  const { container, rerender } = render(<DashboardCancelledMoney collections={{
    cancelled_paid: { orders: 0, amount: '0.00' }, refunds_confirmed: '0.00', refunds_pending: '0.00',
    cancelled_held: '0.00' }} />);
  expect(container).toBeEmptyDOMElement();
  rerender(<DashboardCancelledMoney collections={undefined} />);
  expect(container).toBeEmptyDOMElement();
});

it('la ficha del cliente cuenta sus pedidos cancelados y lo que se le devolvió', () => {
  render(<CustomerCancelledNote cancelledOrders={2} refundsConfirmed="60.00" />);
  expect(screen.getByTestId('customer-cancelled-note'))
    .toHaveTextContent('2 pedidos cancelados (fuera de las compras pagadas) · S/ 60.00 reembolsados');
});

it('un cliente sin cancelados no muestra la nota', () => {
  const { container } = render(<CustomerCancelledNote cancelledOrders={0} refundsConfirmed="0.00" />);
  expect(container).toBeEmptyDOMElement();
});
