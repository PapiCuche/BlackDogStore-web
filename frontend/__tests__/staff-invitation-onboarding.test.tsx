/**
 * STAFF-ONBOARDING-01 — una persona invitada siempre puede terminar.
 *
 * EL DEFECTO: la invitación ofrecía «Crear cuenta», el registro creaba una
 * cuenta sin verificar, y desde ahí el correo «ya estaba registrado» sin que la
 * persona pudiera entrar ni recuperar nada. Además, «Crear cuenta» abría el
 * login, sin el correo de la invitación.
 *
 * LO QUE ESTAS PRUEBAS FIJAN:
 *   · la invitación ofrece el camino que SÍ termina, según lo que diga el
 *     servidor de esa cuenta: crearla, entrar, o establecer la contraseña;
 *   · crear la cuenta usa el correo de la invitación y no otro, lleva la
 *     invitación al servidor, y deja a la persona dentro, de vuelta en ella;
 *   · recuperar la contraseña vuelve a la invitación, y a nada más.
 *
 * Quién puede aceptar lo decide el servidor (`test_staff_onboarding.py`). Aquí
 * se examina qué se le ofrece a la persona.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ThemeProvider } from '@/app/components/ThemeProvider';
import { StorefrontProvider } from '@/app/components/StorefrontProvider';
import { NEUTRAL_CONFIG } from '@/app/lib/storefront';
import { invitationTokenFromNext } from '@/app/lib/invitation';

const TOKEN = 'AbCdEfGhIjKlMnOpQrStUvWxYz0123456789-_AbCdEfGhIjKlMnOpQrStUvWxYz';
const INVITATION = `/invitacion?token=${TOKEN}`;
const WORKER = 'trabajador@empresa.test';

const mockPush = jest.fn();
let mockSearch = new URLSearchParams();
jest.mock('next/navigation', () => ({
  useRouter: () => ({
    push: mockPush, replace: jest.fn(), refresh: jest.fn(),
    back: jest.fn(), forward: jest.fn(), prefetch: jest.fn(),
  }),
  usePathname: () => '/',
  useSearchParams: () => mockSearch,
  useParams: () => ({}),
}));

const mockLogin = jest.fn();
const mockLogout = jest.fn();
const mockRegister = jest.fn();
const mockCurrentUser = jest.fn();
const mockFetchWithAuth = jest.fn();
const mockRequestReset = jest.fn();
const mockConfirmReset = jest.fn();
jest.mock('@/app/lib/auth', () => ({
  login: (...args: unknown[]) => mockLogin(...args),
  logout: (...args: unknown[]) => mockLogout(...args),
  register: (...args: unknown[]) => mockRegister(...args),
  getCurrentUser: () => mockCurrentUser(),
  fetchWithAuth: (...args: unknown[]) => mockFetchWithAuth(...args),
  requestPasswordReset: (...args: unknown[]) => mockRequestReset(...args),
  confirmPasswordReset: (...args: unknown[]) => mockConfirmReset(...args),
  hasInternalAccess: () => Promise.resolve(false),
  forgetInternalAccess: jest.fn(),
}));
jest.mock('@/app/auth/components/DevQuickLogin', () => ({ DevQuickLogin: () => null }));
jest.mock('@/app/components/GoogleSignIn', () => ({ GoogleSignIn: () => null }));

/* eslint-disable @typescript-eslint/no-require-imports */
const InvitationPage = require('@/app/invitacion/page').default;
const AuthPage = require('@/app/auth/page').default;
const ForgotPasswordPage = require('@/app/auth/forgot-password/page').default;
const ResetPasswordPage = require('@/app/auth/reset-password/page').default;
/* eslint-enable @typescript-eslint/no-require-imports */

type Account = 'none' | 'active' | 'unverified';

/** Lo que responde el servidor al leer la invitación. */
function serverSays(account: Account | null, extra: Record<string, unknown> = {}) {
  const body: Record<string, unknown> = {
    company_name: 'Empresa A', email: WORKER, first_name: 'Ana', last_name: 'Torres',
    role_name: 'Ventas', area_name: '', expires_at: '2026-10-14T00:00:00Z',
    requires_authentication: account === 'active' || account === 'unverified',
    ...extra,
  };
  if (account !== null) body.account_state = account;
  (global as unknown as { fetch: jest.Mock }).fetch = jest.fn(() =>
    Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) }),
  );
}

function serverAnswers(status: number) {
  (global as unknown as { fetch: jest.Mock }).fetch = jest.fn(() =>
    Promise.resolve({ ok: false, status, json: () => Promise.resolve({ detail: 'x' }) }),
  );
}

/** Volver atrás a una página que el navegador tenía guardada. */
function restored(): Event {
  const event = new Event('pageshow');
  Object.defineProperty(event, 'persisted', { value: true });
  return event;
}

function wrapped(node: React.ReactNode) {
  return render(
    <ThemeProvider>
      <StorefrontProvider config={NEUTRAL_CONFIG}>{node}</StorefrontProvider>
    </ThemeProvider>,
  );
}

async function openInvitation() {
  mockSearch = new URLSearchParams({ token: TOKEN });
  const view = wrapped(<InvitationPage />);
  await screen.findByText(/Te han invitado a Empresa A/);
  return view;
}

beforeAll(() => {
  if (!window.matchMedia) {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: () => ({
        matches: false, addEventListener: () => {}, removeEventListener: () => {},
        addListener: () => {}, removeListener: () => {},
      }),
    });
  }
});

beforeEach(() => {
  for (const mock of [mockPush, mockLogin, mockLogout, mockRegister, mockCurrentUser, mockFetchWithAuth, mockRequestReset, mockConfirmReset]) {
    mock.mockReset();
  }
  mockCurrentUser.mockResolvedValue(null);
  mockLogout.mockResolvedValue(undefined);
  mockRequestReset.mockResolvedValue({ detail: 'ok' });
  window.sessionStorage.clear();
  window.history.pushState({}, '', '/');
});

describe('la dirección de vuelta', () => {
  it('reconoce la de una invitación y saca su token', () => {
    expect(invitationTokenFromNext(INVITATION)).toBe(TOKEN);
    // Como llega de verdad: leída de la dirección, ya decodificada.
    const search = new URLSearchParams(`?next=${encodeURIComponent(INVITATION)}`);
    expect(invitationTokenFromNext(search.get('next'))).toBe(TOKEN);
  });

  it.each([
    null, '', '/admin', '/invitacion', '/invitacion?token=', '/invitacion?token=corto',
    `https://evil.test${INVITATION}`, `//evil.test${INVITATION}`, `/auth?next=${INVITATION}`,
    '/invitacion?token=con espacio dentro del token', `/invitacion/otra?token=${TOKEN}`,
    encodeURIComponent(INVITATION),
  ])('no toma por invitación %p', (raw) => {
    expect(invitationTokenFromNext(raw)).toBeNull();
  });
});

describe('la invitación ofrece el camino que termina', () => {
  it('sin cuenta: crearla, con el modo de registro y la vuelta a la invitación', async () => {
    serverSays('none');
    await openInvitation();

    const create = screen.getByRole('link', { name: 'Crear mi cuenta' });
    expect(create.getAttribute('href')).toBe(`/auth?mode=register&next=${encodeURIComponent(INVITATION)}`);
    expect(screen.getByText(/Crea tu cuenta para continuar/)).toBeTruthy();
    expect(screen.queryByRole('link', { name: 'Iniciar sesión' })).toBeNull();
    expect(screen.queryByRole('button', { name: /contraseña/i })).toBeNull();
  });

  it('con cuenta: entrar, y NUNCA crear otra', async () => {
    serverSays('active');
    await openInvitation();

    expect(screen.getByText(/Ya tienes una cuenta con este correo/)).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Iniciar sesión' }).getAttribute('href'))
      .toBe(`/auth?next=${encodeURIComponent(INVITATION)}`);
    expect(screen.queryByText(/Crear/)).toBeNull();
  });

  it('con cuenta y sin contraseña conocida: pide el enlace para ESE correo y con vuelta a la invitación', async () => {
    serverSays('active');
    await openInvitation();

    fireEvent.click(screen.getByRole('button', { name: 'No conozco mi contraseña' }));

    await screen.findByText(/Te enviamos un enlace/);
    expect(mockRequestReset).toHaveBeenCalledTimes(1);
    expect(mockRequestReset).toHaveBeenCalledWith(WORKER, INVITATION);
  });

  it('cuenta registrada y nunca confirmada: no se ofrece un inicio de sesión que sólo puede fallar', async () => {
    serverSays('unverified');
    await openInvitation();

    expect(screen.queryByRole('link', { name: 'Iniciar sesión' })).toBeNull();
    expect(screen.queryByText(/Crear/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Establecer mi contraseña' }));
    await screen.findByText(/Te enviamos un enlace/);
    expect(mockRequestReset).toHaveBeenCalledWith(WORKER, INVITATION);
  });

  it('un servidor que aún no dice el estado se entiende como antes', async () => {
    serverSays(null, { requires_authentication: true });
    await openInvitation();
    expect(screen.getByRole('link', { name: 'Iniciar sesión' })).toBeTruthy();
  });

  it('con otra sesión abierta: no se puede aceptar, y se puede salir de ella aquí mismo', async () => {
    serverSays('active');
    mockCurrentUser.mockResolvedValue({ email: 'otra@empresa.test' });
    await openInvitation();

    await screen.findByText(/estás dentro como otra@empresa.test/);
    expect(screen.queryByRole('button', { name: 'Aceptar invitación' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Cerrar sesión' }));
    await waitFor(() => expect(mockLogout).toHaveBeenCalledTimes(1));
    await screen.findByRole('link', { name: 'Iniciar sesión' });
  });

  it('con la sesión del correo invitado: aceptar, y el acceso queda configurado', async () => {
    serverSays('active');
    mockCurrentUser.mockResolvedValue({ email: 'Trabajador@Empresa.test' });
    mockFetchWithAuth.mockResolvedValue({
      ok: true, status: 200, json: () => Promise.resolve({ company_name: 'Empresa A', membership_id: 7 }),
    });
    await openInvitation();

    fireEvent.click(await screen.findByRole('button', { name: 'Aceptar invitación' }));

    await screen.findByText('Tu acceso ha sido configurado correctamente');
    expect(JSON.parse(mockFetchWithAuth.mock.calls[0][1].body)).toEqual({ token: TOKEN });
    expect(screen.getByRole('link', { name: 'Ir al panel' }).getAttribute('href')).toBe('/admin');
  });

  it('vuelve a preguntar al regresar a la pestaña: lo que ofrecía pudo dejar de ser cierto', async () => {
    serverSays('none');
    await openInvitation();
    expect(screen.getByRole('link', { name: 'Crear mi cuenta' })).toBeTruthy();

    serverSays('active');
    await act(async () => { window.dispatchEvent(restored()); });

    await screen.findByRole('link', { name: 'Iniciar sesión' });
    expect(screen.queryByText(/Crear/)).toBeNull();
  });

  it('una carga normal pregunta UNA vez: cada lectura gasta un cupo que es de toda la red', async () => {
    serverSays('none');
    await openInvitation();
    await act(async () => { window.dispatchEvent(new Event('pageshow')); });
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')); });
    expect((global as unknown as { fetch: jest.Mock }).fetch).toHaveBeenCalledTimes(1);
  });

  it('si no se puede comprobar, no se dice que la invitación no vale', async () => {
    serverAnswers(429);
    mockSearch = new URLSearchParams({ token: TOKEN });
    wrapped(<InvitationPage />);

    await screen.findByText(/No pudimos comprobar la invitación/);
    expect(screen.queryByText(/no es válida/)).toBeNull();
    expect(screen.queryByText(/te envíe una nueva/)).toBeNull();
  });

  it('una invitación que servía no deja de servir porque una lectura posterior falle', async () => {
    serverSays('none');
    await openInvitation();

    serverAnswers(429);
    await act(async () => { window.dispatchEvent(restored()); });

    await waitFor(() => expect((global as unknown as { fetch: jest.Mock }).fetch).toHaveBeenCalledTimes(1));
    expect(screen.getByRole('link', { name: 'Crear mi cuenta' })).toBeTruthy();
    expect(screen.queryByText(/no es válida|No pudimos/)).toBeNull();
  });

  it('la que de verdad no sirve se sigue diciendo con un solo mensaje', async () => {
    serverAnswers(404);
    mockSearch = new URLSearchParams({ token: TOKEN });
    wrapped(<InvitationPage />);
    await screen.findByRole('heading', { name: 'Invitación no válida' });
    expect(screen.getByText(/te envíe una nueva/)).toBeTruthy();
  });
});

describe('crear la cuenta desde una invitación', () => {
  async function openRegistration(search: string) {
    window.history.pushState({}, '', `/auth${search}`);
    const view = wrapped(<AuthPage />);
    await waitFor(() => expect(view.container.querySelector('form')).toBeTruthy());
    return view;
  }

  function fill(container: HTMLElement, values: Record<string, string>) {
    for (const [id, value] of Object.entries(values)) {
      fireEvent.change(container.querySelector(`#${id}`) as HTMLInputElement, { target: { value } });
    }
  }

  it('usa el correo de la invitación, lleva la invitación al servidor y deja a la persona dentro, de vuelta en ella', async () => {
    serverSays('none');
    mockRegister.mockResolvedValue({ detail: 'ok', requires_verification: false });
    mockLogin.mockResolvedValue({ detail: 'ok', user: { email: WORKER } });

    const { container } = await openRegistration(`?mode=register&next=${encodeURIComponent(INVITATION)}`);
    const email = await waitFor(() => {
      const input = container.querySelector('#auth-page-correo-electronico') as HTMLInputElement | null;
      if (!input || input.value !== WORKER) throw new Error('todavía sin el correo de la invitación');
      return input;
    });
    expect(email.readOnly).toBe(true);
    expect(screen.getByRole('heading', { name: 'Crear cuenta' })).toBeTruthy();

    fill(container, { 'auth-page-usuario': 'ana.torres', 'auth-page-contrasena': 'Una-clave-2026!', 'auth-page-confirmar-contrasena': 'Una-clave-2026!' });
    // Aunque algo consiguiera escribir en ese campo, la cuenta se crea con el
    // correo de la invitación: «sólo lectura» es una cortesía, no la regla.
    fireEvent.change(email, { target: { value: 'otra@empresa.test' } });
    fireEvent.submit(container.querySelector('form') as HTMLFormElement);

    await waitFor(() => expect(mockPush).toHaveBeenCalledWith(INVITATION));
    expect(mockRegister).toHaveBeenCalledWith({
      username: 'ana.torres', email: WORKER, password: 'Una-clave-2026!',
      password_confirm: 'Una-clave-2026!', invitation_token: TOKEN,
    });
    expect(mockLogin).toHaveBeenCalledWith('ana.torres', 'Una-clave-2026!');
  });

  it('si esa cuenta ya existe, no se ofrece el registro: se abre el inicio de sesión', async () => {
    serverSays('active');
    await openRegistration(`?mode=register&next=${encodeURIComponent(INVITATION)}`);
    await screen.findByRole('heading', { name: 'Iniciar sesión' });
    expect(screen.getByText(/Ya tienes una cuenta con este correo/)).toBeTruthy();
  });

  it('si la invitación no se pudo leer, no se crea la cuenta sin ella', async () => {
    serverAnswers(429);
    const { container } = await openRegistration(`?mode=register&next=${encodeURIComponent(INVITATION)}`);
    await waitFor(() => expect((global as unknown as { fetch: jest.Mock }).fetch).toHaveBeenCalled());
    await screen.findByRole('heading', { name: 'Crear cuenta' });

    fill(container, {
      'auth-page-usuario': 'ana.torres', 'auth-page-correo-electronico': WORKER,
      'auth-page-contrasena': 'Una-clave-2026!', 'auth-page-confirmar-contrasena': 'Una-clave-2026!',
    });
    fireEvent.submit(container.querySelector('form') as HTMLFormElement);

    await screen.findByText(/No pudimos comprobar tu invitación/);
    expect(mockRegister).not.toHaveBeenCalled();
  });

  it('sin invitación, registrarse es lo que era: sin dato de invitación y sin entrar solo', async () => {
    mockRegister.mockResolvedValue({ detail: 'ok', requires_verification: true });
    const { container } = await openRegistration('?mode=register');

    fill(container, {
      'auth-page-usuario': 'cliente', 'auth-page-correo-electronico': 'cliente@correo.test',
      'auth-page-contrasena': 'Una-clave-2026!', 'auth-page-confirmar-contrasena': 'Una-clave-2026!',
    });
    fireEvent.submit(container.querySelector('form') as HTMLFormElement);

    // MAIL-TEMPLATE-01 (3A): en vez del aviso «revisa tu correo», la pantalla pide el
    // código de ese correo. Sigue sin entrar sola.
    await screen.findByRole('heading', { name: 'Verifica tu correo' });
    expect(screen.getByText(/cliente@correo\.test/)).toBeInTheDocument();
    expect(mockRegister).toHaveBeenCalledWith({
      username: 'cliente', email: 'cliente@correo.test', password: 'Una-clave-2026!', password_confirm: 'Una-clave-2026!',
    });
    expect(mockLogin).not.toHaveBeenCalled();
    expect(mockPush).not.toHaveBeenCalled();
  });

  it('«¿Olvidaste tu contraseña?» conserva la vuelta a la invitación', async () => {
    serverSays('active');
    await openRegistration(`?next=${encodeURIComponent(INVITATION)}`);
    const link = await screen.findByRole('link', { name: '¿Olvidaste tu contraseña?' });
    await waitFor(() => expect(link.getAttribute('href')).toBe(`/auth/forgot-password?next=${encodeURIComponent(INVITATION)}`));
  });

  it('trae escrito el usuario que la recuperación acaba de decir, y lo olvida', async () => {
    window.sessionStorage.setItem('bd.auth.username', 'ana.google');
    const { container } = await openRegistration('');
    await waitFor(() => expect((container.querySelector('#auth-page-usuario') as HTMLInputElement).value).toBe('ana.google'));
    expect(window.sessionStorage.getItem('bd.auth.username')).toBeNull();
  });
});

describe('recuperar la contraseña vuelve a la invitación', () => {
  it('pedir el enlace lleva la vuelta sólo si es la de una invitación', async () => {
    for (const [search, expected] of [
      [`?next=${encodeURIComponent(INVITATION)}`, INVITATION],
      ['?next=%2Fadmin', undefined],
      ['?next=https%3A%2F%2Fevil.test', undefined],
      ['', undefined],
    ] as const) {
      mockRequestReset.mockClear();
      window.history.pushState({}, '', `/auth/forgot-password${search}`);
      const { container, unmount } = wrapped(<ForgotPasswordPage />);
      fireEvent.change(container.querySelector('input[type="email"]') as HTMLInputElement, { target: { value: WORKER } });
      fireEvent.submit(container.querySelector('form') as HTMLFormElement);
      await waitFor(() => expect(mockRequestReset).toHaveBeenCalledTimes(1));
      expect(mockRequestReset.mock.calls[0]).toEqual(expected ? [WORKER, expected] : [WORKER]);
      unmount();
    }
  });

  async function resetWith(search: Record<string, string>, answer: Record<string, unknown>) {
    mockSearch = new URLSearchParams(search);
    mockConfirmReset.mockResolvedValue(answer);
    const { container } = wrapped(<ResetPasswordPage />);
    const [first, second] = Array.from(container.querySelectorAll('input[type="password"]')) as HTMLInputElement[];
    fireEvent.change(first, { target: { value: 'Otra-clave-2026!' } });
    fireEvent.change(second, { target: { value: 'Otra-clave-2026!' } });
    fireEvent.submit(container.querySelector('form') as HTMLFormElement);
    await screen.findByText('Contraseña restablecida');
    return container;
  }

  it('tras establecerla, el inicio de sesión lleva de vuelta a la invitación y dice con qué usuario', async () => {
    await resetWith({ token: 'reset-token', next: INVITATION }, { detail: 'ok', username: 'ana.google' });

    expect(mockConfirmReset).toHaveBeenCalledWith('reset-token', 'Otra-clave-2026!');
    expect(screen.getByText('ana.google')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Iniciar sesión' }).getAttribute('href'))
      .toBe(`/auth?next=${encodeURIComponent(INVITATION)}`);
    expect(screen.getByText(/volverás a tu invitación/)).toBeTruthy();
    expect(window.sessionStorage.getItem('bd.auth.username')).toBe('ana.google');
  });

  it.each(['https://evil.test/x', '//evil.test', '/admin', '/auth'])('una vuelta que no es una invitación (%s) no se sigue', async (next) => {
    await resetWith({ token: 'reset-token', next }, { detail: 'ok' });
    expect(screen.getByRole('link', { name: 'Iniciar sesión' }).getAttribute('href')).toBe('/auth');
  });
});
