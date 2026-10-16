/**
 * COMMERCE-ADMIN-UX-01 · A1 — «un Administrador no encuentra "Margen comercial"
 * ni "Valor al costo"».
 *
 * EL DIAGNÓSTICO, fijado aquí. `buildInternalAccess` responde bien a quien tiene
 * `costs.view` (rol propio «administrador», master con empresa); a quien no lo
 * tiene le dice que no. Lo que escondía los costos era otra cosa: no había
 * ninguna entrada en el menú, y la sección del margen devolvía `null` mientras
 * cargaba, ante un error y sin datos. Y el master SIN empresa pasaba el permiso
 * (es master) para luego recibir un 403 que nadie le explicaba.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import CostsPage from '@/app/admin/inventory/costs/page';
import { SalesMarginSection } from '@/app/admin/components/SalesMarginSection';
import { buildInternalAccess } from '@/app/admin/lib/internal-access';
import { navigableModules, type ModuleAccessContext } from '@/app/admin/lib/internal-modules';
import * as api from '@/app/admin/lib/internal-api';
import * as auth from '@/app/lib/auth';
import * as inventory from '@/app/lib/inventory';
import * as stockUnits from '@/app/lib/stock-units';
import * as admin from '@/app/lib/admin';

jest.mock('@/app/lib/auth', () => ({
  ...jest.requireActual('@/app/lib/auth'),
  fetchWithAuth: jest.fn(),
  getCurrentUser: jest.fn(),
}));
jest.mock('@/app/admin/lib/internal-api', () => ({
  ...jest.requireActual('@/app/admin/lib/internal-api'),
  fetchInternalDashboard: jest.fn(),
  fetchSalesMargin: jest.fn(),
}));
jest.mock('@/app/lib/inventory', () => ({
  ...jest.requireActual('@/app/lib/inventory'),
  fetchInventoryBranches: jest.fn(),
  fetchInventorySummary: jest.fn(),
  fetchBranchStock: jest.fn(),
}));
jest.mock('@/app/lib/stock-units', () => ({
  ...jest.requireActual('@/app/lib/stock-units'),
  fetchStockUnits: jest.fn(),
  correctStockUnitCost: jest.fn(),
}));
jest.mock('@/app/lib/admin', () => ({
  ...jest.requireActual('@/app/lib/admin'),
  patchAdminProduct: jest.fn(),
}));
jest.mock('@/app/admin/components/AdminShell', () => ({
  AdminShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

const mApi = api as jest.Mocked<typeof api>;
const mAuth = auth as jest.Mocked<typeof auth>;
const mInv = inventory as jest.Mocked<typeof inventory>;
const mUnits = stockUnits as jest.Mocked<typeof stockUnits>;
const mAdmin = admin as jest.Mocked<typeof admin>;

const ADMIN_CAPS = ['company.view', 'products.view', 'products.manage', 'inventory.view', 'inventory.adjust',
  'sales.analytics.view', 'costs.view'];
const VSTI_CAPS = ['company.view', 'products.view', 'inventory.view', 'inventory.adjust', 'inventory.reports',
  'sales.orders.view', 'sales.pos.use', 'service.orders.view'];

const USER = { id: 1, username: 'ana', email: 'ana@example.com', role: 'customer' } as unknown as auth.AuthUser;

function dashboard(capabilities: string[], { platform = false, company = true } = {}) {
  return {
    company: company ? { id: 7, name: 'Tienda A1', slug: 'a1' } : null,
    membership: null,
    branch_scope: null,
    access: { is_platform_admin: platform, legacy_role: null, roles: [], areas: [], capabilities },
    organization: null, catalog: null, sales: null, inventory: null,
    available_companies: [], requires_company_selection: !company, alerts: [],
  } as unknown as api.InternalDashboard;
}

function menu(ctx: Partial<ModuleAccessContext>) {
  return navigableModules({
    capabilities: [], legacyRole: null, isPlatformAdmin: false, hasCompanyContext: true, ...ctx,
  }).map((m) => m.id);
}

const MARGIN: api.SalesMargin = {
  window_days: 30,
  sales: { orders: 1, gross: '118.00', net: '100.00' },
  collected: { orders: 1, amount: '118.00' },
  net_sales_with_cost: '100.00',
  cost_of_goods_sold: '60.00',
  margin: '40.00',
  margin_percent: '40.00',
  pending_cost: { lines: 0, orders: 0, amount: '0.00' },
  refunds: { orders: 0, amount: '0.00', net: '0.00' },
  estimated: { lines: 1, net_sales: '100.00', cost: '60.00', margin: '40.00', margin_percent: '40.00' },
  realised: { lines: 1, net_sales: '100.00', cost: '60.00', margin: '40.00', margin_percent: '40.00' },
  estimated_only: { lines: 0, net_sales: '0.00', reference_cost_lines: 0, on_order_not_received_lines: 0 },
  unknown_cost: { lines: 0, orders: 0, net_sales: '0.00' },
  by_product: [], by_branch: [], by_channel: [],
};

const EMPTY_MARGIN: api.SalesMargin = {
  ...MARGIN,
  sales: { orders: 0, gross: '0.00', net: '0.00' },
  estimated: { lines: 0, net_sales: '0.00', cost: '0.00', margin: '0.00', margin_percent: null },
  realised: { lines: 0, net_sales: '0.00', cost: '0.00', margin: '0.00', margin_percent: null },
};

const SCOPE = { branch: null, branches: [{ id: 1, name: 'Centro' }], is_aggregate: true };

function stockRow(extra: Partial<inventory.BranchStockRow>): inventory.BranchStockRow {
  return {
    id: 1, branch: 1, branch_name: 'Centro', product: 10, product_name: 'Funda', product_slug: 'funda',
    product_price: '50.00', product_is_active: true, category_name: null, quantity: 4,
    minimum_stock: 0, target_stock: 0, needs_replenishment: false, suggested_quantity: 0,
    updated_at: '2026-10-15T00:00:00Z',
    average_cost: '10.0000', uncosted_quantity: 0, product_reference_cost: null,
    product_is_serialized: false, cost_source: 'average',
    ...extra,
  };
}

function unit(extra: Partial<stockUnits.StockUnit>): stockUnits.StockUnit {
  return {
    id: 50, product_id: 11, product_name: 'iPhone', branch_id: 1, branch_name: 'Centro',
    serial_number: 'SER-1', imei: null, imei2: null, condition: 'new', condition_label: 'Nuevo',
    status: 'available', status_label: 'Disponible', cost: '3000.00', received_at: '2026-10-01T00:00:00Z',
    sold_at: null, order_id: null, notes: '',
    ...extra,
  };
}

function wireData() {
  mInv.fetchInventoryBranches.mockResolvedValue({
    company: { id: 7, name: 'Tienda A1' }, results: [{ id: 1, name: 'Centro' }], count: 1,
    default_branch: { id: 1, name: 'Centro' }, access_mode: 'all', allows_aggregate: false,
  } as unknown as inventory.BranchAccessInfo);
  mInv.fetchInventorySummary.mockResolvedValue({
    total_products: 2, active_products: 2, out_of_stock_count: 0, low_stock_count: 0, stocked_count: 2,
    total_units: 7, inventory_value: '260.00', inventory_value_basis: 'sale_price',
    inventory_cost_value: '3040.00', inventory_cost_pending_units: 3,
    inventory_cost_basis: 'average_and_unit_cost', low_stock_threshold: 5, best_selling_product: null,
    scope: SCOPE,
  });
  mInv.fetchBranchStock.mockResolvedValue({
    results: [
      stockRow({}),
      stockRow({ id: 2, product: 12, product_name: 'Cable', average_cost: null, uncosted_quantity: 3,
        product_reference_cost: '5.00', cost_source: 'reference', quantity: 3 }),
    ],
    count: 2, page: 1, page_size: 50, scope: SCOPE,
  } as unknown as Awaited<ReturnType<typeof inventory.fetchBranchStock>>);
  mUnits.fetchStockUnits.mockResolvedValue({
    results: [unit({}), unit({ id: 51, serial_number: 'SER-2', cost: null }),
      unit({ id: 52, serial_number: 'SER-3', status: 'sold', status_label: 'Vendido' })],
    count: 3, page: 1, page_size: 50, statuses: [], conditions: [],
  });
  mApi.fetchSalesMargin.mockResolvedValue(MARGIN);
}

async function openPage(dash: api.InternalDashboard | null, err?: Error) {
  mAuth.getCurrentUser.mockResolvedValue(USER);
  if (err) mApi.fetchInternalDashboard.mockRejectedValue(err);
  else mApi.fetchInternalDashboard.mockResolvedValue(dash as api.InternalDashboard);
  await act(async () => { render(<CostsPage />); });
}

beforeEach(() => {
  jest.clearAllMocks();
  wireData();
});

describe('A1 · quién puede ver costos (buildInternalAccess)', () => {
  it('el rol propio «administrador» lo trae del servidor y pasa', () => {
    expect(buildInternalAccess(USER, dashboard(ADMIN_CAPS)).can('costs.view', ['admin', 'superadmin'])).toBe(true);
  });
  it('«venta-servicio-tecnico-inventario» no lo tiene y no pasa, diga lo que diga su rol global', () => {
    const admin = { ...USER, role: 'admin' } as auth.AuthUser;
    expect(buildInternalAccess(admin, dashboard(VSTI_CAPS)).can('costs.view', ['admin', 'superadmin'])).toBe(false);
  });
  it('el master con empresa pasa', () => {
    const access = buildInternalAccess(USER, dashboard([], { platform: true }));
    expect(access.can('costs.view')).toBe(true);
    expect(access.hasCompanyContext).toBe(true);
  });
  it('el master SIN empresa «pasa» pero no tiene empresa: la pantalla debe pedirle que elija', () => {
    const access = buildInternalAccess(USER, dashboard([], { platform: true, company: false }));
    expect(access.can('costs.view')).toBe(true);
    expect(access.hasCompanyContext).toBe(false);
  });
  it('el puente legacy pasa sólo si el servidor lo afirma', () => {
    const admin = { ...USER, role: 'admin' } as auth.AuthUser;
    expect(buildInternalAccess(admin, null, true).can('costs.view', ['admin', 'superadmin'])).toBe(true);
    expect(buildInternalAccess(admin, null, false).can('costs.view', ['admin', 'superadmin'])).toBe(false);
  });
});

describe('A1 · la entrada «Costos y precios» del menú', () => {
  it('aparece bajo Inventario para quien tiene costs.view', () => {
    expect(menu({ capabilities: ADMIN_CAPS })).toContain('inventory.costs');
  });
  it('no aparece para venta-servicio-tecnico-inventario', () => {
    expect(menu({ capabilities: VSTI_CAPS, legacyRole: 'admin' })).not.toContain('inventory.costs');
  });
  it('aparece para el master y para el admin del puente legacy', () => {
    expect(menu({ isPlatformAdmin: true })).toContain('inventory.costs');
    expect(menu({ hasCompanyContext: false, legacyRole: 'admin' })).toContain('inventory.costs');
    expect(menu({ hasCompanyContext: false, legacyRole: 'inventory' })).not.toContain('inventory.costs');
  });
});

describe('A1 · el margen nunca desaparece en silencio', () => {
  it('mientras carga dice «Cargando…»', async () => {
    mApi.fetchSalesMargin.mockReturnValue(new Promise(() => {}));
    await act(async () => { render(<SalesMarginSection companyId={7} branchId={null} canSeeCosts />); });
    const region = screen.getByRole('region', { name: 'Margen comercial' });
    expect(region).toHaveTextContent('Cargando…');
  });
  it('un error enseña el mensaje del servidor', async () => {
    mApi.fetchSalesMargin.mockRejectedValue(new Error('Se requiere permiso para ver costos y márgenes.'));
    await act(async () => { render(<SalesMarginSection companyId={7} branchId={null} canSeeCosts />); });
    const region = screen.getByRole('region', { name: 'Margen comercial' });
    expect(region).toHaveTextContent('Se requiere permiso para ver costos y márgenes.');
  });
  it('sin ventas con costo lo dice', async () => {
    mApi.fetchSalesMargin.mockResolvedValue(EMPTY_MARGIN);
    await act(async () => { render(<SalesMarginSection companyId={7} branchId={null} canSeeCosts />); });
    expect(screen.getByRole('region', { name: 'Margen comercial' }))
      .toHaveTextContent('Aún no hay ventas con costo conocido');
  });
  it('sin el permiso explica por qué no está, y no lo pide', async () => {
    await act(async () => { render(<SalesMarginSection companyId={7} branchId={null} canSeeCosts={false} />); });
    expect(screen.queryByRole('region', { name: 'Margen comercial' })).toBeNull();
    expect(screen.getByText(/Necesitas el permiso Ver costos y márgenes; pídeselo al administrador/)).toBeInTheDocument();
    expect(mApi.fetchSalesMargin).not.toHaveBeenCalled();
  });
  it('desde Ventas enlaza a «Costos y precios»', async () => {
    await act(async () => {
      render(<SalesMarginSection companyId={7} branchId={null} canSeeCosts costsHref="/admin/inventory/costs" />);
    });
    expect(screen.getByRole('link', { name: /Costos y precios/ })).toHaveAttribute('href', '/admin/inventory/costs');
  });
});

describe('A1 · la pantalla «Costos y precios»', () => {
  it('venta-servicio-tecnico-inventario recibe la página 403 con el permiso que le falta', async () => {
    await openPage(dashboard(VSTI_CAPS));
    expect(screen.getByText('Necesitas el permiso Ver costos y márgenes; pídeselo al administrador.')).toBeInTheDocument();
    expect(mApi.fetchSalesMargin).not.toHaveBeenCalled();
    expect(mInv.fetchBranchStock).not.toHaveBeenCalled();
  });

  it('el master sin empresa ve que debe elegir una, y no se pide nada', async () => {
    await openPage(dashboard([], { platform: true, company: false }));
    expect(screen.getByText(/Elige una empresa/)).toBeInTheDocument();
    expect(mApi.fetchSalesMargin).not.toHaveBeenCalled();
    expect(mInv.fetchBranchStock).not.toHaveBeenCalled();
  });

  it('el rol «administrador» ve margen, inventario al costo, promedio por sucursal y equipos, con su origen', async () => {
    await openPage(dashboard(ADMIN_CAPS));
    expect(screen.getByRole('heading', { name: 'Costos y precios' })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('region', { name: 'Margen comercial' })).toHaveTextContent('40.00 %'));

    const atCost = screen.getByRole('region', { name: 'Inventario al costo' });
    expect(atCost).toHaveTextContent('3,040.00');
    expect(atCost).toHaveTextContent('3 unidades sin costo');

    const branch = screen.getByRole('region', { name: 'Costo promedio por sucursal' });
    const funda = within(branch).getByText('Funda').closest('tr') as HTMLElement;
    expect(funda).toHaveTextContent('10.00');
    expect(funda).toHaveTextContent('Promedio de la sucursal');
    const cable = within(branch).getByText('Cable').closest('tr') as HTMLElement;
    expect(cable).toHaveTextContent('Costo de referencia');
    expect(cable).toHaveTextContent('3 sin costo');

    const devices = screen.getByRole('region', { name: 'Costo de cada equipo' });
    expect(within(devices).getByText('SER-1').closest('tr')).toHaveTextContent('Costo específico del equipo');
    expect(within(devices).getByText('SER-2').closest('tr')).toHaveTextContent('Costo pendiente');
    const sold = within(devices).getByText('SER-3').closest('tr') as HTMLElement;
    expect(within(sold).queryByRole('button', { name: /Corregir costo/ })).toBeNull();
  });

  it('el master con empresa también la ve', async () => {
    await openPage(dashboard([], { platform: true }));
    expect(screen.getByRole('heading', { name: 'Costos y precios' })).toBeInTheDocument();
    await waitFor(() => expect(mApi.fetchSalesMargin).toHaveBeenCalledWith(7, 1));
  });

  it('el admin del puente legacy entra (el servidor lo afirma)', async () => {
    mAuth.getCurrentUser.mockResolvedValue({ ...USER, role: 'admin' } as auth.AuthUser);
    mApi.fetchInternalDashboard.mockRejectedValue(new api.NoInternalAccessError('Sin empresa.', true));
    await act(async () => { render(<CostsPage />); });
    expect(screen.getByRole('heading', { name: 'Costos y precios' })).toBeInTheDocument();
  });

  it('cada sección enseña su error en vez de desaparecer', async () => {
    mInv.fetchBranchStock.mockRejectedValue(new Error('No tienes permisos para ver el inventario.'));
    mInv.fetchInventorySummary.mockRejectedValue(new Error('Fallo del resumen.'));
    await openPage(dashboard(ADMIN_CAPS));
    await waitFor(() => expect(screen.getByRole('region', { name: 'Costo promedio por sucursal' }))
      .toHaveTextContent('No tienes permisos para ver el inventario.'));
    expect(screen.getByRole('region', { name: 'Inventario al costo' })).toHaveTextContent('Fallo del resumen.');
  });

  it('«Corregir costo» exige un motivo y lo envía', async () => {
    mUnits.correctStockUnitCost.mockResolvedValue(unit({ id: 51, serial_number: 'SER-2', cost: '2900.00' }));
    await openPage(dashboard(ADMIN_CAPS));
    const devices = await screen.findByRole('region', { name: 'Costo de cada equipo' });
    const row = (await within(devices).findByText('SER-2')).closest('tr') as HTMLElement;
    fireEvent.click(within(row).getByRole('button', { name: /Corregir costo/ }));
    fireEvent.change(screen.getByLabelText('Nuevo costo'), { target: { value: '2900.00' } });
    fireEvent.click(screen.getByRole('button', { name: 'Guardar corrección' }));
    expect(screen.getByText('Indica el motivo de la corrección.')).toBeInTheDocument();
    expect(mUnits.correctStockUnitCost).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('Motivo'), { target: { value: 'Factura del proveedor' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Guardar corrección' })); });
    expect(mUnits.correctStockUnitCost).toHaveBeenCalledWith(51, '2900.00', 'Factura del proveedor');
  });

  it('el precio y el costo de referencia se editan por el producto', async () => {
    mAdmin.patchAdminProduct.mockResolvedValue({} as admin.AdminProduct);
    await openPage(dashboard(ADMIN_CAPS));
    const branch = await screen.findByRole('region', { name: 'Costo promedio por sucursal' });
    const cable = (await within(branch).findByText('Cable')).closest('tr') as HTMLElement;
    fireEvent.click(within(cable).getByRole('button', { name: /Editar precio y costo/ }));
    fireEvent.change(screen.getByLabelText('Costo de referencia'), { target: { value: '6.50' } });
    fireEvent.change(screen.getByLabelText('Precio de venta'), { target: { value: '22.00' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Guardar producto' })); });
    expect(mAdmin.patchAdminProduct).toHaveBeenCalledWith(12, { price: '22.00', reference_cost: '6.50' });
  });

  it('sólo envía lo que se cambió: un precio viejo no pisa un cambio de otra persona', async () => {
    mAdmin.patchAdminProduct.mockResolvedValue({} as admin.AdminProduct);
    await openPage(dashboard(ADMIN_CAPS));
    const branch = await screen.findByRole('region', { name: 'Costo promedio por sucursal' });
    const cable = (await within(branch).findByText('Cable')).closest('tr') as HTMLElement;
    fireEvent.click(within(cable).getByRole('button', { name: /Editar precio y costo/ }));
    fireEvent.change(screen.getByLabelText('Costo de referencia'), { target: { value: '6.50' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Guardar producto' })); });
    expect(mAdmin.patchAdminProduct).toHaveBeenCalledWith(12, { reference_cost: '6.50' });

    const again = (await within(branch).findByText('Cable')).closest('tr') as HTMLElement;
    fireEvent.click(within(again).getByRole('button', { name: /Editar precio y costo/ }));
    fireEvent.change(screen.getByLabelText('Precio de venta'), { target: { value: '21.00' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Guardar producto' })); });
    expect(mAdmin.patchAdminProduct).toHaveBeenLastCalledWith(12, { price: '21.00' });
  });

  it('sin cambios no se envía nada', async () => {
    await openPage(dashboard(ADMIN_CAPS));
    const branch = await screen.findByRole('region', { name: 'Costo promedio por sucursal' });
    const cable = (await within(branch).findByText('Cable')).closest('tr') as HTMLElement;
    fireEvent.click(within(cable).getByRole('button', { name: /Editar precio y costo/ }));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Guardar producto' })); });
    expect(mAdmin.patchAdminProduct).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: 'Guardar producto' })).toBeNull();
  });

  it('el motivo avisa que no lleve montos: el historial lo ven personas sin permiso de costos', async () => {
    await openPage(dashboard(ADMIN_CAPS));
    const devices = await screen.findByRole('region', { name: 'Costo de cada equipo' });
    const row = (await within(devices).findByText('SER-2')).closest('tr') as HTMLElement;
    fireEvent.click(within(row).getByRole('button', { name: /Corregir costo/ }));
    const hint = screen.getByText('No escribas montos en el motivo: el historial lo ven personas sin permiso de costos');
    expect(screen.getByLabelText('Motivo')).toHaveAttribute('aria-describedby', hint.id);
  });

  it('un equipo dado de baja no ofrece «Corregir costo»', async () => {
    mUnits.fetchStockUnits.mockResolvedValue({
      results: [unit({ id: 53, serial_number: 'SER-4', status: 'written_off', status_label: 'Dado de baja' })],
      count: 1, page: 1, page_size: 50, statuses: [], conditions: [],
    });
    await openPage(dashboard(ADMIN_CAPS));
    const devices = await screen.findByRole('region', { name: 'Costo de cada equipo' });
    const row = (await within(devices).findByText('SER-4')).closest('tr') as HTMLElement;
    expect(within(row).queryByRole('button', { name: /Corregir costo/ })).toBeNull();
    expect(row).toHaveTextContent('Dado de baja: conserva su costo');
  });

  it('sin products.manage no se ofrece editar el precio', async () => {
    await openPage(dashboard(ADMIN_CAPS.filter((c) => c !== 'products.manage')));
    const branch = await screen.findByRole('region', { name: 'Costo promedio por sucursal' });
    await within(branch).findByText('Cable');
    expect(within(branch).queryByRole('button', { name: /Editar precio y costo/ })).toBeNull();
  });
});
