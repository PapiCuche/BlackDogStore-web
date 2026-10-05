import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { TrackingLinkPanel } from '@/app/admin/service/components/TrackingLinkPanel';
import { fetchWithAuth } from '@/app/lib/auth';

/** TRACKING · el enlace que la recepción entrega al cliente. */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

const TOKEN = 'kQ3xV9aZ0bC1dE2fG3hI4jK5lM6nO7pQ8rS9tU0vW1x';
let calls: { method: string; path: string }[];
let state: { active: boolean; path: string | null; view_count: number; last_viewed_at: string | null };

function reply(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

beforeEach(() => {
  calls = [];
  state = { active: true, path: `/seguimiento/${TOKEN}`, view_count: 3, last_viewed_at: '2026-10-05T15:00:00Z' };
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const method = (init.method ?? 'GET').toUpperCase();
    const path = String(input).replace(/^.*\/service/, '');
    calls.push({ method, path });
    if (path.endsWith('/revoke/')) state = { ...state, active: false, path: null };
    if (path.endsWith('/rotate/')) state = { ...state, path: '/seguimiento/NUEVO', view_count: 0 };
    return reply(200, state);
  });
  Object.assign(navigator, { clipboard: { writeText: jest.fn().mockResolvedValue(undefined) } });
});

test('reception sees the link, how often it was opened, and can copy it', async () => {
  render(<TrackingLinkPanel slug="taller" orderId={4} mayManage />);

  const field = await screen.findByLabelText('Enlace de seguimiento') as HTMLInputElement;
  expect(field.value).toBe(`${window.location.origin}/seguimiento/${TOKEN}`);
  expect(screen.getByText(/Abierto 3 veces/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: 'Copiar enlace' }));
  await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith(field.value));
  expect(await screen.findByText('Copiado')).toBeInTheDocument();
});

test('replacing the link is confirmed and shows the new one', async () => {
  render(<TrackingLinkPanel slug="taller" orderId={4} mayManage />);
  await screen.findByLabelText('Enlace de seguimiento');

  fireEvent.click(screen.getByRole('button', { name: 'Reemplazar enlace' }));
  fireEvent.click(screen.getByRole('button', { name: 'Sí' }));

  await waitFor(() => expect((screen.getByLabelText('Enlace de seguimiento') as HTMLInputElement).value)
    .toBe(`${window.location.origin}/seguimiento/NUEVO`));
  expect(calls.map((c) => `${c.method} ${c.path}`)).toContain('POST /orders/4/tracking-link/rotate/');
});

test('a revoked link says so and offers a new one', async () => {
  render(<TrackingLinkPanel slug="taller" orderId={4} mayManage />);
  await screen.findByLabelText('Enlace de seguimiento');

  fireEvent.click(screen.getByRole('button', { name: 'Desactivar enlace' }));
  fireEvent.click(screen.getByRole('button', { name: 'Sí' }));

  expect(await screen.findByText(/no tiene un enlace activo/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Crear enlace nuevo' })).toBeInTheDocument();
});

test('someone who can only read the order copies the link and nothing else', async () => {
  render(<TrackingLinkPanel slug="taller" orderId={4} mayManage={false} />);
  await screen.findByLabelText('Enlace de seguimiento');

  expect(screen.getByRole('button', { name: 'Copiar enlace' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Reemplazar enlace' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Desactivar enlace' })).toBeNull();
});
