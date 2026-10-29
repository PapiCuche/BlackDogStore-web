/**
 * COMMERCE-ADMIN-UX-01 — el árbol del propietario:
 *
 *   PRODUCTOS   Productos · Modelos y variantes · Registro / Carga masiva · Publicación e-commerce
 *   INVENTARIO  (INV-OPS-01: Centro de operaciones · Recibir mercadería ·) Resumen · Equipos nuevos · Equipos seminuevos · Stock y disponibilidad ·
 *               Costos y precios (con permiso) · Movimientos / Kardex
 *   VENTAS      Resumen comercial · Punto de venta · Consultar disponibilidad · Pedidos ·
 *               Cancelaciones / historial
 *
 * Esas entradas van primero y en ese orden; lo demás que ya existía sigue
 * después, con su ruta. Cada entrada la abre la capacidad de su pantalla. Toda
 * ruta del menú es una página que existe.
 */
import fs from 'fs';
import path from 'path';

import {
  INTERNAL_MODULES,
  activeModuleHref,
  canAccessModule,
  navigableGroups,
  type ModuleAccessContext,
} from '@/app/admin/lib/internal-modules';

const member = (capabilities: string[]): ModuleAccessContext => ({
  capabilities, legacyRole: null, isPlatformAdmin: false, hasCompanyContext: true,
});
const master: ModuleAccessContext = { capabilities: [], legacyRole: null, isPlatformAdmin: true, hasCompanyContext: true };
const mod = (id: string) => {
  const found = INTERNAL_MODULES.find((m) => m.id === id);
  if (!found) throw new Error(`módulo ${id} no existe`);
  return found;
};
const labelsOf = (ctx: ModuleAccessContext, group: string) =>
  navigableGroups(ctx).find((g) => g.group.id === group)?.modules.map((m) => m.label) ?? [];

describe('el árbol del propietario', () => {
  it('Productos', () => {
    expect(labelsOf(master, 'products').slice(0, 4)).toEqual([
      'Productos', 'Modelos y variantes', 'Registro / Carga masiva', 'Publicación e-commerce',
    ]);
  });

  it('Inventario', () => {
    expect(labelsOf(master, 'inventory').slice(0, 8)).toEqual([
      'Centro de operaciones', 'Recibir mercadería', 'Resumen', 'Equipos nuevos', 'Equipos seminuevos', 'Stock y disponibilidad', 'Costos y precios',
      'Movimientos / Kardex',
    ]);
  });

  it('Ventas', () => {
    expect(labelsOf(master, 'sales').slice(0, 5)).toEqual([
      'Resumen comercial', 'Punto de venta', 'Consultar disponibilidad', 'Pedidos', 'Cancelaciones / historial',
    ]);
  });

  it('lo que ya existía sigue en el menú, después', () => {
    for (const id of ['products.categories', 'products.suppliers', 'products.supplier_offers', 'products.import',
      'inventory.bulk', 'inventory.import', 'inventory.units.import', 'inventory.transfers', 'inventory.counts',
      'inventory.reports', 'inventory.replenishment', 'sales.promotions', 'sales.commissions']) {
      expect(mod(id).status).toBe('implemented');
    }
  });
});

describe('las entradas nuevas y sus rutas', () => {
  it('apuntan a pantallas que existen, o a las de siempre con un filtro', () => {
    expect(mod('products.register').href).toBe('/admin/products/register');
    expect(mod('products.publication').href).toBe('/admin/products/publication');
    expect(mod('inventory.availability').href).toBe('/admin/inventory/stock');
    expect(mod('inventory.serial').href).toBe('/admin/inventory/units?line=new');
    expect(mod('inventory.used').href).toBe('/admin/inventory/used');
    expect(mod('inventory.movements').href).toBe('/admin/inventory/movements');
    expect(mod('sales.cancellations').href).toBe('/admin/orders?status=cancelled');
  });

  it('toda ruta del menú es una página que existe', () => {
    const app = path.join(__dirname, '..', 'app');
    for (const m of INTERNAL_MODULES.filter((x) => x.href)) {
      const route = m.href!.split('?')[0];
      const file = path.join(app, ...route.split('/').filter(Boolean), 'page.tsx');
      expect([m.id, fs.existsSync(file)]).toEqual([m.id, true]);
    }
  });

  it('las rutas de antes siguen siendo páginas', () => {
    const app = path.join(__dirname, '..', 'app');
    for (const route of ['admin/products/new', 'admin/inventory/units', 'admin/inventory/used', 'admin/orders',
      'admin/inventory/movements', 'admin/products/models']) {
      expect([route, fs.existsSync(path.join(app, ...route.split('/'), 'page.tsx'))]).toEqual([route, true]);
    }
  });
});

describe('cada entrada la abre la capacidad de su pantalla', () => {
  it('registrar pide gestionar productos; publicar se ve con ver productos', () => {
    expect(canAccessModule(mod('products.register'), member(['products.view']))).toBe(false);
    expect(canAccessModule(mod('products.register'), member(['products.manage']))).toBe(true);
    expect(canAccessModule(mod('products.publication'), member(['products.view']))).toBe(true);
    expect(canAccessModule(mod('products.publication'), member(['inventory.view']))).toBe(false);
  });

  it('inventario: ver; los costos sólo con su permiso', () => {
    const viewer = member(['inventory.view']);
    expect(labelsOf(viewer, 'inventory')).toEqual(expect.arrayContaining([
      'Resumen', 'Equipos nuevos', 'Equipos seminuevos', 'Stock y disponibilidad', 'Movimientos / Kardex',
    ]));
    expect(labelsOf(viewer, 'inventory')).not.toContain('Costos y precios');
    expect(labelsOf(member(['costs.view']), 'inventory')).toContain('Costos y precios');
  });

  it('cancelaciones: lo mismo que los pedidos', () => {
    expect(mod('sales.cancellations').requiredCapabilities).toEqual(mod('sales.orders').requiredCapabilities);
    expect(mod('sales.cancellations').legacyRoles).toEqual(mod('sales.orders').legacyRoles);
    expect(canAccessModule(mod('sales.cancellations'), member(['sales.pos.use']))).toBe(false);
    expect(canAccessModule(mod('sales.cancellations'), member(['sales.orders.view']))).toBe(true);
  });
});

describe('qué entrada se ilumina', () => {
  const hrefs = INTERNAL_MODULES.filter((m) => m.href && m.status === 'implemented').map((m) => m.href!);

  it('una ruta con filtro gana a la misma ruta sin él, sólo si el filtro coincide', () => {
    expect(activeModuleHref('/admin/orders', '?status=cancelled', hrefs)).toBe('/admin/orders?status=cancelled');
    expect(activeModuleHref('/admin/orders', '', hrefs)).toBe('/admin/orders');
    expect(activeModuleHref('/admin/orders', '?status=paid', hrefs)).toBe('/admin/orders');
    expect(activeModuleHref('/admin/orders/12', '', hrefs)).toBe('/admin/orders');
  });

  it('las rutas de siempre siguen encendiendo su entrada', () => {
    expect(activeModuleHref('/admin/inventory/units', '', hrefs)).toBe('/admin/inventory/units?line=new');
    expect(activeModuleHref('/admin/inventory/units', '?line=new', hrefs)).toBe('/admin/inventory/units?line=new');
    expect(activeModuleHref('/admin/inventory/units/import', '', hrefs)).toBe('/admin/inventory/units/import');
    expect(activeModuleHref('/admin/inventory', '', hrefs)).toBe('/admin/inventory');
    expect(activeModuleHref('/admin/inventory/stock', '', hrefs)).toBe('/admin/inventory/stock');
    expect(activeModuleHref('/admin/products/new', '', hrefs)).toBe('/admin/products');
  });
});
