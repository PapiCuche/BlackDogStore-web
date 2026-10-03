import { render, screen } from '@testing-library/react';

import { dashboard, user } from './support/fixtures';

/**
 * RBAC-F4 — Personal no ofrece lo que el servidor va a negar.
 *
 * Ver al personal exige `memberships.view`; invitar, desactivar el acceso y
 * reenviar o revocar una invitación exigen `memberships.manage`. La pantalla
 * pintaba todos los botones a cualquiera que pudiera entrar, y a quien sólo
 * podía ver cada clic le devolvía un 403.
 *
 * La autoridad sigue en el servidor: aquí sólo se comprueba que la pantalla no
 * ofrece una acción sin el permiso que esa acción pide.
 */

const mockFetchStaff = jest.fn();
const mockFetchInvitations = jest.fn();
const mockFetchAreas = jest.fn();
const mockFetchRoles = jest.fn();
const mockFetchBranches = jest.fn();

jest.mock('@/app/lib/staff', () => ({
  fetchStaff: (...a: unknown[]) => mockFetchStaff(...a),
  fetchInvitations: (...a: unknown[]) => mockFetchInvitations(...a),
  fetchAreas: (...a: unknown[]) => mockFetchAreas(...a),
  fetchRoles: (...a: unknown[]) => mockFetchRoles(...a),
  fetchBranches: (...a: unknown[]) => mockFetchBranches(...a),
  createInvitation: jest.fn(),
  resendInvitation: jest.fn(),
  revokeInvitation: jest.fn(),
  setStaffActive: jest.fn(),
}));

// El guard tiene sus propias pruebas. Aquí estorba: pide sesión y panel por
// red, y lo que se examina es la pantalla que va DENTRO.
jest.mock('@/app/admin/components/InternalControlGuard', () => ({
  InternalControlGuard: ({ children }: { children: (ctx: unknown) => React.ReactNode }) =>
    children((globalThis as { __ctx?: unknown }).__ctx),
}));

jest.mock('@/app/admin/components/AdminShell', () => ({
  AdminShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

// eslint-disable-next-line @typescript-eslint/no-require-imports
const StaffPage = require('@/app/admin/staff/page').default;

function persona(overrides: Record<string, unknown> = {}) {
  return {
    id: 41,
    full_name: 'Ana Torres Pérez',
    first_name: 'Ana',
    last_name: 'Torres Pérez',
    email: 'ana@correo.test',
    is_active: true,
    is_self: false,
    areas: [{ id: 5, name: 'Servicio Técnico' }],
    roles: [{ id: 9, name: 'Técnico' }],
    branch_access_mode: 'all',
    branches: [],
    branch_scope_label: 'Todas las sucursales',
    ...overrides,
  };
}

function contexto(capabilities: string[], platformAdmin = false) {
  const base = dashboard(capabilities);
  return {
    user: user(),
    dashboard: { ...base, access: { ...base.access, is_platform_admin: platformAdmin } },
    selectedCompanyId: null,
    reload: jest.fn(),
    selectCompany: jest.fn(),
  };
}

function invitacion() {
  return {
    id: 77,
    email: 'nuevo@correo.test',
    full_name: 'Luis Nuevo',
    role_name: 'Técnico',
    area_name: 'Servicio Técnico',
    status: 'pending',
    is_usable: true,
    created_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 86_400_000).toISOString(),
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockFetchStaff.mockResolvedValue([persona()]);
  mockFetchInvitations.mockResolvedValue([invitacion()]);
  mockFetchAreas.mockResolvedValue([]);
  mockFetchRoles.mockResolvedValue([]);
  mockFetchBranches.mockResolvedValue([]);
});

async function pantalla(ctx: unknown) {
  (globalThis as { __ctx?: unknown }).__ctx = ctx;
  render(<StaffPage />);
  await screen.findByText('Ana Torres Pérez');
  await screen.findByText('Luis Nuevo');
}

const ACCIONES = ['Añadir trabajador', 'Desactivar acceso', 'Reenviar', 'Revocar'];

describe('Personal · acciones según el permiso', () => {
  it('quien sólo puede ver ve la lista y las invitaciones, y ninguna acción', async () => {
    await pantalla(contexto(['memberships.view']));

    for (const name of ACCIONES) {
      expect(screen.queryByRole('button', { name })).not.toBeInTheDocument();
    }
  });

  it('quien puede administrar tiene las cuatro acciones', async () => {
    await pantalla(contexto(['memberships.view', 'memberships.manage']));

    for (const name of ACCIONES) {
      expect(screen.getByRole('button', { name })).toBeInTheDocument();
    }
  });

  it('el master de la plataforma las tiene aunque su lista de capacidades venga vacía', async () => {
    await pantalla(contexto([], true));

    for (const name of ACCIONES) {
      expect(screen.getByRole('button', { name })).toBeInTheDocument();
    }
  });
});
