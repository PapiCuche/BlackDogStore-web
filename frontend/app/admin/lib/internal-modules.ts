/**
 * Central registry of the internal control modules — Phase 2A.2.
 *
 * THIS IS UX METADATA, NOT AUTHORISATION.
 * The backend decides what a request may do. This registry decides what the
 * sidebar bothers to show. Hiding a link protects nobody; every route behind it
 * still enforces its own permissions server-side.
 *
 * THE VISIBILITY RULE
 * -------------------
 *     visible and clickable  =  status === "implemented"
 *                               AND the caller has access
 *                               AND the module has an href
 *
 * A module that does not exist yet never becomes a dead link. Pending and
 * proposed entries exist so the Dashboard can show an honest roadmap, and they
 * are only rendered there — never in the sidebar.
 *
 * TWO ACCESS PREDICATES, ON PURPOSE
 * ---------------------------------
 * `requiredCapabilities` is the target model. `legacyRoles` is what is actually
 * true today for the modules that are still authorised through
 * UserProfile.role. Declaring only capabilities would make the sidebar show
 * links that then 403 — worse than a role check, because it looks correct.
 *
 * As each module is tenantised its `legacyRoles` disappears and its
 * `requiredCapabilities` starts governing. Products lost theirs in Phase 2B;
 * INVENTORY LOST ITS IN PHASE 2D, when stock acquired an owner and a place and
 * `inventory.*` became real authority. Orders still need theirs.
 *
 * Note what this file still cannot express: branch access. A module may be
 * reachable and every one of its screens still show nothing, because the person
 * has no branch granted. That is correct — the sidebar is not the place to
 * explain it, and the screens say so themselves.
 */

import type { IconComponent } from "../components/icons";
import {
  IconAdministration,
  IconCash,
  IconCustomers,
  IconDashboard,
  IconInventory,
  IconProducts,
  IconPurchases,
  IconReports,
  IconSales,
  IconService,
} from "../components/icons";

export type ModuleStatus = "implemented" | "partial" | "pending" | "proposed";

export type ModuleGroupId =
  | "dashboard"
  | "sales"
  | "cash"
  | "purchases"
  | "customers"
  | "products"
  | "inventory"
  | "service"
  | "reports"
  | "administration";

export type InternalModule = {
  id: string;
  group: ModuleGroupId;
  label: string;
  description: string;
  /** Absent while the module does not exist — that is what prevents dead links. */
  href?: string;
  /** Target model: capabilities from the platform catalogue. */
  requiredCapabilities?: string[];
  /** Transitional: what the endpoint actually checks today. */
  legacyRoles?: string[];
  status: ModuleStatus;
  /** Surfaced as a large card on the dashboard. */
  quickAction?: boolean;
};

export type ModuleGroup = {
  id: ModuleGroupId;
  label: string;
  icon: IconComponent;
};

export const MODULE_GROUPS: ModuleGroup[] = [
  { id: "dashboard", label: "Dashboard", icon: IconDashboard },
  { id: "sales", label: "Ventas", icon: IconSales },
  { id: "cash", label: "Caja", icon: IconCash },
  { id: "purchases", label: "Compras", icon: IconPurchases },
  { id: "customers", label: "Clientes", icon: IconCustomers },
  { id: "products", label: "Productos", icon: IconProducts },
  { id: "inventory", label: "Inventario", icon: IconInventory },
  { id: "service", label: "Servicio Técnico", icon: IconService },
  { id: "reports", label: "Reportes", icon: IconReports },
  { id: "administration", label: "Administración", icon: IconAdministration },
];

// Legacy role sets, for the modules whose endpoints still authorise by
// UserProfile.role. Inventory dropped its set in Phase 2D.
const STAFF_ROLES = ["inventory", "sales", "admin", "superadmin"];
const ADMIN_ROLES = ["admin", "superadmin"];

export const INTERNAL_MODULES: InternalModule[] = [
  // ── Dashboard ────────────────────────────────────────────────────────────
  {
    id: "dashboard",
    group: "dashboard",
    label: "Dashboard",
    description: "Contexto de la empresa y accesos rápidos.",
    href: "/admin",
    status: "implemented",
  },

  // ── Ventas ───────────────────────────────────────────────────────────────
  {
    id: "sales.orders",
    group: "sales",
    label: "Pedidos",
    description: "Pedidos del e-commerce y su estado de despacho.",
    href: "/admin/orders",
    requiredCapabilities: ["sales.orders.view"],
    legacyRoles: STAFF_ROLES,
    status: "implemented",
    quickAction: true,
  },
  {
    id: "sales.notes",
    group: "sales",
    label: "Notas de venta",
    description: "Documento interno de venta. Se emite desde el detalle del pedido.",
    requiredCapabilities: ["sales.notes.manage"],
    legacyRoles: ["sales", "admin", "superadmin"],
    status: "partial",
  },
  {
    id: "sales.summary",
    group: "sales",
    label: "Resumen comercial",
    description: "Facturación, canales, más vendidos y reposición sugerida.",
    href: "/admin/sales",
    // Phase C1: real authority, no legacy bridge. The replenishment section
    // inside additionally needs `inventory.reports` and hides itself without it.
    requiredCapabilities: ["sales.analytics.view"],
    status: "implemented",
  },
  {
    id: "sales.pos",
    group: "sales",
    label: "Punto de venta",
    description: "Venta presencial con lector de código de barras.",
    href: "/admin/sales/pos",
    requiredCapabilities: ["sales.pos.use"],
    status: "implemented",
    quickAction: true,
  },
  { id: "sales.quotes", group: "sales", label: "Cotizaciones", description: "Cotizaciones a clientes.", status: "pending" },
  {
    id: "sales.promotions",
    group: "sales",
    label: "Promociones",
    description: "Combos automáticos y códigos de descuento.",
    href: "/admin/sales/promotions",
    // Phase C1.3: giving product away is its own authority — deliberately NOT
    // implied by `products.manage`.
    requiredCapabilities: ["sales.promotions.view"],
    status: "implemented",
  },
  {
    id: "sales.commissions",
    group: "sales",
    label: "Comisiones",
    description: "Comisiones devengadas por vendedor y porcentajes del equipo.",
    href: "/admin/sales/commissions",
    // Phase C1.2: what a colleague earns is management information, so this is
    // NOT part of the sales preset. Configuring a rate needs `manage` on top,
    // checked inside the screen.
    requiredCapabilities: ["sales.commissions.view"],
    status: "implemented",
  },
  { id: "sales.receivables", group: "sales", label: "Cuentas por cobrar", description: "Saldos pendientes de cobro.", status: "pending" },

  // ── Caja ─────────────────────────────────────────────────────────────────
  { id: "cash.session", group: "cash", label: "Apertura / cierre", description: "Sesiones de caja por turno.", status: "pending" },
  { id: "cash.movements", group: "cash", label: "Ingresos / egresos", description: "Movimientos de efectivo.", status: "pending" },
  { id: "cash.petty", group: "cash", label: "Caja chica", description: "Gastos menores.", status: "pending" },

  // ── Compras ──────────────────────────────────────────────────────────────
  { id: "purchases.suppliers", group: "purchases", label: "Proveedores", description: "Directorio de proveedores.", status: "pending" },
  { id: "purchases.orders", group: "purchases", label: "Órdenes de compra", description: "Compras a proveedores.", status: "pending" },
  { id: "purchases.receipts", group: "purchases", label: "Recepciones", description: "Recepción de mercadería.", status: "pending" },

  // ── Clientes ─────────────────────────────────────────────────────────────
  {
    id: "customers.list",
    group: "customers",
    label: "Clientes",
    description: "Ficha e historial comercial de los clientes de la empresa.",
    href: "/admin/customers",
    // Phase 4: no `legacyRoles`. Customer is tenantised from the first commit,
    // so `service.customers.view` is real authority here and there is nothing
    // for a legacy-role bridge to be transitional about.
    requiredCapabilities: ["service.customers.view"],
    status: "implemented",
    quickAction: true,
  },
  { id: "customers.devices", group: "customers", label: "Equipos", description: "Equipos registrados por cliente. Fase 5.", status: "pending" },
  { id: "customers.warranties", group: "customers", label: "Garantías", description: "Garantías vigentes.", status: "pending" },
  { id: "customers.loyalty", group: "customers", label: "Fidelización", description: "Programas, reglas y premios.", status: "proposed" },

  // ── Productos ────────────────────────────────────────────────────────────
  {
    id: "products.list",
    group: "products",
    label: "Productos",
    description: "Catálogo, precios y estado de publicación.",
    href: "/admin/products",
    // Phase 2B: Product is tenantised, so `products.view` is now REAL authority
    // on this endpoint. The legacy bridge is gone for this module only —
    // orders and inventory still need theirs until their models are tenantised.
    requiredCapabilities: ["products.view"],
    status: "implemented",
    quickAction: true,
  },
  {
    id: "products.import",
    group: "products",
    label: "Carga masiva",
    description: "Importar productos desde Excel, con previsualización antes de escribir.",
    href: "/admin/products/import",
    requiredCapabilities: ["products.manage"],
    status: "implemented",
  },
  { id: "products.categories", group: "products", label: "Categorías", description: "Taxonomía del catálogo. API tenant-aware, pantalla pendiente.", status: "partial" },

  // ── Inventario ───────────────────────────────────────────────────────────
  // Phase 2D: `legacyRoles` is gone from every inventory module. Stock belongs
  // to a company and sits in a branch now, so `inventory.*` is real authority
  // rather than a label over globally shared data.
  {
    id: "inventory.stock",
    group: "inventory",
    label: "Stock",
    description: "Existencias por sucursal y alertas de reposición.",
    href: "/admin/inventory",
    requiredCapabilities: ["inventory.view"],
    status: "implemented",
    quickAction: true,
  },
  {
    id: "inventory.import",
    group: "inventory",
    label: "Carga masiva",
    description: "Ajustar existencias desde Excel. Genera movimientos de Kardex.",
    href: "/admin/inventory/import",
    requiredCapabilities: ["inventory.adjust"],
    status: "implemented",
  },
  {
    id: "inventory.movements",
    group: "inventory",
    label: "Movimientos",
    description: "Kardex por sucursal: entradas, salidas y ajustes.",
    href: "/admin/inventory/movements",
    requiredCapabilities: ["inventory.view"],
    status: "implemented",
  },
  {
    id: "inventory.reports",
    group: "inventory",
    label: "Reportes",
    description: "Bajo stock, rotación y valorización estimada.",
    href: "/admin/inventory/reports",
    requiredCapabilities: ["inventory.reports"],
    status: "implemented",
  },
  {
    id: "inventory.transfers",
    group: "inventory",
    label: "Transferencias",
    description: "Traslados de stock entre sucursales.",
    href: "/admin/inventory/transfers",
    requiredCapabilities: ["inventory.view"],
    status: "implemented",
  },
  {
    id: "inventory.counts",
    group: "inventory",
    label: "Recuentos",
    description: "Inventarios físicos y ajustes por diferencia.",
    href: "/admin/inventory/counts",
    requiredCapabilities: ["inventory.view"],
    status: "implemented",
  },
  {
    id: "inventory.replenishment",
    group: "inventory",
    label: "Reposición",
    description: "Sugerencias por mínimo y objetivo de cada sucursal.",
    href: "/admin/inventory/replenishment",
    requiredCapabilities: ["inventory.reports"],
    status: "implemented",
  },
  { id: "inventory.serial", group: "inventory", label: "Serial / IMEI", description: "Trazabilidad por unidad.", status: "pending" },

  // ── Servicio Técnico ─────────────────────────────────────────────────────
  { id: "service.intake", group: "service", label: "Recepción", description: "Ingreso de equipos a taller.", href: "/admin/service", requiredCapabilities: ["service.orders.create"], status: "implemented" },
  { id: "service.orders", group: "service", label: "Órdenes", description: "Órdenes de servicio.", href: "/admin/service", requiredCapabilities: ["service.orders.view"], status: "implemented" },
  { id: "service.diagnostic", group: "service", label: "Diagnóstico", description: "Diagnóstico y cotización.", href: "/admin/service", requiredCapabilities: ["service.diagnostic.manage"], status: "implemented" },
  { id: "service.repair", group: "service", label: "Reparación", description: "Ejecución y repuestos.", href: "/admin/service", requiredCapabilities: ["service.repair.manage"], status: "implemented" },
  { id: "service.quality", group: "service", label: "Control de calidad", description: "Verificación previa a entrega.", href: "/admin/service", requiredCapabilities: ["service.quality.manage"], status: "implemented" },
  { id: "service.delivery", group: "service", label: "Entrega", description: "Entrega al cliente.", status: "pending" },
  { id: "service.warranty", group: "service", label: "Garantías", description: "Reingresos y garantías.", status: "pending" },

  // ── Reportes ─────────────────────────────────────────────────────────────
  {
    id: "reports.inventory",
    group: "reports",
    label: "Inventario",
    description: "Reportes operativos de stock por sucursal.",
    href: "/admin/inventory/reports",
    requiredCapabilities: ["inventory.reports"],
    status: "implemented",
  },
  { id: "reports.sales", group: "reports", label: "Ventas", description: "Más vendidos e ingresos por producto.", status: "partial" },
  { id: "reports.technicians", group: "reports", label: "Técnicos", description: "Productividad de taller.", status: "pending" },
  { id: "reports.profitability", group: "reports", label: "Rentabilidad", description: "Margen por producto y periodo.", status: "pending" },

  // ── Administración ───────────────────────────────────────────────────────
  {
    id: "admin.people",
    group: "administration",
    label: "Personal",
    description: "Usuarios de la plataforma y sus roles.",
    href: "/admin/users",
    requiredCapabilities: ["memberships.view"],
    legacyRoles: ADMIN_ROLES,
    status: "implemented",
    quickAction: true,
  },
  {
    id: "admin.audit",
    group: "administration",
    label: "Auditoría",
    description: "Registro de acciones administrativas.",
    href: "/admin/audit-logs",
    legacyRoles: ADMIN_ROLES,
    status: "implemented",
    quickAction: true,
  },
  {
    id: "admin.company",
    group: "administration",
    label: "Empresa",
    description: "Identidad y datos fiscales. Se editan en Configuración.",
    href: "/admin/settings",
    requiredCapabilities: ["company.view"],
    status: "implemented",
  },
  {
    id: "admin.branches",
    group: "administration",
    label: "Sucursales",
    description: "Ubicaciones de la empresa y sucursal de despacho.",
    href: "/admin/branches",
    requiredCapabilities: ["company.view"],
    status: "implemented",
  },
  {
    id: "admin.areas",
    group: "administration",
    label: "Áreas",
    description: "Áreas internas. API lista, pantalla pendiente.",
    requiredCapabilities: ["areas.manage"],
    status: "partial",
  },
  {
    id: "admin.roles",
    group: "administration",
    label: "Roles y permisos",
    description: "Roles configurables. API lista, pantalla pendiente.",
    requiredCapabilities: ["roles.manage"],
    status: "partial",
  },
  {
    id: "admin.settings",
    group: "administration",
    label: "Configuración",
    description: "Identidad, branding, contacto y políticas de la empresa.",
    href: "/admin/settings",
    // `company.view` to reach the screen; the screen itself renders read-only
    // without `company.manage`, which the backend enforces independently.
    requiredCapabilities: ["company.view"],
    status: "implemented",
    quickAction: true,
  },
];

// ---------------------------------------------------------------------------
// Access resolution — UX only
// ---------------------------------------------------------------------------

export type ModuleAccessContext = {
  capabilities: string[];
  legacyRole: string | null;
  isPlatformAdmin: boolean;
  hasCompanyContext: boolean;
};

/**
 * Whether the caller can reach a module, as far as the UI can tell.
 *
 * Prefers capabilities when the module declares them AND company context exists;
 * otherwise falls back to the legacy role, which is what the commercial
 * endpoints still check. A platform master passes everything.
 */
export function canAccessModule(
  module: InternalModule,
  ctx: ModuleAccessContext,
): boolean {
  if (ctx.isPlatformAdmin) return true;

  if (module.requiredCapabilities?.length && ctx.hasCompanyContext) {
    const covered = module.requiredCapabilities.every((c) =>
      ctx.capabilities.includes(c),
    );
    if (covered) return true;
  }

  if (module.legacyRoles?.length && ctx.legacyRole) {
    return module.legacyRoles.includes(ctx.legacyRole);
  }

  return !module.requiredCapabilities?.length && !module.legacyRoles?.length;
}

/** Sidebar entries: implemented, reachable and actually routable. */
export function navigableModules(ctx: ModuleAccessContext): InternalModule[] {
  return INTERNAL_MODULES.filter(
    (m) => m.status === "implemented" && Boolean(m.href) && canAccessModule(m, ctx),
  );
}

export function navigableGroups(
  ctx: ModuleAccessContext,
): { group: ModuleGroup; modules: InternalModule[] }[] {
  const reachable = navigableModules(ctx);
  return MODULE_GROUPS.map((group) => ({
    group,
    modules: reachable.filter((m) => m.group === group.id && m.id !== "dashboard"),
  })).filter((entry) => entry.modules.length > 0);
}

export function quickActions(ctx: ModuleAccessContext): InternalModule[] {
  return navigableModules(ctx).filter((m) => m.quickAction);
}

/** Roadmap for the dashboard: everything, grouped, with its honest status. */
export function roadmapByGroup(): { group: ModuleGroup; modules: InternalModule[] }[] {
  return MODULE_GROUPS.filter((g) => g.id !== "dashboard").map((group) => ({
    group,
    modules: INTERNAL_MODULES.filter((m) => m.group === group.id),
  }));
}

export const STATUS_LABELS: Record<ModuleStatus, string> = {
  implemented: "Disponible",
  partial: "Parcial",
  pending: "Pendiente",
  proposed: "Propuesta",
};
