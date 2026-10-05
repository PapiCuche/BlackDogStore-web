import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { TrackingLinkPanel } from '@/app/admin/service/components/TrackingLinkPanel';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * TRACKING · el enlace que se entrega al cliente.
 *
 * QUIEN TIENE EL ENLACE PUEDE RESPONDER LA COTIZACIÓN COMO EL CLIENTE. Por eso
 * la orden muestra si el enlace existe y cuánto se usa, pero el enlace mismo se
 * pide aparte, lo entrega el servidor sólo a quien puede anotar la decisión del
 * cliente, y queda registrado quién lo pidió.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

const TOKEN = 'kQ3xV9aZ0bC1dE2fG3hI4jK5lM6nO7pQ8rS9tU0vW1x';
let calls: { method: string; path: string }[];
let status: { active: boolean; view_count: number; last_viewed_at: string | null; can_reveal: boolean };
let path: string;
let revealStatus: number;

function reply(code: number, body: unknown): Response {
  return { ok: code >= 200 && code < 300, status: code, json: async () => body } as Response;
}

beforeEach(() => {
  calls = [];
  status = { active: true, view_count: 3, last_viewed_at: '2026-10-05T15:00:00Z', can_reveal: true };
  path = `/seguimiento/${TOKEN}`;
  revealStatus = 200;
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const method = (init.method ?? 'GET').toUpperCase();
    const url = String(input).replace(/^.*\/service/, '');
    calls.push({ method, path: url });
    if (url.endsWith('/reveal/')) {
      return revealStatus === 200
        ? reply(200, { path, url: `https://tienda.example${path}` })
        : reply(revealStatus, { detail: 'No tienes permiso para esta acción.' });
    }
    if (url.endsWith('/revoke/')) status = { ...status, active: false };
    if (url.endsWith('/rotate/')) { status = { ...status, active: true, view_count: 0 }; path = '/seguimiento/NUEVO'; }
    return reply(200, status);
  });
  Object.assign(navigator, { clipboard: { writeText: jest.fn().mockResolvedValue(undefined) } });
});

const field = () => screen.getByLabelText('Enlace de seguimiento') as HTMLInputElement;

test('opening the order shows that a link exists and is used, not the link', async () => {
  render(<TrackingLinkPanel slug="taller" orderId={4} mayManage />);

  expect(await screen.findByText(/Abierto 3 veces/)).toBeInTheDocument();
  expect(screen.queryByLabelText('Enlace de seguimiento')).toBeNull();
  expect(calls).toEqual([{ method: 'GET', path: '/orders/4/tracking-link/' }]);
});

test('asking for the link is an explicit step, and then it can be copied', async () => {
  render(<TrackingLinkPanel slug="taller" orderId={4} mayManage />);

  fireEvent.click(await screen.findByRole('button', { name: 'Mostrar enlace' }));

  await waitFor(() => expect(field().value).toBe(`${window.location.origin}/seguimiento/${TOKEN}`));
  expect(calls[1]).toEqual({ method: 'POST', path: '/orders/4/tracking-link/reveal/' });
  fireEvent.click(screen.getByRole('button', { name: 'Copiar enlace' }));
  await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith(field().value));
  expect(await screen.findByText('Copiado')).toBeInTheDocument();
});

test('someone who cannot answer for the customer is not offered the link', async () => {
  status = { ...status, can_reveal: false };
  render(<TrackingLinkPanel slug="taller" orderId={4} mayManage />);

  expect(await screen.findByText(/puede responder la cotización en nombre del cliente/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Mostrar enlace' })).toBeNull();
});

test('replacing the link hides the old one: the new one is asked for again', async () => {
  render(<TrackingLinkPanel slug="taller" orderId={4} mayManage />);
  fireEvent.click(await screen.findByRole('button', { name: 'Mostrar enlace' }));
  await waitFor(() => expect(field()).toBeInTheDocument());

  fireEvent.click(screen.getByRole('button', { name: 'Reemplazar enlace' }));
  fireEvent.click(screen.getByRole('button', { name: 'Sí' }));

  await waitFor(() => expect(screen.queryByLabelText('Enlace de seguimiento')).toBeNull());
  // El botón vuelve cuando el reemplazo terminó en el servidor.
  await waitFor(() => expect(screen.getByRole('button', { name: 'Mostrar enlace' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Mostrar enlace' }));
  await waitFor(() => expect(field().value).toBe(`${window.location.origin}/seguimiento/NUEVO`));
});

test('a revoked link says so and offers a new one', async () => {
  render(<TrackingLinkPanel slug="taller" orderId={4} mayManage />);
  await screen.findByText(/Abierto 3 veces/);

  fireEvent.click(screen.getByRole('button', { name: 'Desactivar enlace' }));
  fireEvent.click(screen.getByRole('button', { name: 'Sí' }));

  expect(await screen.findByText(/no tiene un enlace activo/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Crear enlace nuevo' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Mostrar enlace' })).toBeNull();
});

test('a refusal to reveal is shown as the server said it', async () => {
  revealStatus = 403;
  render(<TrackingLinkPanel slug="taller" orderId={4} mayManage />);

  fireEvent.click(await screen.findByRole('button', { name: 'Mostrar enlace' }));

  expect(await screen.findByText('No tienes permiso para esta acción.')).toBeInTheDocument();
  expect(screen.queryByLabelText('Enlace de seguimiento')).toBeNull();
});

test('someone who cannot manage the order replaces and revokes nothing', async () => {
  render(<TrackingLinkPanel slug="taller" orderId={4} mayManage={false} />);
  await screen.findByText(/Abierto 3 veces/);

  expect(screen.queryByRole('button', { name: 'Reemplazar enlace' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Desactivar enlace' })).toBeNull();
});
