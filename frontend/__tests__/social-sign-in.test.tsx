import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { LinkedAccounts } from '@/app/components/LinkedAccounts';
import { SocialSignIn } from '@/app/components/SocialSignIn';
import { socialErrorMessage, socialStartUrl } from '@/app/lib/social-auth';

/**
 * SOCIAL-AUTH-01 · «Continuar con Facebook / Apple» y las cuentas vinculadas.
 *
 * El navegador no sabe nada de la configuración: el servidor le dice qué
 * proveedores están activos, y el botón es un enlace a `/api/auth/<p>/start`,
 * que es donde empieza el intento. Al volver, la sesión ya está en cookies
 * HttpOnly; aquí no se guarda nada.
 */

type Reply = { status: number; body: unknown };
let requests: { method: string; url: string; body: unknown }[];
let config: Reply;
let identities: Reply;
let linkReply: Reply;
let unlinkReply: Reply;

const PROVIDERS = (linked: Partial<Record<string, boolean>> = {}, enabled: Partial<Record<string, boolean>> = {}) => [
  { provider: 'google', label: 'Google', enabled: enabled.google ?? true, linkable: false, linked: !!linked.google, linked_at: null },
  { provider: 'facebook', label: 'Facebook', enabled: enabled.facebook ?? true, linkable: true, linked: !!linked.facebook, linked_at: linked.facebook ? '2026-10-01T10:00:00Z' : null },
  { provider: 'apple', label: 'Apple', enabled: enabled.apple ?? true, linkable: true, linked: !!linked.apple, linked_at: null },
];

function setConfig(google: boolean, facebook: boolean, apple: boolean) {
  config = { status: 200, body: { providers: { google: { enabled: google }, facebook: { enabled: facebook }, apple: { enabled: apple } } } };
}

beforeEach(() => {
  requests = [];
  setConfig(false, true, true);
  identities = { status: 200, body: { has_password: true, email_is_private_relay: false, providers: PROVIDERS() } };
  linkReply = { status: 200, body: { authorize_url: 'https://www.facebook.com/v23.0/dialog/oauth?state=abc' } };
  unlinkReply = { status: 204, body: null };
  window.localStorage.clear();
  window.sessionStorage.clear();
  window.history.replaceState(null, '', '/auth');
  document.cookie = 'csrftoken=csrf-de-prueba';
  global.fetch = jest.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = (init.method ?? 'GET').toUpperCase();
    requests.push({ method, url, body: typeof init.body === 'string' ? JSON.parse(init.body) : null });
    const reply = url.includes('/auth/social/config') ? config
      : url.includes('/link') ? linkReply
      : method === 'DELETE' ? unlinkReply
      : url.includes('/auth/social/identities') ? identities
      : { status: 200, body: {} };
    return {
      ok: reply.status >= 200 && reply.status < 300, status: reply.status, json: async () => reply.body,
    } as Response;
  }) as unknown as typeof fetch;
});

describe('SocialSignIn', () => {
  test('offers nothing when neither Facebook nor Apple is configured here', async () => {
    setConfig(true, false, false);
    const { container } = render(<SocialSignIn />);
    await waitFor(() => expect(requests.length).toBeGreaterThan(0));
    expect(container.querySelector('a')).toBeNull();
  });

  test('offers only the providers the server says are on, as links to the start of the flow', async () => {
    setConfig(false, true, false);
    render(<SocialSignIn next="/orders" />);
    const facebook = await screen.findByRole('link', { name: 'Continuar con Facebook' });
    expect(facebook.getAttribute('href')).toBe('/api/auth/facebook/start?next=%2Forders');
    expect(screen.queryByRole('link', { name: 'Continuar con Apple' })).toBeNull();
  });

  test('the Apple button is Apple\'s: its logo and its words, black or white', async () => {
    render(<SocialSignIn />);
    const apple = await screen.findByRole('link', { name: 'Continuar con Apple' });
    expect(apple.getAttribute('href')).toBe('/api/auth/apple/start');
    expect(apple.querySelector('svg[aria-hidden="true"]')).not.toBeNull();
    expect(apple.getAttribute('data-provider')).toBe('apple');
    expect(apple.className).toMatch(/bg-black/);
  });

  test('draws its own divider only when the Google button is not there to draw one', async () => {
    const { container, unmount } = render(<SocialSignIn />);
    await screen.findByRole('link', { name: 'Continuar con Facebook' });
    expect(container.querySelector('[data-social-divider]')).not.toBeNull();
    unmount();

    setConfig(true, true, true);
    const again = render(<SocialSignIn />);
    await screen.findByRole('link', { name: 'Continuar con Facebook' });
    expect(again.container.querySelector('[data-social-divider]')).toBeNull();
  });

  test('says why a sign-in came back refused, from the address the server landed on', async () => {
    window.history.replaceState(null, '', '/auth?social_error=link_required&provider=facebook');
    render(<SocialSignIn />);
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('Ya existe una cuenta con ese correo');
    expect(alert.textContent).toContain('Mi cuenta');
  });

  test('keeps nothing in the browser', async () => {
    render(<SocialSignIn />);
    await screen.findByRole('link', { name: 'Continuar con Facebook' });
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });

  test('when the server cannot be asked, there are no buttons and the password form stays', async () => {
    global.fetch = jest.fn(async () => { throw new Error('offline'); }) as unknown as typeof fetch;
    const { container } = render(<SocialSignIn />);
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(container.querySelector('a')).toBeNull();
  });
});

describe('social-auth helpers', () => {
  test('a next that leaves the site is never carried to the start', () => {
    expect(socialStartUrl('facebook', 'https://evil.example/x')).toBe('/api/auth/facebook/start');
    expect(socialStartUrl('apple', '//evil.example')).toBe('/api/auth/apple/start');
    expect(socialStartUrl('apple', '/cart')).toBe('/api/auth/apple/start?next=%2Fcart');
  });

  test('every refusal has a sentence, and an unknown one the generic', () => {
    expect(socialErrorMessage('not_allowed', 'apple')).toMatch(/usuario y contraseña/);
    expect(socialErrorMessage('cancelled', 'facebook')).toMatch(/cancel/i);
    expect(socialErrorMessage('lo-que-sea', 'facebook')).toBe(socialErrorMessage('refused', 'facebook'));
  });
});

describe('LinkedAccounts («Mi cuenta»)', () => {
  test('lists the three providers with what is linked', async () => {
    identities = { status: 200, body: { has_password: true, email_is_private_relay: false, providers: PROVIDERS({ facebook: true }) } };
    render(<LinkedAccounts />);
    expect(await screen.findByText('Facebook')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Desvincular Facebook' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Vincular Apple' })).toBeTruthy();
    // Google is linked from the sign-in screen, not from here.
    expect(screen.queryByRole('button', { name: 'Vincular Google' })).toBeNull();
  });

  test('a provider this site has not configured is not offered for linking', async () => {
    identities = { status: 200, body: { has_password: true, email_is_private_relay: false, providers: PROVIDERS({}, { apple: false }) } };
    render(<LinkedAccounts />);
    await screen.findByRole('button', { name: 'Vincular Facebook' });
    expect(screen.queryByRole('button', { name: 'Vincular Apple' })).toBeNull();
  });

  test('linking asks for the password, sends it and goes to the provider', async () => {
    const navigate = jest.fn();
    render(<LinkedAccounts navigate={navigate} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Vincular Facebook' }));
    fireEvent.change(screen.getByLabelText('Contraseña actual'), { target: { value: 'clave-segura-123' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continuar con Facebook' }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('https://www.facebook.com/v23.0/dialog/oauth?state=abc'));
    const post = requests.find((r) => r.method === 'POST');
    expect(post?.url).toContain('/auth/social/facebook/link');
    expect(post?.body).toEqual({ password: 'clave-segura-123' });
  });

  test('a wrong password is said, and nothing navigates', async () => {
    linkReply = { status: 400, body: { code: 'password', detail: 'La contraseña no es correcta.' } };
    const navigate = jest.fn();
    render(<LinkedAccounts navigate={navigate} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Vincular Apple' }));
    fireEvent.change(screen.getByLabelText('Contraseña actual'), { target: { value: 'otra' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continuar con Apple' }));
    expect((await screen.findByRole('alert')).textContent).toContain('La contraseña no es correcta.');
    expect(navigate).not.toHaveBeenCalled();
  });

  test('an account without a password is told to create one first', async () => {
    identities = { status: 200, body: { has_password: false, email_is_private_relay: false, providers: PROVIDERS({ google: true }) } };
    render(<LinkedAccounts />);
    await screen.findByText('Facebook');
    expect(screen.queryByRole('button', { name: 'Vincular Facebook' })).toBeNull();
    expect(screen.getByText(/crea una contraseña/i)).toBeTruthy();
  });

  test('unlinking the last way in shows the server\'s refusal', async () => {
    identities = { status: 200, body: { has_password: false, email_is_private_relay: false, providers: PROVIDERS({ facebook: true }) } };
    unlinkReply = { status: 409, body: { code: 'last_way_in', detail: 'Es tu única forma de entrar.' } };
    render(<LinkedAccounts />);
    fireEvent.click(await screen.findByRole('button', { name: 'Desvincular Facebook' }));
    expect((await screen.findByRole('alert')).textContent).toContain('Es tu única forma de entrar.');
    expect(requests.find((r) => r.method === 'DELETE')?.url).toContain('/auth/social/identities/facebook');
  });

  test('a successful unlink reloads the list', async () => {
    identities = { status: 200, body: { has_password: true, email_is_private_relay: false, providers: PROVIDERS({ facebook: true }) } };
    render(<LinkedAccounts />);
    fireEvent.click(await screen.findByRole('button', { name: 'Desvincular Facebook' }));
    await waitFor(() => expect(requests.filter((r) => r.url.includes('/auth/social/identities') && r.method === 'GET').length).toBe(2));
  });

  test('says the address is an Apple relay', async () => {
    identities = { status: 200, body: { has_password: true, email_is_private_relay: true, providers: PROVIDERS({ apple: true }) } };
    render(<LinkedAccounts />);
    expect(await screen.findByText(/reenvío privado de Apple/i)).toBeTruthy();
  });

  test('comes back from the provider saying what happened', async () => {
    window.history.replaceState(null, '', '/account?linked=apple');
    render(<LinkedAccounts />);
    expect((await screen.findByRole('status')).textContent).toContain('Apple quedó vinculado');
    window.history.replaceState(null, '', '/account?link_error=already_linked&provider=facebook');
    render(<LinkedAccounts />);
    expect((await screen.findByRole('alert')).textContent).toContain('ya está vinculada a otra cuenta');
  });
});
