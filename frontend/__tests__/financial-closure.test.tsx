import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

/**
 * Cierre financiero (propietario, 2026-10-16).
 *
 *  - Una venta POS antigua o compleja la anula un superior: quien no lo es lee
 *    por qué (el servidor lo manda como bloqueo) y el superior ve el aviso con
 *    las condiciones antes de confirmar. Si el servidor se niega, se muestra su
 *    motivo, no un «sin permisos» genérico.
 *  - Confirmar un reembolso pide referencia, evidencia verificable y manda el
 *    importe exacto del pendiente. La evidencia se ve en el reembolso confirmado.
 */

const mockConfirm = jest.fn();
jest.mock('@/app/lib/admin', () => {
  const actual = jest.requireActual('@/app/lib/admin');
  return { ...actual, confirmOrderRefund: (...args: unknown[]) => mockConfirm(...args) };
});
jest.mock('@/app/lib/auth', () => ({
  fetchWithAuth: jest.fn(),
  SELECTED_COMPANY_KEY: 'internal-selected-company',
}));

/* eslint-disable @typescript-eslint/no-require-imports */
const { OrderCancelPanel } = require('@/app/admin/components/OrderCancelPanel');
const { OrderRefundsPanel } = require('@/app/admin/components/OrderRefundsPanel');
const { cancelAdminOrder } = require('@/app/lib/admin');
const { fetchWithAuth } = require('@/app/lib/auth');
/* eslint-enable @typescript-eslint/no-require-imports */

const send = fetchWithAuth as jest.Mock;

function reply(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

function order(overrides: Record<string, unknown> = {}) {
  return {
    id: 42, paid: true, status: 'paid', fulfillment_status: 'delivered', can_cancel: true, cancel_mode: 'pos',
    cancel_blockers: [], cancel_superior_conditions: [], cancellation: null, cancel_fiscal_document: '',
    refunds: null, ...overrides,
  };
}

beforeEach(() => {
  mockConfirm.mockReset();
  send.mockReset();
});

describe('anular una venta POS antigua o compleja', () => {
  const CONDITIONS = [
    { code: 'older_than_window', detail: 'La venta tiene más de 7 días (el plazo de anulación simple de la empresa).' },
    { code: 'live_fiscal_document', detail: 'Tiene Boleta B001-12 vigente: el comprobante no se anula aquí.' },
  ];

  it('el superior ve el aviso con cada condición antes de confirmar', () => {
    render(<OrderCancelPanel order={order({ cancel_superior_conditions: CONDITIONS })} mayCancel
      onCancelled={jest.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Anular venta' }));
    const warning = within(screen.getByRole('dialog')).getByTestId('order-cancel-superior');
    expect(warning).toHaveTextContent('Requiere autorización superior');
    expect(warning).toHaveTextContent('más de 7 días');
    expect(warning).toHaveTextContent('Boleta B001-12');
    expect(warning).toHaveTextContent('queda registrada con tu usuario');
  });

  it('una venta simple no muestra el aviso', () => {
    render(<OrderCancelPanel order={order()} mayCancel onCancelled={jest.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Anular venta' }));
    expect(within(screen.getByRole('dialog')).queryByTestId('order-cancel-superior')).toBeNull();
  });

  it('quien no es superior lee el motivo del servidor y no ve el botón', () => {
    render(<OrderCancelPanel order={order({ can_cancel: false, cancel_mode: null, cancel_blockers: [
      { code: 'superior_required', detail: 'Anular esta venta corresponde a un superior (administrador de la empresa).' },
    ] })} mayCancel onCancelled={jest.fn()} />);
    expect(screen.queryByRole('button', { name: 'Anular venta' })).toBeNull();
    expect(screen.getByTestId('order-cancel-blockers')).toHaveTextContent('corresponde a un superior');
  });

  it('una negativa 403 del servidor dice su motivo', async () => {
    send.mockResolvedValue(reply(403, { code: 'superior_required',
      detail: 'Anular esta venta corresponde a un superior (administrador de la empresa).' }));
    await expect(cancelAdminOrder(42, 'Motivo válido', { goodsReturned: true }))
      .rejects.toThrow('Anular esta venta corresponde a un superior (administrador de la empresa).');
    send.mockResolvedValue(reply(403, {}));
    await expect(cancelAdminOrder(42, 'Motivo válido')).rejects.toThrow('No tienes permisos para cancelar pedidos.');
  });
});

const PENDING = {
  id: 7, amount: '100.00', method: 'transfer', method_label: 'Transferencia', status: 'pending',
  status_label: 'Reembolso pendiente', reference: '', note: '', evidence: '', created_at: '2026-10-15T15:31:00Z',
  created_by: 'ana.ventas', confirmed_at: null, confirmed_by: '',
};
const REFUNDS = {
  collected: '147.00', confirmed: '0.00', pending: '100.00', refundable: '47.00', held: '147.00',
  can_record: false, can_confirm: true, items: [PENDING],
};

describe('confirmar un reembolso', () => {
  it('pide referencia y evidencia, y manda el importe exacto del pendiente', async () => {
    mockConfirm.mockResolvedValue(order({ status: 'cancelled', refunds: REFUNDS }));
    render(<OrderRefundsPanel order={order({ status: 'cancelled', refunds: REFUNDS })} onChanged={jest.fn()} />);
    const button = screen.getByRole('button', { name: 'Confirmar reembolso' });
    fireEvent.change(screen.getByLabelText('Referencia del reembolso #7'), { target: { value: 'OP-55' } });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Evidencia del reembolso #7'), { target: { value: 'corta' } });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Evidencia del reembolso #7'),
      { target: { value: '  BCP op. 99887766 del 16/10  ' } });
    expect(button).toBeEnabled();
    expect(screen.getByText(/Se confirma exactamente S\/ 100\.00/)).toBeInTheDocument();
    fireEvent.click(button);
    await waitFor(() => expect(mockConfirm).toHaveBeenCalledWith(42, 7, {
      reference: 'OP-55', evidence: 'BCP op. 99887766 del 16/10', amount: '100.00' }));
  });

  it('un reembolso confirmado enseña su evidencia', () => {
    const confirmed = { ...PENDING, status: 'confirmed', status_label: 'Reembolso confirmado', reference: 'OP-55',
      evidence: 'BCP op. 99887766 del 16/10', confirmed_at: '2026-10-16T10:00:00Z', confirmed_by: 'admin' };
    render(<OrderRefundsPanel order={order({ status: 'cancelled', refunds: { ...REFUNDS, items: [confirmed] } })}
      onChanged={jest.fn()} />);
    expect(screen.getByText(/Evidencia: BCP op\. 99887766 del 16\/10/)).toBeInTheDocument();
  });
});
