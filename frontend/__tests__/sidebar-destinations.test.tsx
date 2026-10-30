/**
 * NAV-UX-01 — el menú lateral muestra destinos, no pantallas.
 *
 * Productos tenía ocho enlaces e Inventario diecisiete, varios de ellos puertas
 * distintas a la misma tarea. Ahora el menú tiene nueve destinos entre Catálogo
 * e Inventario, y las pantallas que antes eran enlaces sueltos son sus
 * opciones. No se quita ninguna ruta ni se toca ningún permiso: el menú sólo
 * decide qué enseña.
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { InternalSidebar, MobileSidebar } from '@/app/admin/components/InternalSidebar';

import {
  INTERNAL_MODULES,
  activeModuleHref,
  isNavigable,
  navigableModules,
  sidebarClaimedModuleIds,
  sidebarHrefs,
  sidebarSections,
  type ModuleAccessContext,
} from '@/app/admin/lib/internal-modules';

let mockPathname = '/admin';
jest.mock('next/navigation', () => ({
  usePathname: () => mockPathname,
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
}));
jest.mock('@/app/components/BrandLogo', () => ({ BrandLogo: () => null }));

/* eslint-disable @typescript-eslint/no-require-imports */
const { InternalSidebarContent } = require('@/app/admin/components/InternalSidebar');
/* eslint-enable @typescript-eslint/no-require-imports */

const member = (capabilities: string[]): ModuleAccessContext => ({
  capabilities, legacyRole: null, isPlatformAdmin: false, hasCompanyContext: true,
});
const master: ModuleAccessContext = { capabilities: [], legacyRole: null, isPlatformAdmin: true, hasCompanyContext: true };

const ALL_CAPS = INTERNAL_MODULES.flatMap((m) => [
  ...(m.requiredCapabilities ?? []), ...(m.anyCapabilities ?? []),
]);
const FULL = member([...new Set(ALL_CAPS)]);

const section = (ctx: ModuleAccessContext, id: string) => sidebarSections(ctx).find((s) => s.id === id);
const labels = (ctx: ModuleAccessContext, id: string) => section(ctx, id)?.items.map((i) => i.label) ?? [];
const childLabels = (ctx: ModuleAccessContext, id: string, item: string) =>
  section(ctx, id)?.items.find((i) => i.label === item)?.children.map((c) => c.label) ?? [];

const CONTEXTS: Record<string, ModuleAccessContext> = {
  master,
  full: FULL,
  inventoryClerk: member(['inventory.view', 'inventory.adjust', 'products.view', 'inventory.labels.print']),
  viewerOnly: member(['inventory.view']),
  catalogEditor: member(['products.view', 'products.manage']),
  labelsOnly: member(['inventory.labels.print']),
  noCostsFullStock: member(['inventory.view', 'inventory.adjust', 'products.view', 'products.manage', 'inventory.reports']),
  none: member([]),
  noCompany: { capabilities: ['inventory.view', 'products.view'], legacyRole: 'sales', isPlatformAdmin: false, hasCompanyContext: false },
  legacyAdmin: { capabilities: [], legacyRole: 'admin', isPlatformAdmin: false, hasCompanyContext: false },
};

describe('el menú de Catálogo e Inventario', () => {
  it('un administrador con todo ve nueve destinos, no veinticinco enlaces', () => {
    expect(labels(FULL, 'products')).toEqual(['Productos y variantes', 'Proveedores']);
    expect(labels(FULL, 'inventory')).toEqual([
      'Centro de operaciones', 'Recibir mercadería', 'Stock y equipos', 'Recuentos',
      'Etiquetas', 'Movimientos', 'Más herramientas',
    ]);
    expect(section(FULL, 'products')?.label).toBe('Catálogo');
    expect(labels(FULL, 'products').length + labels(FULL, 'inventory').length).toBe(9);
  });

  it('cada pantalla de antes sigue a un clic de un destino', () => {
    const wasLinks = INTERNAL_MODULES.filter((m) => (m.group === 'products' || m.group === 'inventory') && m.href);
    expect(wasLinks.length).toBeGreaterThanOrEqual(24);
    const hrefs = sidebarHrefs(sidebarSections(master));
    for (const m of wasLinks) expect(hrefs).toContain(m.href);
  });

  it('el menú tiene menos de la mitad de los enlaces de primer nivel que antes', () => {
    const before = navigableModules(FULL).filter((m) => m.group === 'products' || m.group === 'inventory').length;
    const after = labels(FULL, 'products').length + labels(FULL, 'inventory').length;
    expect(before).toBe(25);
    expect(after).toBeLessThan(before / 2);
  });

  it('cada módulo de Productos e Inventario pertenece a un único destino', () => {
    const claimed = sidebarClaimedModuleIds();
    expect(new Set(claimed).size).toBe(claimed.length);
    for (const id of claimed) expect(INTERNAL_MODULES.find((m) => m.id === id)).toBeDefined();
    const unclaimed = INTERNAL_MODULES
      .filter((m) => (m.group === 'products' || m.group === 'inventory') && m.href && m.status === 'implemented')
      .filter((m) => !claimed.includes(m.id));
    expect(unclaimed.map((m) => m.id)).toEqual([]);
  });

  it('Stock y equipos reúne resumen, existencias, equipos nuevos y seminuevos', () => {
    expect(childLabels(FULL, 'inventory', 'Stock y equipos')).toEqual(
      ['Existencias', 'Equipos nuevos', 'Equipos seminuevos'],
    );
    expect(section(FULL, 'inventory')?.items.find((i) => i.label === 'Stock y equipos')?.href).toBe('/admin/inventory');
  });

  it('Productos y variantes reúne modelos, registro, publicación y categorías', () => {
    expect(childLabels(FULL, 'products', 'Productos y variantes')).toEqual(
      ['Modelos y variantes', 'Registrar artículo', 'Publicación e-commerce', 'Categorías'],
    );
  });

  it('Proveedores no mezcla el directorio con las ofertas: son dos pantallas distintas', () => {
    const item = section(FULL, 'products')?.items.find((i) => i.label === 'Proveedores');
    expect(item?.href).toBe('/admin/products/suppliers');
    expect(item?.children).toEqual([
      expect.objectContaining({ label: 'Ofertas y listas', href: '/admin/products/suppliers/offers' }),
    ]);
  });

  it('Recuentos, Movimientos y Etiquetas siguen siendo tareas separadas', () => {
    const inv = section(FULL, 'inventory')!;
    expect(inv.items.find((i) => i.label === 'Recuentos')?.href).toBe('/admin/inventory/counts');
    expect(inv.items.find((i) => i.label === 'Movimientos')?.children.map((c) => c.href)).toEqual(['/admin/inventory/transfers']);
    expect(inv.items.find((i) => i.label === 'Movimientos')?.href).toBe('/admin/inventory/movements');
    expect(inv.items.find((i) => i.label === 'Etiquetas')?.children.map((c) => c.href)).toEqual(['/admin/inventory/labels/settings']);
  });

  it('Más herramientas no tiene página propia y agrupa por tarea', () => {
    const tools = section(FULL, 'inventory')!.items.find((i) => i.label === 'Más herramientas')!;
    expect(tools.href).toBeNull();
    expect(tools.children.map((c) => c.href)).toEqual([
      '/admin/inventory/bulk', '/admin/products/import', '/admin/inventory/import',
      '/admin/inventory/units/import', '/admin/inventory/costs', '/admin/inventory/reports',
      '/admin/inventory/replenishment',
    ]);
    expect(tools.children.filter((c) => c.caption).map((c) => c.caption)).toEqual(['Cargas y exportaciones', 'Análisis']);
  });

  it('las demás secciones no cambian', () => {
    const sales = section(FULL, 'sales')!.items.map((i) => i.label);
    expect(sales.slice(0, 5)).toEqual([
      'Resumen comercial', 'Punto de venta', 'Consultar disponibilidad', 'Pedidos', 'Cancelaciones / historial',
    ]);
    expect(section(FULL, 'sales')!.items.every((i) => i.children.length === 0)).toBe(true);
  });
});

describe('permisos: el menú nunca enseña más de lo que el registro permite', () => {
  it.each(Object.keys(CONTEXTS))('%s: todo enlace del menú es un módulo al que se puede entrar', (name) => {
    const ctx = CONTEXTS[name];
    const allowed = new Set(navigableModules(ctx).map((m) => m.href));
    for (const href of sidebarHrefs(sidebarSections(ctx))) expect(allowed).toContain(href);
  });

  it.each(Object.keys(CONTEXTS))('%s: todo módulo al que se puede entrar tiene un enlace en el menú', (name) => {
    const ctx = CONTEXTS[name];
    const shown = new Set(sidebarHrefs(sidebarSections(ctx)));
    for (const m of navigableModules(ctx).filter((x) => x.id !== 'dashboard')) expect(shown).toContain(m.href);
  });

  it('sin products.manage no hay registro ni carga de productos', () => {
    const ctx = CONTEXTS.inventoryClerk;
    expect(childLabels(ctx, 'products', 'Productos y variantes')).not.toContain('Registrar artículo');
    const tools = section(ctx, 'inventory')?.items.find((i) => i.label === 'Más herramientas');
    expect(tools?.children.map((c) => c.label) ?? []).not.toContain('Carga de productos');
  });

  it('sin costs.view, Reportes y Reposición siguen bajo su encabezado «Análisis»', () => {
    const tools = section(CONTEXTS.noCostsFullStock, 'inventory')!.items.find((i) => i.label === 'Más herramientas')!;
    expect(tools.children.map((c) => [c.label, c.caption ?? ''])).toEqual([
      ['Gestión masiva', 'Cargas y exportaciones'], ['Carga de productos', ''], ['Carga de stock', ''], ['Carga de equipos', ''],
      ['Reportes', 'Análisis'], ['Reposición', ''],
    ]);
  });

  it('sin costs.view no aparece Costos y precios', () => {
    const tools = section(CONTEXTS.noCostsFullStock, 'inventory')!.items.find((i) => i.label === 'Más herramientas')!;
    const labelsOfTools = tools.children.map((c) => c.label);
    expect(labelsOfTools).not.toContain('Costos y precios');
    expect(labelsOfTools).toContain('Reportes');
  });

  it('con sólo inventory.view no se ofrece recibir mercadería', () => {
    expect(labels(CONTEXTS.viewerOnly, 'inventory')).not.toContain('Recibir mercadería');
    expect(labels(CONTEXTS.viewerOnly, 'inventory')).toContain('Stock y equipos');
  });

  it('con sólo el permiso de etiquetas se ve Etiquetas, sin su configuración', () => {
    expect(labels(CONTEXTS.labelsOnly, 'inventory')).toEqual(['Etiquetas']);
    expect(childLabels(CONTEXTS.labelsOnly, 'inventory', 'Etiquetas')).toEqual([]);
  });

  it('sin empresa resuelta no queda ningún destino del catálogo ni del inventario', () => {
    expect(section(CONTEXTS.noCompany, 'products')).toBeUndefined();
    expect(section(CONTEXTS.noCompany, 'inventory')).toBeUndefined();
  });

  it('el operador antiguo sin empresa conserva lo que su rol ya abría, y nada más', () => {
    expect(section(CONTEXTS.legacyAdmin, 'products')).toBeUndefined();
    expect(sidebarHrefs(sidebarSections(CONTEXTS.legacyAdmin)).filter((h) => h.startsWith('/admin/inventory') || h.startsWith('/admin/products')))
      .toEqual(['/admin/inventory/costs']);
  });

  it('sin permisos no hay secciones de Catálogo ni de Inventario', () => {
    expect(section(CONTEXTS.none, 'products')).toBeUndefined();
    expect(section(CONTEXTS.none, 'inventory')).toBeUndefined();
  });

  it('un destino sin su página principal cae en la primera pantalla que sí se puede abrir', () => {
    // Registrar un artículo pide sólo products.manage: sin products.view, la entrada cae en esa pantalla.
    const ctx = member(['products.manage']);
    const item = section(ctx, 'products')?.items.find((i) => i.label === 'Productos y variantes');
    expect(item?.href).toBe('/admin/products/register');
    expect(isNavigable(INTERNAL_MODULES.find((m) => m.id === 'products.register')!, ctx)).toBe(true);
  });
});

describe('qué enlace queda marcado', () => {
  const all = sidebarHrefs(sidebarSections(master));
  it.each([
    ['/admin/products', '', '/admin/products'],
    ['/admin/products/123', '', '/admin/products'],
    ['/admin/products/models', '', '/admin/products/models'],
    ['/admin/products/suppliers/offers', '', '/admin/products/suppliers/offers'],
    ['/admin/inventory', '', '/admin/inventory'],
    ['/admin/inventory/units', '?line=new', '/admin/inventory/units?line=new'],
    ['/admin/inventory/used', '', '/admin/inventory/used'],
    ['/admin/inventory/labels/settings', '', '/admin/inventory/labels/settings'],
    ['/admin/inventory/receive', '', '/admin/inventory/receive'],
    ['/admin/inventory/units', '?register=1', '/admin/inventory/units?line=new'],
    ['/admin/inventory/units/123', '', '/admin/inventory/units?line=new'],
    ['/admin/inventory/units/import', '', '/admin/inventory/units/import'],
    ['/admin/products/suppliers', '', '/admin/products/suppliers'],
  ])('%s%s marca %s', (path, search, expected) => {
    expect(activeModuleHref(path, search, all)).toBe(expected);
  });
});

describe('el menú dibujado', () => {
  const render_ = (pathname: string, ctx: ModuleAccessContext = master) => {
    mockPathname = pathname;
    return render(<InternalSidebarContent access={ctx} companyName="Empresa demo" />);
  };
  const nav = () => screen.getByRole('navigation', { name: 'Módulos del control interno' });

  it('en el dashboard todo está recogido salvo lo de uso diario', () => {
    render_('/admin');
    expect(within(nav()).getByRole('link', { name: 'Recibir mercadería' })).toBeInTheDocument();
    expect(within(nav()).getByRole('link', { name: 'Stock y equipos' })).toBeInTheDocument();
    expect(within(nav()).queryByRole('link', { name: 'Equipos seminuevos' })).toBeNull();
    expect(within(nav()).queryByRole('link', { name: 'Carga de equipos' })).toBeNull();
  });

  it('dentro de un destino se abren sus opciones y la pantalla actual queda marcada', () => {
    render_('/admin/inventory/used');
    const used = within(nav()).getByRole('link', { name: 'Equipos seminuevos' });
    expect(used).toHaveAttribute('aria-current', 'page');
    expect(within(nav()).getByRole('link', { name: 'Equipos nuevos' })).not.toHaveAttribute('aria-current');
    expect(within(nav()).getAllByRole('link').filter((a) => a.getAttribute('aria-current') === 'page')).toHaveLength(1);
  });

  it('en la página principal del destino se marca el destino', () => {
    render_('/admin/products');
    expect(within(nav()).getByRole('link', { name: 'Productos y variantes' })).toHaveAttribute('aria-current', 'page');
    expect(within(nav()).getByRole('link', { name: 'Modelos y variantes' })).not.toHaveAttribute('aria-current');
  });

  it('el botón de cada destino muestra y oculta sus opciones, con aria-expanded', () => {
    render_('/admin');
    const toggle = within(nav()).getByRole('button', { name: 'Opciones de Stock y equipos' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(toggle);
    const hide = within(nav()).getByRole('button', { name: 'Opciones de Stock y equipos' });
    expect(hide).toHaveAttribute('aria-expanded', 'true');
    expect(within(nav()).getByRole('link', { name: 'Equipos seminuevos' })).toHaveAttribute('href', '/admin/inventory/used');
    expect(hide).toHaveAttribute('aria-controls', within(nav()).getByRole('link', { name: 'Equipos seminuevos' }).closest('ul')!.id);
    fireEvent.click(hide);
    expect(within(nav()).queryByRole('link', { name: 'Equipos seminuevos' })).toBeNull();
  });

  it('Más herramientas es un botón que abre sus opciones agrupadas', () => {
    render_('/admin');
    expect(within(nav()).queryByRole('link', { name: 'Más herramientas' })).toBeNull();
    const button = within(nav()).getByRole('button', { name: 'Más herramientas' });
    expect(button).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(button);
    expect(button).toHaveAttribute('aria-expanded', 'true');
    for (const name of ['Gestión masiva', 'Carga de productos', 'Carga de stock', 'Carga de equipos', 'Costos y precios', 'Reportes', 'Reposición']) {
      expect(within(nav()).getByRole('link', { name })).toBeInTheDocument();
    }
    expect(within(nav()).getByText('Análisis')).toBeInTheDocument();
  });

  it('una importación avanzada abre Más herramientas sola', () => {
    render_('/admin/inventory/units/import');
    expect(within(nav()).getByRole('button', { name: 'Más herramientas' })).toHaveAttribute('aria-expanded', 'true');
    expect(within(nav()).getByRole('link', { name: 'Carga de equipos' })).toHaveAttribute('aria-current', 'page');
  });

  it('la barra lateral y el cajón abiertos a la vez no repiten ningún id', () => {
    mockPathname = '/admin/inventory/used';
    render(
      <>
        <InternalSidebar access={master} companyName="Empresa demo" />
        <MobileSidebar access={master} companyName="Empresa demo" open onClose={() => {}} />
      </>,
    );
    const ids = [...document.querySelectorAll('[id]')].map((el) => el.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const toggle of document.querySelectorAll('button[aria-controls]')) {
      expect(document.querySelectorAll(`[id="${toggle.getAttribute('aria-controls')}"]`).length).toBeLessThanOrEqual(1);
    }
  });

  it('cada botón de desplegar tiene al menos 44 px de alto y es alcanzable con el teclado', () => {
    render_('/admin');
    const toggle = within(nav()).getByRole('button', { name: 'Opciones de Stock y equipos' });
    expect(toggle.className).toContain('min-h-[44px]');
    expect(toggle.tabIndex).toBe(0);
  });

  it('un usuario sin permisos de inventario no ve ningún destino de inventario', () => {
    render_('/admin', member([]));
    expect(within(nav()).queryByText('Inventario')).toBeNull();
    expect(within(nav()).queryByRole('link', { name: 'Recibir mercadería' })).toBeNull();
  });
});
