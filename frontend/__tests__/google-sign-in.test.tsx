import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

import { GoogleSignIn } from '@/app/components/GoogleSignIn';

/**
 * GOOGLE-AUTH · «Continuar con Google» en la página de acceso.
 *
 * El navegador sólo transporta: recibe de Google un token y lo entrega al
 * backend, que es quien lo verifica y abre la sesión — la de siempre, en
 * cookies HttpOnly. Aquí no se guarda nada: ni el token, ni una sesión.
 */

type Init = { client_id: string; nonce: string; callback: (response: { credential: string }) => void };
let initialized: Init | null;
let rendered: HTMLElement | null;
let requests: { method: string; url: string; body: unknown; credentials?: string }[];
let config: Record<string, unknown>;
let signIn: { status: number; body: unknown };
let link: { status: number; body: unknown };

const USER = { id: 4, username: 'ana', email: 'ana@example.com', first_name: 'Ana', last_name: '' };

beforeEach(() => {
  initialized = null;
  rendered = null;
  requests = [];
  config = { enabled: true, client_id: 'prueba.apps.googleusercontent.com', nonce: 'nonce-firmado' };
  signIn = { status: 200, body: { detail: 'Login correcto.', created: false, user: USER } };
  link = { status: 200, body: { detail: 'Login correcto.', created: false, user: USER } };
  window.localStorage.clear();
  window.sessionStorage.clear();
  (window as unknown as { google: unknown }).google = {
    accounts: { id: {
      initialize: (options: Init) => { initialized = options; },
      renderButton: (element: HTMLElement) => { rendered = element; },
    } },
  };
  global.fetch = jest.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = (init.method ?? 'GET').toUpperCase();
    requests.push({ method, url, body: typeof init.body === 'string' ? JSON.parse(init.body) : null, credentials: init.credentials });
    const reply = url.endsWith('/config/') ? { status: 200, body: config }
      : url.endsWith('/link/') ? link : signIn;
    return { ok: reply.status >= 200 && reply.status < 300, status: reply.status, json: async () => reply.body } as Response;
  }) as unknown as typeof fetch;
});

async function mount(onSignedIn = jest.fn()) {
  render(<GoogleSignIn onSignedIn={onSignedIn} />);
  await waitFor(() => expect(initialized).not.toBeNull());
  return onSignedIn;
}

const posts = () => requests.filter((r) => r.method === 'POST');

test('the button starts with this site\'s client id and the nonce the server issued', async () => {
  await mount();

  expect(initialized?.client_id).toBe('prueba.apps.googleusercontent.com');
  expect(initialized?.nonce).toBe('nonce-firmado');
  await waitFor(() => expect(rendered).not.toBeNull());
  // La cookie del intento tiene que viajar: sin ella el servidor no acepta el token.
  expect(requests[0].credentials).toBe('include');
});

test('where Google is not configured there is no button and nothing is loaded', async () => {
  config = { enabled: false };
  const { container } = render(<GoogleSignIn onSignedIn={jest.fn()} />);

  await waitFor(() => expect(requests).toHaveLength(1));
  expect(initialized).toBeNull();
  expect(container).toBeEmptyDOMElement();
});

test('the token Google returns goes to the backend, and the browser keeps nothing', async () => {
  const onSignedIn = await mount();

  await act(async () => { initialized?.callback({ credential: 'ID.TOKEN.GOOGLE' }); });

  await waitFor(() => expect(onSignedIn).toHaveBeenCalledWith(USER));
  expect(posts()).toEqual([expect.objectContaining({
    url: expect.stringContaining('/auth/google/'), body: { credential: 'ID.TOKEN.GOOGLE' }, credentials: 'include',
  })]);
  expect(window.localStorage.length).toBe(0);
  expect(window.sessionStorage.length).toBe(0);
});

test('an account that already exists asks for its password before linking Google', async () => {
  signIn = { status: 409, body: { code: 'link_required', detail: 'Ya existe una cuenta con este correo.' } };
  const onSignedIn = await mount();

  await act(async () => { initialized?.callback({ credential: 'ID.TOKEN.GOOGLE' }); });

  expect(await screen.findByText(/Ya existe una cuenta con este correo/)).toBeInTheDocument();
  expect(onSignedIn).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Contraseña de tu cuenta'), { target: { value: 'Pass123!segura' } });
  fireEvent.click(screen.getByRole('button', { name: 'Vincular y entrar' }));

  await waitFor(() => expect(onSignedIn).toHaveBeenCalledWith(USER));
  expect(posts()[1]).toEqual(expect.objectContaining({
    url: expect.stringContaining('/auth/google/link/'),
    body: { credential: 'ID.TOKEN.GOOGLE', password: 'Pass123!segura' },
  }));
});

test('a wrong password says so and lets the person try again', async () => {
  signIn = { status: 409, body: { code: 'link_required', detail: 'Ya existe una cuenta con este correo.' } };
  link = { status: 401, body: { detail: 'No se pudo vincular la cuenta. Revisa la contraseña.' } };
  const onSignedIn = await mount();
  await act(async () => { initialized?.callback({ credential: 'ID.TOKEN.GOOGLE' }); });

  fireEvent.change(await screen.findByLabelText('Contraseña de tu cuenta'), { target: { value: 'mala' } });
  fireEvent.click(screen.getByRole('button', { name: 'Vincular y entrar' }));

  expect(await screen.findByText('No se pudo vincular la cuenta. Revisa la contraseña.')).toBeInTheDocument();
  expect(onSignedIn).not.toHaveBeenCalled();
  expect(screen.getByLabelText('Contraseña de tu cuenta')).toBeInTheDocument();
});

test('a token the server refuses is reported without details', async () => {
  signIn = { status: 400, body: { detail: 'No se pudo verificar la cuenta de Google.' } };
  const onSignedIn = await mount();

  await act(async () => { initialized?.callback({ credential: 'ID.TOKEN.GOOGLE' }); });

  expect(await screen.findByRole('alert')).toHaveTextContent('No se pudo verificar la cuenta de Google.');
  expect(onSignedIn).not.toHaveBeenCalled();
});

test('cancelling the link form brings the Google button back', async () => {
  signIn = { status: 409, body: { code: 'link_required', detail: 'Ya existe una cuenta con este correo.' } };
  await mount();
  await waitFor(() => expect(rendered).not.toBeNull());
  await act(async () => { initialized?.callback({ credential: 'ID.TOKEN.GOOGLE' }); });
  await screen.findByLabelText('Contraseña de tu cuenta');
  rendered = null;

  fireEvent.click(screen.getByRole('button', { name: 'Cancelar' }));

  await waitFor(() => expect(rendered).not.toBeNull());
  expect(screen.queryByLabelText('Contraseña de tu cuenta')).toBeNull();
});
