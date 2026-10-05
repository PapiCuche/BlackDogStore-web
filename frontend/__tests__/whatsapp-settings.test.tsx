import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { WhatsAppSettings } from '@/app/admin/settings/messaging/WhatsAppSettings';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * WHATSAPP-NOTIFY · la configuración que ve el administrador de una empresa.
 *
 * NINGUNA CREDENCIAL PASA POR AQUÍ. La pantalla sabe si cada una está o falta,
 * y nada más: no hay un campo donde escribir un token, ni se envía ninguno.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

const EVENTS = [
  { code: 'service.order.created', label: 'Equipo recibido' },
  { code: 'service.ready_for_pickup', label: 'Equipo listo para recoger' },
];

function settings(overrides: Record<string, unknown> = {}) {
  return {
    enabled: false, provider: 'cloud_api', ready: true, missing: [],
    phone_number_configured: true,
    credentials: { access_token: true, app_secret: true, verify_token: true },
    default_calling_code: '51', template_language: 'es',
    templates: { 'service.order.created': 'orden_recibida', 'service.ready_for_pickup': '' },
    events: EVENTS, template_parameters: ['nombre del cliente', 'número de orden', 'enlace de seguimiento'],
    webhook_path: '/api/v1/webhooks/whatsapp/taller/',
    ...overrides,
  };
}

let current: ReturnType<typeof settings>;
let patches: unknown[];
let patchStatus: number;

beforeEach(() => {
  current = settings();
  patches = [];
  patchStatus = 200;
  send.mockReset();
  send.mockImplementation(async (_input: RequestInfo | URL, init: RequestInit = {}) => {
    if ((init.method ?? 'GET') === 'PATCH') {
      const body = JSON.parse(String(init.body));
      patches.push(body);
      if (patchStatus !== 200) {
        return { ok: false, status: patchStatus, json: async () => ({ detail: 'No se puede activar todavía: faltan credenciales del proveedor.' }) } as Response;
      }
      current = settings({ ...current, ...body, templates: { ...current.templates, ...(body.templates ?? {}) } });
    }
    return { ok: true, status: 200, json: async () => current } as Response;
  });
});

test('it says what is ready, and never offers a place to type a credential', async () => {
  render(<WhatsAppSettings slug="taller" canManage />);

  expect(await screen.findByText('Credenciales del proveedor: configuradas')).toBeInTheDocument();
  expect(screen.queryByLabelText(/token/i)).toBeNull();
  expect(screen.queryByLabelText(/secret/i)).toBeNull();
  expect((screen.getByLabelText('Equipo recibido') as HTMLInputElement).value).toBe('orden_recibida');
  expect(screen.getByText(/\/api\/v1\/webhooks\/whatsapp\/taller\//)).toBeInTheDocument();
});

test('what is missing is named in words and sending cannot be turned on', async () => {
  current = settings({ ready: false, missing: ['access_token', 'phone_number_id'], credentials: { access_token: false, app_secret: true, verify_token: true } });
  render(<WhatsAppSettings slug="taller" canManage />);

  expect(await screen.findByText(/Falta: token de acceso, número de WhatsApp Business/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Activar avisos por WhatsApp' })).toBeDisabled();
});

test('templates, language and calling code are saved together', async () => {
  render(<WhatsAppSettings slug="taller" canManage />);

  fireEvent.change(await screen.findByLabelText('Equipo listo para recoger'), { target: { value: 'equipo_listo' } });
  fireEvent.change(screen.getByLabelText('Código de país por defecto'), { target: { value: '52' } });
  fireEvent.click(screen.getByRole('button', { name: 'Guardar' }));

  await waitFor(() => expect(patches).toHaveLength(1));
  expect(patches[0]).toEqual({
    templates: { 'service.order.created': 'orden_recibida', 'service.ready_for_pickup': 'equipo_listo' },
    default_calling_code: '52', template_language: 'es',
  });
  expect(await screen.findByText('Guardado')).toBeInTheDocument();
});

test('turning it on is its own confirmed step', async () => {
  render(<WhatsAppSettings slug="taller" canManage />);

  fireEvent.click(await screen.findByRole('button', { name: 'Activar avisos por WhatsApp' }));
  fireEvent.click(screen.getByRole('button', { name: 'Sí' }));

  await waitFor(() => expect(patches).toEqual([{ enabled: true }]));
  expect(await screen.findByText('Los avisos por WhatsApp están activados.')).toBeInTheDocument();
});

test('a refusal from the server is shown as the server said it', async () => {
  patchStatus = 400;
  render(<WhatsAppSettings slug="taller" canManage />);

  fireEvent.click(await screen.findByRole('button', { name: 'Activar avisos por WhatsApp' }));
  fireEvent.click(screen.getByRole('button', { name: 'Sí' }));

  expect(await screen.findByText(/faltan credenciales del proveedor/)).toBeInTheDocument();
});

test('someone who can only look gets no controls', async () => {
  render(<WhatsAppSettings slug="taller" canManage={false} />);

  await screen.findByText('Credenciales del proveedor: configuradas');
  expect(screen.queryByRole('button', { name: 'Guardar' })).toBeNull();
  expect(screen.queryByRole('button', { name: /Activar/ })).toBeNull();
  expect(screen.getByLabelText('Equipo recibido')).toBeDisabled();
});
