/**
 * NAV-SALES-01 — Ventas y Proveedores por tarea, como ya estaban Catálogo e Inventario.
 *
 *   VENTAS     Resumen comercial · Caja (Punto de venta, Venta rápida de equipo) · Pedidos y bajo pedido (Pedidos,
 *              Bajo pedido, Cancelaciones) · Cotizaciones (sólo cuando exista) · Documentos (Notas de venta, Ventas sin
 *              comprobante) · Promociones · Comisiones
 *   COMPRAS    Proveedores (Directorio, Lista semanal, Ofertas, Disponibilidad) · Finanzas
 *   CATÁLOGO   Productos y variantes (Proveedores salió de aquí: ninguna entrada se repite)
 *
 * Sólo cambia el menú. Cada entrada la abre el mismo permiso que su pantalla, y la pantalla y el servidor siguen
 * decidiendo por su cuenta. Parte de pago no tiene entrada: la función no está aprobada.
 */
import { fireEvent, render, screen, within } from '@testing-library/react';

import { MobileSidebar } from '@/app/admin/components/InternalSidebar';
import {
  INTERNAL_MODULES,
  activeModuleHref,
  navigableModules,
  sectionTabsFor,
  sidebarClaimedModuleIds,
  sidebarHrefs,
  sidebarSections,
  type InternalModule,
  type ModuleAccessContext,
} from '@/app/admin/lib/internal-modules';

let mockPathname = '/admin';
jest.mock('next/navigation', () => ({
  usePathname: () => mockPathname,
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
}));
jest.mock('@/app/components/BrandLogo', () => ({ BrandLogo: () => null }));

const member = (capabilities: string[]): ModuleAccessContext => ({
  capabilities, legacyRole: null, isPlatformAdmin: false, hasCompanyContext: true,
});
const master: ModuleAccessContext = { capabilities: [], legacyRole: null, isPlatformAdmin: true, hasCompanyContext: true };
const ALL_CAPS = INTERNAL_MODULES.flatMap((m) => [...(m.requiredCapabilities ?? []), ...(m.anyCapabilities ?? [])]);
const FULL = member([...new Set(ALL_CAPS)]);

const section = (ctx: ModuleAccessContext, id: string) => sidebarSections(ctx).find((s) => s.id === id);
const item = (ctx: ModuleAccessContext, sectionId: string, label: string) =>
  section(ctx, sectionId)?.items.find((i) => i.label === label);
const tabsAt = (ctx: ModuleAccessContext, path: string, search = '') => {
  const sections = sidebarSections(ctx);
  return sectionTabsFor(sections, activeModuleHref(path, search, sidebarHrefs(sections)));
};

const CONTEXTS: Record<string, ModuleAccessContext> = {
  master,
  full: FULL,
  seller: member(['sales.pos.use']),
  ordersOnly: member(['sales.orders.view']),
  notesOnly: member(['sales.notes.manage']),
  catalogViewer: member(['products.view']),
  sellerAndCatalog: member(['sales.pos.use', 'products.view', 'sales.orders.view', 'sales.notes.manage']),
  none: member([]),
  legacySales: { capabilities: [], legacyRole: 'sales', isPlatformAdmin: false, hasCompanyContext: false },
  legacyAdmin: { capabilities: [], legacyRole: 'admin', isPlatformAdmin: false, hasCompanyContext: false },
};

describe('Ventas por destinos', () => {
  it('un administrador con todo ve los destinos de Ventas en el orden de la tarea', () => {
    expect(section(FULL, 'sales')?.items.map((i) => i.label)).toEqual([
      'Resumen comercial', 'Caja', 'Pedidos y bajo pedido', 'Documentos', 'Promociones', 'Comisiones',
    ]);
  });

  it('Caja: el punto de venta y la venta rápida de equipo, que abre el bloque de equipos de la misma caja', () => {
    const caja = item(FULL, 'sales', 'Caja')!;
    expect(caja.href).toBe('/admin/sales/pos');
    expect(caja.children.map((c) => [c.label, c.href])).toEqual([
      ['Venta rápida de equipo', '/admin/sales/pos?panel=equipo'],
    ]);
    // La caja es una pantalla de trabajo: sin barra de pestañas encima del escáner.
    expect(caja.tabs).toBe(false);
    expect(tabsAt(FULL, '/admin/sales/pos')).toBeNull();
  });

  it('Pedidos y bajo pedido: el listado, lo pedido al proveedor y las cancelaciones', () => {
    const orders = item(FULL, 'sales', 'Pedidos y bajo pedido')!;
    expect(orders.href).toBe('/admin/orders');
    expect(orders.children.map((c) => [c.label, c.href])).toEqual([
      ['Bajo pedido', '/admin/orders?supply=pending'],
      ['Cancelaciones', '/admin/orders?status=cancelled'],
    ]);
  });

  it('Documentos: notas de venta y ventas sin comprobante registrado', () => {
    const docs = item(FULL, 'sales', 'Documentos')!;
    expect(docs.href).toBe('/admin/sales/notes');
    expect(docs.children.map((c) => [c.label, c.href])).toEqual([
      ['Ventas sin comprobante', '/admin/orders?receipt=pending'],
    ]);
  });

  it('no hay entrada de Parte de pago', () => {
    const labels = sidebarSections(master).flatMap((s) => s.items.flatMap((i) => [i.label, ...i.children.map((c) => c.label)]));
    expect(labels.some((l) => /parte de pago/i.test(l))).toBe(false);
  });
});

describe('Cotizaciones: aparece sola cuando el módulo exista', () => {
  const quotes = () => INTERNAL_MODULES.find((m) => m.id === 'sales.quotes')!;

  it('mientras está pendiente no hay entrada, ni para el master', () => {
    expect(quotes().status).toBe('pending');
    expect(quotes().href).toBeUndefined();
    expect(item(master, 'sales', 'Cotizaciones')).toBeUndefined();
  });

  it('cuando tiene pantalla, entra entre Pedidos y Documentos, y sólo para quien tiene su permiso', () => {
    const entry = quotes();
    const original: InternalModule = { ...entry };
    Object.assign(entry, { status: 'implemented', href: '/admin/sales/quotes', requiredCapabilities: ['sales.quotes.view'] });
    try {
      expect(section(master, 'sales')?.items.map((i) => i.label)).toEqual([
        'Resumen comercial', 'Caja', 'Pedidos y bajo pedido', 'Cotizaciones', 'Documentos', 'Promociones', 'Comisiones',
      ]);
      expect(item(member(['sales.quotes.view']), 'sales', 'Cotizaciones')?.href).toBe('/admin/sales/quotes');
      expect(item(member(['sales.pos.use']), 'sales', 'Cotizaciones')).toBeUndefined();
    } finally {
      Object.keys(entry).forEach((key) => delete (entry as Record<string, unknown>)[key]);
      Object.assign(entry, original);
    }
    expect(item(master, 'sales', 'Cotizaciones')).toBeUndefined();
  });
});

describe('Proveedores sale de Catálogo', () => {
  it('Compras › Proveedores: el directorio y, como opciones, lista semanal, ofertas y disponibilidad', () => {
    const suppliers = item(FULL, 'purchases', 'Proveedores')!;
    expect(suppliers.href).toBe('/admin/products/suppliers');
    expect(suppliers.children.map((c) => [c.label, c.href])).toEqual([
      ['Lista semanal', '/admin/products/suppliers/offers/update-list'],
      ['Ofertas', '/admin/products/suppliers/offers'],
      ['Disponibilidad', '/admin/sales/availability'],
    ]);
    expect(section(FULL, 'purchases')?.items.map((i) => i.label)).toEqual(['Proveedores', 'Finanzas']);
  });

  it('Catálogo ya no tiene Proveedores, y Ventas ya no tiene Consultar disponibilidad', () => {
    expect(section(FULL, 'products')?.items.map((i) => i.label)).toEqual(['Productos y variantes']);
    const salesHrefs = sidebarHrefs([section(FULL, 'sales')!]);
    expect(salesHrefs).not.toContain('/admin/sales/availability');
    const catalogHrefs = sidebarHrefs([section(FULL, 'products')!]);
    expect(catalogHrefs.filter((h) => h.startsWith('/admin/products/suppliers'))).toEqual([]);
  });

  it('pestañas: Directorio, Lista semanal, Ofertas y Disponibilidad, con la pantalla actual marcada', () => {
    expect(tabsAt(FULL, '/admin/products/suppliers/offers/update-list')?.tabs.map((t) => [t.label, t.active])).toEqual([
      ['Directorio', false], ['Lista semanal', true], ['Ofertas', false], ['Disponibilidad', false],
    ]);
    expect(tabsAt(FULL, '/admin/sales/availability')?.label).toBe('Proveedores');
  });

  it('quien sólo vende en mostrador llega a Disponibilidad por Proveedores, sin el directorio ni las ofertas', () => {
    const suppliers = item(CONTEXTS.seller, 'purchases', 'Proveedores')!;
    expect(suppliers.href).toBe('/admin/sales/availability');
    expect(suppliers.children).toEqual([]);
  });

  it('quien sólo ve el catálogo tiene el directorio, la lista semanal y las ofertas, sin Disponibilidad', () => {
    const suppliers = item(CONTEXTS.catalogViewer, 'purchases', 'Proveedores')!;
    expect(suppliers.href).toBe('/admin/products/suppliers');
    expect(suppliers.children.map((c) => c.label)).toEqual(['Lista semanal', 'Ofertas']);
  });
});

describe('cada módulo aparece una sola vez y sólo con su permiso', () => {
  it('ningún módulo pertenece a dos destinos', () => {
    const claimed = sidebarClaimedModuleIds();
    expect(new Set(claimed).size).toBe(claimed.length);
    for (const id of claimed) expect(INTERNAL_MODULES.find((m) => m.id === id)).toBeDefined();
  });

  it('todo módulo de Ventas con pantalla tiene su destino', () => {
    const claimed = sidebarClaimedModuleIds();
    const unclaimed = INTERNAL_MODULES
      .filter((m) => (m.group === 'sales' || m.group === 'purchases') && m.href && m.status === 'implemented')
      .filter((m) => !claimed.includes(m.id));
    expect(unclaimed.map((m) => m.id)).toEqual([]);
  });

  it.each(Object.keys(CONTEXTS))('%s: ningún enlace se repite en el menú', (name) => {
    const hrefs = sidebarHrefs(sidebarSections(CONTEXTS[name]));
    expect(hrefs.filter((href, at) => hrefs.indexOf(href) !== at)).toEqual([]);
  });

  it.each(Object.keys(CONTEXTS))('%s: el menú enseña exactamente lo que se puede abrir', (name) => {
    const ctx = CONTEXTS[name];
    const allowed = navigableModules(ctx).filter((m) => m.id !== 'dashboard').map((m) => m.href!).sort();
    expect([...sidebarHrefs(sidebarSections(ctx))].sort()).toEqual(allowed);
  });

  it('todo módulo que puede llegar a ser un enlace declara quién lo abre', () => {
    const open = INTERNAL_MODULES.filter((m) => m.id !== 'dashboard' && m.href)
      .filter((m) => !m.requiredCapabilities?.length && !m.anyCapabilities?.length && !m.legacyRoles?.length && !m.platformAdminOnly);
    expect(open.map((m) => m.id)).toEqual([]);
  });

  it('cada entrada nueva pide lo mismo que su pantalla', () => {
    const mod = (id: string) => INTERNAL_MODULES.find((m) => m.id === id)!;
    const quick = INTERNAL_MODULES.find((m) => m.href === '/admin/sales/pos?panel=equipo')!;
    expect(quick.requiredCapabilities).toEqual(mod('sales.pos').requiredCapabilities);
    for (const href of ['/admin/orders?supply=pending', '/admin/orders?receipt=pending']) {
      const entry = INTERNAL_MODULES.find((m) => m.href === href)!;
      expect(entry.requiredCapabilities).toEqual(mod('sales.orders').requiredCapabilities);
      expect(entry.legacyRoles).toEqual(mod('sales.orders').legacyRoles);
    }
    const weekly = INTERNAL_MODULES.find((m) => m.href === '/admin/products/suppliers/offers/update-list')!;
    expect(weekly.requiredCapabilities).toEqual(['products.view']);
  });

  it('quien sólo vende: Caja con la venta rápida, y ni pedidos ni documentos', () => {
    expect(section(CONTEXTS.seller, 'sales')?.items.map((i) => i.label)).toEqual(['Caja']);
    expect(item(CONTEXTS.seller, 'sales', 'Caja')?.children.map((c) => c.label)).toEqual(['Venta rápida de equipo']);
  });

  it('con sólo ver pedidos, Documentos lleva a las ventas sin comprobante', () => {
    expect(section(CONTEXTS.ordersOnly, 'sales')?.items.map((i) => i.label)).toEqual(['Pedidos y bajo pedido', 'Documentos']);
    expect(item(CONTEXTS.ordersOnly, 'sales', 'Documentos')?.href).toBe('/admin/orders?receipt=pending');
  });

  it('el operador antiguo de ventas sin empresa conserva los pedidos, y nada de la caja', () => {
    const hrefs = sidebarHrefs(sidebarSections(CONTEXTS.legacySales));
    expect(hrefs).toEqual(expect.arrayContaining(['/admin/orders', '/admin/orders?supply=pending', '/admin/sales/notes']));
    expect(hrefs).not.toContain('/admin/sales/pos');
    expect(hrefs).not.toContain('/admin/sales/availability');
  });

  it('sin permisos no hay Ventas ni Compras', () => {
    expect(section(CONTEXTS.none, 'sales')).toBeUndefined();
    expect(section(CONTEXTS.none, 'purchases')).toBeUndefined();
  });
});

describe('qué entrada queda marcada', () => {
  const all = sidebarHrefs(sidebarSections(master));
  it.each([
    ['/admin/sales/pos', '', '/admin/sales/pos'],
    ['/admin/sales/pos', '?panel=equipo', '/admin/sales/pos?panel=equipo'],
    ['/admin/orders', '', '/admin/orders'],
    ['/admin/orders', '?supply=pending', '/admin/orders?supply=pending'],
    ['/admin/orders', '?supply=to_confirm', '/admin/orders'],
    ['/admin/orders', '?receipt=pending', '/admin/orders?receipt=pending'],
    ['/admin/orders/12', '', '/admin/orders'],
    ['/admin/sales/notes', '', '/admin/sales/notes'],
    ['/admin/sales/availability', '', '/admin/sales/availability'],
    ['/admin/products/suppliers/offers/update-list', '', '/admin/products/suppliers/offers/update-list'],
    ['/admin/products/suppliers/offers', '', '/admin/products/suppliers/offers'],
  ])('%s%s marca %s', (path, search, expected) => {
    expect(activeModuleHref(path, search, all)).toBe(expected);
  });

  it('Pedidos y bajo pedido lleva pestañas entre sus tres vistas', () => {
    expect(tabsAt(FULL, '/admin/orders', '?supply=pending')?.tabs.map((t) => [t.label, t.active])).toEqual([
      ['Pedidos', false], ['Bajo pedido', true], ['Cancelaciones', false],
    ]);
  });
});

describe('el cajón del móvil', () => {
  afterEach(() => window.history.pushState({}, '', '/'));

  it('en la caja abre Caja con la venta rápida y marca la pantalla actual', () => {
    mockPathname = '/admin/sales/pos';
    window.history.pushState({}, '', '/admin/sales/pos?panel=equipo');
    render(<MobileSidebar access={master} companyName="Empresa demo" open onClose={() => {}} />);
    const nav = screen.getByRole('navigation', { name: 'Módulos del control interno' });
    expect(within(nav).getByRole('button', { name: 'Opciones de Caja' })).toHaveAttribute('aria-expanded', 'true');
    expect(within(nav).getByRole('link', { name: 'Venta rápida de equipo' })).toHaveAttribute('aria-current', 'page');
    expect(within(nav).getAllByRole('link').filter((a) => a.getAttribute('aria-current') === 'page')).toHaveLength(1);
  });

  it('Pedidos y bajo pedido y Proveedores se despliegan con su botón, con aria-expanded y aria-controls', () => {
    mockPathname = '/admin';
    render(<MobileSidebar access={master} companyName="Empresa demo" open onClose={() => {}} />);
    const nav = screen.getByRole('navigation', { name: 'Módulos del control interno' });
    for (const [destination, child] of [['Pedidos y bajo pedido', 'Bajo pedido'], ['Proveedores', 'Lista semanal'], ['Documentos', 'Ventas sin comprobante']]) {
      const toggle = within(nav).getByRole('button', { name: `Opciones de ${destination}` });
      expect(toggle).toHaveAttribute('aria-expanded', 'false');
      expect(toggle.className).toContain('min-h-[44px]');
      fireEvent.click(toggle);
      expect(toggle).toHaveAttribute('aria-expanded', 'true');
      const link = within(nav).getByRole('link', { name: child });
      expect(toggle).toHaveAttribute('aria-controls', link.closest('ul')!.id);
    }
  });
});
