import type { AuthUser } from '@/app/lib/auth';
import type { InternalDashboard } from '@/app/admin/lib/internal-api';

/**
 * The two shapes every internal screen is built on.
 *
 * Written against the TYPES rather than against a captured response, so the day
 * the contract changes `tsc` says so here instead of a test failing for a
 * reason nobody can read.
 */
export function user(overrides: Partial<AuthUser> = {}): AuthUser {
  return {
    id: 1,
    username: 'recepcion',
    email: 'recepcion@example.invalid',
    first_name: 'Ana',
    last_name: 'Recepción',
    role: 'technician',
    is_staff: true,
    ...overrides,
  };
}

export function dashboard(
  capabilities: string[],
  overrides: Partial<InternalDashboard> = {},
): InternalDashboard {
  return {
    company: { id: 7, name: 'Taller', slug: 'taller', is_active: true },
    membership: { id: 3, branch: { id: 11, name: 'Sucursal Centro' } },
    access: {
      is_platform_admin: false,
      legacy_role: 'technician',
      roles: [{ id: 2, name: 'Servicio Técnico', slug: 'servicio-tecnico', area: null }],
      areas: [],
      capabilities,
      source: 'custom_roles',
    },
    organization: null,
    catalog: null,
    sales: null,
    inventory: null,
    available_companies: [
      { id: 7, name: 'Taller', slug: 'taller', is_active: true },
      { id: 8, name: 'Otra', slug: 'otra', is_active: true },
    ],
    requires_company_selection: false,
    alerts: [],
    ...overrides,
  };
}
