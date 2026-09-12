import { render, screen, waitFor } from '@testing-library/react';

import { buildInternalAccess } from '@/app/admin/lib/internal-access';
import type { InternalDashboard } from '@/app/admin/lib/internal-api';

import { user } from './support/fixtures';

/**
 * Quién decide qué ofrece el control interno — H4.1.2 y H4.1.2B.
 *
 * DEFECTO 1 (RBAC-LEGACY-UI-01). Las pantallas decidían con `UserProfile.role`,
 * el rol global anterior a las empresas, mientras el backend preguntaba la
 * capacidad de la empresa. A quien entró por una invitación —perfil `customer`
 * con capacidades reales— la interfaz lo echaba; a un perfil `sales` sin la
 * capacidad le pintaba botones que respondían 403.
 *
 * DEFECTO 2 (ACCESSGUARD-403-LEGACY-01). El arreglo anterior trataba como
 * «operador legacy» a cualquiera cuyo panel respondiera 403 — y ese 403 lo
 * recibe igual alguien con la membresía REVOCADA. Revocar el acceso devolvía la
 * interfaz, decidida otra vez por el rol global. Un rechazo no es una
 * credencial: ahora el puente sólo existe si lo afirma el servidor.
 */

const mockGetCurrentUser = jest.fn();
const mockFetchDashboard = jest.fn();

jest.mock('@/app/lib/auth', () => ({
  getCurrentUser: () => mockGetCurrentUser(),
}));

jest.mock('@/app/admin/lib/internal-api', () => {
  const actual = jest.requireActual('@/app/admin/lib/internal-api');
  return { ...actual, fetchInternalDashboard: () => mockFetchDashboard() };
});

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { NoInternalAccessError } = require('@/app/admin/lib/internal-api');
// eslint-disable-next-line @typescript-eslint/no-require-imports
const { AccessGuard } = require('@/app/admin/components/AccessGuard');

function dashboard(over: Partial<InternalDashboard> = {}): InternalDashboard {
  return {
    company: { id: 1, name: 'Black Dog Store', slug: 'black-dog-store', is_active: true },
    membership: { id: 3, branch: null },
    branch_scope: { mode: 'all', default_branch: null, branches: [] },
    access: {
      is_platform_admin: false,
      legacy_role: 'sales',
      roles: [],
      areas: [],
      capabilities: ['sales.orders.view'],
    },
    organization: null,
    catalog: null,
    sales: null,
    inventory: null,
    available_companies: [],
    requires_company_selection: false,
    alerts: [],
    ...over,
  };
}

const LEGACY_ORDERS = ['inventory', 'sales', 'admin', 'superadmin'] as const;

describe('buildInternalAccess · con empresa resuelta manda la capacidad', () => {
  it('2 · Membership activa: la capacidad que informa el servidor', () => {
    const access = buildInternalAccess(user({ role: 'customer' }), dashboard());

    expect(access.can('sales.orders.view', LEGACY_ORDERS)).toBe(true);
  });

  it('6 · rol global admin con la capacidad retirada: sin acceso', () => {
    const access = buildInternalAccess(user({ role: 'admin' }), dashboard());

    expect(access.can('products.manage', ['admin', 'superadmin'])).toBe(false);
  });

  it('7 · perfil customer con la capacidad concedida: acceso', () => {
    const access = buildInternalAccess(
      user({ role: 'customer' }),
      dashboard({
        access: {
          is_platform_admin: false, legacy_role: null, roles: [], areas: [],
          capabilities: ['products.manage'],
        },
      }),
    );

    expect(access.can('products.manage', ['admin', 'superadmin'])).toBe(true);
  });
});

describe('buildInternalAccess · el puente legacy lo afirma el servidor', () => {
  it('1 · legacy genuino: el servidor lo confirma y conserva su rol', () => {
    const access = buildInternalAccess(user({ role: 'sales' }), null, true);

    expect(access.isLegacyBridge).toBe(true);
    expect(access.can('sales.orders.view', LEGACY_ORDERS)).toBe(true);
    // Y sólo para lo que ese rol abría: el puente no amplía nada.
    expect(access.can('products.manage', ['admin', 'superadmin'])).toBe(false);
  });

  it('4 · Membership revocada: 403 sin afirmación, y NO hay puente', () => {
    const access = buildInternalAccess(user({ role: 'admin' }), null, false);

    expect(access.isLegacyBridge).toBe(false);
    expect(access.can('sales.orders.view', LEGACY_ORDERS)).toBe(false);
    expect(access.can('products.manage', ['admin', 'superadmin'])).toBe(false);
  });

  it('5 · empresa desactivada: mismo 403, mismo resultado', () => {
    const access = buildInternalAccess(user({ role: 'superadmin' }), null, false);

    expect(access.can('sales.orders.view', LEGACY_ORDERS)).toBe(false);
  });

  it('9 · un 403 sin cuerpo no se interpreta como legacy', () => {
    // El valor por defecto es «sin puente»: ante la duda, cerrado.
    const access = buildInternalAccess(user({ role: 'admin' }), null);

    expect(access.can('sales.orders.view', LEGACY_ORDERS)).toBe(false);
  });

  it('3 · varias empresas sin elegir una: tampoco cae al puente', () => {
    const access = buildInternalAccess(
      user({ role: 'admin' }),
      dashboard({ company: null, requires_company_selection: true }),
      // Aunque alguien mandara `true`, hay contexto: no es un operador legacy.
      true,
    );

    expect(access.hasCompanyContext).toBe(false);
    expect(access.isLegacyBridge).toBe(false);
    expect(access.can('sales.orders.view', LEGACY_ORDERS)).toBe(false);
  });

  it('el master de plataforma pasa siempre', () => {
    const access = buildInternalAccess(
      user({ role: 'superadmin' }),
      dashboard({
        access: {
          is_platform_admin: true, legacy_role: null, roles: [], areas: [], capabilities: [],
        },
      }),
    );

    expect(access.can('sales.fiscal.issue')).toBe(true);
  });
});

describe('AccessGuard · qué hace con cada respuesta del servidor', () => {
  beforeEach(() => {
    mockGetCurrentUser.mockReset();
    mockFetchDashboard.mockReset();
    mockGetCurrentUser.mockResolvedValue(user({ role: 'admin' }));
  });

  function renderGuard() {
    return render(
      <AccessGuard capability="sales.orders.view" legacyRoles={LEGACY_ORDERS}>
        {() => <p>contenido interno</p>}
      </AccessGuard>,
    );
  }

  it('1 · el servidor confirma el puente: abre la pantalla', async () => {
    mockFetchDashboard.mockRejectedValue(new NoInternalAccessError('Sin empresa.', true));
    renderGuard();

    expect(await screen.findByText('contenido interno')).toBeInTheDocument();
  });

  it('4 y 9 · 403 sin afirmación: no abre, aunque el rol global sea admin', async () => {
    mockFetchDashboard.mockRejectedValue(new NoInternalAccessError('Sin empresa.', false));
    renderGuard();

    expect(await screen.findByText(/Acceso restringido/i)).toBeInTheDocument();
    expect(screen.queryByText('contenido interno')).not.toBeInTheDocument();
  });

  it('8 · un fallo de red no abre nada: falla cerrado', async () => {
    mockFetchDashboard.mockRejectedValue(new Error('Failed to fetch'));
    renderGuard();

    await waitFor(() => expect(screen.getByText(/No se pudo comprobar tu acceso/i)).toBeInTheDocument());
    expect(screen.queryByText('contenido interno')).not.toBeInTheDocument();
  });

  it('2 · con empresa y capacidad, abre', async () => {
    mockFetchDashboard.mockResolvedValue(dashboard());
    renderGuard();

    expect(await screen.findByText('contenido interno')).toBeInTheDocument();
  });
});
