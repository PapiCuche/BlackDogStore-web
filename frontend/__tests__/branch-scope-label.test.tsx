import { render, screen } from '@testing-library/react';

import type { BranchScope, InternalDashboard } from '@/app/admin/lib/internal-api';

import { user } from './support/fixtures';

/**
 * La sucursal que anuncia la barra del control interno — H4.1.2A.
 *
 * EL DEFECTO (BRANCH-CONTEXT-UI-01). La etiqueta salía de
 * `dashboard.inventory.branches`, y el backend sólo construye ese bloque para
 * quien tiene `inventory.view` o `inventory.reports`. Un técnico alcanza
 * sucursales y no tiene ninguna de las dos, así que la barra le decía «Sin
 * sucursal»: una frase sobre inventario disfrazada de alcance.
 *
 * Ahora la etiqueta sale de `branch_scope`, que es contexto de acceso.
 */

jest.mock('@/app/admin/components/NotificationBell', () => ({
  NotificationBell: () => null,
}));

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { InternalTopbar, branchScopeLabel } = require('@/app/admin/components/InternalTopbar');

function scope(over: Partial<BranchScope> = {}): BranchScope {
  return { mode: 'all', default_branch: null, branches: [], ...over };
}

function dashboard(branchScope: BranchScope | null): InternalDashboard {
  return {
    company: { id: 1, name: 'Black Dog Store', slug: 'black-dog-store', is_active: true },
    membership: { id: 3, branch: null },
    branch_scope: branchScope,
    access: {
      is_platform_admin: false,
      legacy_role: 'technician',
      roles: [{ id: 1, name: 'Servicio Técnico', slug: 'servicio-tecnico', area: null }],
      areas: [],
      capabilities: ['service.orders.view'],
    },
    organization: null,
    catalog: null,
    sales: null,
    // El técnico no tiene capacidad de inventario: aquí es donde nacía el defecto.
    inventory: null,
    available_companies: [],
    requires_company_selection: false,
    alerts: [],
  };
}

describe('branchScopeLabel', () => {
  it('con una sucursal alcanzable, dice su nombre', () => {
    expect(branchScopeLabel(scope({ branches: [{ id: 7, name: 'Taller Cayma' }] })))
      .toBe('Taller Cayma');
  });

  it('con varias, las cuenta', () => {
    expect(
      branchScopeLabel(
        scope({ branches: [{ id: 1, name: 'A' }, { id: 2, name: 'B' }, { id: 3, name: 'C' }] }),
      ),
    ).toBe('3 sucursales');
  });

  it('a quien las recibe una a una y no tiene ninguna, se lo dice así', () => {
    expect(branchScopeLabel(scope({ mode: 'selected' }))).toBe('Sin sucursales asignadas');
  });

  it('a quien las alcanza todas y la empresa no tiene ninguna activa, también', () => {
    expect(branchScopeLabel(scope({ mode: 'all' }))).toBe('Sin sucursales activas');
  });

  it('sin empresa resuelta no inventa una frase', () => {
    expect(branchScopeLabel(null)).toBeNull();
    expect(branchScopeLabel(undefined)).toBeNull();
  });

  it('el master y el puente legacy leen la misma regla', () => {
    expect(branchScopeLabel(scope({ mode: 'platform', branches: [{ id: 9, name: 'Única' }] })))
      .toBe('Única');
    expect(branchScopeLabel(scope({ mode: 'legacy', branches: [] })))
      .toBe('Sin sucursales activas');
  });
});

describe('InternalTopbar · la barra del técnico', () => {
  function renderTopbar(branchScope: BranchScope | null) {
    return render(
      <InternalTopbar
        user={user({ role: 'technician' })}
        dashboard={dashboard(branchScope)}
        onOpenMenu={() => {}}
        onSelectCompany={() => {}}
      />,
    );
  }

  it('sin bloque de inventario, sigue diciendo su sucursal', () => {
    renderTopbar(scope({ branches: [{ id: 7, name: 'Taller Cayma' }] }));

    expect(screen.getByText('Taller Cayma')).toBeInTheDocument();
    expect(screen.queryByText('Sin sucursal')).not.toBeInTheDocument();
  });

  it('sin alcance resuelto todavía, no dice nada de sucursales', () => {
    const { container } = renderTopbar(null);

    expect(container.textContent).not.toMatch(/sucursal/i);
    expect(screen.getByText('Black Dog Store')).toBeInTheDocument();
  });
});
