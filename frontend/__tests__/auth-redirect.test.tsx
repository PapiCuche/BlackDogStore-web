import { fireEvent, render, waitFor } from '@testing-library/react';

import { StorefrontProvider } from '@/app/components/StorefrontProvider';
import { ThemeProvider } from '@/app/components/ThemeProvider';
import { NEUTRAL_CONFIG } from '@/app/lib/storefront';

import { user } from './support/fixtures';

/**
 * Adónde lleva un login correcto — H4.1.1.
 *
 * EL DEFECTO. Siempre a «/», sin mirar nada. Un técnico aterrizaba en el
 * escaparate, y quien llegaba desde una invitación (`/auth?next=/invitacion…`)
 * perdía la invitación: el enlace que la propia página de aceptación construye
 * se ignoraba.
 *
 * Lo que se fija: un `next` LOCAL manda; sin él, el servidor decide entre panel
 * y tienda; y un `next` hacia fuera del sitio nunca se sigue.
 */

const mockPush = jest.fn();
jest.mock('next/navigation', () => ({
  useRouter: () => ({
    push: mockPush, replace: jest.fn(), refresh: jest.fn(),
    back: jest.fn(), forward: jest.fn(), prefetch: jest.fn(),
  }),
  usePathname: () => '/auth',
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

const mockLogin = jest.fn();
const mockHasInternalAccess = jest.fn();
const mockForget = jest.fn();
jest.mock('@/app/lib/auth', () => ({
  login: (...args: unknown[]) => mockLogin(...args),
  logout: jest.fn(() => Promise.resolve()),
  getCurrentUser: jest.fn(() => Promise.resolve(null)),
  register: jest.fn(),
  hasInternalAccess: () => mockHasInternalAccess(),
  forgetInternalAccess: () => mockForget(),
}));

// La tarjeta de accesos de desarrollo pide cuentas al servidor; no es lo examinado.
jest.mock('@/app/auth/components/DevQuickLogin', () => ({ DevQuickLogin: () => null }));

// eslint-disable-next-line @typescript-eslint/no-require-imports
const AuthPage = require('@/app/auth/page').default;

beforeAll(() => {
  if (!window.matchMedia) {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: () => ({
        matches: false,
        addEventListener: () => {},
        removeEventListener: () => {},
        addListener: () => {},
        removeListener: () => {},
      }),
    });
  }
});

beforeEach(() => {
  mockPush.mockReset();
  mockForget.mockReset();
  mockHasInternalAccess.mockReset();
  mockLogin.mockReset();
  mockLogin.mockResolvedValue({ detail: 'ok', user: user({ role: 'customer', is_staff: false }) });
});

async function loginFrom(search: string): Promise<string> {
  window.history.pushState({}, '', `/auth${search}`);
  const { container } = render(
    <ThemeProvider>
      <StorefrontProvider config={NEUTRAL_CONFIG}>
        <AuthPage />
      </StorefrontProvider>
    </ThemeProvider>,
  );
  const form = await waitFor(() => {
    const found = container.querySelector('form');
    if (!found) throw new Error('todavía sin formulario');
    return found;
  });
  fireEvent.submit(form);
  await waitFor(() => expect(mockPush).toHaveBeenCalledTimes(1));
  return mockPush.mock.calls[0][0] as string;
}

describe('AuthPage · destino tras el login', () => {
  it('vuelve a la invitación que pidió iniciar sesión', async () => {
    const destination = await loginFrom(`?next=${encodeURIComponent('/invitacion?token=AbC-12_xyz')}`);

    expect(destination).toBe('/invitacion?token=AbC-12_xyz');
    // El acceso se vuelve a preguntar con la sesión nueva, no con la anterior.
    expect(mockForget).toHaveBeenCalled();
  });

  it('sin next, quien trabaja en una empresa va al panel', async () => {
    mockHasInternalAccess.mockResolvedValue(true);
    expect(await loginFrom('')).toBe('/admin');
  });

  it('sin next, un cliente va a la tienda', async () => {
    mockHasInternalAccess.mockResolvedValue(false);
    expect(await loginFrom('')).toBe('/');
  });

  it('el panel lo decide el servidor: la sesión dice «customer» y aun así va al panel', async () => {
    mockHasInternalAccess.mockResolvedValue(true);
    mockLogin.mockResolvedValue({ detail: 'ok', user: user({ role: 'customer', is_staff: false }) });
    expect(await loginFrom('')).toBe('/admin');
  });

  it.each([
    'https://evil.example/admin',
    '//evil.example',
    '/%2F%2Fevil.example',
    'javascript:alert(1)',
  ])('nunca sigue un next hacia fuera del sitio: %s', async (next) => {
    mockHasInternalAccess.mockResolvedValue(false);
    expect(await loginFrom(`?next=${encodeURIComponent(next)}`)).toBe('/');
  });
});
