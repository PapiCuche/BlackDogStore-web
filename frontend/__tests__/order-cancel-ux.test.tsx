import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

/**
 * COMMERCE-ADMIN-UX-01 — cancelaciones que pidió el propietario (2026-10-15).
 *
 *  - Un pedido SIN PAGAR se cancela con motivo: el diálogo no habla de dinero
 *    devuelto, dice que no se cuenta ingreso y que los equipos apartados vuelven.
 *  - Una venta POS se ANULA sólo si quien la anula marca que la mercadería
 *    volvió al local.
 *  - Un pedido pagado puede registrar, al cancelarlo, un reembolso PENDIENTE.
 *  - Los reembolsos: lo cobrado, lo pendiente y lo devuelto; registrar uno y
 *    confirmarlo con su referencia, sólo si el servidor lo permite.
 *  - El listado pide `refund=` y el resumen enseña los cobros aparte.
 */

const mockCancel = jest.fn();
const mockRecord = jest.fn();
const mockConfirm = jest.fn();
jest.mock('@/app/lib/admin', () => {
  const actual = jest.requireActual('@/app/lib/admin');
  return {
    ...actual,
    cancelAdminOrder: (...args: unknown[]) => mockCancel(...args),
    recordOrderRefund: (...args: unknown[]) => mockRecord(...args),
    confirmOrderRefund: (...args: unknown[]) => mockConfirm(...args),
  };
});

/* eslint-disable @typescript-eslint/no-require-imports */
const { OrderCancelPanel } = require('@/app/admin/components/OrderCancelPanel');
const { OrderRefundsPanel } = require('@/app/admin/components/OrderRefundsPanel');
const { SalesCollectionsSection } = require('@/app/admin/components/SalesCollectionsSection');
/* eslint-enable @typescript-eslint/no-require-imports */

const CANCELLATION = {
  cancelled_at: '2026-10-15T15:30:00Z',
  cancelled_by: 'ana.ventas',
  reason: 'El cliente no pagó',
  previous_status: 'pending_payment',
  previous_fulfillment_status: 'pending',
  stock_restored: false,
  fiscal_pending: false,
  fiscal_document: '',
  sales_note_voided: false,
  was_paid: false,
  goods_returned: false,
  holds_released: 1,
};

function order(overrides: Record<string, unknown> = {}) {
  return {
    id: 42,
    paid: true,
    status: 'paid',
    fulfillment_status: 'preparing',
    can_cancel: true,
    cancel_mode: 'paid',
    cancel_blockers: [],
    cancellation: null,
    cancel_fiscal_document: '',
    refunds: null,
    ...overrides,
  };
}

function openDialog(props: Record<string, unknown> = {}, button = 'Cancelar pedido') {
  const onCancelled = jest.fn();
  render(<OrderCancelPanel order={order(props)} mayCancel onCancelled={onCancelled} />);
  fireEvent.click(screen.getByRole('button', { name: button }));
  return { onCancelled, dialog: screen.getByRole('dialog') };
}

function confirmIn(dialog: HTMLElement, name: string) {
  return within(dialog).getAllByRole('button', { name }).at(-1)!;
}

beforeEach(() => {
  mockCancel.mockReset();
  mockRecord.mockReset();
  mockConfirm.mockReset();
});

describe('pedido sin pagar', () => {
  it('el diálogo no habla de devolver dinero y dice que no hay ingreso', () => {
    const { dialog } = openDialog({ paid: false, status: 'pending_payment', fulfillment_status: 'pending',
      cancel_mode: 'unpaid' });
    expect(dialog).toHaveTextContent('Sin pago');
    expect(dialog).toHaveTextContent('no se registra ningún ingreso');
    expect(dialog).toHaveTextContent('Los equipos que el comprador tenía apartados vuelven a la tienda');
    expect(dialog).toHaveTextContent('Si llega un pago después');
    expect(dialog).not.toHaveTextContent('el dinero NO se devuelve');
    expect(within(dialog).queryByLabelText(/Registrar reembolso pendiente/)).toBeNull();
  });

  it('cancela con el motivo, sin nada más', async () => {
    mockCancel.mockResolvedValue(order({ status: 'cancelled', cancellation: CANCELLATION }));
    const { dialog } = openDialog({ paid: false, status: 'pending_payment', cancel_mode: 'unpaid' });
    fireEvent.change(screen.getByLabelText('Motivo de la cancelación'), { target: { value: 'No pagó a tiempo' } });
    fireEvent.click(confirmIn(dialog, 'Cancelar pedido'));
    // CANCEL-CROSSED-PAYMENT: lleva el modo que se vio; un pago entre medio es un 409.
    await waitFor(() => expect(mockCancel).toHaveBeenCalledWith(42, 'No pagó a tiempo', { expectedMode: 'unpaid' }));
  });

  it('el bloque dice que se canceló sin pago y los equipos liberados', () => {
    render(<OrderCancelPanel order={order({ status: 'cancelled', paid: false, can_cancel: false,
      cancellation: CANCELLATION })} onCancelled={jest.fn()} />);
    const block = screen.getByRole('region', { name: 'Pedido cancelado' });
    expect(block).toHaveTextContent('Cancelado sin pago: no se registró ningún ingreso');
    expect(block).toHaveTextContent('1 equipo apartado volvió a la tienda');
    expect(block).not.toHaveTextContent('sigue figurando como pagado');
  });
});

describe('venta POS', () => {
  const POS = { cancel_mode: 'pos', fulfillment_status: 'delivered' };

  it('se anula sólo marcando que la mercadería volvió al local', async () => {
    mockCancel.mockResolvedValue(order({ status: 'cancelled', cancellation: { ...CANCELLATION, was_paid: true,
      goods_returned: true } }));
    const { dialog } = openDialog(POS, 'Anular venta');
    expect(dialog).toHaveTextContent('Anular venta #42');
    fireEvent.change(screen.getByLabelText('Motivo de la anulación'), { target: { value: 'Cliente devolvió' } });
    const confirm = confirmIn(dialog, 'Anular venta');
    expect(confirm).toBeDisabled();
    fireEvent.click(screen.getByLabelText('La mercadería volvió al local'));
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    await waitFor(() => expect(mockCancel).toHaveBeenCalledWith(42, 'Cliente devolvió', { goodsReturned: true, expectedMode: 'pos' }));
  });

  it('puede anularse sin reponer stock cuando ya hubo una devolución manual', async () => {
    mockCancel.mockResolvedValue(order({ status: 'cancelled' }));
    const { dialog } = openDialog(POS, 'Anular venta');
    fireEvent.change(screen.getByLabelText('Motivo de la anulación'), { target: { value: 'Ya se devolvió' } });
    fireEvent.click(screen.getByLabelText('La mercadería volvió al local'));
    fireEvent.click(within(dialog).getByLabelText(/Anular sin reponer stock/));
    fireEvent.click(confirmIn(dialog, 'Anular venta'));
    await waitFor(() => expect(mockCancel).toHaveBeenCalledWith(
      42, 'Ya se devolvió', { goodsReturned: true, restoreStock: false, expectedMode: 'pos' }));
  });

  it('el bloque de una venta anulada lo dice con esas palabras', () => {
    render(<OrderCancelPanel order={order({ status: 'cancelled', can_cancel: false, cancellation: {
      ...CANCELLATION, was_paid: true, goods_returned: true, stock_restored: true } })} onCancelled={jest.fn()} />);
    const block = screen.getByRole('region', { name: 'Venta anulada' });
    expect(block).toHaveTextContent('Mercadería devuelta al local');
    expect(block).toHaveTextContent('cancelar no devuelve el dinero');
  });
});

describe('pedido pagado · reembolso pendiente al cancelar', () => {
  it('es opcional y manda el medio elegido', async () => {
    mockCancel.mockResolvedValue(order({ status: 'cancelled' }));
    const { dialog } = openDialog();
    fireEvent.change(screen.getByLabelText('Motivo de la cancelación'), { target: { value: 'Sin stock real' } });
    fireEvent.click(within(dialog).getByLabelText(/Registrar reembolso pendiente/));
    fireEvent.change(within(dialog).getByLabelText('Medio del reembolso'), { target: { value: 'transfer' } });
    fireEvent.click(confirmIn(dialog, 'Cancelar pedido'));
    await waitFor(() => expect(mockCancel).toHaveBeenCalledWith(42, 'Sin stock real', { refundMethod: 'transfer', expectedMode: 'paid' }));
  });
});

const REFUNDS = {
  collected: '147.00',
  confirmed: '0.00',
  pending: '100.00',
  refundable: '47.00',
  held: '147.00',
  can_record: true,
  can_confirm: true,
  items: [
    { id: 7, amount: '100.00', method: 'gateway', method_label: 'Pasarela de pago', status: 'pending',
      status_label: 'Reembolso pendiente', reference: '', note: '', created_at: '2026-10-15T15:31:00Z',
      created_by: 'ana.ventas', confirmed_at: null, confirmed_by: '' },
  ],
};

describe('OrderRefundsPanel', () => {
  it('dice lo cobrado, lo pendiente, lo devuelto y lo que la tienda aún tiene', () => {
    render(<OrderRefundsPanel order={order({ status: 'cancelled', refunds: REFUNDS })} onChanged={jest.fn()} />);
    const panel = screen.getByRole('region', { name: 'Reembolsos' });
    expect(panel).toHaveTextContent('S/ 147.00');
    expect(panel).toHaveTextContent('Reembolso pendiente');
    expect(panel).toHaveTextContent('Pasarela de pago');
    expect(panel).toHaveTextContent('Registrar un reembolso no devuelve el dinero');
  });

  it('registra un reembolso pendiente por lo que queda', async () => {
    const onChanged = jest.fn();
    mockRecord.mockResolvedValue(order({ status: 'cancelled', refunds: REFUNDS }));
    render(<OrderRefundsPanel order={order({ status: 'cancelled', refunds: REFUNDS })} onChanged={onChanged} />);
    expect(screen.getByLabelText('Importe')).toHaveValue('47.00');
    fireEvent.change(screen.getByLabelText('Medio'), { target: { value: 'cash' } });
    fireEvent.click(screen.getByRole('button', { name: 'Registrar reembolso pendiente' }));
    await waitFor(() => expect(mockRecord).toHaveBeenCalledWith(42, { amount: '47.00', method: 'cash', note: '' }));
    expect(onChanged).toHaveBeenCalled();
  });

  it('confirmar pide la referencia y la evidencia (cierre financiero)', async () => {
    mockConfirm.mockResolvedValue(order({ status: 'cancelled', refunds: REFUNDS }));
    render(<OrderRefundsPanel order={order({ status: 'cancelled', refunds: REFUNDS })} onChanged={jest.fn()} />);
    const button = screen.getByRole('button', { name: 'Confirmar reembolso' });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Referencia del reembolso #7'), { target: { value: 'IZI-1' } });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Evidencia del reembolso #7'), { target: { value: 'Izipay op. 1 del 16/10' } });
    expect(button).toBeEnabled();
    fireEvent.click(button);
    await waitFor(() => expect(mockConfirm).toHaveBeenCalledWith(
      42, 7, { reference: 'IZI-1', evidence: 'Izipay op. 1 del 16/10', amount: '100.00' }));
  });

  it('sin autoridad no hay acciones, y sin dinero no se ofrece registrar', () => {
    render(<OrderRefundsPanel order={order({ status: 'cancelled', refunds: { ...REFUNDS, refundable: '0.00',
      can_confirm: false } })} onChanged={jest.fn()} />);
    expect(screen.queryByRole('button', { name: 'Confirmar reembolso' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Registrar reembolso pendiente' })).toBeNull();
  });

  it('un pedido no cancelado no enseña nada', () => {
    const { container } = render(<OrderRefundsPanel order={order()} onChanged={jest.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('SalesCollectionsSection', () => {
  const block = {
    live: { orders: 1, amount: '100.00' },
    cancelled_paid: { orders: 3, amount: '250.00' },
    late_charges: { count: 1, amount: '50.00' },
    gross: '400.00',
    refunds_confirmed: '50.00',
    refunds_pending: '150.00',
    net: '350.00',
    cancelled_held: '250.00',
    cancelled_orders: 5,
  };

  it('separa ventas vigentes, cancelaciones con dinero, reembolsos y el cobro neto', () => {
    render(<SalesCollectionsSection collections={{ last_30d: block }} />);
    const section = screen.getByRole('region', { name: 'Cobros' });
    expect(within(screen.getByTestId('collections-net')).getByText('S/ 350.00')).toBeInTheDocument();
    expect(screen.getByTestId('collections-cancelled')).toHaveTextContent('S/ 250.00');
    expect(screen.getByTestId('collections-cancelled')).toHaveTextContent('3 pedidos');
    expect(screen.getByTestId('collections-refunds')).toHaveTextContent('S/ 50.00');
    expect(screen.getByTestId('collections-refunds')).toHaveTextContent('S/ 150.00 pendientes');
    expect(section).toHaveTextContent('5 pedidos cancelados');
  });

  it('sin el bloque no aparece', () => {
    const { container } = render(<SalesCollectionsSection collections={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });
});
