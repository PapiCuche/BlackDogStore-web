import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { QuoteDecisionPanel } from '@/app/admin/service/components/QuoteDecisionPanel';
import { fetchWithAuth } from '@/app/lib/auth';
import { printPdfResponse } from '@/app/lib/print-pdf';
import type { ServiceQuote } from '@/app/lib/service-console';

/**
 * QUOTE-DECISION · anotar lo que el cliente respondió, y entregarle el ticket.
 *
 * El servidor decide quién puede y qué queda registrado. Aquí se fija lo que la
 * pantalla ofrece, lo que envía, y que el ticket sólo se intenta imprimir
 * DESPUÉS de que el servidor confirmó la aprobación.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/lib/print-pdf', () => ({ printPdfResponse: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;
const print = printPdfResponse as jest.MockedFunction<typeof printPdfResponse>;

type Sent = { method: string; path: string; body: Record<string, unknown> | null };
let sent: Sent[];
let decisionReply: { status: number; body: unknown };

function reply(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

function quote(overrides: Partial<ServiceQuote> = {}): ServiceQuote {
  return {
    id: 9, revision: 1, status: 'sent', status_label: 'Enviada', currency: 'PEN',
    subtotal: '120.00', discount_amount: '0.00', tax_amount: '0.00', total: '120.00',
    valid_until: null, is_expired: false, is_editable: false, customer_notes: '',
    internal_notes: '', items: [], decision: null, created_by_name: 'Rita', sent_at: null,
    ...overrides,
  };
}

const APPROVED = quote({
  status: 'approved', status_label: 'Aprobada',
  decision: {
    decision: 'approve', reason: '', channel: 'staff_phone', channel_label: 'Llamada',
    source: 'staff', recorded_by: 'recepcion', note: 'Llamó a las 10.', decided_at: '2026-10-05T15:00:00Z',
  },
});

beforeEach(() => {
  sent = [];
  decisionReply = { status: 200, body: { quote: APPROVED } };
  print.mockReset();
  print.mockResolvedValue('printed');
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const method = (init.method ?? 'GET').toUpperCase();
    const path = String(input).replace(/^.*\/service/, '');
    sent.push({ method, path, body: typeof init.body === 'string' ? JSON.parse(init.body) : null });
    if (path.endsWith('/ticket/?formato=ticket80')) return reply(200, {});
    return reply(decisionReply.status, decisionReply.body);
  });
});

function mount(q: ServiceQuote, caps: string[] = ['service.quotes.record_decision', 'service.diagnostic.manage']) {
  const onChanged = jest.fn();
  render(
    <QuoteDecisionPanel
      slug="taller" orderId={4} quote={q} orderStatus={q.status === 'approved' ? 'approved' : 'waiting_approval'}
      may={(code) => caps.includes(code)} onChanged={onChanged}
    />,
  );
  return onChanged;
}

const posts = () => sent.filter((call) => call.method === 'POST');

test('staff record an approval with the channel it arrived by, and the ticket prints after the server says yes', async () => {
  const onChanged = mount(quote());

  fireEvent.change(screen.getByLabelText('¿Por dónde respondió?'), { target: { value: 'phone' } });
  fireEvent.change(screen.getByLabelText(/Nota/), { target: { value: 'Llamó a las 10.' } });
  expect(print).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Registrar aprobación' }));
  fireEvent.click(screen.getByRole('button', { name: 'Sí' }));

  await waitFor(() => expect(print).toHaveBeenCalledTimes(1));
  expect(posts()).toEqual([{
    method: 'POST', path: '/orders/4/quotes/9/decision/',
    body: { decision: 'approve', channel: 'phone', note: 'Llamó a las 10.' },
  }]);
  expect(sent[sent.length - 1].path).toBe('/orders/4/quotes/9/ticket/?formato=ticket80');
  expect(onChanged).toHaveBeenCalled();
  expect(await screen.findByText(/Ticket enviado a la impresora/)).toBeInTheDocument();
});

test('the channel has to be chosen: there is no default to blame', () => {
  mount(quote());
  expect(screen.getByRole('button', { name: 'Registrar aprobación' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Registrar rechazo' })).toBeDisabled();
});

test('a rejection is recorded and nothing is printed', async () => {
  decisionReply = { status: 200, body: { quote: quote({ status: 'rejected' }) } };
  const onChanged = mount(quote());

  fireEvent.change(screen.getByLabelText('¿Por dónde respondió?'), { target: { value: 'in_person' } });
  fireEvent.click(screen.getByRole('button', { name: 'Registrar rechazo' }));
  fireEvent.click(screen.getByRole('button', { name: 'Sí' }));

  await waitFor(() => expect(onChanged).toHaveBeenCalled());
  expect(posts()[0].body).toEqual({ decision: 'reject', channel: 'in_person', note: '' });
  expect(print).not.toHaveBeenCalled();
});

test('when the server refuses, nothing prints and the reason is shown', async () => {
  decisionReply = { status: 409, body: { detail: 'Esta cotización ya tiene una respuesta registrada.' } };
  mount(quote());

  fireEvent.change(screen.getByLabelText('¿Por dónde respondió?'), { target: { value: 'whatsapp' } });
  fireEvent.click(screen.getByRole('button', { name: 'Registrar aprobación' }));
  fireEvent.click(screen.getByRole('button', { name: 'Sí' }));

  expect(await screen.findByText('Esta cotización ya tiene una respuesta registrada.')).toBeInTheDocument();
  expect(print).not.toHaveBeenCalled();
});

test('if the print dialog never comes, the button to print stays', async () => {
  print.mockResolvedValue('downloaded');
  mount(quote());

  fireEvent.change(screen.getByLabelText('¿Por dónde respondió?'), { target: { value: 'phone' } });
  fireEvent.click(screen.getByRole('button', { name: 'Registrar aprobación' }));
  fireEvent.click(screen.getByRole('button', { name: 'Sí' }));

  expect(await screen.findByText(/se descargó/)).toBeInTheDocument();
});

test('an approved quote says who recorded it and can always be printed again', async () => {
  mount(APPROVED);

  expect(screen.getByText(/Llamada/)).toBeInTheDocument();
  expect(screen.getByText(/recepcion/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Registrar aprobación' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Imprimir ticket' }));

  await waitFor(() => expect(print).toHaveBeenCalledTimes(1));
  expect(posts()).toEqual([]);
});

test('reopening an approved quote asks why', async () => {
  decisionReply = { status: 201, body: { quote: quote({ id: 10, revision: 2, status: 'draft' }) } };
  const onChanged = mount(APPROVED);

  fireEvent.click(screen.getByRole('button', { name: 'Volver a cotizar' }));
  expect(screen.getByRole('button', { name: 'Anular aprobación' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Motivo'), { target: { value: 'Cambió el repuesto.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Anular aprobación' }));

  await waitFor(() => expect(onChanged).toHaveBeenCalled());
  expect(posts()).toEqual([{
    method: 'POST', path: '/orders/4/quotes/9/reopen/', body: { reason: 'Cambió el repuesto.' },
  }]);
});

test('without the capability the answer cannot be recorded, but the ticket still prints', () => {
  mount(quote(), []);
  expect(screen.queryByRole('button', { name: 'Registrar aprobación' })).toBeNull();
});

test('a decision made by the customer is labelled as theirs', () => {
  mount(quote({
    status: 'approved',
    decision: {
      decision: 'approve', reason: '', channel: 'tracking_link', channel_label: 'Enlace de seguimiento',
      source: 'customer', recorded_by: null, note: '', decided_at: '2026-10-05T15:00:00Z',
    },
  }));
  expect(screen.getByText(/El cliente aprobó/)).toBeInTheDocument();
  expect(screen.getByText(/Enlace de seguimiento/)).toBeInTheDocument();
});
