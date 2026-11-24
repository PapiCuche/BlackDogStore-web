/**
 * NAV-SALES-01 (ampliación) — el objetivo del propietario para Productos e Inventario.
 *
 *   PRODUCTOS   Modelos · Variantes · Colores · Fotografías (y, una sola vez, Registrar artículo, Publicación,
 *               Categorías)
 *   INVENTARIO  Stock y equipos: Equipos nuevos · Equipos seminuevos · Carga de stock (y Carga de equipos, que
 *               salen de «Más herramientas») · Kardex. Transferencias, una sola vez, en su propio destino.
 *
 * «Colores» es la sección «Atributos y opciones» de Modelos (donde viven los colores con su muestra), abierta con
 * `?seccion=colores`. «Variantes» es el listado de productos: cada variante de un modelo es un producto.
 */
import { render, screen, waitFor } from '@testing-library/react';

import { CatalogModelsManager } from '@/app/admin/components/CatalogModelsManager';
import { TOOL_GROUPS } from '@/app/admin/components/InventoryToolsHub';
import { modelsAnchorFor } from '@/app/admin/lib/models-sections';
import {
  INTERNAL_MODULES,
  activeModuleHref,
  sectionTabsFor,
  sidebarClaimedModuleIds,
  sidebarHrefs,
  sidebarSections,
  type ModuleAccessContext,
} from '@/app/admin/lib/internal-modules';
import { fetchWithAuth } from '@/app/lib/auth';

import { reply } from './support/device-commerce-fixtures';

jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
const send = fetchWithAuth as jest.MockedFunction<typeof fetchWithAuth>;

const member = (capabilities: string[]): ModuleAccessContext => ({
  capabilities, legacyRole: null, isPlatformAdmin: false, hasCompanyContext: true,
});
const master: ModuleAccessContext = { capabilities: [], legacyRole: null, isPlatformAdmin: true, hasCompanyContext: true };
const section = (ctx: ModuleAccessContext, id: string) => sidebarSections(ctx).find((s) => s.id === id);
const item = (ctx: ModuleAccessContext, sectionId: string, label: string) =>
  section(ctx, sectionId)?.items.find((i) => i.label === label);
const tabsAt = (ctx: ModuleAccessContext, path: string, search = '') => {
  const sections = sidebarSections(ctx);
  return sectionTabsFor(sections, activeModuleHref(path, search, sidebarHrefs(sections)));
};

describe('Productos: Modelos, Variantes, Colores, Fotografías', () => {
  it('el destino «Productos» abre Modelos y sus pestañas siguen el orden del propietario', () => {
    const products = item(master, 'products', 'Productos')!;
    expect(products.href).toBe('/admin/products/models');
    expect(tabsAt(master, '/admin/products/models')?.tabs.map((t) => [t.label, t.href])).toEqual([
      ['Modelos', '/admin/products/models'],
      ['Variantes', '/admin/products'],
      ['Colores', '/admin/products/models?seccion=colores'],
      ['Fotografías', '/admin/products/images'],
      ['Registrar artículo', '/admin/products/register'],
      ['Publicación e-commerce', '/admin/products/publication'],
      ['Categorías', '/admin/products/categories'],
    ]);
    expect(products.children.find((c) => c.label === 'Registrar artículo')?.caption).toBe('Registro y tienda');
  });

  it('Catálogo tiene un solo destino', () => {
    expect(section(master, 'products')?.items.map((i) => i.label)).toEqual(['Productos']);
  });

  it('cada pestaña se marca en su pantalla; una ficha de modelo se queda en Modelos', () => {
    const active = (path: string, search = '') => tabsAt(master, path, search)?.tabs.find((t) => t.active)?.label;
    expect(active('/admin/products')).toBe('Variantes');
    expect(active('/admin/products/models', '?seccion=colores')).toBe('Colores');
    expect(active('/admin/products/models')).toBe('Modelos');
    expect(active('/admin/products/images')).toBe('Fotografías');
    expect(activeModuleHref('/admin/products/models/12', '', sidebarHrefs(sidebarSections(master)))).toBe('/admin/products/models');
  });

  it('Colores pide lo mismo que Modelos', () => {
    const colours = INTERNAL_MODULES.find((m) => m.href === '/admin/products/models?seccion=colores')!;
    expect(colours.requiredCapabilities).toEqual(INTERNAL_MODULES.find((m) => m.id === 'products.models')!.requiredCapabilities);
  });

  it('sin products.view sólo queda Registrar artículo, y el destino cae en esa pantalla', () => {
    const products = item(member(['products.manage']), 'products', 'Productos')!;
    expect(products.href).toBe('/admin/products/register');
    expect(products.children).toEqual([]);
  });
});

describe('modelsAnchorFor', () => {
  it.each([
    ['seccion=colores', 'atributos'],
    ['?seccion=colores', 'atributos'],
    ['', null],
    ['seccion=otra', null],
  ])('%s → %s', (query, expected) => {
    expect(modelsAnchorFor(query)).toBe(expected);
  });
});

describe('Inventario: Equipos nuevos, Equipos seminuevos, Carga de stock, Kardex', () => {
  it('Stock y equipos reúne las cuatro, con las cargas que salieron de Más herramientas', () => {
    const stock = item(master, 'inventory', 'Stock y equipos')!;
    expect(stock.href).toBe('/admin/inventory');
    expect(stock.children.map((c) => [c.label, c.href])).toEqual([
      ['Existencias', '/admin/inventory/stock'],
      ['Equipos nuevos', '/admin/inventory/units?line=new'],
      ['Equipos seminuevos', '/admin/inventory/used'],
      ['Carga de stock', '/admin/inventory/import'],
      ['Carga de equipos', '/admin/inventory/units/import'],
      ['Kardex', '/admin/inventory/movements'],
    ]);
  });

  it('Transferencias tiene su propio destino; Más herramientas ya no lleva las cargas de stock ni de equipos', () => {
    const inv = section(master, 'inventory')!;
    expect(inv.items.map((i) => i.label)).toEqual([
      'Centro de operaciones', 'Recibir mercadería', 'Stock y equipos', 'Recuentos', 'Etiquetas', 'Transferencias', 'Más herramientas',
    ]);
    expect(item(master, 'inventory', 'Transferencias')?.href).toBe('/admin/inventory/transfers');
    const tools = item(master, 'inventory', 'Más herramientas')!;
    expect(tools.children.map((c) => c.href)).toEqual([
      '/admin/inventory/bulk', '/admin/products/import', '/admin/inventory/costs', '/admin/inventory/reports', '/admin/inventory/replenishment',
    ]);
    const hub = TOOL_GROUPS.flatMap((g) => g.tools.map((t) => t.id));
    expect(hub).not.toContain('inventory.import');
    expect(hub).not.toContain('inventory.units.import');
  });

  it('sin inventory.adjust no aparecen las cargas; con sólo inventory.adjust, sólo las cargas', () => {
    expect(item(member(['inventory.view']), 'inventory', 'Stock y equipos')!.children.map((c) => c.label)).toEqual(
      ['Existencias', 'Equipos nuevos', 'Equipos seminuevos', 'Kardex'],
    );
    const adjustOnly = item(member(['inventory.adjust']), 'inventory', 'Stock y equipos')!;
    expect(adjustOnly.href).toBe('/admin/inventory/import');
    expect(adjustOnly.children.map((c) => c.label)).toEqual(['Carga de equipos']);
  });

  it('ningún módulo se repite y ningún enlace aparece dos veces', () => {
    const claimed = sidebarClaimedModuleIds();
    expect(new Set(claimed).size).toBe(claimed.length);
    const hrefs = sidebarHrefs(sidebarSections(master));
    expect(hrefs.filter((href, at) => hrefs.indexOf(href) !== at)).toEqual([]);
  });
});

describe('«Colores» abre la sección de atributos de Modelos', () => {
  const scrolled = jest.fn();
  beforeAll(() => { Element.prototype.scrollIntoView = scrolled; });
  afterAll(() => { delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView; });
  beforeEach(() => {
    scrolled.mockReset();
    send.mockReset();
    send.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/admin/catalog/attributes/')) return reply([]);
      if (url.includes('/admin/catalog/models/')) return reply([]);
      if (url.includes('/admin/catalog/grades/')) return reply([]);
      if (url.includes('/admin/catalog/settings/')) return reply({ new_line_label: '', used_line_label: '' });
      return reply([]);
    });
  });

  it('cuando llegan los modelos, la página va a «Atributos y opciones» y el foco queda en la sección', async () => {
    render(<CatalogModelsManager canManage companyId={null} goTo={{ anchor: 'atributos', request: 1 }} />);
    const panel = document.getElementById('atributos')!;
    await waitFor(() => expect(panel).toHaveFocus());
    expect(scrolled.mock.contexts).toContain(panel);
  });

  it('una petición nueva vuelve a llevar allí; sin petición no se mueve nada', async () => {
    const view = render(<CatalogModelsManager canManage companyId={null} />);
    await screen.findByText('Todavía no hay atributos.');
    expect(scrolled).not.toHaveBeenCalled();
    view.rerender(<CatalogModelsManager canManage companyId={null} goTo={{ anchor: 'atributos', request: 2 }} />);
    await waitFor(() => expect(document.getElementById('atributos')).toHaveFocus());
  });
});
