import { render, screen, waitFor } from '@testing-library/react';

import { StorefrontProvider } from '@/app/components/StorefrontProvider';
import { ThemeProvider } from '@/app/components/ThemeProvider';
import { NEUTRAL_CONFIG } from '@/app/lib/storefront';

import { user } from './support/fixtures';

/**
 * La puerta visible al control interno — H4.1.1.
 *
 * EL DEFECTO. La cabecera mostraba «Admin» sólo si `user.role` era admin o
 * superadmin. Un técnico con membresía activa —con doce capacidades de servicio
 * confirmadas por el servidor— veía la tienda como un cliente y tenía que
 * escribir /admin a mano.
 *
 * Lo que se fija aquí es que la decisión la toma el SERVIDOR: el rol legacy de
 * la sesión se varía a propósito y no debe cambiar nada.
 */

const mockGetCurrentUser = jest.fn();
const mockHasInternalAccess = jest.fn();

jest.mock('@/app/lib/auth', () => ({
  getCurrentUser: () => mockGetCurrentUser(),
  hasInternalAccess: () => mockHasInternalAccess(),
  forgetInternalAccess: jest.fn(),
  logout: jest.fn(() => Promise.resolve()),
}));

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { Header } = require('@/app/components/Header');

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
  mockGetCurrentUser.mockReset();
  mockHasInternalAccess.mockReset();
  // El contador del carrito pide al servidor; aquí no importa su respuesta.
  global.fetch = jest.fn(() =>
    Promise.resolve({ ok: false, json: () => Promise.resolve([]) }),
  ) as unknown as typeof fetch;
});

function renderHeader() {
  return render(
    <ThemeProvider>
      <StorefrontProvider config={NEUTRAL_CONFIG}>
        <Header />
      </StorefrontProvider>
    </ThemeProvider>,
  );
}

describe('Header · Control interno', () => {
  it('un cliente puro no lo ve', async () => {
    mockGetCurrentUser.mockResolvedValue(user({ role: 'customer', is_staff: false }));
    mockHasInternalAccess.mockResolvedValue(false);
    renderHeader();

    expect(await screen.findByRole('link', { name: 'Pedidos' })).toBeInTheDocument();
    await waitFor(() => expect(mockHasInternalAccess).toHaveBeenCalled());
    expect(screen.queryByRole('link', { name: 'Control interno' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Admin' })).not.toBeInTheDocument();
  });

  it('un técnico con membresía lo ve, aunque su rol legacy no sea de staff', async () => {
    mockGetCurrentUser.mockResolvedValue(user({ role: 'technician' }));
    mockHasInternalAccess.mockResolvedValue(true);
    renderHeader();

    expect(await screen.findByRole('link', { name: 'Control interno' })).toHaveAttribute('href', '/admin');
  });

  it('quien es cliente y trabajador ve las dos cosas', async () => {
    mockGetCurrentUser.mockResolvedValue(user({ role: 'customer', is_staff: false }));
    mockHasInternalAccess.mockResolvedValue(true);
    renderHeader();

    expect(await screen.findByRole('link', { name: 'Control interno' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Pedidos' })).toBeInTheDocument();
  });

  it('un rol legacy «admin» SIN acceso real no lo ve: la autoridad no es el rol', async () => {
    mockGetCurrentUser.mockResolvedValue(user({ role: 'admin' }));
    mockHasInternalAccess.mockResolvedValue(false);
    renderHeader();

    await waitFor(() => expect(mockHasInternalAccess).toHaveBeenCalled());
    expect(screen.queryByRole('link', { name: 'Control interno' })).not.toBeInTheDocument();
  });

  it('sin sesión no pregunta por el acceso interno', async () => {
    mockGetCurrentUser.mockResolvedValue(null);
    renderHeader();

    expect(await screen.findByRole('link', { name: 'Ingresar' })).toBeInTheDocument();
    expect(mockHasInternalAccess).not.toHaveBeenCalled();
  });
});
