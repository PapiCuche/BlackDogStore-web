/**
 * IMPORT-SPLIT-01 — tres cargas, y ninguna hace el trabajo de otra.
 *
 *   productos   crea y edita el CATÁLOGO. No mueve stock.
 *   equipos     una fila = un equipo físico, con su serie y su IMEI.
 *   stock       cantidades de lo que se cuenta por número.
 *
 * Cada pantalla dice cuál es y a dónde ir para las otras dos, y el menú las
 * nombra distinto: dos entradas llamadas «Carga masiva» no decían cuál era cuál.
 */
import { render, screen, within } from '@testing-library/react';

import { ImportKindNotice } from '@/app/admin/components/ImportKindNotice';
import { Notices } from '@/app/admin/components/ImportWizard';
import type { ImportJob } from '@/app/admin/lib/internal-api';
import { INTERNAL_MODULES, canAccessModule, type ModuleAccessContext } from '@/app/admin/lib/internal-modules';
import { unitImportErrorsUrl, unitImportTemplateUrl } from '@/app/lib/stock-units';
import { SELECTED_COMPANY_KEY } from '@/app/lib/auth';

const hrefs = () => Object.fromEntries(screen.getAllByRole('link').map((a) => [a.textContent, a.getAttribute('href')]));

describe('cada carga dice qué es y a dónde ir para lo demás', () => {
  it('productos: catálogo, sin stock', () => {
    render(<ImportKindNotice kind="products" />);
    const notice = screen.getByRole('note', { name: 'Qué hace esta carga' });
    expect(notice).toHaveTextContent(/crea y edita productos y accesorios/i);
    expect(notice).toHaveTextContent(/No mueve stock/);
    expect(notice).toHaveTextContent(/una sola hoja, «Productos»/);
    expect(hrefs()).toEqual({
      'Carga de equipos': '/admin/inventory/units/import',
      'Carga de stock': '/admin/inventory/import',
    });
  });

  it('equipos: una fila, un equipo; el producto se crea antes', () => {
    render(<ImportKindNotice kind="units" />);
    const notice = screen.getByRole('note', { name: 'Qué hace esta carga' });
    expect(notice).toHaveTextContent(/Una fila = un equipo físico/);
    expect(notice).toHaveTextContent(/No hay columna de cantidad/);
    expect(notice).toHaveTextContent(/Serie sola, serie \+ IMEI o serie \+ dos IMEI/);
    expect(notice).toHaveTextContent(/una sola hoja, «Equipos»/);
    expect(hrefs()).toEqual({
      'Carga de productos': '/admin/products/import',
      'Carga de stock': '/admin/inventory/import',
    });
  });

  it('stock: cantidades; lo que lleva serie va por equipos', () => {
    render(<ImportKindNotice kind="stock" />);
    const notice = screen.getByRole('note', { name: 'Qué hace esta carga' });
    expect(notice).toHaveTextContent(/cantidades/);
    expect(within(notice).getByRole('link', { name: 'Carga de equipos' })).toHaveAttribute('href', '/admin/inventory/units/import');
    expect(within(notice).getByRole('link', { name: 'Carga de productos' })).toHaveAttribute('href', '/admin/products/import');
  });
});

describe('una columna de stock en un archivo de productos', () => {
  const job = (unmapped: { column: string; reason: string; important?: boolean }[]) =>
    ({ summary: { reader_notes: [], format_notes: [], unmapped } }) as unknown as ImportJob;

  it('se dice a la vista, no dentro de un desplegable', () => {
    render(<Notices job={job([
      { column: 'Stock', reason: 'Esta carga no mueve stock: sólo crea y edita productos.', important: true },
      { column: 'Marca', reason: 'No hay marcas en el catálogo todavía.' },
    ])} />);

    const said = screen.getByRole('alert');
    expect(said).toHaveTextContent('Stock');
    expect(said).toHaveTextContent('Esta carga no mueve stock');
    expect(said).not.toHaveTextContent('Marca');
    // Lo demás sigue plegado, con su cuenta.
    expect(screen.getByText('1 columna(s) reconocidas que NO se importan')).toBeInTheDocument();
  });

  it('sin columnas así no hay aviso', () => {
    render(<Notices job={job([{ column: 'Marca', reason: 'No hay marcas en el catálogo todavía.' }])} />);
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('dos columnas con la misma cabecera se dicen las dos', () => {
    const errors = jest.spyOn(console, 'error').mockImplementation(() => {});
    render(<Notices job={job([
      { column: 'Stock', reason: 'Esta carga no mueve stock: sólo crea y edita productos.', important: true },
      { column: 'Stock', reason: 'Esta carga no mueve stock: sólo crea y edita productos.', important: true },
    ])} />);
    expect(screen.getByRole('alert').querySelectorAll('li')).toHaveLength(2);
    expect(errors).not.toHaveBeenCalled();                       // sin «two children with the same key»
    errors.mockRestore();
  });
});

describe('el menú las nombra distinto', () => {
  const entry = (id: string) => {
    const found = INTERNAL_MODULES.find((m) => m.id === id);
    if (!found) throw new Error(`módulo ${id} no existe`);
    return found;
  };
  const member = (capabilities: string[]): ModuleAccessContext => ({
    capabilities, legacyRole: null, isPlatformAdmin: false, hasCompanyContext: true,
  });

  it('tres entradas, tres nombres, tres rutas', () => {
    expect([entry('products.import'), entry('inventory.units.import'), entry('inventory.import')].map((m) => [m.label, m.href, m.group])).toEqual([
      ['Carga de productos', '/admin/products/import', 'products'],
      ['Carga de equipos', '/admin/inventory/units/import', 'inventory'],
      ['Carga de stock', '/admin/inventory/import', 'inventory'],
    ]);
    const labels = INTERNAL_MODULES.filter((m) => m.href).map((m) => `${m.group}/${m.label}`);
    expect(new Set(labels).size).toBe(labels.length);
    expect(INTERNAL_MODULES.filter((m) => m.label === 'Carga masiva')).toEqual([]);
  });

  it('cargar equipos pide lo mismo que el servidor: ajustar inventario', () => {
    expect(canAccessModule(entry('inventory.units.import'), member(['inventory.view']))).toBe(false);
    expect(canAccessModule(entry('inventory.units.import'), member(['inventory.adjust']))).toBe(true);
    expect(canAccessModule(entry('products.import'), member(['inventory.adjust']))).toBe(false);
  });
});

describe('la plantilla de equipos es la de la empresa elegida', () => {
  afterEach(() => window.localStorage.clear());

  it('lleva la empresa del selector, que decide qué sucursales lista', () => {
    expect(unitImportTemplateUrl()).toMatch(/\/admin\/inventory\/units\/import\/template\/$/);
    window.localStorage.setItem(SELECTED_COMPANY_KEY, '7');
    expect(unitImportTemplateUrl()).toMatch(/\/admin\/inventory\/units\/import\/template\/\?company=7$/);
  });

  it('y el archivo de errores de un trabajo, también', () => {
    expect(unitImportErrorsUrl(31)).toMatch(/\/admin\/imports\/31\/errors\.csv\/$/);
    window.localStorage.setItem(SELECTED_COMPANY_KEY, '7');
    expect(unitImportErrorsUrl(31)).toMatch(/\/admin\/imports\/31\/errors\.csv\/\?company=7$/);
  });
});
