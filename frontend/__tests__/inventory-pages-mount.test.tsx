/**
 * INVENTORY-UNIFIED-01 · dónde se montan el centro y el panel «Exportar», y con
 * qué permisos.
 *
 * Antes estas dos cosas se «probaban» leyendo el código de las páginas como
 * texto. Aquí se montan las páginas de verdad, con el control interno y el
 * armazón sustituidos, y se mira lo que reciben el centro y el panel: las
 * capacidades de quien mira, tomadas del panel que respondió el servidor.
 */
import { act, render, screen, within } from '@testing-library/react';

import BulkInventoryPage from '@/app/admin/inventory/bulk/page';
import StockImportPage from '@/app/admin/inventory/import/page';
import * as api from '@/app/admin/lib/internal-api';

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/admin/lib/internal-api', () => ({
  ...jest.requireActual('@/app/admin/lib/internal-api'),
  fetchImportHistory: jest.fn(),
}));
jest.mock('@/app/admin/components/AdminShell', () => ({
  AdminShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
jest.mock('@/app/admin/components/BulkInventoryCentre', () => ({
  BulkInventoryCentre: (props: Record<string, unknown>) => <div data-testid="centro" data-props={JSON.stringify(props)} />,
}));
jest.mock('@/app/admin/components/InventoryExportPanel', () => ({
  InventoryExports: (props: Record<string, unknown>) => <div data-testid="exportar" data-props={JSON.stringify(props)} />,
}));

let ctx: Record<string, unknown>;
jest.mock('@/app/admin/components/InternalControlGuard', () => ({
  InternalControlGuard: ({ children }: { children: (value: unknown) => React.ReactNode }) => <>{children(ctx)}</>,
}));

const mocked = api as jest.Mocked<typeof api>;

function member(capabilities: string[], platformAdmin = false) {
  ctx = {
    user: { username: 'ana', role: 'customer' },
    dashboard: { company: { id: 7, name: 'Tienda A' }, access: { capabilities, is_platform_admin: platformAdmin } },
    selectedCompanyId: 7, selectCompany: jest.fn(), reload: jest.fn(),
  };
}

const props = (id: string) => JSON.parse(screen.getByTestId(id).dataset.props!);

beforeEach(() => mocked.fetchImportHistory.mockReset().mockResolvedValue([]));

describe('Gestión masiva de inventario', () => {
  it('pasa al centro la empresa elegida y lo que la persona puede, capacidad por capacidad', async () => {
    member(['products.view', 'inventory.adjust']);
    await act(async () => { render(<BulkInventoryPage />); });

    expect(screen.getByRole('heading', { name: 'Gestión masiva de inventario' })).toBeInTheDocument();
    expect(props('centro')).toEqual({
      companyId: 7,
      can: { productsManage: false, inventoryAdjust: true, productsView: true, inventoryView: false, costsView: false },
    });
  });

  it('sin empresa resuelta no concede nada por el rol antiguo', async () => {
    ctx = { user: { username: 'ana', role: 'admin' }, dashboard: null, selectedCompanyId: null, selectCompany: jest.fn(), reload: jest.fn() };
    await act(async () => { render(<BulkInventoryPage />); });
    expect(props('centro').can).toEqual({ productsManage: false, inventoryAdjust: false, productsView: false, inventoryView: false, costsView: false });
  });
});

describe('Carga de stock', () => {
  it('conserva «Descargar inventario» y, debajo, monta «Exportar» con las capacidades de quien mira', async () => {
    member(['inventory.adjust', 'inventory.view']);
    await act(async () => { render(<StockImportPage />); });

    const legacy = screen.getByRole('heading', { name: 'Descargar inventario' });
    const fresh = screen.getByRole('heading', { name: 'Exportar' });
    expect(legacy.compareDocumentPosition(fresh) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const old = legacy.closest('section')!;
    expect(within(old).getByRole('link', { name: 'Con las cantidades actuales' }).getAttribute('href'))
      .toMatch(/\/admin\/inventory\/export\/\?quantities=current&company=7$/);
    expect(within(old).getByRole('link', { name: 'En blanco, para conteo físico' })).toBeInTheDocument();
    // DC1: ajustar inventario ya no basta para ver el costo; `costs.view` sí.
    expect(props('exportar')).toEqual({ companyId: 7, canCatalogue: false, canInventory: true, canSeeCost: false });
  });

  it('con `costs.view`, «Exportar» trae el costo', async () => {
    member(['inventory.adjust', 'inventory.view', 'costs.view']);
    await act(async () => { render(<StockImportPage />); });
    expect(props('exportar').canSeeCost).toBe(true);
  });

  it('quien sólo puede ajustar, sin poder consultar, no ve «Exportar»', async () => {
    member(['inventory.adjust']);
    await act(async () => { render(<StockImportPage />); });
    expect(screen.getByRole('heading', { name: 'Descargar inventario' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Exportar' })).toBeNull();
    expect(screen.queryByTestId('exportar')).toBeNull();
  });
});
