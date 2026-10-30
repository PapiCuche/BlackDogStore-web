/**
 * NAV-UX-02 — las pantallas relacionadas se agrupan por tarea dentro de la página:
 * barra de pestañas por destino, «Más herramientas» por tarea, «Recibir mercadería» a un clic desde Productos,
 * y «Publicado» sin prometer lo que no sabe. Sólo presentación: ninguna ruta ni permiso cambia.
 */
import { act, render, screen, within } from '@testing-library/react';

import { SectionTabs } from '@/app/admin/components/SectionTabs';
import { InventoryToolsHub, TOOL_GROUPS, visibleToolGroups } from '@/app/admin/components/InventoryToolsHub';
import { InventoryOperationsHub } from '@/app/admin/components/InventoryOperationsHub';
import { ProductsManager } from '@/app/admin/components/ProductsManager';
import { ProductVisibilityBadge } from '@/app/admin/components/ProductStatusBadge';
import { buildInternalAccess } from '@/app/admin/lib/internal-access';
import {
  INTERNAL_MODULES, canAccessModule, sectionTabsFor, sidebarHrefs, sidebarSections, activeModuleHref,
  type ModuleAccessContext,
} from '@/app/admin/lib/internal-modules';
import * as admin from '@/app/lib/admin';

let mockPathname = '/admin';
jest.mock('next/navigation', () => ({
  usePathname: () => mockPathname,
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
}));
jest.mock('@/app/lib/auth', () => ({ fetchWithAuth: jest.fn() }));
jest.mock('@/app/lib/admin', () => ({
  ...jest.requireActual('@/app/lib/admin'),
  fetchAdminProducts: jest.fn(),
  fetchAdminCategories: jest.fn(),
}));

const member = (capabilities: string[]): ModuleAccessContext => ({
  capabilities, legacyRole: null, isPlatformAdmin: false, hasCompanyContext: true,
});
const master: ModuleAccessContext = { capabilities: [], legacyRole: null, isPlatformAdmin: true, hasCompanyContext: true };

const tabsAt = (ctx: ModuleAccessContext, path: string, search = '') => {
  const sections = sidebarSections(ctx);
  return sectionTabsFor(sections, activeModuleHref(path, search, sidebarHrefs(sections)));
};

describe('barra de pestañas por destino', () => {
  it('Stock y equipos: resumen, existencias, nuevos y seminuevos, con la pantalla actual marcada', () => {
    const found = tabsAt(master, '/admin/inventory/used');
    expect(found?.label).toBe('Stock y equipos');
    expect(found?.tabs.map((t) => [t.label, t.active])).toEqual([
      ['Resumen', false], ['Existencias', false], ['Equipos nuevos', false], ['Equipos seminuevos', true],
    ]);
    expect(found?.tabs.map((t) => t.href)).toEqual([
      '/admin/inventory', '/admin/inventory/stock', '/admin/inventory/units?line=new', '/admin/inventory/used',
    ]);
  });

  it('la ficha de un equipo (?register=1 o /units/<id>) se queda en «Equipos nuevos»', () => {
    expect(tabsAt(master, '/admin/inventory/units', '?register=1')?.tabs.find((t) => t.active)?.label).toBe('Equipos nuevos');
    expect(tabsAt(master, '/admin/inventory/units/55')?.tabs.find((t) => t.active)?.label).toBe('Equipos nuevos');
  });

  it('Proveedores no mezcla el directorio con las ofertas: dos pestañas con su propio nombre', () => {
    expect(tabsAt(master, '/admin/products/suppliers/offers')?.tabs.map((t) => [t.label, t.active])).toEqual([
      ['Directorio', false], ['Ofertas y listas', true],
    ]);
  });

  it('Productos y variantes, Etiquetas y Movimientos tienen las suyas; Kardex y Transferencias no se mezclan con recibir ni contar', () => {
    expect(tabsAt(master, '/admin/products/models')?.tabs.map((t) => t.label)).toEqual(
      ['Productos', 'Modelos y variantes', 'Registrar artículo', 'Publicación e-commerce', 'Categorías'],
    );
    expect(tabsAt(master, '/admin/inventory/labels')?.tabs.map((t) => t.label)).toEqual(['Vista previa y PDF', 'Configuración']);
    expect(tabsAt(master, '/admin/inventory/transfers')?.tabs.map((t) => t.label)).toEqual(['Kardex', 'Transferencias']);
    for (const path of ['/admin/inventory/receive', '/admin/inventory/counts', '/admin/inventory/operations']) {
      expect(tabsAt(master, path)).toBeNull();
    }
  });

  it('Más herramientas no lleva pestañas: sus pantallas son tareas distintas', () => {
    for (const path of ['/admin/inventory/tools', '/admin/inventory/bulk', '/admin/inventory/costs', '/admin/inventory/units/import']) {
      expect(tabsAt(master, path)).toBeNull();
    }
  });

  it('las pestañas muestran lo que el menú muestra: sin permiso no hay pestaña, y con una sola pantalla no hay barra', () => {
    const noManage = member(['products.view']);
    expect(tabsAt(noManage, '/admin/products/models')?.tabs.map((t) => t.label)).toEqual(
      ['Productos', 'Modelos y variantes', 'Publicación e-commerce', 'Categorías'],
    );
    expect(tabsAt(member(['inventory.view']), '/admin/inventory/used')?.tabs.map((t) => t.label)).toEqual(
      ['Resumen', 'Existencias', 'Equipos nuevos', 'Equipos seminuevos'],
    );
    expect(tabsAt(member(['inventory.view']), '/admin/inventory/counts')).toBeNull();
    // Sin ver ofertas (products.view) no hay Proveedores en absoluto.
    expect(tabsAt(member(['inventory.view']), '/admin/products/suppliers')).toBeNull();
  });

  it('ninguna pestaña lleva a una pantalla que el registro niegue', () => {
    for (const ctx of [master, member(['inventory.view']), member(['products.view', 'inventory.view', 'inventory.labels.print'])]) {
      const allowed = new Set(INTERNAL_MODULES.filter((m) => m.href && canAccessModule(m, ctx)).map((m) => m.href));
      for (const path of ['/admin/inventory/used', '/admin/products/models', '/admin/inventory/labels', '/admin/inventory/movements', '/admin/products/suppliers']) {
        for (const tab of tabsAt(ctx, path)?.tabs ?? []) expect(allowed).toContain(tab.href);
      }
    }
  });

  it('se dibuja como navegación con nombre, la actual con aria-current y objetivos de 44 px', () => {
    mockPathname = '/admin/inventory/used';
    render(<SectionTabs access={master} />);
    const nav = screen.getByRole('navigation', { name: 'Secciones de Stock y equipos' });
    expect(within(nav).getAllByRole('link')).toHaveLength(4);
    expect(within(nav).getByRole('link', { name: 'Equipos seminuevos' })).toHaveAttribute('aria-current', 'page');
    expect(within(nav).getByRole('link', { name: 'Existencias' })).not.toHaveAttribute('aria-current');
    for (const link of within(nav).getAllByRole('link')) expect(link.className).toContain('min-h-11');
    expect(nav.className).toContain('overflow-x-auto'); // se desplaza dentro de su franja, no la página
  });

  it('en una pantalla sin destino con pestañas no se dibuja nada', () => {
    mockPathname = '/admin/inventory/receive';
    const { container } = render(<SectionTabs access={master} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('Más herramientas', () => {
  it('es una pantalla del registro con su ruta y todas sus herramientas están agrupadas por tarea', () => {
    expect(INTERNAL_MODULES.find((m) => m.id === 'inventory.tools')?.href).toBe('/admin/inventory/tools');
    const grouped = TOOL_GROUPS.flatMap((g) => g.tools.map((t) => t.id));
    expect(new Set(grouped).size).toBe(grouped.length);
    // Todo lo que el menú ofrece bajo «Más herramientas» está en la página.
    const tools = sidebarSections(master).find((s) => s.id === 'inventory')!.items.find((i) => i.label === 'Más herramientas')!;
    expect(tools.children.map((c) => c.id).sort()).toEqual([...grouped].sort());
  });

  it('muestra cada herramienta según el permiso y no repite ni inventa ninguna', () => {
    const labelsFor = (ctx: ModuleAccessContext) => visibleToolGroups(ctx).flatMap((g) => g.tools.map((t) => t.mod.label));
    expect(labelsFor(master)).toEqual([
      'Gestión masiva de inventario', 'Carga de productos', 'Carga de stock', 'Carga de equipos',
      'Costos y precios', 'Reportes', 'Reposición',
    ]);
    expect(labelsFor(member(['inventory.view']))).toEqual(['Gestión masiva de inventario']);
    expect(labelsFor(member(['inventory.view', 'inventory.adjust']))).toEqual([
      'Gestión masiva de inventario', 'Carga de stock', 'Carga de equipos',
    ]);
    expect(labelsFor(member(['inventory.view', 'inventory.reports']))).not.toContain('Costos y precios');
    expect(labelsFor(member([]))).toEqual([]);
  });

  it('se dibuja con un encabezado por tarea y enlaces a las pantallas de siempre', () => {
    render(<InventoryToolsHub access={master} />);
    expect(screen.getByRole('heading', { name: 'Cargar o sacar datos con Excel' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Revisar cómo va el negocio' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Reponer' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Carga de equipos/ })).toHaveAttribute('href', '/admin/inventory/units/import');
    expect(screen.getByRole('link', { name: /Gestión masiva de inventario/ })).toHaveAttribute('href', '/admin/inventory/bulk');
  });

  it('sin ninguna herramienta lo dice, sin enlaces', () => {
    render(<InventoryToolsHub access={member([])} />);
    expect(screen.getByRole('status')).toHaveTextContent(/no tiene herramientas avanzadas/);
    expect(screen.queryAllByRole('link')).toHaveLength(0);
  });
});

describe('Centro de operaciones · modo avanzado por tarea', () => {
  it('agrupa las pantallas en Consultar, Mover y contar, Cargas y Etiquetas, sin quitar ninguna', () => {
    const access = buildInternalAccess(
      { username: 'ana', role: 'customer' } as never,
      { company: { id: 7, name: 'T' }, access: { capabilities: ['inventory.view', 'inventory.adjust', 'costs.view', 'inventory.reports', 'products.manage', 'company.view'], is_platform_admin: false } } as never,
    );
    render(<InventoryOperationsHub access={access} />);
    const region = screen.getByRole('region', { name: 'Modo avanzado' });
    expect(within(region).getAllByRole('heading', { level: 3 }).map((h) => h.textContent)).toEqual([
      'Consultar', 'Mover y contar', 'Cargas con Excel, análisis y reposición', 'Etiquetas',
    ]);
    const hrefs = within(region).getAllByRole('link').map((a) => a.getAttribute('href'));
    for (const href of ['/admin/inventory', '/admin/inventory/movements', '/admin/inventory/transfers', '/admin/inventory/counts',
      '/admin/inventory/units?line=new', '/admin/inventory/used', '/admin/inventory/stock', '/admin/inventory/import',
      '/admin/inventory/units/import', '/admin/inventory/bulk', '/admin/inventory/costs', '/admin/inventory/reports',
      '/admin/inventory/replenishment', '/admin/inventory/tools']) {
      expect(hrefs).toContain(href);
    }
  });
});

describe('Productos', () => {
  const base: admin.AdminProduct = {
    id: 1, name: 'Funda', slug: 'funda', description: '', price: '59.00', inventory: 4, image_url: '', category_id: 3,
    category_name: 'Accesorios', is_active: true, is_published_online: true, is_serialized: false, publication_problems: [],
    created_at: '2026-10-13T00:00:00Z', updated_at: '2026-10-13T00:00:00Z',
  };
  const api = admin as jest.Mocked<typeof admin>;

  async function open(props: { canManage: boolean; canReceive?: boolean }) {
    api.fetchAdminProducts.mockResolvedValue({ count: 1, page: 1, page_size: 20, results: [base] });
    api.fetchAdminCategories.mockResolvedValue([] as never);
    await act(async () => { render(<ProductsManager {...props} companyId={7} />); });
    await screen.findByText('Funda');
  }

  it('«Recibir mercadería» está a un clic en la cabecera, sólo con permiso de ajustar inventario', async () => {
    await open({ canManage: true, canReceive: true });
    expect(screen.getByRole('link', { name: 'Recibir mercadería' })).toHaveAttribute('href', '/admin/inventory/receive');
    // El enlace de cada fila sigue yendo a su pantalla de siempre.
    expect(screen.getByRole('link', { name: 'Recibir mercadería de Funda' })).toHaveAttribute('href', '/admin/inventory/movements?receive=1&product=1');
  });

  it('sin poder ajustar el inventario no se ofrece, y quien sólo recibe no ve «Nuevo producto»', async () => {
    await open({ canManage: true, canReceive: false });
    expect(screen.queryByRole('link', { name: 'Recibir mercadería' })).toBeNull();
    expect(screen.getByRole('link', { name: 'Nuevo producto' })).toBeInTheDocument();
  });

  it('quien recibe pero no gestiona productos ve «Recibir mercadería» y no «Nuevo producto»', async () => {
    await open({ canManage: false, canReceive: true });
    expect(screen.getByRole('link', { name: 'Recibir mercadería' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Nuevo producto' })).toBeNull();
  });

  it('«Publicado» es un ajuste, no una promesa: un artículo inactivo lo dice', () => {
    const { rerender } = render(<ProductVisibilityBadge publishedOnline isActive />);
    expect(screen.getByText('Publicado')).toBeInTheDocument();
    expect(screen.queryByText(/la tienda no lo muestra/)).toBeNull();
    rerender(<ProductVisibilityBadge publishedOnline isActive={false} />);
    expect(screen.getByText('Publicado')).toBeInTheDocument();
    expect(screen.getByText(/Configurado, pero inactivo: la tienda no lo muestra/)).toBeInTheDocument();
    rerender(<ProductVisibilityBadge publishedOnline={false} isActive={false} />);
    expect(screen.getByText('Solo stock interno')).toBeInTheDocument();
    expect(screen.queryByText(/la tienda no lo muestra/)).toBeNull();
  });
});
