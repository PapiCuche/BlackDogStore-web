import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

/**
 * D3 — cancelar un pedido con productos bajo pedido que el proveedor aún no
 * entregó pide decir por qué: si el proveedor no pudo, lo disponible de su
 * oferta no cambia; si fue otra razón, esas unidades vuelven a venderse.
 */

const mockCancel = jest.fn();
jest.mock('@/app/lib/admin', () => {
  const actual = jest.requireActual('@/app/lib/admin');
  return { ...actual, cancelAdminOrder: (...args: unknown[]) => mockCancel(...args) };
});

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { OrderCancelPanel } = require('@/app/admin/components/OrderCancelPanel');
// eslint-disable-next-line @typescript-eslint/no-require-imports
const { waitsForSupplier } = require('@/app/lib/admin');

const line = (extra: Record<string, unknown>) => ({ id: 1, product_name: 'Equipo X', quantity: 1, price: '10.00', ...extra });

function order(items: Record<string, unknown>[]) {
  return {
    id: 42, paid: true, status: 'paid', fulfillment_status: 'preparing', can_cancel: true, cancel_blockers: [],
    cancellation: null, cancel_fiscal_document: '', items,
  };
}

function openDialog(items: Record<string, unknown>[]) {
  render(<OrderCancelPanel order={order(items)} mayCancel onCancelled={jest.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: 'Cancelar pedido' }));
  const dialog = screen.getByRole('dialog');
  fireEvent.change(screen.getByLabelText('Motivo de la cancelación'), { target: { value: 'El cliente desistió' } });
  const confirm = within(dialog).getAllByRole('button', { name: 'Cancelar pedido' }).pop()!;
  return { dialog, confirm };
}

beforeEach(() => {
  mockCancel.mockReset();
  mockCancel.mockResolvedValue(order([]));
});

describe('cancelar con líneas bajo pedido sin entregar', () => {
  it('exige elegir la causa, explica su efecto y la envía', async () => {
    const { dialog, confirm } = openDialog([line({ supply_origin: 'supplier', supply_status: 'pending' })]);
    expect(within(dialog).getByText(/lo disponible de su oferta no cambia/)).toBeInTheDocument();
    expect(confirm).toBeDisabled();
    fireEvent.click(within(dialog).getByRole('radio', { name: 'El proveedor no pudo abastecer' }));
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    await waitFor(() => expect(mockCancel).toHaveBeenCalled());
    expect(mockCancel.mock.calls[0][2]).toEqual({ supplyCause: 'supplier_failure' });
  });

  it('«otra razón» envía `ordinary`', async () => {
    const { dialog, confirm } = openDialog([line({ supply_origin: 'supplier', supply_status: 'pending' })]);
    fireEvent.click(within(dialog).getByRole('radio', { name: 'Se cancela por otra razón; el proveedor sigue teniendo el equipo' }));
    fireEvent.click(confirm);
    await waitFor(() => expect(mockCancel).toHaveBeenCalled());
    expect(mockCancel.mock.calls[0][2]).toEqual({ supplyCause: 'ordinary' });
  });

  it('sin líneas que esperen al proveedor, nada cambia', async () => {
    const { dialog, confirm } = openDialog([
      line({ supply_origin: 'own', supply_status: '' }),
      line({ id: 2, supply_origin: 'supplier', supply_status: 'to_confirm' }),
      line({ id: 3, supply_origin: 'supplier', supply_status: 'received' }),
    ]);
    expect(within(dialog).queryByRole('radio')).toBeNull();
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    await waitFor(() => expect(mockCancel).toHaveBeenCalled());
    expect(mockCancel.mock.calls[0]).toEqual([42, 'El cliente desistió']);
  });

  it('`waitsForSupplier` sólo cuenta las líneas comprometidas y pendientes', () => {
    expect(waitsForSupplier({ items: [line({ supply_origin: 'supplier', supply_status: 'pending' })] })).toBe(true);
    expect(waitsForSupplier({ items: [line({ supply_origin: 'supplier', supply_status: 'failed' })] })).toBe(false);
    expect(waitsForSupplier({ items: [] })).toBe(false);
  });
});
