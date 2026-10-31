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
 *     visible and clickable  =  status is "implemented" or "partial"
 *                               AND the caller has access
 *                               AND the module has an href
 *
 * A module that does not exist yet never becomes a dead link: a partial module
 * carries an `href` only when its page exists (a test checks every `href`
 * against the page files). Pending and proposed entries exist so company
 * administrators can see an honest roadmap in a collapsed section of the
 * Dashboard — never in the sidebar, and never as a link.
 *
 * ONE ENTRY PER MEANING. Two entries for the same screen or the same pending
 * flow make the reader ask which one is real; each duplicate removed below
 * says where its meaning lives now.
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
  /**
   * ANY of these opens the module (INVENTORY-UNIFIED-01). For a screen that
   * gathers several things, each with its own capability: whoever may do one of
   * them has a reason to be there, and the screen shows only that part.
   */
  anyCapabilities?: string[];
  /** Transitional: what the endpoint actually checks today. */
  legacyRoles?: string[];
  /**
   * Only the platform master. No company role reaches it, whatever its
   * capabilities, and neither does the legacy operator: the module is about
   * the installation, not about a company. Checked before anything else.
   */
  platformAdminOnly?: boolean;
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
  // COMMERCE-ADMIN-UX-01 — the owner's order first: Resumen comercial · Punto de
  // venta · Consultar disponibilidad · Pedidos · Cancelaciones / historial.
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
  {
    // COMMERCE-ADMIN-UX-01: whoever sells at the counter may ask where a product
    // is. Same capability as the till, no legacy bridge (the endpoint asks for a
    // Membership, like the till does).
    id: "sales.availability",
    group: "sales",
    label: "Consultar disponibilidad",
    description: "Stock propio por sucursal, equipos y ofertas de proveedores bajo pedido.",
    href: "/admin/sales/availability",
    requiredCapabilities: ["sales.pos.use"],
    status: "implemented",
  },
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
    // COMMERCE-ADMIN-UX-01: the orders list, filtered by «cancelado» (the
    // filter itself belongs to the cancellations work). Same authority as the
    // orders list, because it IS the orders list.
    id: "sales.cancellations",
    group: "sales",
    label: "Cancelaciones / historial",
    description: "Pedidos cancelados y su historial.",
    href: "/admin/orders?status=cancelled",
    requiredCapabilities: ["sales.orders.view"],
    legacyRoles: STAFF_ROLES,
    status: "implemented",
  },
  {
    id: "sales.notes",
    group: "sales",
    label: "Notas de venta",
    description: "Documento interno de venta: consulta, anuladas y observaciones. Se emite desde el pedido o el POS.",
    href: "/admin/sales/notes",
    requiredCapabilities: ["sales.notes.manage"],
    legacyRoles: ["sales", "admin", "superadmin"],
    status: "implemented",
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
  {
    // Partial and without `href`: there is no screen of its own. The electronic
    // receipt is issued and followed from the order (and the till); the daily
    // summary and the void communications have no screen yet.
    id: "sales.invoicing",
    group: "sales",
    label: "Facturación electrónica",
    description: "Boletas y facturas se emiten y consultan desde el pedido. El Resumen Diario y las comunicaciones de baja todavía no tienen pantalla.",
    status: "partial",
  },

  // ── Caja ─────────────────────────────────────────────────────────────────
  { id: "cash.session", group: "cash", label: "Apertura / cierre", description: "Sesiones de caja por turno.", status: "pending" },
  { id: "cash.movements", group: "cash", label: "Ingresos / egresos", description: "Movimientos de efectivo.", status: "pending" },
  { id: "cash.petty", group: "cash", label: "Caja chica", description: "Gastos menores.", status: "pending" },

  // ── Compras ──────────────────────────────────────────────────────────────
  // «Proveedores» is `products.suppliers` (Productos › Proveedores): the pending
  // duplicate that used to sit here is gone.
  { id: "purchases.orders", group: "purchases", label: "Órdenes de compra", description: "Compras a proveedores.", status: "pending" },
  { id: "purchases.receipts", group: "purchases", label: "Recepciones", description: "Recepción de mercadería.", status: "pending" },
  {
    // FIN-REMIND-01 — Finanzas › Pagos fijos y vencimientos. Lives under Compras next to the payables entry below
    // (what the company must pay), not under Reportes: it is where payments are registered and reminders are kept.
    // `finance.view` opens it; recording or reversing a payment, managing the rules and editing the reminder policy
    // each have their own capability, checked inside the screen and again by the server.
    id: "finance.obligations",
    group: "purchases",
    label: "Finanzas",
    description: "Pagos fijos y vencimientos: lo que hay que pagar, cuándo, y los avisos para que nada venza sin verse.",
    href: "/admin/finance",
    requiredCapabilities: ["finance.view"],
    status: "implemented",
  },
  {
    // PAYABLES-01. Partial and without `href`: what is owed to each supplier
    // for on-order sales can be READ in Proveedores (with `costs.view`), but
    // nothing records a payment or settles it yet.
    id: "purchases.payables",
    group: "purchases",
    label: "Cuentas por pagar",
    description: "Lo pendiente de liquidar a cada proveedor se consulta en Proveedores; registrar pagos y liquidar está pendiente.",
    status: "partial",
  },

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
  {
    // Partial and without `href`: a device is registered when Servicio Técnico
    // receives it, and every service order shows its own; there is no screen
    // that lists a customer's devices.
    id: "customers.devices",
    group: "customers",
    label: "Equipos",
    description: "Los equipos se registran al recibirlos en Servicio Técnico; aún no hay una lista de equipos por cliente.",
    status: "partial",
  },
  // «Garantías» is one pending flow, `service.warranty`: the duplicate here is gone.
  { id: "customers.loyalty", group: "customers", label: "Fidelización", description: "Programas, reglas y premios.", status: "proposed" },

  // ── Productos ────────────────────────────────────────────────────────────
  // COMMERCE-ADMIN-UX-01 — the owner's order first: Productos · Modelos y
  // variantes · Registro / Carga masiva · Publicación e-commerce.
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
    // DEVICE-COMMERCE-01 · DC2: un modelo agrupa los productos que son opciones
    // de un mismo equipo. Ver con `products.view`; cambiar, `products.manage`.
    id: "products.models",
    group: "products",
    label: "Modelos y variantes",
    description: "Modelos con sus variantes, atributos, escala de estado y nombres de línea.",
    href: "/admin/products/models",
    requiredCapabilities: ["products.view"],
    status: "implemented",
  },
  {
    // COMMERCE-ADMIN-UX-01: «Registrar artículo», one screen for the four kinds,
    // with the Excel loads one click away. `products.manage`, like the endpoint;
    // devices, stock and costs are gated inside, step by step.
    id: "products.register",
    group: "products",
    label: "Registro / Carga masiva",
    description: "Registrar un equipo nuevo, un seminuevo, un accesorio o lo que vende un proveedor; o muchos desde Excel.",
    href: "/admin/products/register",
    requiredCapabilities: ["products.manage"],
    status: "implemented",
  },
  {
    // COMMERCE-ADMIN-UX-01: what the shop shows, for every kind of article and
    // for models. `products.view` to look; publishing needs `products.manage`.
    id: "products.publication",
    group: "products",
    label: "Publicación e-commerce",
    description: "Publicar u ocultar en bloque productos, variantes y modelos, con el motivo de lo que no se puede publicar.",
    href: "/admin/products/publication",
    requiredCapabilities: ["products.view"],
    status: "implemented",
  },
  {
    id: "products.categories",
    group: "products",
    label: "Categorías",
    description: "Familias del catálogo: cuáles se ofrecen, cuáles van en la portada y en qué orden.",
    href: "/admin/products/categories",
    // `products.view` para llegar; cambiar exige `products.manage`, que el
    // servidor comprueba en cada guardado.
    requiredCapabilities: ["products.view"],
    status: "implemented",
  },
  {
    // DEVICE-COMMERCE-01 · DC3: quién abastece lo que se vende bajo pedido.
    // Ver con `products.view`; cambiar, `products.manage`. Lo pendiente de
    // liquidar, sólo con `costs.view` (la pantalla lo decide).
    id: "products.suppliers",
    group: "products",
    label: "Proveedores",
    description: "Proveedores de la venta bajo pedido y lo pendiente de liquidarles.",
    href: "/admin/products/suppliers",
    requiredCapabilities: ["products.view"],
    status: "implemented",
  },
  {
    id: "products.supplier_offers",
    group: "products",
    label: "Ofertas de proveedores",
    description: "Precio, cantidad confirmada, vigencia y plazo de entrega de cada oferta; carga semanal desde Excel.",
    href: "/admin/products/suppliers/offers",
    requiredCapabilities: ["products.view"],
    status: "implemented",
  },
  {
    id: "products.import",
    group: "products",
    label: "Carga de productos",
    description: "Crear y editar productos y accesorios desde Excel. No mueve stock.",
    href: "/admin/products/import",
    requiredCapabilities: ["products.manage"],
    status: "implemented",
  },

  // ── Inventario ───────────────────────────────────────────────────────────
  // Phase 2D: `legacyRoles` is gone from every inventory module. Stock belongs
  // to a company and sits in a branch now, so `inventory.*` is real authority
  // rather than a label over globally shared data.
  // COMMERCE-ADMIN-UX-01 — the owner's order first: Resumen · Equipos nuevos ·
  // Equipos seminuevos · Stock y disponibilidad · Costos y precios · Movimientos / Kardex.
  {
    // INV-OPS-01: la entrada principal del módulo. Las cinco tareas del día a
    // día (recibir, registrar un equipo, contar, etiquetar, consultar) y, en
    // «Modo avanzado», todas las pantallas de abajo. Cada tarea pide la
    // capacidad que su pantalla ya pedía; ver el centro exige `inventory.view`.
    id: "inventory.operations",
    group: "inventory",
    label: "Centro de operaciones",
    description: "Recibir mercadería, registrar equipos, contar, etiquetar y consultar stock, paso a paso.",
    href: "/admin/inventory/operations",
    requiredCapabilities: ["inventory.view"],
    status: "implemented",
  },
  {
    // INV-OPS-01: el asistente de recepción. Mueve stock: `inventory.adjust`,
    // la misma capacidad de «Registrar equipo» y de los ajustes.
    id: "inventory.receive",
    group: "inventory",
    label: "Recibir mercadería",
    description: "Asistente en cuatro pasos: procedencia, artículos, revisión y etiquetas listas.",
    href: "/admin/inventory/receive",
    // También `products.view`: el primer paso es buscar el artículo, y esa búsqueda la autoriza ese permiso.
    requiredCapabilities: ["inventory.adjust", "products.view"],
    status: "implemented",
  },
  {
    id: "inventory.stock",
    group: "inventory",
    label: "Resumen",
    description: "Existencias por sucursal, alertas de reposición y últimos movimientos.",
    href: "/admin/inventory",
    requiredCapabilities: ["inventory.view"],
    status: "implemented",
    quickAction: true,
  },
  {
    id: "inventory.serial",
    group: "inventory",
    label: "Equipos nuevos",
    description: "Equipos nuevos con número de serie e IMEI, como parte del stock.",
    // COMMERCE-ADMIN-UX-01: the same screen, opened on the new line. Without
    // the filter (old links) it still lights this entry.
    href: "/admin/inventory/units?line=new",
    // Ver exige `inventory.view`; registrar o dar de baja, `inventory.adjust`.
    // El servidor comprueba las dos en cada operación.
    requiredCapabilities: ["inventory.view"],
    status: "implemented",
  },
  {
    // DEVICE-COMMERCE-01 · DC2: la ficha pública de cada equipo seminuevo.
    // Ver con `inventory.view`; cambiarla, `inventory.adjust`.
    id: "inventory.used",
    group: "inventory",
    label: "Equipos seminuevos",
    description: "Equipos seminuevos: batería, estado, precio propio y fotos.",
    href: "/admin/inventory/used",
    requiredCapabilities: ["inventory.view"],
    status: "implemented",
  },
  {
    // COMMERCE-ADMIN-UX-01: the shelf of every branch, product by product —
    // what «Resumen» only samples. Same authority as the rest of the inventory.
    id: "inventory.availability",
    group: "inventory",
    label: "Stock y disponibilidad",
    description: "Existencias de cada producto por sucursal, con búsqueda y filtros.",
    href: "/admin/inventory/stock",
    requiredCapabilities: ["inventory.view"],
    status: "implemented",
  },
  {
    // COMMERCE-ADMIN-UX-01 · A1. El margen comercial y el valor al costo
    // existían, pero escondidos: una sección dentro de Ventas y una tarjeta
    // dentro de Stock, sin entrada propia. Un Administrador no los encontraba.
    // `legacyRoles`: el backend da `costs.view` en el puente a admin/superadmin.
    id: "inventory.costs",
    group: "inventory",
    label: "Costos y precios",
    description: "Margen comercial, inventario al costo, costo promedio por sucursal y costo de cada equipo.",
    href: "/admin/inventory/costs",
    requiredCapabilities: ["costs.view"],
    legacyRoles: ADMIN_ROLES,
    status: "implemented",
  },
  {
    id: "inventory.movements",
    group: "inventory",
    label: "Movimientos / Kardex",
    description: "Kardex por sucursal: entradas, salidas y ajustes.",
    href: "/admin/inventory/movements",
    requiredCapabilities: ["inventory.view"],
    status: "implemented",
  },
  {
    // LABEL-03: vista previa y PDF a escala real; no imprime ni marca nada.
    // La abre `inventory.labels.print` (LABEL-02); el IMEI en papel pide además
    // `inventory.labels.sensitive` y el perfil «IMEI 1» de la empresa.
    id: "inventory.labels",
    group: "inventory",
    label: "Etiquetas y códigos de barras",
    description: "Vista previa y PDF a escala real de las etiquetas de accesorios y equipos.",
    href: "/admin/inventory/labels",
    requiredCapabilities: ["inventory.labels.print"],
    status: "implemented",
  },
  {
    // LABEL-03b: el prefijo del código interno de los equipos y qué código lleva
    // su etiqueta (código interno o IMEI 1). Verla, `company.view`; cambiarla,
    // `company.manage` (el servidor responde `can_manage`).
    id: "inventory.labels.settings",
    group: "inventory",
    label: "Configuración de etiquetas",
    description: "Prefijo del código interno de los equipos y si su etiqueta lleva el IMEI 1.",
    href: "/admin/inventory/labels/settings",
    requiredCapabilities: ["company.view"],
    status: "implemented",
  },
  {
    // INVENTORY-UNIFIED-01: un centro para importar, exportar, imágenes e
    // historial. Las tres cargas conservan su entrada y su ruta; esto las reúne,
    // no las reemplaza.
    id: "inventory.bulk",
    group: "inventory",
    label: "Gestión masiva de inventario",
    description: "Importar, exportar, imágenes en lote e historial de cargas, en un solo lugar.",
    href: "/admin/inventory/bulk",
    anyCapabilities: ["products.manage", "inventory.adjust", "products.view", "inventory.view"],
    status: "implemented",
  },
  {
    id: "inventory.import",
    group: "inventory",
    label: "Carga de stock",
    description: "Ajustar cantidades desde Excel. Genera movimientos de Kardex.",
    href: "/admin/inventory/import",
    requiredCapabilities: ["inventory.adjust"],
    status: "implemented",
  },
  {
    // IMPORT-SPLIT-01: la carga de equipos tiene su propia entrada. Antes sólo
    // se llegaba desde un enlace dentro de «Equipos».
    id: "inventory.units.import",
    group: "inventory",
    label: "Carga de equipos",
    description: "Equipos con serie e IMEI desde Excel: una fila, un equipo.",
    href: "/admin/inventory/units/import",
    requiredCapabilities: ["inventory.adjust"],
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
    id: "inventory.reports",
    group: "inventory",
    label: "Reportes",
    description: "Bajo stock, rotación y valorización estimada.",
    href: "/admin/inventory/reports",
    requiredCapabilities: ["inventory.reports"],
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
  {
    // NAV-UX-02: la puerta de las herramientas avanzadas, por tarea. No es una función nueva: sólo reúne, con una
    // frase cada una, pantallas que ya existen. Entra quien tenga permiso para alguna de ellas; la página ofrece
    // cada una según la suya y cada pantalla y cada servidor deciden por su cuenta.
    id: "inventory.tools",
    group: "inventory",
    label: "Más herramientas",
    description: "Cargas desde Excel, exportaciones, costos, reportes y reposición, agrupados por tarea.",
    href: "/admin/inventory/tools",
    anyCapabilities: ["products.manage", "inventory.adjust", "inventory.view", "products.view", "inventory.reports", "costs.view"],
    status: "implemented",
  },

  // ── Servicio Técnico ─────────────────────────────────────────────────────
  // SVC-NAV-01. Each stage has its own route. They all used to point at
  // `/admin/service`, so they opened one screen and the sidebar lit all six.
  // The capability each one needs is unchanged: a route is not authority.
  { id: "service.intake", group: "service", label: "Recepción", description: "Ingreso de equipos a taller.", href: "/admin/service/intake", requiredCapabilities: ["service.orders.create"], status: "implemented" },
  { id: "service.orders", group: "service", label: "Órdenes", description: "Órdenes de servicio.", href: "/admin/service/orders", requiredCapabilities: ["service.orders.view"], status: "implemented" },
  { id: "service.diagnostic", group: "service", label: "Diagnóstico", description: "Diagnóstico y cotización.", href: "/admin/service/diagnostics", requiredCapabilities: ["service.diagnostic.manage"], status: "implemented" },
  { id: "service.repair", group: "service", label: "Reparación", description: "Ejecución y repuestos.", href: "/admin/service/repairs", requiredCapabilities: ["service.repair.manage"], status: "implemented" },
  { id: "service.quality", group: "service", label: "Control de calidad", description: "Verificación previa a entrega.", href: "/admin/service/quality", requiredCapabilities: ["service.quality.manage"], status: "implemented" },
  { id: "service.delivery", group: "service", label: "Entrega", description: "Entrega al cliente. El cobro del servicio se registra en la orden, y la entrega puede exigir el saldo pagado.", href: "/admin/service/delivery", requiredCapabilities: ["service.delivery.manage"], status: "implemented" },
  { id: "service.warranty", group: "service", label: "Garantías", description: "Garantías vigentes, reingresos y reclamos de garantía.", status: "pending" },

  // ── Reportes ─────────────────────────────────────────────────────────────
  // Two entries left this group because they already exist elsewhere, and a
  // second entry to the same screen breaks «one entry per screen»:
  //   · «Ventas» (más vendidos e ingresos por producto) is `sales.summary`,
  //     Ventas › Resumen comercial (/admin/sales).
  //   · «Rentabilidad» (margen por producto, sucursal y canal) is
  //     `inventory.costs`, Inventario › Costos y precios (/admin/inventory/costs),
  //     gated by `costs.view`. Removed rather than reclassified: reclassified, it
  //     would be a second link to the same page under another name.
  { id: "reports.technicians", group: "reports", label: "Técnicos", description: "Productividad de taller.", status: "pending" },
  {
    // ACCOUNTING-03 — Contabilidad › Centro contable. Read-only; capability only,
    // no legacy bridge (the endpoints ask `accounting.view` and nothing else).
    id: "reports.accounting",
    group: "reports",
    label: "Centro contable",
    description: "Contabilidad: operaciones, comprobantes y pendientes del período. Sólo lectura; no es una declaración.",
    href: "/admin/accounting",
    requiredCapabilities: ["accounting.view"],
    status: "implemented",
  },

  // ── Administración ───────────────────────────────────────────────────────
  {
    id: "admin.people",
    group: "administration",
    label: "Personal",
    // El lenguaje cambió con la pantalla: ya no son «usuarios de la
    // plataforma», son las personas que trabajan en esta empresa.
    description: "Trabajadores, responsabilidades y acceso por sucursal.",
    href: "/admin/staff",
    requiredCapabilities: ["memberships.view"],
    legacyRoles: ADMIN_ROLES,
    status: "implemented",
    quickAction: true,
  },
  {
    id: "admin.areas",
    group: "administration",
    label: "Áreas",
    description: "Cómo se organiza el equipo. Los permisos los da el rol.",
    href: "/admin/areas",
    requiredCapabilities: ["areas.manage"],
    legacyRoles: ADMIN_ROLES,
    status: "implemented",
  },
  {
    id: "admin.audit",
    group: "administration",
    label: "Auditoría",
    description: "Registro de acciones administrativas.",
    href: "/admin/audit-logs",
    // Lo que piden su página y el servidor. Sin rol antiguo: el puente legacy
    // no abre esta pantalla.
    requiredCapabilities: ["memberships.view"],
    status: "implemented",
    quickAction: true,
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
    id: "admin.roles",
    group: "administration",
    label: "Roles y permisos",
    description: "Roles y capacidades configurables por empresa.",
    href: "/admin/roles",
    requiredCapabilities: ["roles.manage"],
    status: "implemented",
  },
  {
    id: "admin.communications",
    group: "administration",
    label: "Comunicados",
    description: "Mensajes internos para el personal de la empresa.",
    // CON `href`, y ese es el punto. En M12B se envió una consola sin él y
    // quedó inalcanzable: registrada, descrita y sin forma de llegar.
    href: "/admin/communications",
    requiredCapabilities: ["communications.manage"],
    status: "implemented",
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
  {
    id: "admin.printing",
    group: "administration",
    label: "Impresoras",
    description: "Térmicas de cada local, agentes y tickets recientes.",
    href: "/admin/settings/printing",
    // Configurar exige `company.manage`; el servidor lo aplica. Con
    // `company.view` se llega y se ve la cola.
    requiredCapabilities: ["company.view"],
    status: "implemented",
  },
  {
    id: "admin.documents",
    group: "administration",
    label: "Documentos",
    description: "Vista previa de los documentos impresos de la empresa, con datos de muestra.",
    href: "/admin/settings/documents",
    // La misma capacidad que exige el servidor para dibujar la muestra
    // (`company.manage`): una pantalla que enseña cómo quedará un cambio no
    // debe ser más ancha que la que lo hace.
    requiredCapabilities: ["company.manage"],
    status: "implemented",
  },
  {
    id: "admin.messaging",
    group: "administration",
    label: "Mensajería",
    description: "Avisos al cliente por WhatsApp: plantillas, activación y estado.",
    href: "/admin/settings/messaging",
    // `settings.view` para llegar; cambiar exige `settings.manage`, que el
    // servidor comprueba. Las credenciales no se gestionan desde aquí.
    requiredCapabilities: ["settings.view"],
    status: "implemented",
  },
  {
    id: "admin.storefront",
    group: "administration",
    label: "Escaparate",
    description: "Portada y campañas de la tienda pública.",
    // CON `href`, y por el mismo motivo que la consola de comunicados: un
    // módulo registrado sin forma de llegar es un módulo que no existe.
    href: "/admin/settings/storefront",
    // Misma autoridad que la configuración: `company.view` para llegar, y el
    // backend exige `company.manage` para escribir, con independencia de lo
    // que esta pantalla decida enseñar.
    requiredCapabilities: ["company.view"],
    status: "implemented",
  },
  {
    id: "admin.integrations",
    group: "administration",
    label: "Integraciones",
    description: "Correo, pasarela de pago, WhatsApp, Google y SUNAT: credenciales y pruebas.",
    href: "/admin/settings/integrations",
    // Sin capacidades: no es de ningún rol de empresa. El servidor responde 403
    // a quien no sea master (`IsPlatformAdmin`), con el rol que tenga.
    platformAdminOnly: true,
    status: "implemented",
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
 * The same rule each page applies through `InternalAccess.can` (RBAC-F3):
 * with a resolved company, capabilities decide and the legacy role grants
 * nothing; the legacy role only speaks for the pre-SaaS operator who has no
 * company context. A platform master passes everything, and a module marked
 * `platformAdminOnly` lets nobody else in.
 *
 * The menu used to fall back to the legacy role whenever the capabilities fell
 * short, so it listed modules whose page then answered "no access".
 */
export function canAccessModule(
  module: InternalModule,
  ctx: ModuleAccessContext,
): boolean {
  if (ctx.isPlatformAdmin) return true;
  if (module.platformAdminOnly) return false;

  const required = module.requiredCapabilities ?? [];
  const anyOf = module.anyCapabilities ?? [];
  const legacyRoles = module.legacyRoles ?? [];

  if (anyOf.length) {
    // Capabilities belong to a company: without one resolved there is none to
    // ask about, and no legacy role stands in for «any of these».
    return ctx.hasCompanyContext
      && required.every((c) => ctx.capabilities.includes(c))
      && anyOf.some((c) => ctx.capabilities.includes(c));
  }

  if (ctx.hasCompanyContext && required.length) {
    return required.every((c) => ctx.capabilities.includes(c));
  }

  if (!required.length && !legacyRoles.length) return true;

  return Boolean(ctx.legacyRole) && legacyRoles.includes(ctx.legacyRole as string);
}

/**
 * Whether a module is a link for this caller: it exists (implemented, or
 * partial with a page of its own), it has an `href` and the caller reaches it.
 */
export function isNavigable(module: InternalModule, ctx: ModuleAccessContext): boolean {
  return (module.status === "implemented" || module.status === "partial")
    && Boolean(module.href)
    && canAccessModule(module, ctx);
}

/** Sidebar entries: existing, reachable and actually routable. */
export function navigableModules(ctx: ModuleAccessContext): InternalModule[] {
  return INTERNAL_MODULES.filter((m) => isNavigable(m, ctx));
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

// ---------------------------------------------------------------------------
// Sidebar — NAV-UX-01
// ---------------------------------------------------------------------------
//
// The registry above lists every screen (the dashboard roadmap, the quick
// actions and the permission checks read it). The sidebar does NOT list every
// screen: Productos and Inventario had grown to twenty-odd flat links, several
// of them different doors to the same job. Here each job is one destination,
// and the screens that belong to it are its children, shown while the person
// is working inside it.
//
// This only decides what the menu shows. No route changes, no screen is
// removed, and a hidden link protects nobody: every page and endpoint still
// asks for its own permission.

export type SidebarChild = {
  id: string;
  label: string;
  href: string;
  /** Small heading above this child, when it starts a new run of tasks. */
  caption?: string;
};

export type SidebarItem = {
  id: string;
  label: string;
  /** Null: a destination that opens its children instead of a page of its own. */
  href: string | null;
  children: SidebarChild[];
  /** The tab bar across the destination's screens appears on each of them. */
  tabs: boolean;
};

export type SidebarSection = {
  id: string;
  label: string;
  icon: IconComponent;
  items: SidebarItem[];
};

type DestinationMember = {
  /** Registry module this member stands for. */
  id: string;
  /** What the sidebar calls it, when the registry's name is too long or too narrow. */
  label?: string;
  caption?: string;
};

type Destination = {
  id: string;
  label: string;
  /** Without a page of its own: the entry only opens its children. */
  noPage?: boolean;
  /** Its screens are different tasks, not views of one: no tab bar across them. */
  noTabs?: boolean;
  /** The first reachable member is the destination's own page. */
  members: DestinationMember[];
};

const m = (id: string, label?: string, caption?: string): DestinationMember => ({ id, label, caption });

const SIDEBAR_DESTINATIONS: Partial<Record<ModuleGroupId, Destination[]>> = {
  products: [
    {
      id: "nav.catalog",
      label: "Productos y variantes",
      members: [
        m("products.list", "Productos"),
        m("products.models"),
        m("products.register", "Registrar artículo"),
        m("products.publication"),
        m("products.categories"),
      ],
    },
    {
      id: "nav.suppliers",
      label: "Proveedores",
      members: [m("products.suppliers", "Directorio"), m("products.supplier_offers", "Ofertas y listas")],
    },
  ],
  inventory: [
    { id: "nav.operations", label: "Centro de operaciones", members: [m("inventory.operations")] },
    { id: "nav.receive", label: "Recibir mercadería", members: [m("inventory.receive")] },
    {
      id: "nav.stock",
      label: "Stock y equipos",
      members: [
        m("inventory.stock", "Resumen"),
        m("inventory.availability", "Existencias"),
        m("inventory.serial"),
        m("inventory.used"),
      ],
    },
    { id: "nav.counts", label: "Recuentos", members: [m("inventory.counts")] },
    {
      id: "nav.labels",
      label: "Etiquetas",
      members: [m("inventory.labels", "Vista previa y PDF"), m("inventory.labels.settings", "Configuración")],
    },
    {
      id: "nav.movements",
      label: "Movimientos",
      members: [m("inventory.movements", "Kardex"), m("inventory.transfers")],
    },
    {
      id: "nav.tools",
      label: "Más herramientas",
      noTabs: true,
      members: [
        m("inventory.tools"),
        m("inventory.bulk", "Gestión masiva", "Cargas y exportaciones"),
        m("products.import", "Carga de productos", "Cargas y exportaciones"),
        m("inventory.import", undefined, "Cargas y exportaciones"),
        m("inventory.units.import", undefined, "Cargas y exportaciones"),
        m("inventory.costs", undefined, "Análisis"),
        m("inventory.reports", undefined, "Análisis"),
        m("inventory.replenishment", undefined, "Análisis"),
      ],
    },
  ],
};

/** Section names the sidebar uses where the registry's group name is not the menu's. */
const SIDEBAR_SECTION_LABELS: Partial<Record<ModuleGroupId, string>> = { products: "Catálogo" };

/** The registry modules each restructured group hands to a destination. */
export function sidebarClaimedModuleIds(): string[] {
  return Object.values(SIDEBAR_DESTINATIONS).flatMap((list) =>
    (list ?? []).flatMap((d) => d.members.map((member) => member.id)),
  );
}

function sidebarItemFor(destination: Destination, ctx: ModuleAccessContext): SidebarItem | null {
  const reachable = destination.members.flatMap((member) => {
    const found = INTERNAL_MODULES.find((x) => x.id === member.id);
    return found && isNavigable(found, ctx) ? [{ member, module: found }] : [];
  });
  if (!reachable.length) return null;

  const [first, ...rest] = reachable;
  const own = destination.noPage ? null : first;
  const kids = destination.noPage ? reachable : rest;
  return {
    id: destination.id,
    label: destination.label,
    href: own ? own.module.href! : null,
    tabs: !destination.noTabs,
    // A caption heads a run of members: it is drawn before the first REACHABLE member of that run, so a person
    // who cannot open the one that carried it still sees the heading above the rest.
    children: kids.map(({ member, module }, at) => ({
      id: module.id,
      label: member.label ?? module.label,
      href: module.href!,
      caption: member.caption && member.caption !== kids[at - 1]?.member.caption ? member.caption : undefined,
    })),
  };
}

/**
 * What the sidebar draws, in order. Restructured groups become a few
 * destinations; every other group keeps one link per screen.
 */
export function sidebarSections(ctx: ModuleAccessContext): SidebarSection[] {
  const reachable = navigableModules(ctx);
  return MODULE_GROUPS.flatMap((group) => {
    const destinations = SIDEBAR_DESTINATIONS[group.id];
    const label = SIDEBAR_SECTION_LABELS[group.id] ?? group.label;
    if (group.id === "dashboard") return [];
    const items: SidebarItem[] = destinations
      ? destinations.flatMap((d) => {
          const item = sidebarItemFor(d, ctx);
          return item ? [item] : [];
        })
      : reachable
          .filter((x) => x.group === group.id)
          .map((x) => ({ id: x.id, label: x.label, href: x.href!, children: [], tabs: false }));
    // `Más herramientas` carries products.import, which belongs to the catalogue
    // group in the registry: the inventory section may therefore be the only
    // place a catalogue-only caller finds it.
    return items.length ? [{ id: group.id, label, icon: group.icon, items }] : [];
  });
}

export type SectionTab = { id: string; label: string; href: string; active: boolean };

/**
 * NAV-UX-02 — the tab bar inside a destination: the screens of «Stock y equipos», of «Proveedores»… side by side
 * on each of them, so that nobody has to go back to the menu to move between related screens. It is the same
 * list the sidebar draws (same labels, same access), only laid out across the page.
 */
export function sectionTabsFor(
  sections: SidebarSection[],
  activeHref: string | null,
): { id: string; label: string; tabs: SectionTab[] } | null {
  if (!activeHref) return null;
  for (const section of sections) {
    for (const item of section.items) {
      if (!item.tabs || !item.children.length) continue;
      const own = item.href ? [{ id: `${item.id}.own`, label: ownTabLabel(item), href: item.href }] : [];
      const all = [...own, ...item.children.map((c) => ({ id: c.id, label: c.label, href: c.href }))];
      if (!all.some((tab) => tab.href === activeHref)) continue;
      return { id: item.id, label: item.label, tabs: all.map((tab) => ({ ...tab, active: tab.href === activeHref })) };
    }
  }
  return null;
}

/** The first tab is the destination's own page; it keeps the name the menu gave the member it stands for. */
function ownTabLabel(item: SidebarItem): string {
  for (const list of Object.values(SIDEBAR_DESTINATIONS)) {
    const destination = list?.find((d) => d.id === item.id);
    const first = destination?.members.find((member) => {
      const found = INTERNAL_MODULES.find((x) => x.id === member.id);
      return found?.href === item.href;
    });
    if (first) return first.label ?? INTERNAL_MODULES.find((x) => x.id === first.id)!.label;
  }
  return item.label;
}

/** Every link the sidebar can draw, for working out which one is active. */
export function sidebarHrefs(sections: SidebarSection[]): string[] {
  return sections.flatMap((section) =>
    section.items.flatMap((item) => [...(item.href ? [item.href] : []), ...item.children.map((c) => c.href)]),
  );
}

export function quickActions(ctx: ModuleAccessContext): InternalModule[] {
  return navigableModules(ctx).filter((m) => m.quickAction);
}

/**
 * Which of `hrefs` the sidebar lights for the page at `pathname` + `search`.
 * COMMERCE-ADMIN-UX-01.
 *
 * Some entries are the same screen opened with a filter
 * (`/admin/orders?status=cancelled`, `/admin/inventory/units?line=new`). The
 * rule: the LONGEST path that is the page or one of its parents wins; among
 * entries with that path, one whose filter the address carries beats the bare
 * path; and a filtered entry with no bare sibling still lights for the bare
 * path, so an old link to the same screen keeps its place in the menu.
 */
export function activeModuleHref(pathname: string, search: string, hrefs: string[]): string | null {
  const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  const pathOf = (href: string) => href.split("?")[0];
  const covers = (path: string) =>
    path === "/admin" ? pathname === "/admin" : pathname === path || pathname.startsWith(`${path}/`);
  const candidates = hrefs.filter((href) => covers(pathOf(href)));
  if (!candidates.length) return null;
  const longest = Math.max(...candidates.map((href) => pathOf(href).length));
  const same = candidates.filter((href) => pathOf(href).length === longest);
  const filtered = same.filter((href) => href.includes("?"));
  const bare = same.find((href) => !href.includes("?"));
  if (pathname === pathOf(same[0])) {
    const matching = filtered.find((href) => {
      const wanted = new URLSearchParams(href.split("?")[1]);
      return [...wanted.entries()].every(([key, value]) => params.get(key) === value);
    });
    if (matching) return matching;
  }
  return bare ?? filtered[0] ?? null;
}

/**
 * «En desarrollo / Próximamente»: what is not finished (partial, pending,
 * proposed), grouped, empty groups left out. What exists is in the sidebar
 * and in «Herramientas operativas», not here.
 */
export function upcomingByGroup(): { group: ModuleGroup; modules: InternalModule[] }[] {
  return MODULE_GROUPS.map((group) => ({
    group,
    modules: INTERNAL_MODULES.filter((m) => m.group === group.id && m.status !== "implemented"),
  })).filter((entry) => entry.modules.length > 0);
}

/**
 * Who sees the roadmap: whoever administers the company (`company.manage`) and
 * the platform master. A seller has nothing to do with modules that do not
 * exist yet; they only crowd the screen where the real tools are.
 */
export function seesRoadmap(ctx: ModuleAccessContext): boolean {
  return ctx.isPlatformAdmin || (ctx.hasCompanyContext && ctx.capabilities.includes("company.manage"));
}

export const STATUS_LABELS: Record<ModuleStatus, string> = {
  implemented: "Disponible",
  partial: "Parcial",
  pending: "Pendiente",
  proposed: "Propuesta",
};
