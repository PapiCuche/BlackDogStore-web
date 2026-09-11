import { buildInternalAccess } from '@/app/admin/lib/internal-access';
import type { InternalDashboard } from '@/app/admin/lib/internal-api';

import { user } from './support/fixtures';

/**
 * Qué ofrece el control interno, y con qué autoridad lo decide — H4.1.2A.
 *
 * EL DEFECTO (RBAC-LEGACY-UI-01). Las pantallas decidían con `UserProfile.role`,
 * el rol global anterior a las empresas, mientras el backend preguntaba la
 * capacidad de la empresa. Resultado: a quien entró por una invitación —perfil
 * `customer` con capacidades reales— la interfaz lo echaba, y a un perfil
 * `sales` sin la capacidad le pintaba botones que respondían 403.
 */

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
  it('la concede cuando el servidor la informa', () => {
    const access = buildInternalAccess(user({ role: 'customer' }), dashboard());

    expect(access.can('sales.orders.view', LEGACY_ORDERS)).toBe(true);
  });

  it('el perfil global no abre lo que la empresa no concedió', () => {
    const access = buildInternalAccess(user({ role: 'admin' }), dashboard());

    expect(access.can('products.manage', ['admin', 'superadmin'])).toBe(false);
  });

  it('un perfil «customer» con la capacidad entra igual', () => {
    const access = buildInternalAccess(
      user({ role: 'customer' }),
      dashboard({
        access: {
          is_platform_admin: false,
          legacy_role: null,
          roles: [],
          areas: [],
          capabilities: ['products.manage'],
        },
      }),
    );

    expect(access.can('products.manage', ['admin', 'superadmin'])).toBe(true);
  });
});

describe('buildInternalAccess · sin empresa resuelta', () => {
  it('el operador legacy conserva su rol como autoridad', () => {
    const access = buildInternalAccess(user({ role: 'sales' }), null);

    expect(access.can('sales.orders.view', LEGACY_ORDERS)).toBe(true);
    expect(access.can('products.manage', ['admin', 'superadmin'])).toBe(false);
  });

  it('quien sí tiene empresas pero no eligió una, no pasa por el puente legacy', () => {
    const access = buildInternalAccess(
      user({ role: 'admin' }),
      dashboard({ company: null, requires_company_selection: true }),
    );

    expect(access.hasCompanyContext).toBe(false);
    expect(access.can('sales.orders.view', LEGACY_ORDERS)).toBe(false);
  });

  it('un rol que el puente no reconoce tampoco', () => {
    const access = buildInternalAccess(user({ role: 'technician' }), null);

    expect(access.can('sales.orders.view', LEGACY_ORDERS)).toBe(false);
  });
});

describe('buildInternalAccess · master de plataforma', () => {
  it('pasa siempre, porque el servidor le concede todo el catálogo', () => {
    const access = buildInternalAccess(
      user({ role: 'superadmin' }),
      dashboard({
        access: {
          is_platform_admin: true,
          legacy_role: null,
          roles: [],
          areas: [],
          capabilities: [],
        },
      }),
    );

    expect(access.isPlatformAdmin).toBe(true);
    expect(access.can('sales.fiscal.issue')).toBe(true);
  });
});
