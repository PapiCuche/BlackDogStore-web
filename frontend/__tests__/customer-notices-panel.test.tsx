import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { CustomerNoticesPanel } from '@/app/admin/service/components/CustomerNoticesPanel';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * WHATSAPP-NOTIFY · lo que el taller ve de cada aviso al cliente.
 *
 * El estado se LEE del servidor: «enviado» significa que un proveedor lo tomó.
 * La pantalla no afirma nada por su cuenta, no muestra el número completo y no
 * escribe a nadie: registra el consentimiento y pide un reintento.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

type Sent = { method: string; path: string; body: unknown };
let sent: Sent[];
let answer: { status: number; body: unknown };

function reply(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

function notice(overrides: Record<string, unknown> = {}) {
  return {
    id: 7, title: 'Tu equipo está listo para recoger', created_at: '2026-10-05T15:00:00Z',
    email_status: 'sent', whatsapp_status: 'delivered', whatsapp_detail: null,
    whatsapp_recipient: '•••• 4321', whatsapp_sent_at: '2026-10-05T15:00:01Z',
    whatsapp_delivered_at: '2026-10-05T15:00:04Z', whatsapp_read_at: null,
    ...overrides,
  };
}

beforeEach(() => {
  sent = [];
  answer = { status: 200, body: {} };
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    sent.push({
      method: (init.method ?? 'GET').toUpperCase(), path: String(input).replace(/^.*\/service/, ''),
      body: typeof init.body === 'string' ? JSON.parse(init.body) : null,
    });
    return reply(answer.status, answer.body);
  });
});

function mount(props: Partial<Parameters<typeof CustomerNoticesPanel>[0]> = {}) {
  const onChanged = jest.fn();
  render(
    <CustomerNoticesPanel
      slug="taller" orderId={4} customerId={9} notices={[notice()]}
      whatsapp={{ enabled: true, customer_opt_in: true }}
      may={() => true} onChanged={onChanged} {...props}
    />,
  );
  return onChanged;
}

test('each notice says what happened on each channel, as the server reports it', () => {
  mount({ notices: [
    notice(),
    notice({ id: 8, title: 'Recibimos tu equipo', whatsapp_status: 'read', whatsapp_read_at: '2026-10-05T16:00:00Z', email_status: 'not_applicable' }),
  ] });

  const first = screen.getByText('Tu equipo está listo para recoger').closest('li') as HTMLElement;
  expect(within(first).getByText(/WhatsApp: entregado/)).toBeInTheDocument();
  expect(within(first).getByText(/•••• 4321/)).toBeInTheDocument();
  expect(within(first).getByText(/Correo: enviado/)).toBeInTheDocument();
  const second = screen.getByText('Recibimos tu equipo').closest('li') as HTMLElement;
  expect(within(second).getByText(/WhatsApp: leído/)).toBeInTheDocument();
});

test('a failed message shows its reason and can be retried', async () => {
  answer = { status: 200, body: notice({ whatsapp_status: 'sent' }) };
  const onChanged = mount({ notices: [notice({ whatsapp_status: 'failed', whatsapp_detail: '132001: Template does not exist' })] });

  expect(screen.getByText(/132001: Template does not exist/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Reintentar WhatsApp' }));

  await waitFor(() => expect(onChanged).toHaveBeenCalled());
  expect(sent).toEqual([{ method: 'POST', path: '/orders/4/notifications/7/whatsapp/retry/', body: {} }]);
});

test('a skipped message says why nobody was written to', () => {
  mount({ notices: [notice({ whatsapp_status: 'skipped', whatsapp_detail: 'sin consentimiento del cliente', whatsapp_recipient: null })] });
  expect(screen.getByText(/WhatsApp: no enviado/)).toBeInTheDocument();
  expect(screen.getByText(/sin consentimiento del cliente/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Reintentar WhatsApp' })).toBeNull();
});

test('a delivered message offers no retry: it already left', () => {
  mount();
  expect(screen.queryByRole('button', { name: 'Reintentar WhatsApp' })).toBeNull();
});

test('consent is recorded by the person the customer told', async () => {
  const onChanged = mount({ whatsapp: { enabled: true, customer_opt_in: false } });

  expect(screen.getByText(/no ha aceptado recibir avisos por WhatsApp/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Registrar que acepta' }));
  fireEvent.click(screen.getByRole('button', { name: 'Sí' }));

  await waitFor(() => expect(onChanged).toHaveBeenCalled());
  expect(sent).toEqual([{ method: 'POST', path: '/customers/9/whatsapp-consent/', body: { opt_in: true } }]);
});

test('a customer who agreed can be taken off', async () => {
  mount();
  fireEvent.click(screen.getByRole('button', { name: 'Registrar que ya no acepta' }));
  fireEvent.click(screen.getByRole('button', { name: 'Sí' }));
  await waitFor(() => expect(sent[0]?.body).toEqual({ opt_in: false }));
});

test('without the capabilities the state is shown and nothing can be changed', () => {
  mount({ may: () => false, notices: [notice({ whatsapp_status: 'failed', whatsapp_detail: 'x' })] });
  expect(screen.queryByRole('button', { name: 'Reintentar WhatsApp' })).toBeNull();
  expect(screen.queryByRole('button', { name: /Registrar que/ })).toBeNull();
});

test('a company that does not use WhatsApp is not shown a WhatsApp column', () => {
  mount({ whatsapp: { enabled: false, customer_opt_in: false }, notices: [notice({ whatsapp_status: 'not_applicable', whatsapp_recipient: null })] });
  expect(screen.queryByText(/WhatsApp:/)).toBeNull();
  expect(screen.getByText(/no tiene activados los avisos por WhatsApp/)).toBeInTheDocument();
});

test('with no notices yet it says so', () => {
  mount({ notices: [] });
  expect(screen.getByText('Sin avisos registrados para esta orden.')).toBeInTheDocument();
});
