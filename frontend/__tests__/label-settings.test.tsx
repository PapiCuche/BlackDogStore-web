/**
 * LABEL-03b · «Configuración de etiquetas» (Inventario › Etiquetas).
 *
 * El panel de `GET/PATCH /api/admin/label-settings/`:
 *  - lee con `company.view`; cambia sólo si el servidor dice `can_manage`;
 *  - el prefijo de los equipos (1–4 mayúsculas) se muestra junto al de los
 *    productos, con la regla de que nunca coinciden;
 *  - un 400 del servidor se muestra tal cual, en su campo;
 *  - «IMEI 1» lleva un aviso: sólo con `inventory.labels.sensitive` y auditado.
 * Y la pantalla de etiquetas no llama al inventario sin `inventory.view`.
 */
import { act, fireEvent, render, screen, within } from '@testing-library/react';

import { LabelsWorkbench } from '@/app/admin/inventory/labels/LabelsWorkbench';
import InventoryLabelsPage from '@/app/admin/inventory/labels/page';
import { LabelSettingsPanel } from '@/app/admin/inventory/labels/settings/LabelSettingsPanel';
import { INTERNAL_MODULES, activeModuleHref, canAccessModule, type ModuleAccessContext } from '@/app/admin/lib/internal-modules';
import { fetchWithAuth } from '@/app/lib/auth';
import { fetchBranchStock, fetchInventoryBranches } from '@/app/lib/inventory';
import { fetchSerializedProducts, fetchStockUnits } from '@/app/lib/stock-units';

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/lib/inventory', () => ({ fetchBranchStock: jest.fn(), fetchInventoryBranches: jest.fn() }));
jest.mock('@/app/admin/components/AdminShell', () => ({
  AdminShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
let viewerCaps: string[] = [];
jest.mock('@/app/admin/components/AccessGuard', () => ({
  AccessGuard: ({ children }: { children: (access: unknown) => React.ReactNode }) => (
    <>{children({ user: { username: 'ana' }, can: (cap: string) => viewerCaps.includes(cap) })}</>
  ),
}));
jest.mock('@/app/lib/stock-units', () => ({ fetchStockUnits: jest.fn(), fetchSerializedProducts: jest.fn() }));

const fetchMock = fetchWithAuth as jest.Mock;

const SETTINGS = {
  unit_code_prefix: 'U', device_symbol: 'internal', device_symbol_label: 'Código interno del equipo',
  device_symbols: [
    { value: 'internal', label: 'Código interno del equipo' },
    { value: 'imei1', label: 'IMEI 1 (requiere permiso de datos sensibles)' },
  ],
  can_manage: true,
};

const SEQUENCES = {
  scope: 'company', can_change_scope: true, can_manage: true, notice: '',
  results: [
    { id: 1, company: 7, branch: null, document_type: 'sales_note', prefix: 'NV-' },
    { id: 2, company: 7, branch: null, document_type: 'product_code', prefix: 'C' },
  ],
};

const json = (status: number, body: unknown) => ({ ok: status < 400, status, json: async () => body });

function server(over: { settings?: Record<string, unknown>; patch?: (body: Record<string, unknown>) => ReturnType<typeof json>; sequences?: unknown } = {}) {
  const calls: { url: string; method: string; body?: Record<string, unknown> }[] = [];
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });
    if (url.includes('/admin/sequences/')) return json(200, over.sequences ?? SEQUENCES);
    if (url.includes('/admin/label-settings/') && method === 'PATCH') {
      return over.patch ? over.patch(body) : json(200, { ...SETTINGS, ...over.settings, ...body });
    }
    if (url.includes('/admin/label-settings/')) return json(200, { ...SETTINGS, ...over.settings });
    return json(404, {});
  });
  return calls;
}

async function mount(companyId: number | null = 7) {
  await act(async () => { render(<LabelSettingsPanel companyId={companyId} />); });
}

beforeEach(() => { fetchMock.mockReset(); jest.clearAllMocks(); });

describe('Configuración de etiquetas', () => {
  it('muestra el prefijo de equipos y el de productos, con la regla de que no coinciden', async () => {
    const calls = server();
    await mount();
    expect(screen.getByLabelText('Prefijo de los equipos')).toHaveValue('U');
    expect(screen.getByTestId('product-prefix')).toHaveTextContent('C');
    expect(screen.getByText(/nunca puede ser igual al de los productos/)).toBeInTheDocument();
    expect(calls.every((c) => c.url.includes('company=7'))).toBe(true);
  });

  it('sin serie de productos guardada, el prefijo de productos es el de siempre (P)', async () => {
    server({ sequences: { ...SEQUENCES, results: [] } });
    await mount();
    expect(screen.getByTestId('product-prefix')).toHaveTextContent('P');
  });

  it('guarda sólo lo que cambió y dice que quedó guardado', async () => {
    const calls = server();
    await mount();
    fireEvent.change(screen.getByLabelText('Prefijo de los equipos'), { target: { value: 'eq' } });
    expect(screen.getByLabelText('Prefijo de los equipos')).toHaveValue('EQ');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Guardar' })); });
    const patch = calls.find((c) => c.method === 'PATCH')!;
    expect(patch.url).toContain('/admin/label-settings/?company=7');
    expect(patch.body).toEqual({ unit_code_prefix: 'EQ' });
    expect(screen.getByRole('status')).toHaveTextContent('Guardado');
  });

  it('un 400 del servidor se muestra tal cual en su campo', async () => {
    const message = '«C» ya es el prefijo de los códigos de producto: usa otro, para que nadie confunda un equipo con un producto.';
    server({ patch: () => json(400, { unit_code_prefix: [message] }) });
    await mount();
    fireEvent.change(screen.getByLabelText('Prefijo de los equipos'), { target: { value: 'XY' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Guardar' })); });
    expect(screen.getByText(message)).toBeInTheDocument();
    expect(screen.getByLabelText('Prefijo de los equipos')).toHaveAttribute('aria-invalid', 'true');
  });

  it('avisa antes de enviar un prefijo que no sirve, sin inventar la regla del servidor', async () => {
    const calls = server();
    await mount();
    const input = screen.getByLabelText('Prefijo de los equipos');
    fireEvent.change(input, { target: { value: 'C' } });
    expect(screen.getByText(/es el prefijo de los productos/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Guardar' })).toBeDisabled();
    fireEvent.change(input, { target: { value: 'A1' } });
    expect(screen.getByText(/De 1 a 4 letras mayúsculas/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Guardar' })).toBeDisabled();
    expect(calls.some((c) => c.method === 'PATCH')).toBe(false);
  });

  it('«IMEI 1» advierte que sólo lo imprime quien tiene el permiso y que queda auditado', async () => {
    const calls = server();
    await mount();
    expect(screen.queryByRole('note', { name: 'Aviso IMEI 1' })).toBeNull();
    fireEvent.click(screen.getByLabelText('IMEI 1 (requiere permiso de datos sensibles)'));
    const warning = screen.getByRole('note', { name: 'Aviso IMEI 1' });
    expect(warning).toHaveTextContent('inventory.labels.sensitive');
    expect(warning).toHaveTextContent(/queda registrad/);
    expect(warning).toHaveTextContent(/código interno/);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Guardar' })); });
    expect(calls.find((c) => c.method === 'PATCH')!.body).toEqual({ device_symbol: 'imei1' });
  });

  it('sin can_manage se ve, pero no se cambia', async () => {
    server({ settings: { can_manage: false, device_symbol: 'imei1' } });
    await mount();
    expect(screen.getByLabelText('Prefijo de los equipos')).toBeDisabled();
    expect(screen.getByLabelText('IMEI 1 (requiere permiso de datos sensibles)')).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Guardar' })).toBeNull();
    expect(screen.getByText(/Sólo quien administra la empresa/)).toBeInTheDocument();
    expect(screen.getByRole('note', { name: 'Aviso IMEI 1' })).toBeInTheDocument();
  });

  it('sin permiso para leerla, el mensaje del servidor', async () => {
    fetchMock.mockImplementation(async () => json(403, { detail: 'No tienes permisos sobre la configuración.' }));
    await mount();
    expect(screen.getByRole('alert')).toHaveTextContent('No tienes permisos sobre la configuración.');
    expect(screen.queryByLabelText('Prefijo de los equipos')).toBeNull();
  });

  it('está en el menú de Inventario, la abre company.view y se ilumina sola', () => {
    const entry = INTERNAL_MODULES.find((m) => m.id === 'inventory.labels.settings')!;
    expect(entry).toMatchObject({
      group: 'inventory', href: '/admin/inventory/labels/settings', requiredCapabilities: ['company.view'],
    });
    const member = (capabilities: string[]): ModuleAccessContext => ({
      capabilities, legacyRole: null, isPlatformAdmin: false, hasCompanyContext: true,
    });
    expect(canAccessModule(entry, member(['inventory.labels.print']))).toBe(false);
    expect(canAccessModule(entry, member(['company.view']))).toBe(true);
    const hrefs = INTERNAL_MODULES.flatMap((m) => (m.href ? [m.href] : []));
    expect(activeModuleHref('/admin/inventory/labels/settings', '', hrefs)).toBe('/admin/inventory/labels/settings');
    expect(activeModuleHref('/admin/inventory/labels', '', hrefs)).toBe('/admin/inventory/labels');
  });
});

describe('Etiquetas sin inventory.view', () => {
  it('la página explica qué falta y no pide sucursales ni stock', async () => {
    viewerCaps = ['inventory.labels.print'];
    await act(async () => { render(<InventoryLabelsPage />); });
    expect(screen.getByRole('note', { name: 'Falta Ver inventario' })).toHaveTextContent('inventory.view');
    expect(fetchInventoryBranches).not.toHaveBeenCalled();
    expect(fetchBranchStock).not.toHaveBeenCalled();
  });

  it('con inventory.view la página carga sus sucursales', async () => {
    viewerCaps = ['inventory.labels.print', 'inventory.view'];
    (fetchInventoryBranches as jest.Mock).mockResolvedValue({ results: [], default_branch: null, allows_aggregate: false });
    await act(async () => { render(<InventoryLabelsPage />); });
    expect(fetchInventoryBranches).toHaveBeenCalled();
    expect(screen.queryByRole('note', { name: 'Falta Ver inventario' })).toBeNull();
  });

  it('el buscador no llama al inventario y explica qué permiso falta', async () => {
    await act(async () => { render(<LabelsWorkbench branch={3} canSearch={false} />); });
    const notice = screen.getByRole('note', { name: 'Buscador no disponible' });
    expect(notice).toHaveTextContent('Ver inventario');
    expect(screen.queryByRole('button', { name: 'Buscar' })).toBeNull();
    expect(fetchBranchStock).not.toHaveBeenCalled();
    expect(fetchStockUnits).not.toHaveBeenCalled();
    expect(fetchSerializedProducts).not.toHaveBeenCalled();
    expect(within(screen.getByRole('region', { name: 'Selección' })).getByText(/Busca y agrega/)).toBeInTheDocument();
  });
});
