/**
 * INV-COUNT-UX-04 · abrir un recuento y llegar a contar, sin adivinar.
 *
 * El propietario «no puede hacer recuentos»: al pulsar «Abrir recuento» el formulario se vaciaba y el recuento nuevo
 * (un borrador) podía no verse en el historial —filtrado por la sucursal de arriba o por un estado sin «Borrador»—.
 * Aquí: se abre su pantalla de conteo; la sucursal se dice y viaja siempre explícita; quien no puede contar sabe por
 * qué; cada fila del historial tiene su acción; un borrador se puede anular.
 */
import { act, fireEvent, render, screen, within } from '@testing-library/react';

import CountsPage from '@/app/admin/inventory/counts/page';
import CountDetailPage from '@/app/admin/inventory/counts/[id]/page';
import * as inventory from '@/app/lib/inventory';
import * as api from '@/app/admin/lib/counts-api';
import type { BranchAccessInfo, InventoryCount } from '@/app/lib/inventory';

const mockRouter = { push: jest.fn(), replace: jest.fn(), refresh: jest.fn(), back: jest.fn(), forward: jest.fn(), prefetch: jest.fn() };
jest.mock('next/navigation', () => ({ useRouter: () => mockRouter, useParams: () => ({ id: '9' }), usePathname: () => '/admin/inventory/counts' }));
jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/lib/admin', () => ({ fetchAdminProducts: jest.fn() }));
jest.mock('@/app/lib/inventory', () => ({
  ...jest.requireActual('@/app/lib/inventory'),
  fetchInventoryBranches: jest.fn(),
  fetchCounts: jest.fn(),
  createCount: jest.fn(),
  cancelCount: jest.fn(),
  approveCount: jest.fn(),
}));
jest.mock('@/app/admin/lib/counts-api', () => ({
  ...jest.requireActual('@/app/admin/lib/counts-api'),
  fetchCountDetail: jest.fn(),
}));
jest.mock('@/app/admin/components/AdminShell', () => ({
  AdminShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
let capabilities: string[] = [];
jest.mock('@/app/admin/components/AccessGuard', () => ({
  AccessGuard: ({ children }: { children: (a: unknown) => React.ReactNode }) => (
    <>{children({
      user: { id: 1, username: 'ana', role: 'customer' }, dashboard: null, hasCompanyContext: true,
      isPlatformAdmin: false, isLegacyBridge: false, can: (cap: string) => capabilities.includes(cap),
    })}</>
  ),
}));

const inv = inventory as jest.Mocked<typeof inventory>;
const counts = api as jest.Mocked<typeof api>;

const CENTRO = { id: 11, name: 'Sucursal Centro' };
const NORTE = { id: 12, name: 'Sucursal Norte' };
function access(over: Partial<BranchAccessInfo> = {}): BranchAccessInfo {
  return {
    results: [CENTRO, NORTE], default_branch: CENTRO, allows_aggregate: true, mode: 'all', ...over,
  } as unknown as BranchAccessInfo;
}
function count(over: Partial<InventoryCount> = {}): InventoryCount {
  return {
    id: 30, company: 7, branch: 11, branch_name: 'Sucursal Centro', status: 'draft', status_label: 'Borrador', reason: '',
    items: [], counted_items: 0, created_by: 1, created_by_username: 'ana', created_at: '2026-11-04T10:00:00Z',
    approved_at: null, cancelled_at: null, updated_at: '2026-11-04T10:00:00Z', ...over,
  } as unknown as InventoryCount;
}

async function mountList() {
  await act(async () => { render(<CountsPage />); });
}

beforeEach(() => {
  capabilities = ['inventory.view', 'inventory.adjust', 'inventory.counts.approve'];
  mockRouter.push.mockReset();
  inv.fetchInventoryBranches.mockReset().mockResolvedValue(access());
  inv.fetchCounts.mockReset().mockResolvedValue({ results: [], scope: null, count: 0 } as never);
  inv.createCount.mockReset().mockResolvedValue(count({ id: 31 }));
});

describe('abrir un recuento', () => {
  it('al crearlo se abre su pantalla de conteo, con la sucursal explícita', async () => {
    await mountList();
    const panel = screen.getByRole('region', { name: 'Nuevo recuento' });
    // Con varias sucursales y la vista en «todas», se propone la sucursal por defecto, dicha por su nombre.
    expect(within(panel).getByLabelText('Sucursal que se cuenta')).toHaveValue('11');
    expect(within(panel).queryByText('Mi sucursal por defecto')).not.toBeInTheDocument();
    fireEvent.change(within(panel).getByLabelText('Motivo'), { target: { value: 'Inventario mensual' } });
    await act(async () => { fireEvent.click(within(panel).getByRole('button', { name: 'Abrir recuento' })); });
    expect(inv.createCount).toHaveBeenCalledWith({ branch: 11, reason: 'Inventario mensual', include_stocked: true });
    expect(mockRouter.push).toHaveBeenCalledWith('/admin/inventory/counts/31');
  });

  it('la sucursal elegida arriba es la que se propone; nunca viaja una sucursal vacía', async () => {
    await mountList();
    const panel = screen.getByRole('region', { name: 'Nuevo recuento' });
    fireEvent.click(screen.getByRole('button', { name: /Todas las sucursales/ }));
    await act(async () => { fireEvent.click(within(screen.getByRole('listbox')).getByRole('option', { name: 'Sucursal Norte' })); });
    expect(within(panel).getByLabelText('Sucursal que se cuenta')).toHaveValue('12');
    await act(async () => { fireEvent.click(within(panel).getByRole('button', { name: 'Abrir recuento' })); });
    expect(inv.createCount.mock.calls[0][0].branch).toBe(12);
  });

  it('se puede abrir vacío, sin listar lo que hay en la sucursal', async () => {
    await mountList();
    fireEvent.click(screen.getByLabelText(/Listar los productos con stock en esta sucursal/));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Abrir recuento' })); });
    expect(inv.createCount.mock.calls[0][0].include_stocked).toBe(false);
  });

  it('sin una sucursal por defecto y con varias, hay que elegir antes de abrir', async () => {
    inv.fetchInventoryBranches.mockResolvedValue(access({ default_branch: null, allows_aggregate: true }));
    await mountList();
    const panel = screen.getByRole('region', { name: 'Nuevo recuento' });
    expect(within(panel).getByLabelText('Sucursal que se cuenta')).toHaveValue('');
    await act(async () => { fireEvent.click(within(panel).getByRole('button', { name: 'Abrir recuento' })); });
    expect(inv.createCount).not.toHaveBeenCalled();
    expect(within(panel).getByRole('alert')).toHaveTextContent('Elige la sucursal que vas a contar.');
  });

  it('un fallo al abrir se dice y no navega', async () => {
    inv.createCount.mockRejectedValue(new Error('Sucursal no encontrada o sin acceso.'));
    await mountList();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Abrir recuento' })); });
    expect(screen.getByText('Sucursal no encontrada o sin acceso.')).toBeInTheDocument();
    expect(mockRouter.push).not.toHaveBeenCalled();
  });

  it('quien sólo ve inventario sabe por qué no puede abrir uno', async () => {
    capabilities = ['inventory.view'];
    await mountList();
    expect(screen.queryByRole('button', { name: 'Abrir recuento' })).not.toBeInTheDocument();
    expect(screen.getByRole('note')).toHaveTextContent(/permiso «Mover inventario»/);
  });

  it('sin sucursales asignadas lo dice, en lugar de una pantalla vacía', async () => {
    inv.fetchInventoryBranches.mockResolvedValue(access({ results: [], default_branch: null, allows_aggregate: false }));
    await mountList();
    expect(screen.getByText(/No tienes ninguna sucursal asignada/)).toBeInTheDocument();
  });
});

describe('revisión independiente de INV-COUNT-UX-04', () => {
  it('si la respuesta no trae el número del recuento, lo dice y no navega a una dirección rota', async () => {
    inv.createCount.mockResolvedValue({} as never);
    await mountList();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Abrir recuento' })); });
    expect(mockRouter.push).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent(/no llegó su número/);
    expect(screen.getByRole('button', { name: 'Abrir recuento' })).toBeEnabled();
  });

  it('con una sola sucursal se nombra (no un control desactivado) y viaja explícita', async () => {
    inv.fetchInventoryBranches.mockResolvedValue(access({ results: [CENTRO], allows_aggregate: false }));
    await mountList();
    const panel = screen.getByRole('region', { name: 'Nuevo recuento' });
    expect(within(panel).queryByRole('combobox')).not.toBeInTheDocument();
    expect(within(panel).getByText('Sucursal Centro')).toBeInTheDocument();
    await act(async () => { fireEvent.click(within(panel).getByRole('button', { name: 'Abrir recuento' })); });
    expect(inv.createCount.mock.calls[0][0].branch).toBe(11);
  });

  it('un recuento cerrado no alarma por lo no contado: lo llama «no contados» y muestra los primeros', async () => {
    const blank = (n: number) => ({
      id: 500 + n, product: 600 + n, product_name: `Accesorio ${n}`, product_slug: `a-${n}`, theoretical_at_start: null,
      physical_quantity: null, theoretical_at_approval: null, difference: null, is_counted: false, note: '', updated_at: '2026-11-04T10:00:00Z',
    });
    counts.fetchCountDetail.mockResolvedValue(count({
      id: 9, status: 'approved', status_label: 'Aprobado', approved_at: '2026-11-04T12:00:00Z',
      items: Array.from({ length: 45 }, (_, n) => blank(n)),
    } as Partial<InventoryCount>) as never);
    await act(async () => { render(<CountDetailPage />); });
    expect(await screen.findByRole('heading', { name: 'No contados (45): no se tocaron' })).toBeInTheDocument();
    const section = screen.getByRole('region', { name: 'No contados (45): no se tocaron' });
    expect(within(section).getByText('Accesorio 29')).toBeInTheDocument();
    expect(within(section).queryByText('Accesorio 30')).not.toBeInTheDocument();
    fireEvent.click(within(section).getByRole('button', { name: 'Mostrar los 15 restantes' }));
    expect(within(section).getByText('Accesorio 44')).toBeInTheDocument();
  });
});

describe('historial', () => {
  it('cada recuento abierto tiene «Continuar»; uno cerrado, «Ver»; y se puede filtrar «Borrador»', async () => {
    inv.fetchCounts.mockResolvedValue({
      results: [count({ id: 30, status: 'draft' }), count({ id: 29, status: 'counting', status_label: 'En conteo' }),
        count({ id: 28, status: 'approved', status_label: 'Aprobado', approved_at: '2026-11-03T10:00:00Z' })],
      scope: null, count: 3,
    } as never);
    await mountList();
    const table = screen.getByRole('table');
    expect(within(table).getByRole('link', { name: 'Continuar el recuento #30' })).toHaveAttribute('href', '/admin/inventory/counts/30');
    expect(within(table).getByRole('link', { name: 'Continuar el recuento #29' })).toBeInTheDocument();
    expect(within(table).getByRole('link', { name: 'Ver el recuento #28' })).toBeInTheDocument();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Borrador' })); });
    expect(inv.fetchCounts).toHaveBeenLastCalledWith(expect.objectContaining({ status: 'draft' }));
  });
});

describe('pantalla del recuento', () => {
  it('un borrador se puede anular', async () => {
    counts.fetchCountDetail.mockResolvedValue(count({ id: 9, status: 'draft', items: [] }) as never);
    inv.cancelCount.mockResolvedValue(count({ id: 9, status: 'cancelled' }) as never);
    jest.spyOn(window, 'confirm').mockReturnValue(true);
    await act(async () => { render(<CountDetailPage />); });
    await act(async () => { fireEvent.click(await screen.findByRole('button', { name: 'Anular recuento' })); });
    expect(inv.cancelCount).toHaveBeenCalledWith(9);
  });
});
