/**
 * INVENTORY-UNIFIED-01 — «Gestión masiva de inventario»: un centro.
 *
 * Una entrada más en el menú, y ninguna de las que había cambia. Dentro, cada
 * sección se ofrece según lo que la persona puede hacer: importar (tres cargas
 * que siguen siendo tres), exportar, imágenes e historial. Y un asistente que
 * dice a qué carga va un archivo — lo dice el servidor; la pantalla manda ahí.
 */
import { act, fireEvent, render, screen, within } from '@testing-library/react';

import { BulkInventoryCentre, ImportAssistant } from '@/app/admin/components/BulkInventoryCentre';
import { IMPORT_KINDS, ImportKindNotice } from '@/app/admin/components/ImportKindNotice';
import { HistoryTable } from '@/app/admin/components/ImportWizard';
import * as api from '@/app/admin/lib/internal-api';
import {
  INTERNAL_MODULES, canAccessModule, navigableModules, type ModuleAccessContext,
} from '@/app/admin/lib/internal-modules';

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/admin/lib/internal-api', () => ({
  ...jest.requireActual('@/app/admin/lib/internal-api'),
  whichImport: jest.fn(),
  fetchImportHistory: jest.fn(),
}));
jest.mock('@/app/admin/components/InventoryExportPanel', () => ({
  InventoryExports: (props: Record<string, unknown>) => <div data-testid="exportar" data-props={JSON.stringify(props)} />,
}));
jest.mock('@/app/admin/components/BulkImageImport', () => ({
  BulkImageImport: (props: Record<string, unknown>) => <div data-testid="imagenes" data-props={JSON.stringify(props)} />,
}));

const mocked = api as jest.Mocked<typeof api>;

const ALL = { productsManage: true, inventoryAdjust: true, productsView: true, inventoryView: true, costsView: true };
const NONE = { productsManage: false, inventoryAdjust: false, productsView: false, inventoryView: false, costsView: false };

const job = (over: Partial<api.ImportJob> = {}): api.ImportJob => ({
  id: 1, import_type: 'products', status: 'applied', stock_mode: '', original_filename: 'productos.xlsx',
  file_sha256: '', mapping: {}, options: {},
  counts: { total: 3, create: 2, update: 1, no_change: 0, skip: 0, error: 0 }, summary: {},
  created_at: '2026-10-13T15:00:00Z', applied_at: '2026-10-13T15:01:00Z', created_by: 'ana', applied_by: 'ana',
  is_applicable: false, ...over,
});

const suggestion = (type: api.ImportSuggestion['suggested_import_type'], detail: string): api.ImportSuggestion => ({
  suggested_import_type: type, suggestion_reason: 'x', suggestion_detail: detail, suggestion_sheet: 'Hoja', sheet_names: ['Hoja'],
});

async function show(can = ALL) {
  await act(async () => { render(<BulkInventoryCentre companyId={7} can={can} />); });
}

const section = (name: string) => screen.getByRole('region', { name });
const sections = () => screen.queryAllByRole('region').map((region) => region.getAttribute('aria-label'));
const workbook = () => new File(['x'], 'archivo.xlsx');
const choose = async (file = workbook()) => {
  await act(async () => { fireEvent.change(screen.getByLabelText('Elegir un archivo de Excel'), { target: { files: [file] } }); });
};

beforeEach(() => {
  mocked.whichImport.mockReset();
  mocked.fetchImportHistory.mockReset().mockResolvedValue([]);
});

describe('la entrada del menú', () => {
  const entry = () => INTERNAL_MODULES.find((m) => m.id === 'inventory.bulk')!;
  const member = (capabilities: string[]): ModuleAccessContext => ({
    capabilities, legacyRole: null, isPlatformAdmin: false, hasCompanyContext: true,
  });

  it('es una, en Inventario, y lleva al centro', () => {
    expect(INTERNAL_MODULES.filter((m) => m.label === 'Gestión masiva de inventario')).toHaveLength(1);
    expect([entry().group, entry().href, entry().status]).toEqual(['inventory', '/admin/inventory/bulk', 'implemented']);
  });

  it('la ve quien tiene CUALQUIERA de las cuatro capacidades, y nadie más', () => {
    for (const capability of ['products.manage', 'inventory.adjust', 'products.view', 'inventory.view']) {
      expect(canAccessModule(entry(), member([capability]))).toBe(true);
    }
    expect(canAccessModule(entry(), member([]))).toBe(false);
    expect(canAccessModule(entry(), member(['sales.orders.view', 'service.manage']))).toBe(false);
    expect(navigableModules(member(['inventory.view'])).map((m) => m.id)).toContain('inventory.bulk');
    // Sin empresa resuelta no hay capacidades que preguntar: no se ofrece.
    expect(canAccessModule(entry(), { capabilities: [], legacyRole: 'admin', isPlatformAdmin: false, hasCompanyContext: false })).toBe(false);
    // Una capacidad sin su empresa no es de ninguna parte: tampoco.
    expect(canAccessModule(entry(), { capabilities: ['inventory.view'], legacyRole: 'admin', isPlatformAdmin: false, hasCompanyContext: false })).toBe(false);
    expect(canAccessModule(entry(), { capabilities: [], legacyRole: null, isPlatformAdmin: true, hasCompanyContext: false })).toBe(true);
  });

  it('las tres cargas siguen en el menú, con su nombre, su ruta y su capacidad', () => {
    const three = ['products.import', 'inventory.units.import', 'inventory.import']
      .map((id) => INTERNAL_MODULES.find((m) => m.id === id)!)
      .map((m) => [m.label, m.href, m.requiredCapabilities]);
    expect(three).toEqual([
      ['Carga de productos', '/admin/products/import', ['products.manage']],
      ['Carga de equipos', '/admin/inventory/units/import', ['inventory.adjust']],
      ['Carga de stock', '/admin/inventory/import', ['inventory.adjust']],
    ]);
  });

  it('una entrada que pide TODAS sus capacidades sigue pidiéndolas todas', () => {
    const both = { ...entry(), anyCapabilities: undefined, requiredCapabilities: ['products.view', 'inventory.view'] };
    expect(canAccessModule(both, member(['products.view']))).toBe(false);
    expect(canAccessModule(both, member(['products.view', 'inventory.view']))).toBe(true);
  });
});

describe('las secciones, según lo que la persona puede hacer', () => {
  it('con todo, las cinco', async () => {
    await show();
    expect(sections()).toEqual(['¿Qué archivo tengo?', 'Importar', 'Exportar', 'Imágenes', 'Historial']);
  });

  it('quien sólo consulta, sólo exporta', async () => {
    await show({ ...NONE, productsView: true, inventoryView: true });
    expect(sections()).toEqual(['Exportar']);
    expect(mocked.fetchImportHistory).not.toHaveBeenCalled();
  });

  it('quien gestiona el catálogo importa productos e imágenes, y no stock ni equipos', async () => {
    await show({ ...NONE, productsManage: true, productsView: true });
    expect(sections()).toEqual(['¿Qué archivo tengo?', 'Importar', 'Exportar', 'Imágenes', 'Historial']);
    expect(within(section('Importar')).getAllByRole('heading', { level: 3 }).map((h) => h.textContent)).toEqual(['Carga de productos']);
  });

  it('quien ajusta inventario importa stock y equipos, sin imágenes', async () => {
    await show({ ...NONE, inventoryAdjust: true });
    expect(sections()).toEqual(['¿Qué archivo tengo?', 'Importar', 'Historial']);
    expect(within(section('Importar')).getAllByRole('heading', { level: 3 }).map((h) => h.textContent))
      .toEqual(['Carga de stock', 'Carga de equipos']);
  });

  it('sin nada, lo dice en vez de enseñar una página vacía', async () => {
    await show(NONE);
    expect(sections()).toEqual([]);
    expect(screen.getByText(/No tienes permisos para la gestión masiva/)).toBeInTheDocument();
  });

  it('exportar e imágenes reciben la empresa y lo que la persona puede', async () => {
    await show({ ...ALL, costsView: false });
    expect(JSON.parse(screen.getByTestId('exportar').dataset.props!)).toEqual({
      companyId: 7, canCatalogue: true, canInventory: true, canSeeCost: false,
    });
    expect(JSON.parse(screen.getByTestId('imagenes').dataset.props!)).toEqual({ companyId: 7 });
    expect(section('Imágenes')).toHaveAttribute('id', 'imagenes');
  });

  it('el costo en las descargas es de `costs.view`, no de ajustar inventario (DC1)', async () => {
    await show({ ...ALL, inventoryAdjust: false, costsView: true });
    expect(JSON.parse(screen.getByTestId('exportar').dataset.props!).canSeeCost).toBe(true);
  });
});

describe('importar: tres cargas que siguen siendo tres', () => {
  it('cada tarjeta dice qué hace y qué no, y lleva a su asistente de siempre', async () => {
    await show();
    const cards = within(section('Importar')).getAllByRole('listitem');
    expect(cards.map((card) => within(card).getByRole('heading').textContent))
      .toEqual(['Carga de productos', 'Carga de stock', 'Carga de equipos']);
    for (const [index, kind] of (['products', 'stock', 'units'] as const).entries()) {
      expect(cards[index]).toHaveTextContent(IMPORT_KINDS[kind].does);
      expect(cards[index]).toHaveTextContent(IMPORT_KINDS[kind].doesNot);
      expect(within(cards[index]).getByRole('link', { name: `Abrir ${IMPORT_KINDS[kind].label}` }))
        .toHaveAttribute('href', IMPORT_KINDS[kind].href);
    }
  });

  it('los textos son los del aviso de cada carga: una sola redacción', () => {
    expect(IMPORT_KINDS.products).toMatchObject({ href: '/admin/products/import', doesNot: 'No mueve stock.' });
    for (const kind of ['products', 'stock', 'units'] as const) {
      const { unmount } = render(<ImportKindNotice kind={kind} />);
      const notice = screen.getByRole('note', { name: 'Qué hace esta carga' });
      expect(notice).toHaveTextContent(IMPORT_KINDS[kind].does);
      expect(notice).toHaveTextContent(IMPORT_KINDS[kind].doesNot);
      unmount();
    }
  });
});

describe('¿qué archivo tengo?', () => {
  it.each([
    ['products', 'Tiene nombre y precio de venta: es la carga de productos.', 'Ir a Carga de productos', '/admin/products/import'],
    ['stock', 'Tiene columnas de cantidad por almacén: es la carga de stock.', 'Ir a Carga de stock', '/admin/inventory/import'],
    ['units', 'Tiene columna de número de serie: es la carga de equipos.', 'Ir a Carga de equipos', '/admin/inventory/units/import'],
    ['images', 'Tiene código e imágenes, sin nombre ni precio: es la carga de imágenes.', 'Ir a Imágenes', '#imagenes'],
  ] as const)('un archivo de %s: dice el motivo y manda a su carga', async (type, detail, link, href) => {
    mocked.whichImport.mockResolvedValueOnce(suggestion(type, detail));
    render(<ImportAssistant companyId={7} />);
    const file = workbook();
    await choose(file);

    expect(mocked.whichImport).toHaveBeenCalledWith(7, file);
    const answer = screen.getByRole('status');
    expect(answer).toHaveTextContent('archivo.xlsx');
    expect(answer).toHaveTextContent(detail);
    expect(within(answer).getByRole('link', { name: link })).toHaveAttribute('href', href);
  });

  it.each([
    ['report', '«Reporte de stock» es un reporte: sirve para leer y no se importa.'],
    ['unknown', 'Tiene columna de número de serie y columna de cantidad. Sepáralo en dos archivos.'],
    ['forbidden', 'Este archivo es de una carga para la que no tienes permiso.'],
  ] as const)('%s: lo dice y no manda a ninguna carga', async (type, detail) => {
    mocked.whichImport.mockResolvedValueOnce(suggestion(type, detail));
    render(<ImportAssistant companyId={7} />);
    await choose();

    const answer = screen.getByRole('status');
    expect(answer).toHaveTextContent(detail);
    expect(within(answer).queryByRole('link')).toBeNull();
  });

  it('no sube nada por su cuenta: dice que sólo mira las cabeceras', () => {
    render(<ImportAssistant companyId={7} />);
    expect(screen.getByText(/Sólo mira los nombres de las hojas y las cabeceras/)).toBeInTheDocument();
    expect(mocked.whichImport).not.toHaveBeenCalled();
  });

  it('mientras revisa lo dice, y un archivo nuevo borra la respuesta anterior', async () => {
    let finish: (value: api.ImportSuggestion) => void = () => {};
    mocked.whichImport.mockResolvedValueOnce(suggestion('units', 'Es la carga de equipos.'));
    render(<ImportAssistant companyId={7} />);
    await choose();
    expect(screen.getByRole('status')).toHaveTextContent('Es la carga de equipos.');

    mocked.whichImport.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    await choose(new File(['y'], 'otro.xlsx'));
    expect(screen.getByRole('status')).toHaveTextContent('Revisando otro.xlsx…');
    expect(screen.queryByText('Es la carga de equipos.')).toBeNull();
    expect(screen.getByLabelText('Elegir un archivo de Excel')).toBeDisabled();

    await act(async () => { finish(suggestion('stock', 'Es la carga de stock.')); });
    expect(screen.getByRole('status')).toHaveTextContent('Es la carga de stock.');
  });

  it('si el archivo no se puede leer, lo dice', async () => {
    mocked.whichImport.mockRejectedValueOnce(new Error('No se pudo leer el archivo de Excel.'));
    render(<ImportAssistant companyId={7} />);
    await choose();
    expect(screen.getByRole('alert')).toHaveTextContent('No se pudo leer el archivo de Excel.');
    expect(screen.queryByRole('status')).toBeNull();
  });
});

describe('historial', () => {
  it('lista lo que la persona puede ver, de todas las cargas, con su tipo', async () => {
    mocked.fetchImportHistory.mockResolvedValueOnce([
      job({ id: 4, import_type: 'product_images', original_filename: '' }),
      job({ id: 3, import_type: 'units', original_filename: 'equipos.xlsx' }),
      job({ id: 2, import_type: 'stock' }),
      job({ id: 1 }),
    ]);
    await show();

    expect(mocked.fetchImportHistory).toHaveBeenCalledWith(7);
    const rows = within(section('Historial')).getAllByRole('row').slice(1);
    expect(rows.map((row) => within(row).getAllByRole('cell')[1].textContent))
      .toEqual(['Imágenes de productos', 'Equipos con serie', 'Inventario', 'Productos']);
    expect(rows[1]).toHaveTextContent('equipos.xlsx');
    expect(rows[1]).toHaveTextContent('ana');
  });

  it('«Descargar errores» sólo en la carga que los tiene', async () => {
    mocked.fetchImportHistory.mockResolvedValueOnce([
      job({ id: 9, status: 'previewed', counts: { total: 5, create: 0, update: 0, no_change: 0, skip: 0, error: 2 } }),
      job({ id: 8 }),
    ]);
    await show();

    const links = within(section('Historial')).getAllByRole('link', { name: /Descargar errores/ });
    expect(links).toHaveLength(1);
    expect(links[0].getAttribute('href')).toMatch(/\/admin\/imports\/9\/errors\.csv\?company=7$/);
  });

  it('las pantallas de siempre, que no pasan la empresa, no enseñan esa columna', () => {
    render(<HistoryTable jobs={[job({ counts: { total: 5, create: 0, update: 0, no_change: 0, skip: 0, error: 2 } })]} />);
    expect(screen.queryByRole('link', { name: /Descargar errores/ })).toBeNull();
    expect(screen.queryByRole('columnheader', { name: 'Reporte' })).toBeNull();
  });

  it('si no se puede leer, lo dice', async () => {
    mocked.fetchImportHistory.mockRejectedValueOnce(new Error('No se pudo cargar el historial.'));
    await show();
    expect(within(section('Historial')).getByText('No se pudo cargar el historial.')).toBeInTheDocument();
  });
});

describe('la pregunta al servidor', () => {
  const real = jest.requireActual('@/app/admin/lib/internal-api') as typeof api;
  /* eslint-disable-next-line @typescript-eslint/no-require-imports */
  const { fetchWithAuth } = require('@/app/lib/auth') as { fetchWithAuth: jest.Mock };

  beforeEach(() => fetchWithAuth.mockReset());

  it('es la inspección de siempre, pidiendo sólo a dónde va el archivo', async () => {
    fetchWithAuth.mockResolvedValueOnce({ ok: true, status: 200, json: async () => suggestion('units', 'x') });
    const file = workbook();
    const answer = await real.whichImport(7, file);

    const [url, init] = fetchWithAuth.mock.calls[0];
    expect(String(url)).toMatch(/\/admin\/imports\/inspect\/\?company=7$/);
    expect(init.method).toBe('POST');
    expect((init.body as FormData).get('import_type')).toBe('auto');
    expect((init.body as FormData).get('file')).toBe(file);
    expect(init.headers).toBeUndefined();
    expect(answer.suggested_import_type).toBe('units');
  });

  it('lo que el servidor explica al rechazar se conserva', async () => {
    fetchWithAuth.mockResolvedValueOnce({ ok: false, status: 400, json: async () => ({ detail: 'No se pudo leer el archivo de Excel.' }) });
    await expect(real.whichImport(null, workbook())).rejects.toThrow('No se pudo leer el archivo de Excel.');
  });
});
