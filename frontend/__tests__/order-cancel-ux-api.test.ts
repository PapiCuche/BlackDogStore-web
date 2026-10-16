import {
  cancelAdminOrder,
  confirmOrderRefund,
  fetchAdminOrders,
  recordOrderRefund,
} from '@/app/lib/admin';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * COMMERCE-ADMIN-UX-01 · lo que el panel pide al servidor: el filtro de
 * reembolsos del listado, la cancelación con lo que se marcó (y nada más) y
 * los dos pasos de un reembolso.
 */

jest.mock('@/app/lib/auth', () => ({
  fetchWithAuth: jest.fn(),
  SELECTED_COMPANY_KEY: 'internal-selected-company',
}));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

function reply(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

beforeEach(() => send.mockReset());

it('el filtro «Reembolso pendiente» se pide al servidor', async () => {
  send.mockResolvedValue(reply(200, { count: 0, page: 1, page_size: 25, results: [] }));
  await fetchAdminOrders({ status: 'cancelled', refund: 'pending', page: 1 });
  expect(String(send.mock.calls[0][0])).toContain('refund=pending');
  expect(String(send.mock.calls[0][0])).toContain('status=cancelled');
});

it('cancelar manda sólo lo que se marcó', async () => {
  send.mockResolvedValue(reply(200, { id: 42 }));
  await cancelAdminOrder(42, 'Motivo válido', { goodsReturned: true, refundMethod: 'cash' });
  expect(JSON.parse(String(send.mock.calls[0][1]?.body))).toEqual(
    { reason: 'Motivo válido', goods_returned: true, refund_method: 'cash' });
  await cancelAdminOrder(42, 'Motivo válido');
  expect(JSON.parse(String(send.mock.calls[1][1]?.body))).toEqual({ reason: 'Motivo válido' });
});

it('registrar y confirmar un reembolso van a sus direcciones, con el motivo del servidor si se niega', async () => {
  send.mockResolvedValue(reply(201, { id: 42 }));
  await recordOrderRefund(42, { amount: '10.00', method: 'cash', note: '' });
  expect(String(send.mock.calls[0][0])).toContain('/admin/orders/42/refunds/');
  expect(send.mock.calls[0][1]?.method).toBe('POST');

  send.mockResolvedValue(reply(200, { id: 42 }));
  await confirmOrderRefund(42, 7, { reference: 'OP-1' });
  expect(String(send.mock.calls[1][0])).toContain('/admin/orders/42/refunds/7/confirm/');
  expect(JSON.parse(String(send.mock.calls[1][1]?.body))).toEqual({ reference: 'OP-1' });

  send.mockResolvedValue(reply(409, { detail: 'El reembolso no puede pasar de S/ 47.00.', code: 'amount_exceeds' }));
  await expect(recordOrderRefund(42, { amount: '50.00', method: 'cash', note: '' }))
    .rejects.toThrow('El reembolso no puede pasar de S/ 47.00.');
  send.mockResolvedValue(reply(403, {}));
  await expect(confirmOrderRefund(42, 7, { reference: 'OP-1' }))
    .rejects.toThrow('Confirmar un reembolso corresponde al administrador de la empresa.');
});
