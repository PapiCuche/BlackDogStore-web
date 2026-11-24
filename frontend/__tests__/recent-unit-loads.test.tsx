import { fireEvent, render, screen, within } from '@testing-library/react';

import { RecentUnitLoads } from '@/app/admin/components/RecentUnitLoads';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * INV-LOAD-02 · las cargas de equipos ya registradas: su «Antes y después» y «Anular esta carga» también después
 * de salir de la pantalla de la carga. Sólo se listan las registradas.
 */

jest.mock('@/app/lib/auth', () => ({ ...jest.requireActual('@/app/lib/auth'), fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

function reply(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

function load(id: number, overrides: Record<string, unknown> = {}) {
  return {
    id, import_type: 'units', status: 'applied', original_filename: `stock-${id}.xlsx`, applied_at: '2026-11-24T15:00:00Z',
    counts: { total: 3, create: 3, skip: 0, error: 0 }, options: { line: 'new' }, is_applicable: false,
    summary: { applied: { units: 3, movements: 1 } }, ...overrides,
  };
}

beforeEach(() => send.mockReset());

test('nothing is asked until the person asks; only registered loads are listed', async () => {
  send.mockResolvedValueOnce(reply(200, { results: [load(41), load(40, { status: 'previewed' }), load(39, { options: { line: 'used' } })] }));
  render(<RecentUnitLoads />);
  expect(send).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Ver cargas registradas' }));
  const table = await screen.findByRole('table', { name: 'Cargas de equipos registradas' });
  expect(String(send.mock.calls[0][0])).toMatch(/\/admin\/imports\/\?type=units$/);
  expect(within(table).getByText('stock-41.xlsx')).toBeInTheDocument();
  expect(within(table).getByText('stock-39.xlsx')).toBeInTheDocument();
  expect(within(table).queryByText('stock-40.xlsx')).toBeNull();
  const row = within(table).getByText('stock-41.xlsx').closest('tr') as HTMLElement;
  expect(within(row).getByRole('link', { name: 'Antes y después (CSV)' }).getAttribute('href'))
    .toMatch(/\/admin\/inventory\/units\/import\/41\/report\/\?as=csv$/);
  expect(within(row).getByRole('button', { name: 'Anular esta carga' })).toBeInTheDocument();
});

test('a load already annulled says so instead of offering it again', async () => {
  send.mockResolvedValueOnce(reply(200, { results: [
    load(42, { summary: { applied: { units: 2, movements: 1 }, voided: { units: 2, at: '2026-11-24T16:00:00Z', by: 'ana', reason: 'Duplicada' } } }),
  ] }));
  render(<RecentUnitLoads />);
  fireEvent.click(screen.getByRole('button', { name: 'Ver cargas registradas' }));
  const table = await screen.findByRole('table', { name: 'Cargas de equipos registradas' });
  expect(within(table).getByText(/Esta carga se anuló/)).toBeInTheDocument();
  expect(within(table).queryByRole('button', { name: 'Anular esta carga' })).toBeNull();
});

test('an empty history says so', async () => {
  send.mockResolvedValueOnce(reply(200, { results: [] }));
  render(<RecentUnitLoads />);
  fireEvent.click(screen.getByRole('button', { name: 'Ver cargas registradas' }));
  expect(await screen.findByText('Todavía no hay cargas de equipos registradas.')).toBeInTheDocument();
});
