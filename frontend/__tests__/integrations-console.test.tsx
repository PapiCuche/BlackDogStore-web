import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { IntegrationsConsole } from '@/app/admin/settings/integrations/IntegrationsConsole';
import { fetchWithAuth } from '@/app/lib/auth';

/**
 * INTEGRATIONS-CONSOLE · la pantalla donde un MASTER configura los servicios externos.
 *
 * Lo que estas pruebas fijan:
 *
 *   · un secreto se escribe y no vuelve: la pantalla sabe que «está», nunca cuál es;
 *     lo que no se toca no se reenvía, y nada se guarda en el navegador;
 *   · el orden es guardar → probar → activar, y lo que se activa es lo que se probó;
 *   · lo que cuesta dinero o no tiene vuelta atrás se confirma escribiendo una palabra;
 *   · la pantalla se dibuja con lo que declara cada proveedor: no conoce a ninguno.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

const TYPED = 'clave-escrita-NoEsReal';

const SMTP_FIELDS = [
  { name: 'host', label: 'Servidor SMTP', kind: 'host', required: true, secret: false, help: 'Sólo el nombre.' },
  { name: 'port', label: 'Puerto', kind: 'int', required: true, secret: false, help: '', default: 587 },
  { name: 'security', label: 'Seguridad', kind: 'choice', required: true, secret: false, help: '', default: 'starttls',
    choices: [{ value: 'starttls', label: 'STARTTLS (puerto 587)' }, { value: 'ssl', label: 'SSL/TLS (puerto 465)' }] },
  { name: 'username', label: 'Usuario', kind: 'text', required: false, secret: false, help: '' },
  { name: 'password', label: 'Contraseña', kind: 'text', required: false, secret: true, help: '' },
  { name: 'from_email', label: 'Correo remitente', kind: 'email', required: true, secret: false, help: '' },
];
const SEND_TO = { name: 'send_to', label: 'Enviar un correo de prueba a', kind: 'email', required: false, secret: false, help: '' };

type Row = Record<string, unknown> | null;

function row(overrides: Record<string, unknown> = {}) {
  return {
    public: { host: 'smtp.example.pe', port: 587, security: 'starttls', username: 'tienda', from_email: 'tienda@example.pe' },
    secrets: { password: { configured: true, updated_at: '2026-10-06T10:00:00Z', updated_by: 'master' } },
    enabled: true, validated: false, version: 1, last_tested_at: null, last_test_status: '', last_test_message: '',
    updated_at: '2026-10-06T10:00:00Z', updated_by: 'master', mode: '', activation_confirmation: null,
    ...overrides,
  };
}

function integration(overrides: Record<string, unknown> = {}) {
  return {
    id: 'smtp', label: 'Correo SMTP', category: 'email', scope: 'platform',
    description: 'El servidor por el que salen los correos.', supported_modes: [],
    state: 'NOT_CONFIGURED', source: 'none', company: null, active: null as Row, draft: null as Row, env: null,
    interruption: null as { word: string; message: string } | null,
    store_available: true, fields: SMTP_FIELDS, test_fields: [SEND_TO],
    ...overrides,
  };
}

const WHATSAPP = {
  id: 'whatsapp_cloud', label: 'WhatsApp Business', category: 'messaging', scope: 'company',
  description: 'Cada empresa tiene su número.', state: null,
  companies: [
    { id: 7, name: 'Taller Uno', slug: 'taller-uno', state: 'ACTIVE', source: 'panel' },
    { id: 9, name: 'Taller Dos', slug: 'taller-dos', state: 'NOT_CONFIGURED', source: 'none' },
  ],
};

let current: Record<string, ReturnType<typeof integration>>;
let calls: { method: string; url: string; body: Record<string, unknown> | null }[];
let respond: ((call: { method: string; url: string; body: Record<string, unknown> | null }) => Response | null) | null;

// A copy, as a network answer is: the screen must not be looking at the server's own object.
const json = (status: number, body: unknown) => {
  const copy = JSON.parse(JSON.stringify(body));
  return { ok: status < 400, status, json: async () => copy } as Response;
};

function summary(item: ReturnType<typeof integration>) {
  const copy: Record<string, unknown> = { ...item };
  delete copy.fields;
  delete copy.test_fields;
  return copy;
}

beforeEach(() => {
  current = { smtp: integration() };
  calls = [];
  respond = null;
  send.mockReset();
  send.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = init.method ?? 'GET';
    const body = init.body ? JSON.parse(String(init.body)) : null;
    const call = { method, url, body };
    calls.push(call);
    const custom = respond?.(call);
    if (custom) return custom;

    const match = url.match(/\/admin\/integrations\/(?:([a-z_]+)\/(?:([a-z-]+)\/)?)?(\?.*)?$/);
    const [, id, action] = match ?? [];
    if (!id) {
      // As the server lists them: one entry per platform integration, one per company-scoped one with its companies.
      const platform = Object.values(current).filter((item) => item.scope !== 'company').map(summary);
      return json(200, { results: [...platform, WHATSAPP], store_available: true });
    }
    const item = current[id];
    if (method === 'GET') return json(200, item);
    if (action === 'draft') {
      const previous = (item.draft ?? item.active) as ReturnType<typeof row> | null;
      const secrets = { ...(previous?.secrets ?? {}) } as Record<string, unknown>;
      for (const [name, value] of Object.entries((body?.secrets ?? {}) as Record<string, unknown>)) {
        secrets[name] = value ? { configured: true, updated_by: 'master' } : { configured: false };
      }
      item.draft = row({ public: body?.public, secrets, version: ((item.draft?.version as number) ?? 0) + 1 });
      item.state = item.active ? item.state : 'CONFIGURED';
      return json(200, item);
    }
    if (action === 'test') {
      const target = (body?.target === 'active' ? item.active : item.draft) as ReturnType<typeof row>;
      Object.assign(target, { validated: true, last_test_status: 'ok', last_test_message: 'Conectado.',
                              last_tested_at: '2026-10-06T11:00:00Z', version: (target.version as number) + 1 });
      if (!item.active) item.state = 'VALIDATED';
      return json(200, { ok: true, status: 'ok', message: 'Conectado.', integration: item });
    }
    if (action === 'activate') {
      item.active = { ...(item.draft as object), enabled: true } as Row;
      item.draft = null;
      Object.assign(item, { state: 'ACTIVE', source: 'panel' });
    }
    if (action === 'disable') Object.assign(item, { state: 'DISABLED', active: { ...(item.active as object), enabled: false } });
    if (action === 'enable') Object.assign(item, { state: 'ACTIVE', active: { ...(item.active as object), enabled: true } });
    if (action === 'revoke') Object.assign(item, { state: 'NOT_CONFIGURED', source: 'none', active: null, draft: null });
    if (action === 'import-env') item.draft = row({ public: (item.env as { public: object } | null)?.public });
    return json(200, item);
  });
});

const sent = (method: string, fragment: string) => calls.filter((c) => c.method === method && c.url.includes(fragment));

async function open(label = 'Correo SMTP') {
  render(<IntegrationsConsole />);
  const card = await screen.findByRole('article', { name: label });
  fireEvent.click(within(card).getByRole('button', { name: 'Configurar' }));
  await screen.findByRole('heading', { name: label });
}

function fill(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

// -- the list --------------------------------------------------------------------

test('each integration is a card with its state, its last test and where it is configured', async () => {
  current.smtp = integration({
    state: 'ACTIVE', source: 'panel',
    active: row({ validated: true, last_test_status: 'ok', last_tested_at: '2026-10-06T11:00:00Z' }),
  });
  current.google = integration({
    id: 'google', label: 'Inicio de sesión con Google', category: 'identity', state: 'ACTIVE', source: 'env',
    env: { public: { client_id: '1-x.apps.googleusercontent.com' }, secrets: [], mode: '' },
  });
  current.sunat = integration({ id: 'sunat', label: 'SUNAT', category: 'fiscal' });
  render(<IntegrationsConsole />);

  const smtp = await screen.findByRole('article', { name: 'Correo SMTP' });
  expect(within(smtp).getByText('Activa')).toBeInTheDocument();
  expect(within(smtp).getByText(/Última prueba: Correcto/)).toBeInTheDocument();
  const google = screen.getByRole('article', { name: 'Inicio de sesión con Google' });
  expect(within(google).getByText('Configurado mediante entorno')).toBeInTheDocument();
  const sunat = screen.getByRole('article', { name: 'SUNAT' });
  expect(within(sunat).getByText('Sin configurar')).toBeInTheDocument();
  expect(within(sunat).getByText('Sin probar')).toBeInTheDocument();
});

test('it says whose these credentials are', async () => {
  render(<IntegrationsConsole />);
  expect(await screen.findByText(
    'Estas credenciales controlan servicios externos de la empresa. Sólo usuarios MASTER pueden modificarlas.',
  )).toBeInTheDocument();
});

test('an integration that belongs to each company asks which company', async () => {
  current.whatsapp_cloud = integration({
    id: 'whatsapp_cloud', label: 'WhatsApp Business', scope: 'company',
    company: { id: 9, name: 'Taller Dos', slug: 'taller-dos' },
    fields: [{ name: 'phone_number_id', label: 'Identificador del número', kind: 'text', required: true, secret: false, help: '' }],
    test_fields: [],
  });
  render(<IntegrationsConsole />);
  const card = await screen.findByRole('article', { name: 'WhatsApp Business' });
  expect(within(card).getByText('1 de 2 empresas configuradas')).toBeInTheDocument();
  expect(within(card).getByRole('button', { name: 'Configurar' })).toBeDisabled();

  fireEvent.change(within(card).getByLabelText('Empresa'), { target: { value: '9' } });
  fireEvent.click(within(card).getByRole('button', { name: 'Configurar' }));
  await screen.findByRole('heading', { name: 'WhatsApp Business · Taller Dos' });
  expect(sent('GET', '/whatsapp_cloud/?company=9')).toHaveLength(1);

  fill('Identificador del número', '2077700000');
  fireEvent.click(screen.getByRole('button', { name: 'Guardar borrador' }));
  await waitFor(() => expect(sent('PUT', '/whatsapp_cloud/draft/?company=9')).toHaveLength(1));
});

// -- secrets ---------------------------------------------------------------------

test('a stored secret is shown as stored and never as a value', async () => {
  current.smtp = integration({ state: 'ACTIVE', source: 'panel', active: row({ validated: true }) });
  await open();

  expect(screen.getByText('••••••••••')).toBeInTheDocument();
  expect(screen.getByText(/Configurada/)).toBeInTheDocument();
  expect(screen.queryByLabelText('Contraseña')).toBeNull();          // no input to read it back from
  expect(document.body.innerHTML).not.toContain('type="password"');
});

test('what is not replaced is not sent again', async () => {
  current.smtp = integration({ state: 'ACTIVE', source: 'panel', active: row({ validated: true }) });
  await open();
  fill('Servidor SMTP', 'smtp.otro.example.pe');
  fireEvent.click(screen.getByRole('button', { name: 'Guardar borrador' }));

  await waitFor(() => expect(sent('PUT', '/smtp/draft/')).toHaveLength(1));
  const body = sent('PUT', '/smtp/draft/')[0].body!;
  expect(body.secrets).toEqual({});
  expect((body.public as Record<string, unknown>).host).toBe('smtp.otro.example.pe');
});

test('replacing a secret sends it once and then forgets it', async () => {
  const stored = jest.spyOn(Storage.prototype, 'setItem');
  current.smtp = integration({ state: 'ACTIVE', source: 'panel', active: row({ validated: true }) });
  await open();

  fireEvent.click(screen.getByRole('button', { name: 'Reemplazar Contraseña' }));
  const input = screen.getByLabelText('Contraseña') as HTMLInputElement;
  expect(input.type).toBe('password');
  expect(input.autocomplete).toBe('new-password');
  fireEvent.change(input, { target: { value: TYPED } });
  fireEvent.click(screen.getByRole('button', { name: 'Guardar borrador' }));

  await waitFor(() => expect(sent('PUT', '/smtp/draft/')).toHaveLength(1));
  expect(sent('PUT', '/smtp/draft/')[0].body!.secrets).toEqual({ password: TYPED });
  await screen.findByText('Borrador guardado.');
  expect(document.body.innerHTML).not.toContain(TYPED);
  expect(screen.queryByLabelText('Contraseña')).toBeNull();
  expect(stored).not.toHaveBeenCalled();
  stored.mockRestore();
});

test('a secret can be removed without typing another', async () => {
  current.smtp = integration({ state: 'ACTIVE', source: 'panel', active: row({ validated: true }) });
  await open();
  fireEvent.click(screen.getByRole('button', { name: 'Quitar Contraseña' }));
  fireEvent.click(screen.getByRole('button', { name: 'Guardar borrador' }));
  await waitFor(() => expect(sent('PUT', '/smtp/draft/')).toHaveLength(1));
  expect(sent('PUT', '/smtp/draft/')[0].body!.secrets).toEqual({ password: null });
});

// -- save → test → activate ------------------------------------------------------

test('a configuration is saved, tested and only then activated', async () => {
  await open();
  expect(screen.getByRole('button', { name: 'Probar conexión' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Activar' })).toBeDisabled();

  fill('Servidor SMTP', 'smtp.example.pe');
  fill('Correo remitente', 'tienda@example.pe');
  fill('Contraseña', TYPED);
  fireEvent.click(screen.getByRole('button', { name: 'Guardar borrador' }));
  await screen.findByText('Borrador guardado.');
  expect(screen.getByRole('button', { name: 'Activar' })).toBeDisabled();       // saved is not tested

  fireEvent.click(screen.getByRole('button', { name: 'Probar conexión' }));
  expect(await screen.findByText('Correcto')).toBeInTheDocument();
  expect(screen.getByText('Conectado.')).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: 'Activar' }));
  fireEvent.click(screen.getByRole('button', { name: 'Sí' }));
  await waitFor(() => expect(sent('POST', '/smtp/activate/')).toHaveLength(1));
  // The version that is activated is the one the test left, not the one that was saved.
  expect(sent('POST', '/smtp/activate/')[0].body).toEqual({ version: 2 });
  expect(await screen.findByText('Activa')).toBeInTheDocument();
});

test('the defaults a provider declares are what an empty form starts with', async () => {
  await open();
  expect((screen.getByLabelText('Puerto') as HTMLInputElement).value).toBe('587');
  expect((screen.getByLabelText('Seguridad') as HTMLSelectElement).value).toBe('starttls');
  fill('Servidor SMTP', 'smtp.example.pe');
  fireEvent.click(screen.getByRole('button', { name: 'Guardar borrador' }));
  await waitFor(() => expect(sent('PUT', '/smtp/draft/')).toHaveLength(1));
  expect((sent('PUT', '/smtp/draft/')[0].body!.public as Record<string, unknown>).port).toBe(587);
});

test('a change that was not saved has to be saved before it is tested', async () => {
  current.smtp = integration({ state: 'CONFIGURED', draft: row() });
  await open();
  expect(screen.getByRole('button', { name: 'Probar conexión' })).toBeEnabled();
  fill('Servidor SMTP', 'smtp.otro.example.pe');
  expect(screen.getByRole('button', { name: 'Probar conexión' })).toBeDisabled();
  expect(screen.getByText('Guarda los cambios antes de probar.')).toBeInTheDocument();
});

test('the test can be given what the provider says it takes', async () => {
  current.smtp = integration({ state: 'CONFIGURED', draft: row() });
  await open();
  fill('Enviar un correo de prueba a', 'yo@example.pe');
  fireEvent.click(screen.getByRole('button', { name: 'Probar conexión' }));
  await waitFor(() => expect(sent('POST', '/smtp/test/')).toHaveLength(1));
  expect(sent('POST', '/smtp/test/')[0].body).toEqual({ target: 'draft', send_to: 'yo@example.pe' });
});

test('a failed test says what kind of failure it was and leaves activation closed', async () => {
  current.smtp = integration({ state: 'CONFIGURED', draft: row() });
  respond = (call) => (call.url.includes('/test/')
    ? json(200, { ok: false, status: 'auth_failed', message: 'El servidor rechazó el usuario o la contraseña.',
                  integration: { ...current.smtp, draft: row({ version: 2, last_test_status: 'auth_failed' }) } })
    : null);
  await open();
  fireEvent.click(screen.getByRole('button', { name: 'Probar conexión' }));
  expect(await screen.findByText('Credenciales rechazadas')).toBeInTheDocument();
  expect(screen.getByText('El servidor rechazó el usuario o la contraseña.')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Activar' })).toBeDisabled();
});

test('what the server refuses is shown under its field', async () => {
  respond = (call) => (call.method === 'PUT'
    ? json(400, { detail: 'Configuración no válida.', errors: { host: 'Tiene que ser el nombre del servidor.' } })
    : null);
  await open();
  fill('Servidor SMTP', 'https://smtp.example.pe');
  fireEvent.click(screen.getByRole('button', { name: 'Guardar borrador' }));
  expect(await screen.findByText('Tiene que ser el nombre del servidor.')).toBeInTheDocument();
});

test('when somebody else changed it, it says so and loads what is there now', async () => {
  current.smtp = integration({ state: 'CONFIGURED', draft: row() });
  let refused = false;
  respond = (call) => {
    if (call.method === 'PUT' && !refused) {
      refused = true;
      (current.smtp.draft as ReturnType<typeof row>).public = { ...row().public, host: 'smtp.de-otra-persona.example.pe' };
      return json(409, { detail: 'Otra persona cambió este borrador. Vuelve a cargarlo antes de guardar.', version: 5 });
    }
    return null;
  };
  await open();
  fill('Servidor SMTP', 'smtp.mio.example.pe');
  fireEvent.click(screen.getByRole('button', { name: 'Guardar borrador' }));
  expect(await screen.findByText(/Otra persona cambió este borrador/)).toBeInTheDocument();
  await waitFor(() => expect((screen.getByLabelText('Servidor SMTP') as HTMLInputElement).value)
    .toBe('smtp.de-otra-persona.example.pe'));
});

// -- what has to be meant ----------------------------------------------------------

test('activating what charges real money asks for the word', async () => {
  const confirmation = { word: 'PRODUCCION', message: 'Vas a activar claves de PRODUCCIÓN: se cobrará dinero real. Escribe PRODUCCION para confirmarlo.' };
  current.smtp = integration({
    id: 'smtp', label: 'Izipay', state: 'VALIDATED', supported_modes: ['test', 'production'],
    draft: row({ validated: true, mode: 'production', activation_confirmation: confirmation }),
  });
  await open('Izipay');
  expect(screen.getByText('PRODUCCIÓN')).toBeInTheDocument();
  expect(screen.getByText(confirmation.message)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Activar' })).toBeDisabled();

  fill('Confirmación', 'produccion');
  expect(screen.getByRole('button', { name: 'Activar' })).toBeDisabled();
  fill('Confirmación', 'PRODUCCION');
  fireEvent.click(screen.getByRole('button', { name: 'Activar' }));
  fireEvent.click(screen.getByRole('button', { name: 'Sí' }));
  await waitFor(() => expect(sent('POST', '/smtp/activate/')).toHaveLength(1));
  expect(sent('POST', '/smtp/activate/')[0].body).toEqual({ version: 1, confirm: 'PRODUCCION' });
});

test('replacing what runs is asked, and says that it replaces', async () => {
  current.smtp = integration({ state: 'ACTIVE', source: 'panel', active: row({ validated: true }), draft: row({ validated: true, version: 4 }) });
  await open();
  fireEvent.click(screen.getByRole('button', { name: 'Activar' }));
  expect(screen.getByText('¿Sustituir la configuración en uso por este borrador?')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'No' }));
  expect(sent('POST', '/smtp/activate/')).toHaveLength(0);
});

test('what runs can be tested, switched off and on, and revoked by typing the word', async () => {
  current.smtp = integration({ state: 'ACTIVE', source: 'panel', active: row({ validated: true }) });
  await open();

  fireEvent.click(screen.getByRole('button', { name: 'Probar la configuración en uso' }));
  expect(await screen.findByText('Correcto')).toBeInTheDocument();
  expect(sent('POST', '/smtp/test/')[0].body).toEqual({ target: 'active' });

  fireEvent.click(screen.getByRole('button', { name: 'Desactivar' }));
  fireEvent.click(screen.getByRole('button', { name: 'Sí' }));
  expect(await screen.findByText('Desactivada')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Volver a activar' }));
  await waitFor(() => expect(sent('POST', '/smtp/enable/')).toHaveLength(1));

  expect(screen.getByRole('button', { name: 'Revocar' })).toBeDisabled();
  fill('Escribe REVOCAR para borrar esta configuración', 'REVOCAR');
  fireEvent.click(screen.getByRole('button', { name: 'Revocar' }));
  await waitFor(() => expect(sent('POST', '/smtp/revoke/')).toHaveLength(1));
  expect(sent('POST', '/smtp/revoke/')[0].body).toEqual({ confirm: 'REVOCAR' });
  expect(await screen.findByText('Sin configurar')).toBeInTheDocument();
});

// -- the environment ---------------------------------------------------------------

test('what the environment configures is said, and can be moved in without being shown', async () => {
  current.smtp = integration({
    state: 'ACTIVE', source: 'env',
    env: { public: { host: 'smtp.entorno.example.pe', port: 587, security: 'starttls', username: 'tienda', from_email: 't@example.pe' },
           secrets: ['password'], mode: '' },
  });
  await open();
  expect(screen.getByText(/Configurado mediante entorno/)).toBeInTheDocument();
  expect((screen.getByLabelText('Servidor SMTP') as HTMLInputElement).value).toBe('smtp.entorno.example.pe');
  expect(screen.getByText(/El entorno tiene: Contraseña/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: 'Copiar a la consola' }));
  await waitFor(() => expect(sent('POST', '/smtp/import-env/')).toHaveLength(1));
  expect(await screen.findByText('••••••••••')).toBeInTheDocument();
});

test('without the root key of the server nothing can be saved, and it says why', async () => {
  current.smtp = integration({ store_available: false });
  await open();
  expect(screen.getByText(/APP_CONFIG_ENCRYPTION_KEY/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Guardar borrador' })).toBeDisabled();
});

// -- files -------------------------------------------------------------------------

const CERTIFICATE = { name: 'certificate_p12', label: 'Certificado digital', kind: 'file', required: true, secret: true, help: '', max_bytes: 16 };

test('a file is sent as the provider asks, and a file that is too big is refused here', async () => {
  current.smtp = integration({ fields: [CERTIFICATE], test_fields: [] });
  await open();
  const input = screen.getByLabelText('Certificado digital') as HTMLInputElement;
  expect(input.type).toBe('file');

  fireEvent.change(input, { target: { files: [new File([new Uint8Array(17)], 'grande.p12')] } });
  expect(await screen.findByText(/supera el máximo/)).toBeInTheDocument();

  fireEvent.change(input, { target: { files: [new File([new Uint8Array([1, 2, 3])], 'certificado.p12')] } });
  expect(await screen.findByText('certificado.p12')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Guardar borrador' }));
  await waitFor(() => expect(sent('PUT', '/smtp/draft/')).toHaveLength(1));
  expect(sent('PUT', '/smtp/draft/')[0].body!.secrets).toEqual({ certificate_p12: 'AQID' });
});

// -- REVIEW ------------------------------------------------------------------------

test('a first draft whose test failed is not called active', async () => {
  current.smtp = integration({ state: 'ERROR', draft: row({ last_test_status: 'auth_failed', last_tested_at: '2026-10-06T11:00:00Z' }) });
  render(<IntegrationsConsole />);
  const card = await screen.findByRole('article', { name: 'Correo SMTP' });
  expect(within(card).getByText('Borrador con la prueba fallida')).toBeInTheDocument();
  expect(within(card).queryByText(/Activa/)).toBeNull();
});

test('what is active and failed its last test says both', async () => {
  current.smtp = integration({ state: 'ERROR', source: 'panel', active: row({ last_test_status: 'auth_failed', last_tested_at: '2026-10-06T11:00:00Z' }) });
  render(<IntegrationsConsole />);
  const card = await screen.findByRole('article', { name: 'Correo SMTP' });
  expect(within(card).getByText('Activa, con error')).toBeInTheDocument();
});

test('keys nobody could verify are not called correct, and can still be activated on purpose', async () => {
  const confirmation = { word: 'PRODUCCION', message: 'Vas a activar claves de PRODUCCIÓN.' };
  current.smtp = integration({ state: 'CONFIGURED', draft: row({ mode: 'production', activation_confirmation: confirmation }) });
  respond = (call) => (call.url.includes('/test/')
    ? json(200, { ok: true, status: 'unverified', message: 'Claves coherentes, pero no se han verificado.',
                  integration: { ...current.smtp, state: 'VALIDATED',
                                 draft: row({ version: 2, validated: true, last_test_status: 'unverified', mode: 'production', activation_confirmation: confirmation }) } })
    : null);
  await open();
  fireEvent.click(screen.getByRole('button', { name: 'Probar conexión' }));
  expect(await screen.findByText('Coherente, sin verificar')).toBeInTheDocument();
  expect(screen.queryByText('Correcto')).toBeNull();
  fill('Confirmación', 'PRODUCCION');
  expect(screen.getByRole('button', { name: 'Activar' })).toBeEnabled();
});

test('stopping something a buyer is in the middle of has to be typed', async () => {
  const interruption = { word: 'INTERRUMPIR', message: 'Hay 1 cobro abierto con Izipay en la última hora. Escribe INTERRUMPIR para seguir de todos modos.' };
  current.smtp = integration({ state: 'ACTIVE', source: 'panel', active: row({ validated: true }), draft: row({ validated: true, version: 3 }), interruption });
  await open();
  expect(screen.getByText(interruption.message)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Desactivar' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Activar' })).toBeDisabled();
  fill('Escribe REVOCAR para borrar esta configuración', 'REVOCAR');
  expect(screen.getByRole('button', { name: 'Revocar' })).toBeDisabled();

  fill('Confirmación de la interrupción', 'INTERRUMPIR');
  fireEvent.click(screen.getByRole('button', { name: 'Activar' }));
  fireEvent.click(screen.getByRole('button', { name: 'Sí' }));
  await waitFor(() => expect(sent('POST', '/smtp/activate/')).toHaveLength(1));
  expect(sent('POST', '/smtp/activate/')[0].body).toEqual({ version: 3, acknowledge: 'INTERRUMPIR' });
});

test('the word travels with switching off and with revoking too', async () => {
  const interruption = { word: 'INTERRUMPIR', message: 'Hay 2 cobros abiertos.' };
  current.smtp = integration({ state: 'ACTIVE', source: 'panel', active: row({ validated: true }), interruption });
  await open();
  fill('Confirmación de la interrupción', 'INTERRUMPIR');
  fill('Escribe REVOCAR para borrar esta configuración', 'REVOCAR');
  fireEvent.click(screen.getByRole('button', { name: 'Revocar' }));
  await waitFor(() => expect(sent('POST', '/smtp/revoke/')).toHaveLength(1));
  expect(sent('POST', '/smtp/revoke/')[0].body).toEqual({ confirm: 'REVOCAR', acknowledge: 'INTERRUMPIR' });
});

test('switching back on asks for the word that activating asked for', async () => {
  const confirmation = { word: 'EMITIR', message: 'Vas a habilitar la emisión hacia SUNAT. Escribe EMITIR.' };
  current.smtp = integration({ state: 'DISABLED', source: 'panel', active: row({ enabled: false, validated: true, activation_confirmation: confirmation }) });
  await open();
  expect(screen.getByText(confirmation.message)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Volver a activar' })).toBeDisabled();
  fill('Confirmación para volver a activar', 'EMITIR');
  fireEvent.click(screen.getByRole('button', { name: 'Volver a activar' }));
  await waitFor(() => expect(sent('POST', '/smtp/enable/')).toHaveLength(1));
  expect(sent('POST', '/smtp/enable/')[0].body).toEqual({ confirm: 'EMITIR' });
});

test('nothing of a stored secret is shown, not even how it ends', async () => {
  current.smtp = integration({ state: 'ACTIVE', source: 'panel', active: row({ validated: true }) });
  await open();
  expect(screen.queryByText(/termina en/)).toBeNull();
});
