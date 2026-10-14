import { fireEvent, render, screen, waitFor } from '@testing-library/react';

/**
 * ORDER-CANCEL-01 — cancelar un pedido pagado desde su detalle.
 *
 * Lo que se fija:
 *  - el botón aparece sólo si el servidor dice `can_cancel`; si no, se explica
 *    por qué (los motivos del servidor), y nada para un pedido sin pagar;
 *  - el diálogo dice que el dinero NO se devuelve, qué pasa al cancelar y, si
 *    hay boleta/factura, que queda pendiente su nota de crédito;
 *  - el motivo es obligatorio: el botón espera 5 caracteres (sin contar espacios);
 *  - Esc cierra, el foco no sale del diálogo;
 *  - tras cancelar se muestra el bloque: quién, cuándo, por qué, stock y aviso fiscal.
 */

const mockCancel = jest.fn();
jest.mock('@/app/lib/admin', () => {
  const actual = jest.requireActual('@/app/lib/admin');
  return {
    ...actual,
    cancelAdminOrder: (...args: unknown[]) => mockCancel(...args),
  };
});

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { OrderCancelPanel } = require('@/app/admin/components/OrderCancelPanel');

const CANCELLATION = {
  cancelled_at: '2026-10-14T15:30:00Z',
  cancelled_by: 'ana.ventas',
  reason: 'El cliente desistió de la compra',
  previous_status: 'paid',
  previous_fulfillment_status: 'preparing',
  stock_restored: true,
  fiscal_pending: true,
  fiscal_document: 'Boleta de venta electrónica B001-12',
  sales_note_voided: true,
};

function order(overrides: Record<string, unknown> = {}) {
  return {
    id: 42,
    paid: true,
    status: 'paid',
    fulfillment_status: 'preparing',
    can_cancel: true,
    cancel_blockers: [],
    cancellation: null,
    cancel_fiscal_document: '',
    ...overrides,
  };
}

function openDialog(props: Record<string, unknown> = {}) {
  const onCancelled = jest.fn();
  render(<OrderCancelPanel order={order(props)} mayCancel onCancelled={onCancelled} />);
  fireEvent.click(screen.getByRole('button', { name: 'Cancelar pedido' }));
  return { onCancelled, dialog: screen.getByRole('dialog') };
}

beforeEach(() => {
  mockCancel.mockReset();
});

describe('OrderCancelPanel · el botón', () => {
  it('sólo aparece si el servidor dice que se puede', () => {
    const { rerender } = render(<OrderCancelPanel order={order()} mayCancel onCancelled={jest.fn()} />);
    expect(screen.getByRole('button', { name: 'Cancelar pedido' })).toBeInTheDocument();

    rerender(<OrderCancelPanel order={order({ can_cancel: false })} mayCancel onCancelled={jest.fn()} />);
    expect(screen.queryByRole('button', { name: 'Cancelar pedido' })).toBeNull();
  });

  it('cuando no se puede, dice por qué con las palabras del servidor', () => {
    render(
      <OrderCancelPanel
        mayCancel
        order={order({
          can_cancel: false, fulfillment_status: 'shipped',
          cancel_blockers: [{ code: 'goods_left', detail: 'El pedido ya salió: requiere devolución, no cancelación.' }],
        })}
        onCancelled={jest.fn()}
      />,
    );
    expect(screen.queryByRole('button', { name: 'Cancelar pedido' })).toBeNull();
    expect(screen.getByTestId('order-cancel-blockers')).toHaveTextContent('requiere devolución');
  });

  it('a quien no puede cancelar no le explica por qué no se puede', () => {
    const { container } = render(
      <OrderCancelPanel
        mayCancel={false}
        order={order({
          can_cancel: false, fulfillment_status: 'shipped',
          cancel_blockers: [{ code: 'goods_left', detail: 'El pedido ya salió: requiere devolución.' }],
        })}
        onCancelled={jest.fn()}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('a un pedido sin pagar no le explica nada', () => {
    const { container } = render(
      <OrderCancelPanel
        order={order({ paid: false, status: 'pending_payment', can_cancel: false,
          cancel_blockers: [{ code: 'not_paid', detail: 'No figura como pagado.' }] })}
        onCancelled={jest.fn()}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});

describe('OrderCancelPanel · el diálogo', () => {
  it('dice el pedido, que el dinero no se devuelve y qué pasa', () => {
    const { dialog } = openDialog();
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(dialog).toHaveTextContent('Cancelar pedido #42');
    expect(dialog).toHaveTextContent('el dinero NO se devuelve al cancelar; el reembolso se gestiona aparte');
    expect(dialog).toHaveTextContent('El stock de sus productos vuelve a la sucursal');
    expect(dialog).toHaveTextContent('nota de venta interna, queda anulada');
    expect(dialog).not.toHaveTextContent('nota de crédito');
  });

  it('avisa del comprobante fiscal que quedará pendiente', () => {
    const { dialog } = openDialog({ cancel_fiscal_document: 'Boleta de venta electrónica B001-12' });
    expect(dialog).toHaveTextContent('B001-12');
    expect(dialog).toHaveTextContent('pendiente su nota de crédito o baja');
  });

  it('no deja confirmar hasta que el motivo tiene 5 caracteres de verdad', () => {
    const { dialog } = openDialog();
    const confirm = screen.getAllByRole('button', { name: 'Cancelar pedido' }).find((b) => dialog.contains(b))!;
    const reason = screen.getByLabelText('Motivo de la cancelación');

    expect(document.activeElement).toBe(reason);
    expect(confirm).toBeDisabled();
    fireEvent.change(reason, { target: { value: '   abcd   ' } });
    expect(confirm).toBeDisabled();
    fireEvent.click(confirm);
    expect(mockCancel).not.toHaveBeenCalled();
    fireEvent.change(reason, { target: { value: 'Error' } });
    expect(confirm).toBeEnabled();
    // Cuenta como el servidor: los espacios de dentro se juntan en uno.
    fireEvent.change(reason, { target: { value: 'ab     c' } });
    expect(confirm).toBeDisabled();
    fireEvent.change(reason, { target: { value: 'a\n b \n c' } });
    expect(confirm).toBeEnabled();
  });

  it('Esc lo cierra sin cancelar nada', () => {
    openDialog();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(mockCancel).not.toHaveBeenCalled();
  });

  it('mientras se cancela, Esc no lo cierra ni se puede volver a pulsar', async () => {
    mockCancel.mockReturnValue(new Promise(() => {}));
    const { dialog } = openDialog();
    fireEvent.change(screen.getByLabelText('Motivo de la cancelación'), { target: { value: 'Motivo válido' } });
    fireEvent.click(screen.getAllByRole('button', { name: 'Cancelar pedido' }).find((b) => dialog.contains(b))!);

    expect(await screen.findByRole('button', { name: 'Cancelando…' })).toBeDisabled();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(mockCancel).toHaveBeenCalledTimes(1);
  });

  it('el foco da la vuelta dentro del diálogo', () => {
    const { dialog } = openDialog();
    fireEvent.change(screen.getByLabelText('Motivo de la cancelación'), { target: { value: 'Motivo válido' } });
    const stops = [screen.getByLabelText('Motivo de la cancelación'), ...dialog.querySelectorAll('button')];
    (stops[stops.length - 1] as HTMLElement).focus();
    fireEvent.keyDown(window, { key: 'Tab' });
    expect(document.activeElement).toBe(stops[0]);
    fireEvent.keyDown(window, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(stops[stops.length - 1]);
  });

  it('confirma con el motivo recortado y entrega el pedido que contesta el servidor', async () => {
    const updated = order({ status: 'cancelled', can_cancel: false, cancellation: CANCELLATION });
    mockCancel.mockResolvedValue(updated);
    const { dialog, onCancelled } = openDialog();
    fireEvent.change(screen.getByLabelText('Motivo de la cancelación'), { target: { value: '  Cliente desistió  ' } });
    const confirm = screen.getAllByRole('button', { name: 'Cancelar pedido' }).find((b) => dialog.contains(b))!;
    fireEvent.click(confirm);

    await waitFor(() => expect(onCancelled).toHaveBeenCalledWith(updated));
    expect(mockCancel).toHaveBeenCalledWith(42, 'Cliente desistió');
  });

  it('manda el motivo como lo guardará el servidor', async () => {
    mockCancel.mockResolvedValue(order({ status: 'cancelled', cancellation: CANCELLATION }));
    const { dialog } = openDialog();
    fireEvent.change(screen.getByLabelText('Motivo de la cancelación'), { target: { value: ' Cliente\n\n  desistió ' } });
    fireEvent.click(screen.getAllByRole('button', { name: 'Cancelar pedido' }).find((b) => dialog.contains(b))!);
    await waitFor(() => expect(mockCancel).toHaveBeenCalledWith(42, 'Cliente desistió'));
  });

  it('si el servidor se niega, lo dice y el diálogo sigue abierto', async () => {
    mockCancel.mockRejectedValue(new Error('Este pedido ya está cancelado.'));
    const { dialog } = openDialog();
    fireEvent.change(screen.getByLabelText('Motivo de la cancelación'), { target: { value: 'Motivo válido' } });
    fireEvent.click(screen.getAllByRole('button', { name: 'Cancelar pedido' }).find((b) => dialog.contains(b))!);

    expect(await screen.findByRole('alert')).toHaveTextContent('ya está cancelado');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });
});

describe('OrderCancelPanel · después', () => {
  it('muestra quién, cuándo, por qué, el stock y el aviso fiscal, sin botón', () => {
    render(
      <OrderCancelPanel
        order={order({ status: 'cancelled', can_cancel: false,
          cancel_blockers: [{ code: 'already_cancelled', detail: 'Este pedido ya está cancelado.' }],
          cancellation: CANCELLATION })}
        onCancelled={jest.fn()}
      />,
    );
    const block = screen.getByRole('region', { name: 'Pedido cancelado' });
    expect(block).toHaveTextContent('ana.ventas');
    expect(block).toHaveTextContent('El cliente desistió de la compra');
    expect(block).toHaveTextContent('En preparación');
    expect(block).toHaveTextContent('Repuesto en la sucursal');
    expect(block).toHaveTextContent('Anulada (se conserva)');
    expect(screen.getByRole('alert')).toHaveTextContent('B001-12');
    expect(screen.getByRole('alert')).toHaveTextContent('pendiente su nota de crédito o baja');
    expect(block).toHaveTextContent('cancelar no devuelve el dinero');
    expect(screen.queryByRole('button', { name: 'Cancelar pedido' })).toBeNull();
  });

  it('sin comprobante no hay aviso fiscal', () => {
    render(
      <OrderCancelPanel
        order={order({ status: 'cancelled', can_cancel: false,
          cancellation: { ...CANCELLATION, fiscal_pending: false, fiscal_document: '', stock_restored: false } })}
        onCancelled={jest.fn()}
      />,
    );
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByRole('region', { name: 'Pedido cancelado' })).toHaveTextContent('No había stock que reponer');
  });
});
