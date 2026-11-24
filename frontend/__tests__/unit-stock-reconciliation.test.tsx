import { fireEvent, render, screen, within } from '@testing-library/react';

import { UnitStockReconciliation } from '@/app/admin/components/UnitStockReconciliation';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * INV-LOAD-02 · «Conciliación»: el stock de cada producto con serie contra sus equipos disponibles.
 * Sólo lee: enseña lo que respondió el servidor y no ofrece corregir nada.
 */

jest.mock('@/app/lib/auth', () => ({ ...jest.requireActual('@/app/lib/auth'), fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

function reply(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

beforeEach(() => send.mockReset());

test('nothing is asked until the person asks, and a clean shop says so', async () => {
  send.mockResolvedValue(reply(200, { checked: 4, mismatches: [] }));
  render(<UnitStockReconciliation />);
  expect(send).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Comprobar ahora' }));
  expect(await screen.findByRole('status')).toHaveTextContent('Todo cuadra: 4 estantes comprobados');
  expect(String(send.mock.calls[0][0])).toMatch(/\/admin\/inventory\/units\/reconciliation\/$/);
});

test('each shelf that does not add up is listed with its difference, and nothing offers to fix it', async () => {
  send.mockResolvedValue(reply(200, { checked: 3, mismatches: [
    { branch_id: 1, branch: 'Centro', product_id: 9, product: 'iPhone 16 128 GB Negro', quantity: 3, available: 2, difference: 1 },
  ] }));
  render(<UnitStockReconciliation />);
  fireEvent.click(screen.getByRole('button', { name: 'Comprobar ahora' }));
  const table = await screen.findByRole('table', { name: 'Estantes que no cuadran' });
  const row = within(table).getByText('iPhone 16 128 GB Negro').closest('tr') as HTMLElement;
  expect(within(row).getByText('+1')).toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent('1 estante no cuadra');
  expect(screen.queryByRole('button', { name: /corregir|ajustar/i })).toBeNull();
});

test('a refusal is shown as the server said it', async () => {
  send.mockResolvedValue(reply(403, { detail: 'No tienes permisos para esta operación.' }));
  render(<UnitStockReconciliation />);
  fireEvent.click(screen.getByRole('button', { name: 'Comprobar ahora' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('No tienes permisos');
});
