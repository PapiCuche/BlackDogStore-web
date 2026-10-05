import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { TrackingView } from '@/app/seguimiento/[token]/TrackingView';

/**
 * TRACKING · lo que ve el cliente al abrir su enlace.
 *
 * Sin sesión y sin cookies: la página habla con la API pública del enlace. Lo
 * que se fija es que dibuja lo que el servidor manda —nunca un estado
 * calculado aquí— y que responder la cotización pasa por el servidor.
 */

const TOKEN = 'kQ3xV9aZ0bC1dE2fG3hI4jK5lM6nO7pQ8rS9tU0vW1x';

const QUOTE = {
  id: 9, revision: 1, status: 'sent', status_label: 'Enviada', currency: 'PEN',
  subtotal: '120.00', discount_amount: '0.00', tax_amount: '0.00', total: '120.00',
  valid_until: null, is_expired: false, can_be_decided: true, customer_notes: 'Incluye limpieza.',
  items: [{ id: 1, description: 'Cambio de pantalla', quantity: '1.00', unit_price: '120.00', line_total: '120.00', item_type_label: 'Servicio' }],
  decision: null, sent_at: '2026-10-05T14:00:00Z',
};

function payload(overrides: Record<string, unknown> = {}) {
  return {
    company: {
      name: 'Taller Uno', phone: '', whatsapp_link: 'https://wa.me/51999', logo_url: '',
      warranty_policy_text: 'Garantía de 90 días en mano de obra.', warranty_policy_url: '',
    },
    order: {
      number: 'SRV-000042', status: 'waiting_approval', status_label: 'Esperando tu aprobación',
      device_summary: 'Genérica X200', reported_issue: 'No enciende.',
      received_at: '2026-10-04T10:00:00Z', closed_at: null, updated_at: '2026-10-05T14:00:00Z',
    },
    device: { type_label: 'Teléfono', brand: 'Genérica', model: 'X200', serial_number: '••••••ABC9', imei: '•••••••••••7518' },
    timeline: [
      { id: 1, status: 'received', status_label: 'Recibido', occurred_at: '2026-10-04T10:00:00Z' },
      { id: 2, status: 'waiting_approval', status_label: 'Esperando tu aprobación', occurred_at: '2026-10-05T14:00:00Z' },
    ],
    quote: QUOTE, can_decide: true,
    payments: { currency: 'PEN', quoted_total: '120.00', paid: '0.00', outstanding: '120.00', status: 'unpaid' },
    evidence: [{ id: 5, stage: 'intake', stage_label: 'Recepción', caption: 'Estado al recibirlo.', width: 800, height: 600, created_at: '2026-10-04T10:05:00Z' }],
    ...overrides,
  };
}

let requests: { method: string; url: string; body: unknown; credentials?: string }[];
let current: ReturnType<typeof payload>;
let status: number;
let decisionStatus: number;

beforeEach(() => {
  requests = [];
  current = payload();
  status = 200;
  decisionStatus = 200;
  global.fetch = jest.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const method = (init.method ?? 'GET').toUpperCase();
    const url = String(input);
    requests.push({ method, url, body: typeof init.body === 'string' ? JSON.parse(init.body) : null, credentials: init.credentials });
    if (method === 'POST') {
      if (decisionStatus !== 200) {
        return { ok: false, status: decisionStatus, json: async () => ({ detail: 'Esta cotización ya tiene una respuesta registrada.' }) } as Response;
      }
      const decision = (JSON.parse(String(init.body)) as { decision: string }).decision;
      current = payload({
        quote: { ...QUOTE, status: decision === 'approve' ? 'approved' : 'rejected', status_label: decision === 'approve' ? 'Aprobada' : 'Rechazada', can_be_decided: false },
        can_decide: false,
      });
      return { ok: true, status: 200, json: async () => ({ quote: current.quote }) } as Response;
    }
    if (status !== 200) return { ok: false, status, json: async () => ({ detail: 'No encontrado.' }) } as Response;
    return { ok: true, status: 200, json: async () => current } as Response;
  }) as unknown as typeof fetch;
});

test('the order is shown as the server describes it, with masked identifiers', async () => {
  render(<TrackingView token={TOKEN} />);

  expect(await screen.findByRole('heading', { name: 'SRV-000042' })).toBeInTheDocument();
  expect(screen.getAllByText('Esperando tu aprobación').length).toBeGreaterThan(0);
  expect(screen.getByText('Taller Uno')).toBeInTheDocument();
  expect(screen.getByText('Genérica X200')).toBeInTheDocument();
  expect(screen.getByText('•••••••••••7518')).toBeInTheDocument();
  const steps = within(screen.getByRole('list', { name: 'Avance de la reparación' })).getAllByRole('listitem');
  expect(steps.map((s) => s.textContent)).toEqual([
    expect.stringContaining('Recibido'), expect.stringContaining('Esperando tu aprobación'),
  ]);
  // Sin sesión: la credencial es el enlace, y las cookies no viajan.
  expect(requests[0].url).toContain(`/v1/tracking/${TOKEN}/`);
  expect(requests[0].credentials).toBe('omit');
});

test('the quote shows its lines and the total the server computed', async () => {
  render(<TrackingView token={TOKEN} />);

  expect(await screen.findByText('Cambio de pantalla')).toBeInTheDocument();
  expect(screen.getByText('Incluye limpieza.')).toBeInTheDocument();
  expect(screen.getByTestId('quote-total')).toHaveTextContent('120.00');
});

test('approving asks once, goes to the server, and redraws from its answer', async () => {
  render(<TrackingView token={TOKEN} />);

  fireEvent.click(await screen.findByRole('button', { name: 'Aprobar cotización' }));
  fireEvent.click(screen.getByRole('button', { name: 'Sí, aprobar' }));

  expect(await screen.findByText(/Aprobaste esta cotización/)).toBeInTheDocument();
  const post = requests.find((r) => r.method === 'POST');
  expect(post?.url).toContain(`/v1/tracking/${TOKEN}/quotes/9/decision/`);
  expect(post?.body).toEqual({ decision: 'approve' });
  expect(screen.queryByRole('button', { name: 'Aprobar cotización' })).toBeNull();
});

test('a refusal from the server is shown and the page reloads what is true', async () => {
  decisionStatus = 409;
  render(<TrackingView token={TOKEN} />);

  fireEvent.click(await screen.findByRole('button', { name: 'Rechazar' }));
  fireEvent.click(screen.getByRole('button', { name: 'Sí, rechazar' }));

  expect(await screen.findByText('Esta cotización ya tiene una respuesta registrada.')).toBeInTheDocument();
});

test('shared photos are listed with their note; nothing else is requested', async () => {
  render(<TrackingView token={TOKEN} />);

  const photo = await screen.findByRole('img', { name: 'Estado al recibirlo.' });
  expect(photo.getAttribute('src')).toContain(`/v1/tracking/${TOKEN}/evidence/5/content/`);
});

test('a link that does not resolve says so without saying why', async () => {
  status = 404;
  render(<TrackingView token={"x".repeat(43)} />);

  expect(await screen.findByText('Este enlace no está disponible')).toBeInTheDocument();
  expect(screen.queryByText(/SRV-/)).toBeNull();
});

test('with no quote and nothing to decide, no buttons are offered', async () => {
  current = payload({ quote: null, can_decide: false });
  render(<TrackingView token={TOKEN} />);

  await screen.findByRole('heading', { name: 'SRV-000042' });
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Aprobar cotización' })).toBeNull());
});

test('the warranty policy accompanies a delivered order, and only then', async () => {
  render(<TrackingView token={TOKEN} />);
  await screen.findByRole('heading', { name: 'SRV-000042' });
  expect(screen.queryByText('Garantía de 90 días en mano de obra.')).toBeNull();
});

test('a delivered order shows the warranty policy of the shop', async () => {
  current = payload({
    order: { ...payload().order, status: 'delivered', status_label: 'Entregado' },
    quote: null, can_decide: false,
  });
  render(<TrackingView token={TOKEN} />);
  expect(await screen.findByText('Garantía de 90 días en mano de obra.')).toBeInTheDocument();
});
