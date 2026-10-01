import { act, fireEvent, render, screen, within } from '@testing-library/react';
import type { InternalContext } from '@/app/admin/components/InternalControlGuard';
import type { BranchScope } from '@/app/admin/lib/internal-api';
import { fetchWithAuth } from '@/app/lib/auth';
import { dashboard, user } from './support/fixtures';

/**
 * DRIFT-07 · UI-SCOPE-01. WHAT (capabilities) and WHERE (branch scope) are both
 * needed for a write, and the backend enforces both (F-BRANCH-01,
 * WRITE-SCOPE-01). These screens used to look only at WHAT, so a member
 * restricted to some branches was offered "Todas", other branches, branch
 * creation, the fulfillment branch, company settings and the company-level
 * numbering — every one of them a guaranteed 403.
 *
 * The scope comes from the server's `branch_scope`, as the fixtures here do.
 * Reading stays as it is: a SELECTED member still SEES the whole roster.
 */

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/admin/components/InternalControlGuard', () => ({
  InternalControlGuard: ({ children }: { children: (ctx: InternalContext) => React.ReactNode }) =>
    children((globalThis as { __ctx?: InternalContext }).__ctx as InternalContext),
}));
jest.mock('@/app/admin/components/AdminShell', () => ({
  AdminShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

const mockStaffBranches = jest.fn();
jest.mock('@/app/lib/staff', () => ({
  fetchStaff: jest.fn(async () => []),
  fetchInvitations: jest.fn(async () => []),
  fetchAreas: jest.fn(async () => []),
  fetchRoles: jest.fn(async () => [{ id: 9, name: 'Técnico' }]),
  fetchBranches: (...a: unknown[]) => mockStaffBranches(...a),
  createInvitation: jest.fn(),
  resendInvitation: jest.fn(),
  revokeInvitation: jest.fn(),
  setStaffActive: jest.fn(),
}));

/* eslint-disable @typescript-eslint/no-require-imports */
const BranchesPage = require('@/app/admin/branches/page').default;
const SettingsPage = require('@/app/admin/settings/page').default;
const UsersPage = require('@/app/admin/users/page').default;
const StaffPage = require('@/app/admin/staff/page').default;
const PromotionsPage = require('@/app/admin/sales/promotions/page').default;
/* eslint-enable @typescript-eslint/no-require-imports */

const CENTRO = { id: 11, name: 'Sucursal Centro' };
const NORTE = { id: 12, name: 'Sucursal Norte' };

const SELECTED: BranchScope = { mode: 'selected', default_branch: CENTRO, branches: [CENTRO] };
const ALL: BranchScope = { mode: 'all', default_branch: CENTRO, branches: [CENTRO, NORTE] };

const branchRow = (b: { id: number; name: string }) => ({
  ...b, company: 7, company_name: 'Taller', address: '', phone: '', email: '', is_active: true,
});

const sequenceRow = (id: number, branch: { id: number; name: string } | null) => ({
  id, company: 7, branch: branch?.id ?? null, branch_name: branch?.name ?? null,
  document_type: 'sales_note', document_type_label: 'Nota de venta', prefix: 'NV-',
  padding: 6, next_value: 1, preview: 'NV-000001', has_issued: false,
  can_edit_next_value: true, is_active: true, updated_at: '2026-09-30T10:00:00Z',
});

function membership(id: number, username: string, mode: 'all' | 'selected', grants: typeof CENTRO[]) {
  return {
    id, username, company: 7, company_name: 'Taller', role_label: 'Personal',
    branch_access_mode: mode, branch_access: grants.map((b) => ({ ...b, is_active: true })),
    is_active: true,
  };
}

function promotion(id: number, name: string, scope: 'all' | 'selected', branches: typeof CENTRO[]) {
  return {
    id, name, promotion_type: 'bundle_fixed_price', promotion_type_label: 'Combo a precio fijo',
    priority: 0, is_active: true, is_live: true, starts_at: null, ends_at: null,
    branch_scope: scope, branches, fixed_price: '100.00', discount_percent: null,
    max_applications_per_order: null, items: [], stats: {},
  };
}

/** `can_manage` is the server's WHAT for company.manage on these screens. */
function mockApi(canManage: boolean) {
  jest.mocked(fetchWithAuth).mockImplementation(async (input) => {
    const path = String(input);
    let body: unknown = { results: [], count: 0 };
    if (path.includes('/admin/branches/')) body = { results: [branchRow(CENTRO), branchRow(NORTE)], count: 2 };
    else if (path.includes('/admin/company-settings/')) body = {
      company: { id: 7, name: 'Taller', legal_name: 'Taller SAC', tax_id: '20123456789', slug: 'taller', is_active: true },
      settings: {}, fulfillment_branch: CENTRO,
      status: { has_settings: true, missing: [], missing_count: 0, consequential: [], is_complete: true },
      can_manage: canManage,
    };
    else if (path.includes('/admin/sequences/')) body = {
      scope: 'branch', can_change_scope: true, can_manage: canManage,
      results: [sequenceRow(1, null), sequenceRow(2, CENTRO)], notice: '',
    };
    else if (path.includes('/admin/memberships/')) body = { results: [
      membership(31, 'propia', 'selected', [CENTRO]),
      membership(32, 'global', 'all', []),
    ] };
    else if (path.includes('/admin/sales/promotions/')) body = {
      can_manage: true, branches: [CENTRO, NORTE],
      results: [
        promotion(41, 'Combo global', 'all', []),
        promotion(42, 'Combo Centro', 'selected', [CENTRO]),
        promotion(43, 'Combo Norte', 'selected', [NORTE]),
      ],
    };
    else if (path.includes('/admin/sales/coupons/')) body = { can_manage: true, results: [] };
    else if (path.includes('/admin/roles/')) body = { results: [] };
    else if (path.includes('/admin/areas/')) body = { results: [] };
    return { ok: true, status: 200, json: async () => body } as Response;
  });
}

function asCaller(scope: BranchScope, caps: string[], canManage = true) {
  (globalThis as { __ctx?: InternalContext }).__ctx = {
    user: user(), dashboard: dashboard(caps, { branch_scope: scope }),
    selectedCompanyId: 7, selectCompany: jest.fn(), reload: jest.fn(),
  };
  mockApi(canManage);
  mockStaffBranches.mockResolvedValue([CENTRO, NORTE]);
}

async function show(Page: React.ComponentType) {
  await act(async () => { render(<Page />); });
}

const COMPANY = ['company.view', 'company.manage'];
const PEOPLE = ['memberships.view', 'memberships.manage'];

function rowOf(name: string) {
  return screen.getByRole('cell', { name }).closest('tr') as HTMLElement;
}

// -- branches ----------------------------------------------------------------

describe('Sucursales', () => {
  it('SELECTED sees the whole roster but only edits its own branch', async () => {
    asCaller(SELECTED, COMPANY);
    await show(BranchesPage);
    expect(await screen.findByRole('cell', { name: 'Sucursal Norte' })).toBeInTheDocument();
    expect(within(rowOf('Sucursal Centro')).getByRole('button', { name: 'Editar' })).toBeEnabled();
    expect(within(rowOf('Sucursal Norte')).getByRole('button', { name: 'Editar' })).toBeDisabled();
  });

  it('SELECTED is not offered branch creation nor the fulfillment branch', async () => {
    asCaller(SELECTED, COMPANY);
    await show(BranchesPage);
    await screen.findByRole('cell', { name: 'Sucursal Norte' });
    expect(screen.queryByRole('button', { name: 'Crear sucursal' })).not.toBeInTheDocument();
    expect(screen.getByLabelText('Sucursal de despacho de la tienda online')).toBeDisabled();
  });

  it('ALL with company.manage keeps every branch action', async () => {
    asCaller(ALL, COMPANY);
    await show(BranchesPage);
    await screen.findByRole('cell', { name: 'Sucursal Norte' });
    expect(screen.getByRole('button', { name: 'Crear sucursal' })).toBeInTheDocument();
    expect(within(rowOf('Sucursal Norte')).getByRole('button', { name: 'Editar' })).toBeEnabled();
    expect(screen.getByLabelText('Sucursal de despacho de la tienda online')).toBeEnabled();
  });

  it('ALL without company.manage still edits nothing', async () => {
    asCaller(ALL, ['company.view'], false);
    await show(BranchesPage);
    await screen.findByRole('cell', { name: 'Sucursal Norte' });
    expect(screen.queryByRole('button', { name: 'Crear sucursal' })).not.toBeInTheDocument();
    expect(within(rowOf('Sucursal Centro')).getByRole('button', { name: 'Editar' })).toBeDisabled();
    expect(screen.getByLabelText('Sucursal de despacho de la tienda online')).toBeDisabled();
  });
});

// -- company settings and numbering ------------------------------------------

describe('Configuración', () => {
  it('SELECTED reads company settings but cannot save them', async () => {
    asCaller(SELECTED, COMPANY);
    await show(SettingsPage);
    await screen.findByText(/Numeración interna/);
    expect(screen.queryByRole('button', { name: 'Guardar configuración' })).not.toBeInTheDocument();
    expect(screen.getByDisplayValue('Taller SAC')).toBeDisabled();
  });

  it('SELECTED edits the series of its branch, not the company series nor the scope', async () => {
    asCaller(SELECTED, COMPANY);
    await show(SettingsPage);
    await screen.findByText('Toda la empresa');
    expect(screen.getAllByRole('button', { name: 'Guardar serie' })).toHaveLength(1);
    for (const radio of screen.getAllByRole('radio')) expect(radio).toBeDisabled();
  });

  it('ALL with company.manage keeps settings, both series and the scope', async () => {
    asCaller(ALL, COMPANY);
    await show(SettingsPage);
    await screen.findByText('Toda la empresa');
    expect(screen.getByRole('button', { name: 'Guardar configuración' })).toBeInTheDocument();
    expect(screen.getByDisplayValue('Taller SAC')).toBeEnabled();
    expect(screen.getAllByRole('button', { name: 'Guardar serie' })).toHaveLength(2);
    expect(screen.getAllByRole('radio').some((r) => !(r as HTMLInputElement).disabled)).toBe(true);
  });
});

// -- branch access of people -------------------------------------------------

describe('Usuarios · alcance por sucursal', () => {
  async function open(username: string) {
    await show(UsersPage);
    await act(async () => { fireEvent.click(await screen.findByText(username)); });
    return screen.getByText(username).closest('article') as HTMLElement;
  }

  it('SELECTED is not offered "Todas" nor branches it does not reach', async () => {
    asCaller(SELECTED, PEOPLE);
    const card = await open('propia');
    expect(within(card).queryByRole('button', { name: 'Todas' })).not.toBeInTheDocument();
    expect(within(card).getByLabelText('Sucursal Centro')).toBeInTheDocument();
    expect(within(card).queryByLabelText('Sucursal Norte')).not.toBeInTheDocument();
    expect(within(card).getByRole('button', { name: 'Guardar sucursales' })).toBeInTheDocument();
  });

  it('SELECTED cannot change the scope of someone who reaches further', async () => {
    asCaller(SELECTED, PEOPLE);
    const card = await open('global');
    expect(within(card).queryByRole('button', { name: 'Guardar sucursales' })).not.toBeInTheDocument();
  });

  it('ALL keeps "Todas" and every branch', async () => {
    asCaller(ALL, PEOPLE);
    const card = await open('propia');
    expect(within(card).getByRole('button', { name: 'Todas' })).toBeInTheDocument();
    expect(within(card).getByLabelText('Sucursal Norte')).toBeInTheDocument();
  });
});

// -- staff invitations -------------------------------------------------------

describe('Personal · invitación', () => {
  async function openForm() {
    await show(StaffPage);
    await act(async () => {
      fireEvent.click(await screen.findByRole('button', { name: 'Añadir trabajador' }));
    });
  }

  it('SELECTED invites only into branches it reaches, never to all of them', async () => {
    asCaller(SELECTED, PEOPLE);
    await openForm();
    expect(screen.queryByLabelText('Todas las sucursales')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Sucursales seleccionadas')).toBeChecked();
    expect(screen.getByLabelText('Sucursal Centro')).toBeInTheDocument();
    expect(screen.queryByLabelText('Sucursal Norte')).not.toBeInTheDocument();
  });

  it('ALL keeps "Todas las sucursales" and every branch', async () => {
    asCaller(ALL, PEOPLE);
    await openForm();
    expect(screen.getByLabelText('Todas las sucursales')).toBeChecked();
    fireEvent.click(screen.getByLabelText('Sucursales seleccionadas'));
    expect(screen.getByLabelText('Sucursal Norte')).toBeInTheDocument();
  });
});

// -- promotions (F-BRANCH-02) --------------------------------------------------

describe('Promociones', () => {
  const SALES = ['sales.promotions.view', 'sales.promotions.manage'];

  it('SELECTED archives only promotions that fire inside its reach', async () => {
    asCaller(SELECTED, SALES);
    await show(PromotionsPage);
    await screen.findByText('Combo Norte');
    expect(screen.getAllByRole('button', { name: 'Archivar' })).toHaveLength(1);
  });

  it('SELECTED is told a new combo applies only in its branches', async () => {
    asCaller(SELECTED, SALES);
    await show(PromotionsPage);
    await act(async () => {
      fireEvent.click(await screen.findByRole('button', { name: 'Nuevo combo' }));
    });
    expect(screen.getByText(/Se aplicará solo en tus sucursales: Sucursal Centro/)).toBeInTheDocument();
  });

  it('ALL archives any promotion and is not told about a limit', async () => {
    asCaller(ALL, SALES);
    await show(PromotionsPage);
    await screen.findByText('Combo Norte');
    expect(screen.getAllByRole('button', { name: 'Archivar' })).toHaveLength(3);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Nuevo combo' }));
    });
    expect(screen.queryByText(/Se aplicará solo en tus sucursales/)).not.toBeInTheDocument();
  });
});
