/**
 * INVENTORY-UNIFIED-01 — el panel «Exportar».
 *
 * Cinco descargas y una diferencia que la pantalla tiene que dejar clara: un
 * archivo «para volver a cargar» no es un reporte. Lo que se fija aquí es que
 * el panel ofrezca sólo lo que la persona puede descargar, sólo sus sucursales,
 * y que pida al servidor exactamente lo elegido.
 *
 * Qué contiene cada archivo y quién puede bajarlo lo decide el servidor
 * (`test_inventory_exports.py`).
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { InventoryExportPanel, InventoryExports } from '@/app/admin/components/InventoryExportPanel';
import * as exportsApi from '@/app/lib/inventory-exports';
import * as admin from '@/app/lib/admin';

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/lib/inventory-exports', () => ({
  ...jest.requireActual('@/app/lib/inventory-exports'),
  downloadInventoryExport: jest.fn(),
  fetchExportBranches: jest.fn(),
}));
jest.mock('@/app/lib/admin', () => ({
  ...jest.requireActual('@/app/lib/admin'),
  fetchAdminCategories: jest.fn(),
}));

const api = exportsApi as jest.Mocked<typeof exportsApi>;
const adminApi = admin as jest.Mocked<typeof admin>;

const BRANCHES = [{ id: 11, name: 'Centro' }, { id: 12, name: 'Norte' }];
const CATEGORIES = [{ id: 3, name: 'Accesorios' }, { id: 5, name: 'Teléfonos' }];

const CATALOGUE = 'Catálogo general';
const ACCESSORIES = 'Productos por cantidad';
const STOCK = 'Stock por sucursal';
const DEVICES = 'Equipos con serie';
const FULL = 'Inventario completo (ZIP)';

function show(props: Partial<React.ComponentProps<typeof InventoryExportPanel>> = {}) {
  return render(
    <InventoryExportPanel
      companyId={7} canCatalogue canInventory branches={BRANCHES} categories={CATEGORIES} {...props}
    />,
  );
}

const card = (title: string) => screen.getByRole('heading', { name: title }).closest('li')!;
const download = async (title: string) => {
  await act(async () => { fireEvent.click(within(card(title)).getByRole('button', { name: /Descargar/ })); });
};
const lastCall = () => api.downloadInventoryExport.mock.calls[api.downloadInventoryExport.mock.calls.length - 1];
const titles = () => screen.queryAllByRole('heading', { level: 3 }).map((heading) => heading.textContent);

beforeEach(() => {
  api.downloadInventoryExport.mockReset().mockResolvedValue(undefined);
  api.fetchExportBranches.mockReset().mockResolvedValue(BRANCHES);
  adminApi.fetchAdminCategories.mockReset().mockResolvedValue(CATEGORIES as admin.AdminCategory[]);
});

describe('las cinco descargas', () => {
  it('se ofrecen con su nombre y una frase que dice qué trae cada una', () => {
    show();
    expect(titles()).toEqual([CATALOGUE, ACCESSORIES, STOCK, DEVICES, FULL]);
    expect(card(CATALOGUE)).toHaveTextContent(/una fila por producto/i);
    expect(card(ACCESSORIES)).toHaveTextContent(/sin los equipos con número de serie/i);
    expect(card(STOCK)).toHaveTextContent(/una columna por sucursal/i);
    expect(card(DEVICES)).toHaveTextContent(/una fila por unidad física/i);
    expect(card(FULL)).toHaveTextContent(/Productos\.xlsx, Stock\.xlsx y Equipos\.xlsx/);
  });

  it('dice lo que un archivo para volver a cargar no lleva y lo que hace al cargarlo', () => {
    // Revisión N2, N15, N16: sólo palabras, pero son las que evitan una sorpresa.
    show();
    expect(card(CATALOGUE)).toHaveTextContent(/Para volver a cargar lleva sólo los productos activos; el reporte, todos/);
    expect(card(ACCESSORIES)).toHaveTextContent(/sólo los productos activos/);
    expect(card(STOCK)).toHaveTextContent(/si el stock se movió desde la descarga, volver a cargarlo lo deja como dice el archivo/i);
    expect(card(STOCK)).toHaveTextContent(/sucursales activas/);
    expect(card(DEVICES)).toHaveTextContent(/sucursales activas/);
    expect(card(CATALOGUE)).toHaveTextContent(/«Stock total» del reporte suma toda la empresa/);
  });

  it('cada una pide su archivo, para volver a cargar si no se dice otra cosa', async () => {
    show();
    const expected: Array<[string, string, Record<string, unknown>]> = [
      [CATALOGUE, 'products', {}],
      [ACCESSORIES, 'products', { type: 'quantity' }],
      [STOCK, 'stock', {}],
      [DEVICES, 'units', {}],
      [FULL, 'full', {}],
    ];
    for (const [title, kind, filters] of expected) {
      await download(title);
      const [calledKind, mode, calledFilters, company] = lastCall();
      expect([calledKind, mode, company]).toEqual([kind, 'reimport', 7]);
      expect(calledFilters).toMatchObject(filters);
    }
    expect(api.downloadInventoryExport).toHaveBeenCalledTimes(5);
  });
});

describe('para volver a cargar o reporte', () => {
  it('son dos opciones, cada una explicada en una frase', () => {
    show();
    const choice = screen.getByRole('radiogroup', { name: 'Tipo de archivo' });
    const reload = within(choice).getByRole('radio', { name: /Para volver a cargar/ });
    const report = within(choice).getByRole('radio', { name: /Reporte/ });
    expect(reload).toBeChecked();
    expect(report).not.toBeChecked();
    expect(choice).toHaveTextContent(/las mismas columnas que piden las cargas masivas.*no cambia nada/i);
    expect(choice).toHaveTextContent(/columnas de sólo lectura.*no se puede volver a cargar/i);
  });

  it('el reporte se pide como reporte', async () => {
    show();
    fireEvent.click(screen.getByRole('radio', { name: /Reporte/ }));
    await download(STOCK);
    expect(lastCall()[1]).toBe('report');
    await download(DEVICES);
    expect(lastCall()[1]).toBe('report');
  });

  it('el ZIP es siempre para volver a cargar, y lo dice', async () => {
    show();
    fireEvent.click(screen.getByRole('radio', { name: /Reporte/ }));
    expect(card(FULL)).toHaveTextContent(/siempre para volver a cargar/i);
    await download(FULL);
    expect(lastCall().slice(0, 2)).toEqual(['full', 'reimport']);
  });
});

describe('lo que la persona puede descargar', () => {
  it('con sólo el catálogo, sólo el catálogo: ni stock, ni equipos, ni sucursales', () => {
    show({ canInventory: false });
    expect(titles()).toEqual([CATALOGUE, ACCESSORIES]);
    expect(screen.queryByRole('group', { name: 'Sucursales' })).toBeNull();
    expect(screen.queryByLabelText('Condición del equipo')).toBeNull();
  });

  it('con sólo el inventario, stock y equipos', () => {
    show({ canCatalogue: false });
    expect(titles()).toEqual([STOCK, DEVICES]);
    expect(screen.getByRole('group', { name: 'Sucursales' })).toBeInTheDocument();
  });

  it('sin ninguno de los dos, el panel no aparece', () => {
    const { container } = show({ canCatalogue: false, canInventory: false });
    expect(container).toBeEmptyDOMElement();
  });

  it('el costo sólo se anuncia a quien puede ajustar inventario', () => {
    show({ canSeeCost: true });
    expect(card(DEVICES)).toHaveTextContent(/incluye el costo/i);
  });

  it('a quien no puede, se le dice que la columna va vacía', () => {
    show({ canSeeCost: false });
    expect(card(DEVICES)).toHaveTextContent(/«Costo» va vacía/i);
  });
});

describe('las sucursales', () => {
  it('sólo se ofrecen las de la persona', () => {
    show({ branches: [BRANCHES[0]] });
    const group = screen.getByRole('group', { name: 'Sucursales' });
    expect(within(group).getAllByRole('checkbox').map((box) => (box as HTMLInputElement).labels?.[0].textContent)).toEqual(['Centro']);
  });

  it('sin marcar ninguna se descargan todas las suyas; marcadas, sólo esas', async () => {
    show();
    await download(STOCK);
    expect(lastCall()[2].branches ?? []).toEqual([]);

    fireEvent.click(screen.getByRole('checkbox', { name: 'Norte' }));
    await download(STOCK);
    expect(lastCall()[2].branches).toEqual([12]);

    fireEvent.click(screen.getByRole('checkbox', { name: 'Centro' }));
    await download(DEVICES);
    expect(lastCall()[2].branches).toEqual([11, 12]);

    fireEvent.click(screen.getByRole('checkbox', { name: 'Norte' }));
    await download(FULL);
    expect(lastCall()[2].branches).toEqual([11]);
  });

  it('sin sucursales a su alcance lo dice en vez de ofrecer una lista vacía', () => {
    show({ branches: [] });
    expect(screen.getByText(/No tienes sucursales asignadas/)).toBeInTheDocument();
  });
});

describe('los filtros', () => {
  it('viajan los que se pusieron', async () => {
    show();
    fireEvent.change(screen.getByLabelText('Categoría'), { target: { value: '5' } });
    fireEvent.change(screen.getByLabelText('Tipo de producto'), { target: { value: 'serialized' } });
    fireEvent.change(screen.getByLabelText('Existencia'), { target: { value: 'in_stock' } });
    fireEvent.change(screen.getByLabelText('Condición del equipo'), { target: { value: 'used' } });
    fireEvent.change(screen.getByLabelText('Visibilidad en e-commerce'), { target: { value: 'internal' } });
    fireEvent.change(screen.getByLabelText('Estado del producto'), { target: { value: 'true' } });

    await download(DEVICES);
    expect(lastCall()[2]).toMatchObject({
      category: '5', type: 'serialized', stock: 'in_stock', condition: 'used', visibility: 'internal', is_active: 'true',
    });
  });

  it('las opciones dicen lo que el servidor entiende', () => {
    show();
    const options = (label: string) => [...(screen.getByLabelText(label) as HTMLSelectElement).options].map((o) => [o.value, o.textContent]);
    expect(options('Categoría')).toEqual([['', 'Todas las categorías'], ['3', 'Accesorios'], ['5', 'Teléfonos']]);
    expect(options('Tipo de producto')).toEqual([['', 'Todos los tipos'], ['serialized', 'Por serie'], ['quantity', 'Por cantidad']]);
    expect(options('Existencia')).toEqual([['', 'Con y sin existencia'], ['in_stock', 'Con existencia'], ['out_of_stock', 'Sin existencia']]);
    expect(options('Condición del equipo')).toEqual([
      ['', 'Cualquier condición'], ['new', 'Nuevo'], ['open_box', 'Caja abierta'], ['refurbished', 'Reacondicionado'], ['used', 'Usado'],
    ]);
    expect(options('Visibilidad en e-commerce')).toEqual([['', 'Publicados e internos'], ['published', 'Publicados'], ['internal', 'Solo stock interno']]);
    expect(options('Estado del producto')).toEqual([['', 'Activos e inactivos'], ['true', 'Activos'], ['false', 'Inactivos']]);
  });

  it('«Productos por cantidad» es por cantidad aunque el filtro de tipo diga otra cosa', async () => {
    show();
    fireEvent.change(screen.getByLabelText('Tipo de producto'), { target: { value: 'serialized' } });
    await download(ACCESSORIES);
    expect(lastCall()[2].type).toBe('quantity');
    await download(CATALOGUE);
    expect(lastCall()[2].type).toBe('serialized');
  });

  it('el ZIP dice cuándo NO es el inventario completo', async () => {
    // Revisión, grupo 9 (f): el «inventario completo» obedecía en silencio a los filtros puestos.
    show();
    expect(within(card(FULL)).queryByRole('alert')).toBeNull();

    fireEvent.change(screen.getByLabelText('Categoría'), { target: { value: '5' } });
    expect(within(card(FULL)).getByRole('alert')).toHaveTextContent(/Con los filtros o las sucursales de arriba NO es el inventario completo/);
    await download(FULL);
    expect(lastCall()[2]).toMatchObject({ category: '5' });

    fireEvent.change(screen.getByLabelText('Categoría'), { target: { value: '' } });
    expect(within(card(FULL)).queryByRole('alert')).toBeNull();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Norte' }));
    expect(within(card(FULL)).getByRole('alert')).toBeInTheDocument();
    // Las demás tarjetas no llevan ese aviso: en ellas filtrar es lo que se espera.
    expect(within(card(STOCK)).queryByRole('alert')).toBeNull();
  });

  it('pedir «Inactivos» en un archivo para volver a cargar se dice: ese archivo lleva sólo activos', () => {
    // Segunda revisión: el servidor cambia el filtro por «activos» y la pantalla lo callaba.
    show();
    const said = /lleva sólo productos activos: el filtro «Inactivos» no le aplica/;
    expect(within(card(CATALOGUE)).queryByText(said)).toBeNull();

    fireEvent.change(screen.getByLabelText('Estado del producto'), { target: { value: 'false' } });
    expect(within(card(CATALOGUE)).getByRole('alert')).toHaveTextContent(said);
    expect(within(card(ACCESSORIES)).getByRole('alert')).toHaveTextContent(said);
    expect(within(card(FULL)).getByText(said)).toBeInTheDocument();
    // Stock y equipos sí obedecen el filtro: no llevan el aviso.
    expect(within(card(STOCK)).queryByText(said)).toBeNull();

    // El reporte lleva activos e inactivos: ahí el filtro vale y el aviso se va.
    fireEvent.click(screen.getByRole('radio', { name: /Reporte/ }));
    expect(within(card(CATALOGUE)).queryByText(said)).toBeNull();
    expect(within(card(ACCESSORIES)).queryByText(said)).toBeNull();
    // El ZIP es siempre para volver a cargar.
    expect(within(card(FULL)).getByText(said)).toBeInTheDocument();
  });

  it('no hay filtro de marca: el catálogo no tiene ese dato', () => {
    show();
    expect(screen.queryByLabelText(/Marca/i)).toBeNull();
  });
});

describe('mientras descarga y si falla', () => {
  it('el botón espera y no se puede pedir dos veces', async () => {
    let finish: () => void = () => {};
    api.downloadInventoryExport.mockReturnValueOnce(new Promise<void>((resolve) => { finish = resolve; }));
    show();
    await download(STOCK);

    const button = within(card(STOCK)).getByRole('button');
    expect(button).toBeDisabled();
    expect(button).toHaveTextContent('Generando…');
    // Las demás también esperan: una descarga a la vez.
    expect(within(card(DEVICES)).getByRole('button')).toBeDisabled();

    await act(async () => { finish(); });
    expect(within(card(STOCK)).getByRole('button')).toBeEnabled();
    expect(api.downloadInventoryExport).toHaveBeenCalledTimes(1);
  });

  it('lo que el servidor explica se muestra', async () => {
    api.downloadInventoryExport.mockRejectedValueOnce(new Error('Son 24000 filas y una descarga admite hasta 20000.'));
    show();
    await download(CATALOGUE);
    expect(await screen.findByRole('alert')).toHaveTextContent('Son 24000 filas');

    await download(CATALOGUE);
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  });
});

describe('el panel con sus datos', () => {
  it('lee las sucursales y las categorías de la empresa elegida', async () => {
    await act(async () => { render(<InventoryExports companyId={7} canCatalogue canInventory />); });
    expect(await screen.findByRole('checkbox', { name: 'Centro' })).toBeInTheDocument();
    expect(api.fetchExportBranches).toHaveBeenCalledWith(7);
    expect(adminApi.fetchAdminCategories).toHaveBeenCalledWith(7);
    expect([...(screen.getByLabelText('Categoría') as HTMLSelectElement).options]).toHaveLength(3);
  });

  it('no pregunta por lo que la persona no puede descargar', async () => {
    await act(async () => { render(<InventoryExports companyId={7} canCatalogue canInventory={false} />); });
    await screen.findByRole('heading', { name: CATALOGUE });
    expect(api.fetchExportBranches).not.toHaveBeenCalled();
  });

  it('si las sucursales no se pueden leer, lo dice y el catálogo sigue ahí', async () => {
    api.fetchExportBranches.mockRejectedValueOnce(new Error('No se pudieron cargar las sucursales.'));
    await act(async () => { render(<InventoryExports companyId={7} canCatalogue canInventory />); });
    expect(await screen.findByText(/No se pudieron cargar las sucursales/)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: CATALOGUE })).toBeInTheDocument();
  });
});

describe('la petición al servidor', () => {
  const real = jest.requireActual('@/app/lib/inventory-exports') as typeof exportsApi;
  /* eslint-disable-next-line @typescript-eslint/no-require-imports */
  const { fetchWithAuth } = require('@/app/lib/auth') as { fetchWithAuth: jest.Mock };

  beforeEach(() => fetchWithAuth.mockReset());

  it('lleva sólo los filtros puestos, la empresa y el modo', () => {
    const path = real.inventoryExportPath('units', 'report', {
      branches: [11, 12], category: '5', type: '', stock: 'in_stock', condition: 'used', visibility: '', is_active: '',
    }, 7);
    const url = new URL(path, 'http://panel.test');
    expect(url.pathname).toMatch(/\/admin\/inventory\/exports\/units\/$/);
    expect(Object.fromEntries(url.searchParams)).toEqual({
      company: '7', mode: 'report', branches: '11,12', category: '5', stock: 'in_stock', condition: 'used',
    });
  });

  it('sin filtros no manda ninguno, y el ZIP no lleva modo', () => {
    expect(real.inventoryExportPath('products', 'reimport', {}, null)).toMatch(/\/exports\/products\/\?mode=reimport$/);
    expect(real.inventoryExportPath('full', 'report', {}, null)).toMatch(/\/exports\/full\/$/);
  });

  describe('al guardar', () => {
    const created: HTMLAnchorElement[] = [];
    let click: jest.SpyInstance;

    beforeEach(() => {
      created.length = 0;
      click = jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
        created.push(this);
      });
      URL.createObjectURL = jest.fn(() => 'blob:descarga');
      URL.revokeObjectURL = jest.fn();
    });
    afterEach(() => click.mockRestore());

    const served = (disposition?: string) => fetchWithAuth.mockResolvedValueOnce({
      ok: true, status: 200, blob: async () => new Blob(['x']),
      headers: new Headers(disposition ? { 'Content-Disposition': disposition } : {}),
    });

    it('el archivo lleva el nombre que puso el servidor', async () => {
      served('attachment; filename="nombre-del-servidor.xlsx"');
      await real.downloadInventoryExport('stock', 'report', { branches: [11] }, 7);

      expect(String(fetchWithAuth.mock.calls[0][0])).toContain('/admin/inventory/exports/stock/?company=7&mode=report&branches=11');
      expect(created[0].download).toBe('nombre-del-servidor.xlsx');
      expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:descarga');
    });

    it('si el servidor no lo nombra, lleva el nombre de ese archivo y ese modo', async () => {
      served();
      await real.downloadInventoryExport('units', 'report', {}, null);
      served();
      await real.downloadInventoryExport('full', 'report', {}, null);
      expect(created.map((link) => link.download)).toEqual(['reporte-equipos.xlsx', 'inventario-completo.zip']);
    });
  });

  it.each([
    [400, { code: 'too_many_rows', detail: 'Son 24000 filas y una descarga admite hasta 20000.' }, 'Son 24000 filas'],
    [403, { detail: 'x' }, 'No tienes permisos para esta descarga.'],
    [404, { detail: 'No encontrado.' }, 'ya no está disponible'],
    [429, {}, 'Demasiadas descargas'],
    [500, null, 'No se pudo generar la descarga.'],
  ])('un %i se explica', async (status, body, message) => {
    fetchWithAuth.mockResolvedValueOnce({ ok: false, status, json: async () => body, headers: new Headers() });
    await expect(real.downloadInventoryExport('products', 'reimport', {}, null)).rejects.toThrow(message as string);
  });
});

// Dónde se monta el panel (Carga de stock y Gestión masiva) se prueba montando esas páginas:
// `inventory-pages-mount.test.tsx`.
