/**
 * Typed client for the internal control APIs — Phase 2A.2.
 *
 * All requests go through fetchWithAuth: session cookies + CSRF header. No
 * Bearer, no localStorage, no token handling of any kind.
 *
 * The company id may be passed to SELECT context, but it is never authority:
 * the backend re-validates it against the caller's own memberships on every
 * request. Nothing here is trusted client-side.
 */

import { fetchWithAuth } from "../../lib/auth";
import { API_BASE } from "../../lib/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type CompanySummary = {
  id: number;
  name: string;
  slug: string;
  is_active: boolean;
};

export type BranchSummary = {
  id: number;
  name: string;
};

export type MembershipSummary = {
  id: number;
  branch: BranchSummary | null;
};

/**
 * Dónde puede trabajar quien mira — H4.1.2A.
 *
 * Viene del contexto de ACCESO, no del resumen de inventario. Antes la barra
 * leía las sucursales de `inventory`, que el backend sólo construye para quien
 * tiene capacidad de inventario: un técnico con sucursal veía «Sin sucursal».
 *
 * Ids y nombres, nada más. No es autoridad: el servidor vuelve a decidir en cada
 * petición. `null` cuando todavía no hay empresa resuelta.
 */
export type BranchScope = {
  mode: "platform" | "legacy" | "all" | "selected" | "none";
  /** Dónde abre el panel. Ausente no significa «sin alcance». */
  default_branch: BranchSummary | null;
  branches: BranchSummary[];
};

export type CompanyRoleSummary = {
  id: number;
  name: string;
  slug: string;
  area: string | null;
};

export type CompanyAreaSummary = {
  id: number;
  name: string;
  slug: string;
};

export type CompanyAccess = {
  is_platform_admin: boolean;
  legacy_role: string | null;
  roles: CompanyRoleSummary[];
  areas: CompanyAreaSummary[];
  capabilities: string[];
  source?: "custom_roles" | "legacy_role";
};

/** A labelled series for a chart. Always company-scoped by the backend. */
export type SeriesPoint = { label: string; value: number };

export type OrganizationCounts = {
  active_branches: number;
  active_memberships: number;
  active_areas: number;
  active_roles: number;
  assignments_per_area: SeriesPoint[];
  assignments_per_role: SeriesPoint[];
};

export type CatalogCounts = {
  products: number;
  active_products: number;
  inactive_products: number;
  categories: number;
  products_per_category: SeriesPoint[];
};

/** Commercial KPIs. Null unless the caller holds `sales.orders.view`. */
export type SalesSnapshot = {
  today_revenue: string;
  today_orders: number;
  total_revenue: string;
  total_paid_orders: number;
  average_ticket: string;
  pending_payment: number;
  awaiting_fulfillment: number;
  revenue_trend: SeriesPoint[];
  orders_by_status: SeriesPoint[];
  /** Cierre financiero: el dinero de los cancelados, aparte de las ventas. Ausente en una respuesta anterior. */
  collections?: DashboardCollections;
};

/** Cierre financiero (2026-10-16): importes en soles, como texto con dos decimales. */
export type DashboardCollections = {
  cancelled_paid: { orders: number; amount: string };
  refunds_confirmed: string;
  refunds_pending: string;
  cancelled_held: string;
};

/**
 * Inventory KPIs — BRANCH-SCOPED. Null unless the caller holds inventory.view
 * or inventory.reports.
 *
 * `branches` names what the figures actually cover. For an operator granted two
 * of five shops these are the totals of two shops, not a filtered view of the
 * company — so the section header says so rather than overclaiming.
 *
 * `value_basis` is always "sale_price". There is no cost model in the platform,
 * so calling the figure a cost or "capital invertido" would be a number wearing
 * a name it has not earned.
 */
export type InventorySnapshot = {
  has_branch_access: boolean;
  branches: { id: number; name: string }[];
  total_units: number;
  out_of_stock_count: number;
  low_stock_count: number;
  stocked_count: number;
  inventory_value: string;
  value_basis: "sale_price";
  transfers_in_transit: number;
  pending_counts: number;
  /**
   * Devices tracked by serial that can be sold now. A SUBSET of `total_units`,
   * never more stock on top of it.
   */
  equipment_available: number;
  equipment_reserved: number;
  stock_by_branch: SeriesPoint[];
  low_stock_by_branch: SeriesPoint[];
};

export type DashboardAlert = {
  level: "info" | "warning" | "critical";
  code: string;
  title: string;
  detail: string;
};

export type InternalDashboard = {
  company: CompanySummary | null;
  membership: MembershipSummary | null;
  /** H4.1.2A: las sucursales que alcanza, independientes de inventario. */
  branch_scope: BranchScope | null;
  access: CompanyAccess;
  organization: OrganizationCounts | null;
  /** Phase 2B: per-tenant catalogue counters. Null without `products.view`. */
  catalog: CatalogCounts | null;
  /** Phase 2C: real, per-tenant sales figures. */
  sales: SalesSnapshot | null;
  /** Phase 2D: per-tenant, per-branch inventory figures. */
  inventory: InventorySnapshot | null;
  available_companies: CompanySummary[];
  requires_company_selection: boolean;
  alerts: DashboardAlert[];
};

/** Distinguishes "you have no internal access" from a transport failure. */
export class NoInternalAccessError extends Error {
  /**
   * Si el SERVIDOR dice que esta cuenta cruza el puente legacy — H4.1.2B.
   *
   * Un 403 no prueba que alguien sea legacy: lo reciben igual el operador
   * pre-SaaS que todavía cruza el puente y la persona a la que acaban de
   * revocar su membresía. Quien puede distinguirlos es el backend, así que lo
   * dice en el cuerpo y aquí no se deduce nada.
   *
   * Por defecto `false`: ante la duda, sin puente.
   */
  readonly legacyBridge: boolean;

  constructor(message = "No tienes acceso al control interno.", legacyBridge = false) {
    super(message);
    this.name = "NoInternalAccessError";
    this.legacyBridge = legacyBridge;
  }
}

// ---------------------------------------------------------------------------
// Requests
// ---------------------------------------------------------------------------

async function readDetail(res: Response, fallback: string): Promise<string> {
  const body = await res.json().catch(() => null);
  return body?.detail ? String(body.detail) : fallback;
}

export async function fetchInternalDashboard(
  companyId?: number | null,
): Promise<InternalDashboard> {
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  const res = await fetchWithAuth(`${API_BASE}/me/internal-dashboard/${qs}`);

  if (res.status === 403) {
    // El cuerpo se lee entero: además del mensaje trae si el puente legacy
    // aplica, y esa respuesta es del servidor, no una deducción del 403.
    const body = await res.json().catch(() => null);
    throw new NoInternalAccessError(
      body?.detail ? String(body.detail) : "No tienes acceso al control interno.",
      // Estrictamente `true`: cualquier otra cosa —ausente, "1", null— es «no».
      body?.legacy_bridge === true,
    );
  }
  if (res.status === 404) {
    // A company that is not yours answers exactly like one that does not exist.
    throw new Error("Empresa no encontrada o sin acceso.");
  }
  if (!res.ok) {
    throw new Error(await readDetail(res, "No se pudo cargar el control interno."));
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Membership branch access — Phase 2D
// ---------------------------------------------------------------------------
//
// The company id is a SELECTION, never authority: the backend re-validates it
// against the caller's own memberships, and every branch id in `branch_access`
// is checked against that company's own branches before anything is written.

export type MembershipBranchGrant = {
  id: number;
  name: string;
  is_active: boolean;
};

export type MembershipRow = {
  id: number;
  user: number;
  username: string;
  company: number;
  company_name: string;
  role: string;
  role_label: string;
  branch: number | null;
  branch_name: string | null;
  branch_access_mode: "all" | "selected";
  branch_access: MembershipBranchGrant[];
  is_active: boolean;
  created_at: string;
  updated_at: string;
};

export type BranchRow = {
  id: number;
  company: number;
  company_name: string;
  name: string;
  address: string;
  phone: string;
  email: string;
  is_active: boolean;
};

export async function fetchMemberships(
  companyId?: number | null,
): Promise<{ results: MembershipRow[]; count: number }> {
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  const res = await fetchWithAuth(`${API_BASE}/admin/memberships/${qs}`);
  if (res.status === 403) {
    throw new NoInternalAccessError(
      await readDetail(res, "No tienes permisos para ver el personal."),
    );
  }
  if (!res.ok) throw new Error(await readDetail(res, "No se pudo cargar el personal."));
  return res.json();
}

export async function fetchCompanyBranches(
  companyId?: number | null,
): Promise<{ results: BranchRow[]; count: number }> {
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  const res = await fetchWithAuth(`${API_BASE}/admin/branches/${qs}`);
  if (!res.ok) throw new Error(await readDetail(res, "No se pudieron cargar las sucursales."));
  return res.json();
}

/**
 * Update one membership's branch scope.
 *
 * An explicit `branch_access` list REPLACES the grants. Sending `[]` revokes
 * every branch, which in `selected` mode means the person can operate nowhere —
 * a real, intended state, and the reason "empty means all" was never on the
 * table.
 */
export async function updateMembershipBranchAccess(
  membershipId: number,
  data: {
    branch_access_mode?: "all" | "selected";
    branch_access?: number[];
    branch?: number | null;
  },
): Promise<MembershipRow> {
  const res = await fetchWithAuth(`${API_BASE}/admin/memberships/${membershipId}/`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
  if (res.status === 403) {
    throw new Error(
      await readDetail(res, "No tienes permisos para cambiar el acceso por sucursal."),
    );
  }
  if (res.status === 404) {
    throw new Error(await readDetail(res, "Sucursal no encontrada o sin acceso."));
  }
  if (!res.ok) {
    throw new Error(await readDetail(res, "No se pudo guardar el acceso por sucursal."));
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Company configuration — Phase 3
// ---------------------------------------------------------------------------

export type CompanySettingsPayload = {
  company_name: string;
  company_slug: string;
  contact_email: string;
  phone: string;
  whatsapp_number: string;
  whatsapp_link: string;
  website_url: string;
  facebook_url: string;
  instagram_url: string;
  legal_address: string;
  city: string;
  country_code: string;
  logo_url: string;
  primary_color: string;
  accent_color: string;
  background_color: string;
  surface_color: string;
  text_color: string;
  border_color: string;
  timezone: string;
  /** Read-only: checkout charges in one platform-level currency. */
  currency: string;
  warranty_policy_text: string;
  warranty_policy_url: string;
  terms_url: string;
  privacy_url: string;
  order_notification_email: string;
  /**
   * CLIENT-SEC-01: an order of this total or more gets one more reason to be
   * reviewed. Null: the company has not named an amount and the rule is off.
   */
  risk_review_amount: string | null;
  /** CLIENT-SEC-01: whether an order waiting for its review may be sent or handed over. */
  hold_orders_pending_review: boolean;
  /** INVENTORY-UNIFIED-01: whether publishing in bulk asks for a picture. Off as created. */
  publication_requires_image?: boolean;
  /** DEVICE-COMMERCE: minutes a device stays set aside in a cart (5–60). */
  device_cart_hold_minutes?: number;
  /** DEVICE-COMMERCE: minutes it stays set aside at checkout/payment (30–120). */
  device_checkout_hold_minutes?: number;
  /** DEVICE-COMMERCE: total ceiling for one hold (35–240, ≥ cart + 30). */
  device_hold_ceiling_minutes?: number;
  /** DEVICE-COMMERCE: whether a web buyer may order from the supplier although there is stock. Off as created. */
  web_on_order_over_stock?: boolean;
  /** Cierre financiero: días en que una venta POS se anula sin superior (0–365, 7 al crear). */
  pos_cancel_simple_days?: number;
  updated_at: string;
};

export type ConfigurationStatus = {
  has_settings: boolean;
  missing: { field: string; label: string }[];
  missing_count: number;
  /** Gaps that change BEHAVIOUR, not just appearance. */
  consequential: string[];
  is_complete: boolean;
};

export type CompanyConfiguration = {
  company: {
    id: number;
    name: string;
    legal_name: string;
    tax_id: string;
    slug: string;
    is_active: boolean;
  };
  settings: CompanySettingsPayload;
  fulfillment_branch: { id: number; name: string } | null;
  status: ConfigurationStatus;
  can_manage: boolean;
  /**
   * CLIENT-SEC-01: whether THIS caller may change the two order-review options
   * (`company.manage` AND `settings.manage`). Decided by the server.
   */
  can_manage_order_review?: boolean;
};

export async function fetchCompanyConfiguration(
  companyId?: number | null,
): Promise<CompanyConfiguration> {
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  const res = await fetchWithAuth(`${API_BASE}/admin/company-settings/${qs}`);
  if (res.status === 403) {
    throw new NoInternalAccessError(
      await readDetail(res, "No tienes permisos sobre la configuración."),
    );
  }
  if (res.status === 404) throw new Error("Empresa no encontrada o sin acceso.");
  if (!res.ok) {
    throw new Error(await readDetail(res, "No se pudo cargar la configuración."));
  }
  return res.json();
}

/**
 * Save configuration. Field-level errors come back as `{field: [msg]}`.
 *
 * Surfaced verbatim rather than replaced with a generic message: "el color debe
 * tener el formato #RRGGBB" tells somebody what to fix, "no se pudo guardar"
 * does not.
 */
export async function updateCompanyConfiguration(
  companyId: number | null | undefined,
  data: Record<string, string | number | boolean | null>,
): Promise<CompanyConfiguration> {
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  const res = await fetchWithAuth(`${API_BASE}/admin/company-settings/${qs}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
  if (res.ok) return res.json();

  const body = await res.json().catch(() => null);
  if (res.status === 400 && body && typeof body === "object") {
    const err = new Error("Revisa los campos marcados.") as Error & {
      fields?: Record<string, string>;
    };
    err.fields = Object.fromEntries(
      Object.entries(body as Record<string, unknown>).map(([k, v]) => [
        k,
        Array.isArray(v) ? String(v[0]) : String(v),
      ]),
    );
    throw err;
  }
  throw new Error(
    (body as { detail?: string })?.detail ?? "No se pudo guardar la configuración.",
  );
}

/**
 * Point the storefront at a branch. Separate endpoint, separate authority:
 * `PATCH /admin/companies/{pk}/` is platform-only, but WHERE THIS BUSINESS SHIPS
 * FROM is the business's own decision.
 */
export async function updateFulfillmentBranch(
  companyId: number,
  branchId: number | null,
): Promise<unknown> {
  const res = await fetchWithAuth(
    `${API_BASE}/admin/companies/${companyId}/fulfillment-branch/`,
    { method: "PATCH", body: JSON.stringify({ branch: branchId }) },
  );
  if (!res.ok) {
    throw new Error(
      await readDetail(res, "No se pudo cambiar la sucursal de despacho."),
    );
  }
  return res.json();
}

export async function updateBranch(
  branchId: number,
  data: Partial<{
    name: string;
    address: string;
    phone: string;
    email: string;
    is_active: boolean;
  }>,
): Promise<BranchRow> {
  const res = await fetchWithAuth(`${API_BASE}/admin/branches/${branchId}/`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    throw new Error(await readDetail(res, "No se pudo guardar la sucursal."));
  }
  return res.json();
}

export async function createBranch(data: {
  company: number;
  name: string;
  address?: string;
  phone?: string;
  email?: string;
}): Promise<BranchRow> {
  const res = await fetchWithAuth(`${API_BASE}/admin/branches/`, {
    method: "POST",
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    throw new Error(await readDetail(res, "No se pudo crear la sucursal."));
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Internal document sequences — Phase 2E
// ---------------------------------------------------------------------------

export type SequenceScope = "company" | "branch";

export type InternalSequenceRow = {
  id: number;
  company: number;
  branch: number | null;
  branch_name: string | null;
  document_type: string;
  document_type_label: string;
  prefix: string;
  padding: number;
  next_value: number;
  /** What the NEXT number would look like. Computed — allocates nothing. */
  preview: string;
  has_issued: boolean;
  can_edit_next_value: boolean;
  is_active: boolean;
  updated_at: string;
};

export type SequenceList = {
  scope: SequenceScope;
  /** False once the company has issued its first document. */
  can_change_scope: boolean;
  can_manage: boolean;
  results: InternalSequenceRow[];
  notice: string;
};

export async function fetchSequences(
  companyId?: number | null,
): Promise<SequenceList> {
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  const res = await fetchWithAuth(`${API_BASE}/admin/sequences/${qs}`);
  if (res.status === 403) {
    throw new NoInternalAccessError(
      await readDetail(res, "No tienes permisos sobre la numeración."),
    );
  }
  if (!res.ok) {
    throw new Error(await readDetail(res, "No se pudo cargar la numeración."));
  }
  return res.json();
}

/** Field errors come back as `{field: [msg]}` and are shown where they belong. */
async function patchWithFieldErrors<T>(
  path: string,
  body: unknown,
  fallback = "No se pudo guardar la numeración.",
  method: "PATCH" | "POST" = "PATCH",
): Promise<T> {
  const res = await fetchWithAuth(`${API_BASE}${path}`, {
    method,
    body: JSON.stringify(body),
  });
  if (res.ok) return res.json();

  const payload = await res.json().catch(() => null);
  if (res.status === 400 && payload && typeof payload === "object") {
    const err = new Error("Revisa los campos marcados.") as Error & {
      fields?: Record<string, string>;
    };
    err.fields = Object.fromEntries(
      Object.entries(payload as Record<string, unknown>).map(([k, v]) => [
        k,
        Array.isArray(v) ? String(v[0]) : String(v),
      ]),
    );
    throw err;
  }
  // 409 carries the record that already holds this identity, so the caller can
  // offer to open it instead of leaving the user at a dead end.
  if (res.status === 409 && payload && typeof payload === "object") {
    const err = new Error(
      (payload as { detail?: string })?.detail ?? "Ya existe un registro con esos datos.",
    ) as Error & { conflict?: unknown };
    err.conflict = (payload as { existing?: unknown }).existing;
    throw err;
  }
  throw new Error((payload as { detail?: string })?.detail ?? fallback);
}

export function updateSequence(
  sequenceId: number,
  data: Partial<{
    prefix: string;
    padding: number;
    next_value: number;
    is_active: boolean;
  }>,
): Promise<InternalSequenceRow> {
  return patchWithFieldErrors(`/admin/sequences/${sequenceId}/`, data);
}

export function updateSequenceScope(
  companyId: number | null | undefined,
  scope: SequenceScope,
): Promise<{ scope: SequenceScope; changed: boolean }> {
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  return patchWithFieldErrors(`/admin/sequences/scope/${qs}`, { scope });
}

/**
 * Render what a number would look like, in the browser.
 *
 * The same rule the backend uses, duplicated ON PURPOSE: a live preview that
 * asked the server would either lag behind the form or, worse, tempt somebody to
 * implement it by allocating a number and throwing it away. The authority stays
 * server-side; this is only what the operator sees while typing.
 */
export function previewNumber(prefix: string, padding: number, value: number): string {
  const digits = Math.min(Math.max(Math.trunc(padding) || 1, 1), 12);
  const n = Math.max(Math.trunc(value) || 1, 1);
  return `${prefix}${String(n).padStart(digits, "0")}`;
}

// ---------------------------------------------------------------------------
// Phase 4 — customers (internal CRM)
// ---------------------------------------------------------------------------

export type CustomerType = "person" | "business";

/** The list row. Deliberately without `notes` — see the backend serializer. */
export type CustomerRow = {
  id: number;
  display_name: string;
  has_account: boolean;
  customer_type: CustomerType;
  document_type: string;
  document_number: string;
  phone: string;
  email: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
};

export type CustomerDetail = CustomerRow & {
  first_name: string;
  last_name: string;
  business_name: string;
  document_type_label: string;
  address_line: string;
  district: string;
  city: string;
  notes: string;
  can_manage: boolean;
  summary: {
    orders_total: number;
    paid_orders: number;
    /** Decimal as a string: money does not survive a round trip through float. */
    paid_amount: string;
    last_purchase_at: string | null;
    /** Cierre financiero: pedidos cancelados (fuera de las compras) y reembolsos confirmados. */
    cancelled_orders?: number;
    refunds_confirmed?: string;
  };
  orders: CustomerOrderRow[];
  orders_truncated: boolean;
};

export type CustomerOrderRow = {
  id: number;
  created_at: string;
  paid_at: string | null;
  status: string;
  fulfillment_status: string;
  total: string;
  discount_amount: string;
  paid: boolean;
  customer_name: string;
  customer_phone: string;
  document_type: string;
  document_number: string;
  delivery_method: string;
};

export type CustomerList = {
  count: number;
  page: number;
  page_size: number;
  can_manage: boolean;
  results: CustomerRow[];
};

export type CustomerWrite = Partial<{
  customer_type: CustomerType;
  first_name: string;
  last_name: string;
  business_name: string;
  document_type: string;
  document_number: string;
  phone: string;
  email: string;
  address_line: string;
  district: string;
  city: string;
  notes: string;
  is_active: boolean;
}>;

export async function fetchCustomers(
  companyId: number | null,
  params: {
    search?: string;
    state?: "active" | "archived" | "all";
    type?: CustomerType | "";
    page?: number;
  } = {},
): Promise<CustomerList> {
  const qs = new URLSearchParams();
  if (companyId) qs.set("company", String(companyId));
  if (params.search) qs.set("search", params.search);
  if (params.state) qs.set("state", params.state);
  if (params.type) qs.set("type", params.type);
  if (params.page && params.page > 1) qs.set("page", String(params.page));

  const res = await fetchWithAuth(`${API_BASE}/admin/customers/?${qs.toString()}`);
  if (!res.ok) throw new Error(await readDetail(res, "No se pudieron cargar los clientes."));
  return res.json();
}

export async function fetchCustomer(
  customerId: number,
  companyId: number | null,
): Promise<CustomerDetail> {
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  const res = await fetchWithAuth(`${API_BASE}/admin/customers/${customerId}/${qs}`);
  if (res.status === 404) throw new Error("Cliente no encontrado.");
  if (!res.ok) throw new Error(await readDetail(res, "No se pudo cargar el cliente."));
  return res.json();
}

/**
 * Create. A 409 means the document already identifies somebody here; the error
 * carries that record so the caller can offer to open it.
 */
export function createCustomer(
  companyId: number | null,
  data: CustomerWrite,
): Promise<CustomerDetail & { possible_duplicates: CustomerRow[] }> {
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  return patchWithFieldErrors(
    `/admin/customers/${qs}`, data, "No se pudo crear el cliente.", "POST",
  );
}

export function updateCustomer(
  customerId: number,
  companyId: number | null,
  data: CustomerWrite,
): Promise<CustomerDetail> {
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  return patchWithFieldErrors(
    `/admin/customers/${customerId}/${qs}`, data, "No se pudo guardar el cliente.",
  );
}

// ---------------------------------------------------------------------------
// Commercial Phase C1 — point of sale
// ---------------------------------------------------------------------------

export type PosBranch = { id: number; name: string };
export type PosPaymentMethod = { value: string; label: string };

/**
 * Un documento que la caja puede preparar, o por qué no puede.
 *
 * `branches` son las sucursales donde SÍ hay una serie resoluble; `enabled`
 * dice si la opción sirve en alguna. Una opción deshabilitada llega explicada
 * (`disabled_reason`) sólo a quien tiene la capacidad fiscal: a quien no la
 * tiene, el backend no le manda las opciones fiscales en absoluto.
 */
export type PosReceiptOption = {
  value: string;
  label: string;
  branches: number[];
  enabled: boolean;
  /** `FISCAL_DISABLED` · `FISCAL_NOT_CONFIGURED` · `NO_SERIES_FOR_BRANCH` · `AMBIGUOUS_SERIES` · `UNSUPPORTED_ENVIRONMENT` · `NO_BRANCH`, o vacío. */
  disabled_code: string;
  disabled_reason: string;
};

export type PosContext = {
  receipt_options: PosReceiptOption[];
  company: { id: number; name: string };
  branches: PosBranch[];
  /** null when the till must ask: several branches and no authorised default. */
  default_branch: number | null;
  payment_methods: PosPaymentMethod[];
  can_manage_customers: boolean;
  /** Resolved once at open, so the UI never offers a control that then 403s. */
  can_assign_seller: boolean;
  can_apply_discount: boolean;
  can_view_commissions: boolean;
  /**
   * `sales.orders.manage`: may sell «bajo pedido» although the branch shelf
   * covers the line, giving a `supply_reason`. Absent on older servers: false.
   */
  can_order_over_stock?: boolean;
  seller: { id: number; username: string; name: string };
  /** Empty unless the caller may reassign — staffing is not public. */
  sellers: PosSeller[];
};

export type PosProduct = {
  id: number;
  name: string;
  /** Decimal as a string. Displayed, never sent back as authority. */
  price: string;
  available: number;
  barcode: string;
  /** SERIAL-PICK-01: se vende equipo por equipo; la venta debe decir cuáles. */
  is_serialized?: boolean;
  requires_imei?: boolean;
  /** DC3: la oferta vigente que lo cubre bajo pedido (plazo y precio), o null. */
  on_order?: { lead_time_days: number; price: string } | null;
};

export type PosSaleLine = {
  product: number;
  quantity: number;
  /** De cada equipo con serie, cuál es y cómo se identificó. */
  units?: { id: number; method: "scanner" | "manual" }[];
  /** DC3: la línea entera es bajo pedido; no lleva `units`, aunque el producto tenga serie. */
  on_order?: boolean;
  /** Bajo pedido aunque el estante la cubre: por qué (≤200). Sólo con `can_order_over_stock`. */
  supply_reason?: string;
};

/** Un equipo que la caja puede vender, descrito para confirmar que es ese. */
export type PosUnit = {
  unit_id: number;
  product_id: number;
  product_name: string;
  serial_number: string;
  imei: string;
  imei2: string;
  condition: string;
  condition_label: string;
  requires_imei: boolean;
};

/** El servidor no acepta ese equipo. `code` dice por qué; el mensaje, a la persona. */
export class PosUnitError extends Error {
  code: string;
  constructor(message: string, code: string) {
    super(message);
    this.name = "PosUnitError";
    this.code = code;
  }
}

/**
 * El desglose tributario de una venta. TODO son cadenas: estas cifras tienen
 * que coincidir al céntimo con el documento impreso, y un importe que pasa por
 * `number` en JavaScript deja de ser exacto.
 *
 * Lo calcula el servidor. Esta capa lo transporta y lo pinta; no lo deriva.
 */
export type TaxBreakdown = {
  currency: string;
  subtotal: string;
  discount_amount: string;
  taxable_amount: string;
  tax_amount: string;
  tax_rate: string;
  tax_treatment: "taxed" | "exempt" | "unaffected";
  total: string;
  base_label: string;
  tax_label: string;
};

export type PosSaleResult = {
  receipt_type: string;
  document_number: string;
  document_status: string;
  order_id: number;
  created: boolean;
  subtotal: string;
  discount: string;
  discount_source: string;
  discount_reason: string;
  total: string;
  /**
   * Opcional en el tipo aunque el servidor lo mande siempre: una venta ya
   * cobrada no puede quedarse sin resumen en pantalla porque falte una clave.
   */
  tax?: TaxBreakdown;
  paid_at: string | null;
  payment_method: string;
  amount_received: string | null;
  change_amount: string | null;
  payment_reference: string;
  branch: PosBranch;
  seller: string;
  customer: string;
  commission: string | null;
  items: {
    product: number; name: string; quantity: number; price: string;
    /** Los equipos que salieron en esta línea, como se identificaron. */
    units?: { serial_number: string; imei: string; imei2: string; method: string }[];
  }[];
  /** El ticket que la venta dejó en la cola de la impresora del local, si la hay. */
  print_job?: { id: number; status: string; printer: string; agent_online?: boolean } | null;
};

/** A sale refused because the shelf is empty, with where the units actually are. */
export class PosStockError extends Error {
  elsewhere: { branch: string; product: string; quantity: number }[];
  constructor(message: string, elsewhere: PosStockError["elsewhere"] = []) {
    super(message);
    this.name = "PosStockError";
    this.elsewhere = elsewhere;
  }
}

/** The idempotency key was already spent on a different basket. */
export class PosConflictError extends Error {
  existingOrder: number | null;
  constructor(message: string, existingOrder: number | null) {
    super(message);
    this.name = "PosConflictError";
    this.existingOrder = existingOrder;
  }
}

export async function fetchPosContext(companyId: number | null): Promise<PosContext> {
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  const res = await fetchWithAuth(`${API_BASE}/admin/pos/context/${qs}`);
  if (!res.ok) throw new Error(await readDetail(res, "No se pudo abrir el punto de venta."));
  return res.json();
}

/**
 * Resolve a scanned code. A 404 is an ordinary answer here — an unknown label
 * is a normal event at a counter, not a transport failure.
 */
export async function posLookup(
  companyId: number | null,
  branchId: number,
  code: string,
): Promise<PosProduct | null> {
  const qs = new URLSearchParams({ branch: String(branchId), code });
  if (companyId) qs.set("company", String(companyId));
  const res = await fetchWithAuth(`${API_BASE}/admin/pos/products/lookup/?${qs}`, { activity: true });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(await readDetail(res, "No se pudo leer el código."));
  return res.json();
}

/**
 * SERIAL-PICK-01 — qué equipo es ese IMEI o serie, si se puede vender desde
 * esta sucursal. `method` es cómo llegó el código: el servidor exige el IMEI,
 * comprobado, a lo que se escribe a mano.
 */
export async function posUnitLookup(
  companyId: number | null,
  branchId: number,
  code: string,
  method: "scanner" | "manual",
): Promise<{ unit: PosUnit; product: PosProduct }> {
  const qs = new URLSearchParams({ branch: String(branchId), code, method });
  if (companyId) qs.set("company", String(companyId));
  const res = await fetchWithAuth(`${API_BASE}/admin/pos/units/lookup/?${qs}`, { activity: true });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new PosUnitError(
      body?.detail ? String(body.detail) : "No se pudo comprobar ese equipo.",
      body?.code ? String(body.code) : "",
    );
  }
  return res.json();
}

export async function posSearch(
  companyId: number | null,
  branchId: number,
  q: string,
): Promise<PosProduct[]> {
  const qs = new URLSearchParams({ branch: String(branchId), q });
  if (companyId) qs.set("company", String(companyId));
  const res = await fetchWithAuth(`${API_BASE}/admin/pos/products/search/?${qs}`, { activity: true });
  if (!res.ok) throw new Error(await readDetail(res, "No se pudo buscar."));
  return (await res.json()).results ?? [];
}

/** AUTH-C — what the server says about a sale sent under this key. Lookup only. */
export type PosSaleLookup =
  | { found: false }
  | { found: true; order_id: number; total: string; paid_at: string | null; payment_method: string; branch: string };

export async function lookupPosSaleByKey(companyId: number | null, key: string): Promise<PosSaleLookup> {
  const qs = new URLSearchParams({ key });
  if (companyId) qs.set("company", String(companyId));
  const res = await fetchWithAuth(`${API_BASE}/admin/pos/sales/by-key/?${qs}`, { activity: false });
  if (!res.ok) throw new Error(await readDetail(res, "No se pudo comprobar la venta anterior."));
  return res.json();
}

/**
 * AUTH-C (owner, point 5) — the cashier's decision that the sale under this key
 * was NOT charged, recorded by the server. Refused (409, with the order) if a
 * sale under that key exists after all. It sells nothing.
 */
export async function recordPosSaleNotCharged(
  companyId: number | null,
  key: string,
): Promise<{ recorded: true } | { found: true; order_id: number; total: string }> {
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  const res = await fetchWithAuth(`${API_BASE}/admin/pos/sales/by-key/decision/${qs}`, {
    method: "POST",
    body: JSON.stringify({ key, decision: "not_charged" }),
  });
  if (res.ok) return res.json();
  if (res.status === 409) return res.json();
  throw new Error(await readDetail(res, "No se pudo registrar la decisión."));
}

export async function posSale(
  companyId: number | null,
  body: PosSaleInput,
): Promise<PosSaleResult> {
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  const res = await fetchWithAuth(`${API_BASE}/admin/pos/sales/${qs}`, {
    method: "POST",
    body: JSON.stringify(body),
  });
  if (res.ok) return res.json();

  const payload = await res.json().catch(() => null);
  const detail = (payload as { detail?: string })?.detail ?? "No se pudo cobrar.";
  if (res.status === 409) {
    if ((payload as { code?: string })?.code === "key_reserved") {
      // AUTH-C: this key was recorded as «no cobrada»; nothing was sold now.
      const reserved = new Error(detail) as Error & { status?: number; code?: string };
      reserved.status = 409;
      reserved.code = "key_reserved";
      throw reserved;
    }
    if ((payload as { code?: string })?.code === "insufficient_stock") {
      throw new PosStockError(
        detail,
        (payload as { available_elsewhere?: PosStockError["elsewhere"] })
          ?.available_elsewhere ?? [],
      );
    }
    throw new PosConflictError(
      detail,
      (payload as { existing_order?: number })?.existing_order ?? null,
    );
  }
  // AUTH-C: the status says whether this was an ANSWER (a 4xx: nothing sold)
  // or not (5xx, 401): the till keeps its in-flight mark for the latter.
  const error = new Error(detail) as Error & { status?: number };
  error.status = res.status;
  throw error;
}

// ---------------------------------------------------------------------------
// Commercial Phase C1 — analytics
// ---------------------------------------------------------------------------

export type SalesKpi = {
  revenue: string;
  orders: number;
  units: number;
  average_ticket: string;
};

/**
 * COMMERCE-ADMIN-UX-01 — el dinero de una ventana, aparte de las ventas.
 * `net` = `gross` − reembolsos confirmados; los pendientes no se restan.
 */
export type SalesCollections = {
  live: { orders: number; amount: string };
  cancelled_paid: { orders: number; amount: string };
  late_charges: { count: number; amount: string };
  gross: string;
  refunds_confirmed: string;
  refunds_pending: string;
  net: string;
  /** Lo que aún se tiene de los pedidos cancelados de la ventana. */
  cancelled_held: string;
  cancelled_orders: number;
};

export type SalesDashboard = {
  company: { id: number; name: string };
  branches: PosBranch[];
  today: string;
  kpis: Record<string, SalesKpi>;
  /** COMMERCE-ADMIN-UX-01: `today`, `last_7d`, `last_30d`, `last_90d`. */
  collections?: Record<string, SalesCollections>;
  channels: {
    window_days: number;
    by_channel: Record<string, { orders: number; revenue: string; units: number }>;
  };
  trend: { date: string; revenue: string; orders: number }[];
  top_products: {
    window_days: number;
    results: {
      product_id: number;
      product_name: string;
      units_sold: number;
      revenue: string;
      current_stock: number;
      /** null when there is no recent consumption to divide by. */
      days_of_cover: number | null;
    }[];
  };
  stock_alerts: { out_of_stock: number; low: number } | null;
};

export type ReplenishmentRow = {
  branch_id: number;
  branch_name: string;
  product_id: number;
  product_name: string;
  quantity: number;
  minimum_stock: number;
  target_stock: number;
  safety_stock: number;
  lead_time_days: number;
  days_of_cover: number | null;
  estimated_stockout_date: string | null;
  reorder_point: number | null;
  reorder_state: string;
  suggested_quantity: number;
  risk: string;
  forecast: {
    daily: number;
    avg_7: number;
    avg_30: number;
    avg_90: number;
    history_days: number;
    selling_days: number;
    sufficient: boolean;
    confidence: string;
    trend: string;
  };
  transfer_options?: {
    branch_id: number;
    branch_name: string;
    quantity: number;
    can_transfer: number;
  }[];
};

export type ReplenishmentReport = {
  today: string;
  branches: PosBranch[];
  method: { formula: string; demand_source: string; note: string };
  results: ReplenishmentRow[];
};

export async function fetchSalesDashboard(
  companyId: number | null,
  branchId?: number | null,
): Promise<SalesDashboard> {
  const qs = new URLSearchParams();
  if (companyId) qs.set("company", String(companyId));
  if (branchId) qs.set("branch", String(branchId));
  const res = await fetchWithAuth(`${API_BASE}/admin/sales/dashboard/?${qs}`);
  if (!res.ok) throw new Error(await readDetail(res, "No se pudo cargar la analítica."));
  return res.json();
}

// ---------------------------------------------------------------------------
// DEVICE-COMMERCE-01 · DC1 — margen comercial (`costs.view`)
// ---------------------------------------------------------------------------

export type SalesMarginRow = {
  key: number | string;
  name: string;
  units: number;
  net_sales: string;
  cost: string;
  margin: string;
  margin_percent: string | null;
  pending_lines: number;
  /** Parte de `net_sales` viene de ventas anteriores a los costos, calculada desde su pedido. */
  net_estimated?: boolean;
};

/** Un margen sobre un conjunto de líneas. `margin_percent`: 2 decimales, o null (sin ventas). */
export type SalesMarginTotals = {
  lines: number;
  net_sales: string;
  cost: string;
  margin: string;
  margin_percent: string | null;
};

/** Margen COMERCIAL estimado: ventas netas con costo conocido − su costo. No es utilidad. */
export type SalesMargin = {
  window_days: number;
  sales: { orders: number; gross: string; net: string };
  /**
   * COMMERCE-ADMIN-UX-01: `amount` es el cobro NETO (ventas vigentes + dinero
   * aún en la tienda por cancelaciones − reembolsos confirmados); `orders`, las
   * ventas vigentes. El resto, el desglose de `SalesCollections`.
   */
  collected: { orders: number; amount: string } & Partial<SalesCollections>;
  /** COMMERCE-ADMIN-UX-01: pagados y luego cancelados, fuera del margen, desde sus snapshots. */
  cancellations?: {
    orders: number; units: number; gross: string; net: string; cost: string;
    stock_restored_orders: number; refunds_confirmed: string;
  };
  net_sales_with_cost: string;
  cost_of_goods_sold: string;
  margin: string;
  margin_percent: string | null;
  pending_cost: { lines: number; orders: number; amount: string };
  /** `amount` con IGV; `net` sin IGV. */
  refunds: { orders: number; amount: string; net: string };
  /** Todas las líneas con costo conocido (promedio, unidad, referencia u oferta del proveedor). */
  estimated: SalesMarginTotals;
  /** Sólo las líneas con costo confirmado: stock propio vendido o bajo pedido recibido con su costo real. */
  realised: SalesMarginTotals;
  /** Lo que entra en el estimado pero aún no en el realizado. */
  estimated_only: {
    lines: number; net_sales: string; reference_cost_lines: number; on_order_not_received_lines: number;
    /** D2: recibidas bajo pedido sin costo real registrado todavía. */
    on_order_without_real_cost_lines?: number;
  };
  /** Líneas sin costo: fuera de ambos márgenes. */
  unknown_cost: { lines: number; orders: number; net_sales: string };
  /**
   * SUPPLIER-MARGIN (D2): líneas bajo pedido sin costo real todavía (sin
   * recibir, por confirmar, o recibidas sin costo real registrado): fuera del
   * realizado. Las no abastecidas no cuentan: van al flujo de reembolso.
   */
  real_cost_pending?: {
    lines: number; orders: number; net_sales: string; not_received_lines: number; to_confirm_lines?: number;
    received_without_real_cost_lines: number;
  };
  by_product: SalesMarginRow[];
  by_branch: SalesMarginRow[];
  by_channel: SalesMarginRow[];
};

export async function fetchSalesMargin(
  companyId: number | null,
  branchId?: number | null,
): Promise<SalesMargin> {
  const qs = new URLSearchParams();
  if (companyId) qs.set("company", String(companyId));
  if (branchId) qs.set("branch", String(branchId));
  const res = await fetchWithAuth(`${API_BASE}/admin/sales/margin/?${qs}`);
  if (!res.ok) throw new Error(await readDetail(res, "No se pudo cargar el margen comercial."));
  return res.json();
}

export async function fetchReplenishment(
  companyId: number | null,
  branchId?: number | null,
): Promise<ReplenishmentReport> {
  const qs = new URLSearchParams();
  if (companyId) qs.set("company", String(companyId));
  if (branchId) qs.set("branch", String(branchId));
  const res = await fetchWithAuth(`${API_BASE}/admin/sales/replenishment/?${qs}`);
  if (!res.ok) throw new Error(await readDetail(res, "No se pudo cargar la reposición."));
  return res.json();
}

// ---------------------------------------------------------------------------
// Commercial Phase C1.2 — enriched sale
// ---------------------------------------------------------------------------

export type PosSeller = { id: number; name: string };

export type AppliedPromotionPreview = {
  id: number;
  name: string;
  applications: number;
  regular_amount: string;
  discount_amount: string;
};

export type PosPreview = {
  subtotal: string;
  discount: string;
  discount_source: "none" | "coupon" | "manual" | "promotion";
  /** Fired by the basket itself — nobody typed anything. */
  promotions: AppliedPromotionPreview[];
  coupon_code: string;
  total: string;
  /** Lo que el operador lee en voz alta. Sale del mismo cálculo que hará la venta. */
  tax: TaxBreakdown;
  seller: { id: number | null; name: string };
  customer: { id: number; name: string } | null;
  /** null unless the caller may see earnings. */
  commission: { rate_percent: string; base_amount: string; amount: string } | null;
  lines: {
    product: number; name: string; quantity: number; price: string;
    /** DC3: la línea es bajo pedido, con su plazo. */
    on_order?: boolean;
    lead_time_days?: number | null;
  }[];
};

export type PosSaleInput = {
  receipt_type?: string;
  branch: number;
  items: PosSaleLine[];
  customer?: number | null;
  seller?: number | null;
  payment_method: string;
  idempotency_key: string;
  terms_confirmed: boolean;
  coupon_code?: string;
  manual_discount_type?: "percent" | "amount" | "";
  manual_discount_value?: string | number | null;
  discount_reason?: string;
  amount_received?: string | number | null;
  payment_reference?: string;
  external_reference?: string;
  sale_notes?: string;
};

export type CommissionRow = {
  seller_id: number | null;
  seller_name: string;
  sales: number;
  net_amount: string;
  commission: string;
  /** Cierre financiero: por estado. `recoverable` es negativo. */
  accrued?: string;
  settled?: string;
  voided?: string;
  voided_sales?: number;
  /** Revisión: ventas anuladas cuya comisión ya estaba liquidada (fuera de `sales`). */
  annulled_sales?: number;
  annulled_net_amount?: string;
  recoverable?: string;
  /** Shown BESIDE the historical total, never used to compute it. */
  current_rate_percent: string | null;
};

export type CommissionAdjustmentRow = {
  id: number;
  order_id: number;
  commission_id: number;
  seller_name: string;
  /** Negativo: lo que el vendedor debe devolver de una comisión ya liquidada. */
  amount: string;
  kind: string;
  label: string;
  created_at: string;
};

export type CommissionReport = {
  window_days: number;
  today: string;
  branches: PosBranch[];
  /** Devengada + liquidada + saldo a recuperar: lo que los vendedores conservan. */
  total_commission: string;
  /** Cierre financiero (2026-10-16). Ausente en una respuesta anterior. */
  totals?: { accrued: string; settled: string; voided: string; recoverable: string };
  note: string;
  results: CommissionRow[];
  adjustments?: CommissionAdjustmentRow[];
};

export type CommissionSetting = {
  membership_id: number;
  user_id: number;
  name: string;
  role: string;
  commission_rate_percent: string;
};

/**
 * Price a basket without charging it.
 *
 * Runs the server's own arithmetic, so the total shown is the total that will
 * be charged. A discount the sale would refuse is refused here too, rather than
 * displaying a number that then fails at the till.
 */
export async function posPreview(
  companyId: number | null,
  body: Omit<PosSaleInput, "idempotency_key" | "terms_confirmed">,
): Promise<PosPreview> {
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  const res = await fetchWithAuth(`${API_BASE}/admin/pos/preview/${qs}`, {
    method: "POST",
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await readDetail(res, "No se pudo calcular el total."));
  return res.json();
}

export async function fetchCommissions(
  companyId: number | null,
  params: { days?: number; branch?: number | null } = {},
): Promise<CommissionReport> {
  const qs = new URLSearchParams();
  if (companyId) qs.set("company", String(companyId));
  if (params.days) qs.set("days", String(params.days));
  if (params.branch) qs.set("branch", String(params.branch));
  const res = await fetchWithAuth(`${API_BASE}/admin/sales/commissions/?${qs}`);
  if (!res.ok) throw new Error(await readDetail(res, "No se pudieron cargar las comisiones."));
  return res.json();
}

export async function fetchCommissionSettings(
  companyId: number | null,
): Promise<{ can_manage: boolean; results: CommissionSetting[] }> {
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  const res = await fetchWithAuth(`${API_BASE}/admin/sales/commission-settings/${qs}`);
  if (!res.ok) throw new Error(await readDetail(res, "No se pudo cargar la configuración."));
  return res.json();
}

export function updateCommissionRate(
  membershipId: number,
  companyId: number | null,
  rate: string,
): Promise<{ membership_id: number; commission_rate_percent: string }> {
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  return patchWithFieldErrors(
    `/admin/sales/commission-settings/${membershipId}/${qs}`,
    { commission_rate_percent: rate },
    "No se pudo guardar el porcentaje.",
  );
}

// ---------------------------------------------------------------------------
// Commercial Phase C1.3 — promotions, combos and coupons
// ---------------------------------------------------------------------------

export type PromotionItemRow = {
  product: number;
  product_name: string;
  price: string;
  quantity: number;
};

export type PromotionRow = {
  id: number;
  name: string;
  promotion_type: "bundle_fixed_price" | "bundle_percent";
  promotion_type_label: string;
  priority: number;
  is_active: boolean;
  /** Active AND inside its window — what the till actually sees. */
  is_live: boolean;
  starts_at: string | null;
  ends_at: string | null;
  branch_scope: "all" | "selected";
  branches: { id: number; name: string }[];
  fixed_price: string | null;
  discount_percent: string | null;
  max_applications_per_order: number | null;
  items: PromotionItemRow[];
  stats: {
    orders?: number;
    applications?: number;
    discount_given?: string;
    regular_value?: string;
  };
};

export type PromotionList = {
  can_manage: boolean;
  branches: PosBranch[];
  results: PromotionRow[];
};

export type CouponRow = {
  id: number;
  code: string;
  discount_percent: number;
  is_active: boolean;
  expires_at: string | null;
  is_expired: boolean;
};

export type ComboOffer = {
  id: number;
  name: string;
  promotion_type: string;
  components: {
    product_id: number;
    product_name: string;
    quantity: number;
    available: number;
    price: string;
    is_serialized?: boolean;
    requires_imei?: boolean;
  }[];
  regular_amount: string;
  discount_amount: string;
  combo_amount: string;
  /** How many complete sets the shelf can supply right now. */
  available_sets: number;
};

export async function fetchPromotions(companyId: number | null): Promise<PromotionList> {
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  const res = await fetchWithAuth(`${API_BASE}/admin/sales/promotions/${qs}`);
  if (!res.ok) throw new Error(await readDetail(res, "No se pudieron cargar las promociones."));
  return res.json();
}

export type PromotionWrite = Partial<{
  name: string;
  promotion_type: string;
  fixed_price: string | null;
  discount_percent: string | null;
  priority: number;
  is_active: boolean;
  starts_at: string | null;
  ends_at: string | null;
  branch_scope: string;
  branches: number[];
  max_applications_per_order: number | null;
  items: { product: number; quantity: number }[];
}>;

export function createPromotion(
  companyId: number | null,
  data: PromotionWrite,
): Promise<PromotionRow> {
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  return patchWithFieldErrors(
    `/admin/sales/promotions/${qs}`, data, "No se pudo crear la promoción.", "POST",
  );
}

export function updatePromotion(
  id: number,
  companyId: number | null,
  data: PromotionWrite,
): Promise<PromotionRow> {
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  return patchWithFieldErrors(
    `/admin/sales/promotions/${id}/${qs}`, data, "No se pudo guardar la promoción.",
  );
}

export async function fetchCoupons(
  companyId: number | null,
): Promise<{ can_manage: boolean; results: CouponRow[] }> {
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  const res = await fetchWithAuth(`${API_BASE}/admin/sales/coupons/${qs}`);
  if (!res.ok) throw new Error(await readDetail(res, "No se pudieron cargar los códigos."));
  return res.json();
}

export function createCoupon(
  companyId: number | null,
  data: { code: string; discount_percent: number; expires_at?: string | null },
): Promise<CouponRow> {
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  return patchWithFieldErrors(
    `/admin/sales/coupons/${qs}`, data, "No se pudo crear el código.", "POST",
  );
}

export function updateCoupon(
  id: number,
  companyId: number | null,
  data: Partial<{ is_active: boolean; discount_percent: number; expires_at: string | null }>,
): Promise<CouponRow> {
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  return patchWithFieldErrors(
    `/admin/sales/coupons/${id}/${qs}`, data, "No se pudo guardar el código.",
  );
}

/** Combos the current branch can actually complete, for the POS shortcut. */
export async function fetchCombos(
  companyId: number | null,
  branchId: number,
): Promise<ComboOffer[]> {
  const qs = new URLSearchParams({ branch: String(branchId) });
  if (companyId) qs.set("company", String(companyId));
  const res = await fetchWithAuth(`${API_BASE}/admin/pos/combos/?${qs}`);
  if (!res.ok) return [];
  return (await res.json()).results ?? [];
}

// ---------------------------------------------------------------------------
// C1.4 — bulk imports
// ---------------------------------------------------------------------------
//
// Every import is TWO calls: preview, then apply. There is no single-shot
// endpoint, and adding one would defeat the design — the point of staging is
// that a person sees what will happen to six hundred rows before it happens.

export type ImportAction = "create" | "update" | "no_change" | "skip" | "error";

export type ImportRow = {
  sheet: string;
  row: number;
  action: ImportAction;
  match_key: string;
  data: Record<string, unknown>;
  errors: string[];
  warnings: string[];
};

/** Lo que el servidor casó entre las filas y los archivos de imagen adjuntos. */
export type ImportMediaSummary = {
  attached: number;
  referenced: number;
  valid: number;
  invalid: number;
  missing: number;
  duplicates: number;
  orphans: number;
  orphan_names: string[];
  ignored: number;
  ignored_names: string[];
  already_present: number;
  staged: number;
  separator: string;
};

export type ImportJob = {
  id: number;
  import_type: "products" | "stock" | "units" | "product_images";
  status: "previewed" | "applied" | "failed";
  stock_mode: string;
  original_filename: string;
  file_sha256: string;
  mapping: Record<string, unknown>;
  options: Record<string, unknown>;
  counts: {
    total: number;
    create: number;
    update: number;
    no_change: number;
    skip: number;
    error: number;
  };
  summary: {
    detected?: string;
    reader_notes?: string[];
    format_notes?: string[];
    /** `important`: a column that belongs to ANOTHER upload (stock, devices). Shown in the open. */
    unmapped?: { column: string; reason: string; important?: boolean }[];
    branches?: { column: string; branch_id: number; branch_name: string }[];
    sheets?: string[];
    applied?: Record<string, number>;
    media?: ImportMediaSummary;
    /** Carga de productos: cuántos que ya existen pasan a la web o salen de ella. */
    destination_changes?: { to_web: number; to_internal: number };
    /** Carga de productos: filas que piden la web para un producto no elegible. Se carga, y no se publica. */
    publication_refused?: number;
    /** Sólo en una carga de imágenes (`product_images`). */
    products?: number;
    images_to_add?: number;
    images_to_remove?: number;
    unmatched?: number;
    /** La palabra que hay que escribir para aplicar un reemplazo. Vacía si no lo es. */
    confirm_word?: string;
  };
  created_at: string;
  applied_at: string | null;
  created_by: string;
  applied_by: string;
  is_applicable: boolean;
  rows?: ImportRow[];
  rows_truncated?: boolean;
};

export type InspectedSheet = {
  name: string;
  header_row: number;
  headers: string[];
  sample_rows: number;
  detected: string;
  preset: string;
  mapping: Record<string, number>;
  warehouse_columns: { index: number; header: string }[];
  notes: string[];
  signature: string;
  profile: { id: number; name: string; mapping: Record<string, number> } | null;
  /** A sheet of the template that explains («Instrucciones», «Ejemplo»): never imported. */
  help?: boolean;
};

/** How images travel with a product file, with the limits the server applies. */
export type ImageRules = {
  separator: string;
  formats: string[];
  max_per_product: number;
  max_file_mb: number;
  max_files: number;
  max_total_mb: number;
};

export type InspectResult = {
  import_type: string;
  reader_notes: string[];
  sheets: InspectedSheet[];
  fields: Record<string, { label: string; required: boolean }>;
  branches: { id: number; name: string }[];
  image_rules?: ImageRules | null;
};

/**
 * POST multipart WITHOUT a Content-Type.
 *
 * A multipart body needs the boundary the browser generates, so no header is
 * passed at all and `fetchWithAuth` leaves it to the browser. This used to pass
 * `"Content-Type": ""` to "delete" it; an empty header is still a header, the
 * browser sent it, and the server answered 415 to every import.
 */
async function postForm<T>(path: string, form: FormData, fallback: string): Promise<T> {
  const res = await fetchWithAuth(`${API_BASE}${path}`, { method: "POST", body: form });
  if (res.ok) return res.json();
  // Un 413 puede venir de un servidor intermedio, sin cuerpo que leer.
  throw new Error(await readDetail(
    res, res.status === 413 ? "El envío es demasiado grande. Adjunta menos imágenes o divídelas en varias importaciones." : fallback,
  ));
}

export function inspectImportFile(
  companyId: number | null,
  file: File,
  importType: "products" | "stock",
): Promise<InspectResult> {
  const form = new FormData();
  form.append("file", file);
  form.append("import_type", importType);
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  return postForm(`/admin/imports/inspect/${qs}`, form, "No se pudo leer el archivo.");
}

export function previewProductImport(
  companyId: number | null,
  file: File,
  opts: {
    sheetName?: string;
    headerRow?: number;
    mapping?: Record<string, number>;
    options?: Record<string, unknown>;
    /** BULK-MEDIA: los archivos de imagen que las filas citan por nombre. */
    images?: File[];
    /** O un ZIP con ellas. Se pueden enviar las dos cosas. */
    imagesZip?: File | null;
  } = {},
): Promise<ImportJob> {
  const form = new FormData();
  form.append("file", file);
  if (opts.sheetName) form.append("sheet_name", opts.sheetName);
  if (opts.headerRow) form.append("header_row", String(opts.headerRow));
  if (opts.mapping) form.append("mapping", JSON.stringify(opts.mapping));
  if (opts.options) form.append("options", JSON.stringify(opts.options));
  for (const image of opts.images ?? []) form.append("images", image);
  if (opts.imagesZip) form.append("images_zip", opts.imagesZip);
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  return postForm(
    `/admin/products/import/preview/${qs}`, form,
    "No se pudo previsualizar el archivo.",
  );
}

export function previewStockImport(
  companyId: number | null,
  file: File,
  opts: {
    branchMap: Record<string, number>;
    mode: string;
    sheetName?: string;
    headerRow?: number;
    mapping?: Record<string, number>;
  },
): Promise<ImportJob> {
  const form = new FormData();
  form.append("file", file);
  form.append("branch_map", JSON.stringify(opts.branchMap));
  form.append("mode", opts.mode);
  if (opts.sheetName) form.append("sheet_name", opts.sheetName);
  if (opts.headerRow) form.append("header_row", String(opts.headerRow));
  if (opts.mapping) form.append("mapping", JSON.stringify(opts.mapping));
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  return postForm(
    `/admin/inventory/import/preview/${qs}`, form,
    "No se pudo previsualizar el inventario.",
  );
}

export async function applyImport(
  companyId: number | null,
  job: ImportJob,
): Promise<ImportJob> {
  const base =
    job.import_type === "products"
      ? `/admin/products/import/${job.id}/apply/`
      : `/admin/inventory/import/${job.id}/apply/`;
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  const res = await fetchWithAuth(`${API_BASE}${base}${qs}`, {
    method: "POST",
    body: JSON.stringify({}),
  });
  if (!res.ok) throw new Error(await readDetail(res, "No se pudo aplicar la importación."));
  return res.json();
}

export async function fetchImportHistory(
  companyId: number | null,
  importType?: ImportJob["import_type"],
): Promise<ImportJob[]> {
  const qs = new URLSearchParams();
  if (companyId) qs.set("company", String(companyId));
  if (importType) qs.set("type", importType);
  const res = await fetchWithAuth(`${API_BASE}/admin/imports/?${qs}`);
  if (!res.ok) throw new Error(await readDetail(res, "No se pudo cargar el historial."));
  return (await res.json()).results ?? [];
}

// ---------------------------------------------------------------------------
// INVENTORY-UNIFIED-01 — «¿Qué archivo tengo?» y las imágenes en lote
// ---------------------------------------------------------------------------

/** A qué carga pertenece un libro, según el servidor. `forbidden`: una que esta persona no puede hacer. */
export type ImportSuggestion = {
  suggested_import_type: "products" | "stock" | "units" | "images" | "report" | "unknown" | "forbidden";
  suggestion_reason: string;
  /** La frase que se muestra tal cual. */
  suggestion_detail: string;
  suggestion_sheet: string;
  sheet_names: string[];
};

/** Cómo se nombra un archivo de imagen y qué admite una carga. Lo dice el servidor. */
export type ImageImportRules = {
  pattern: string;
  suffixes: string[];
  examples: string[];
  confirm_word: string;
  workbook: { sheet: string; headers: string[] };
  max_per_product: number;
  image_rules?: ImageRules | null;
};

export type ImageImportMode = "add" | "replace";

/**
 * «¿Qué archivo tengo?»: la inspección de siempre, pidiendo sólo a qué carga va
 * el libro. El servidor mira los nombres de las hojas y las cabeceras; no
 * guarda nada ni empieza ninguna carga.
 */
export function whichImport(companyId: number | null, file: File): Promise<ImportSuggestion> {
  const form = new FormData();
  form.append("file", file);
  form.append("import_type", "auto");
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  return postForm(`/admin/imports/inspect/${qs}`, form, "No se pudo leer el archivo.");
}

/**
 * Imágenes en lote, sin libro de productos. Viaja por la vista previa de la
 * carga de productos (`kind=images`): es la única ruta dimensionada para llevar
 * un lote de imágenes. No coloca nada: devuelve qué pasaría.
 */
export function previewImageImport(
  companyId: number | null,
  opts: { images: File[]; imagesZip?: File | null; book?: File | null; mode: ImageImportMode; useMain: boolean },
): Promise<ImportJob> {
  const form = new FormData();
  form.append("kind", "images");
  form.append("mode", opts.mode);
  if (opts.useMain) form.append("use_main", "true");
  for (const image of opts.images) form.append("images", image);
  if (opts.imagesZip) form.append("images_zip", opts.imagesZip);
  if (opts.book) form.append("file", opts.book);
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  return postForm(`/admin/products/import/preview/${qs}`, form, "No se pudieron revisar las imágenes.");
}

/** Coloca lo previsualizado. `confirm` es la palabra que pide un reemplazo. */
export async function applyImageImport(companyId: number | null, jobId: number, confirm?: string): Promise<ImportJob> {
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  const res = await fetchWithAuth(`${API_BASE}/admin/products/images/import/${jobId}/apply/${qs}`, {
    method: "POST",
    body: JSON.stringify(confirm ? { confirm } : {}),
  });
  if (!res.ok) throw new Error(await readDetail(res, "No se pudieron aplicar las imágenes."));
  return res.json();
}

export async function fetchImageImportRules(companyId: number | null): Promise<ImageImportRules> {
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  const res = await fetchWithAuth(`${API_BASE}/admin/products/images/import/rules/${qs}`);
  if (!res.ok) throw new Error(await readDetail(res, "No se pudieron leer las reglas de imágenes."));
  return res.json();
}

/** Absolute URLs for the browser to navigate to — downloads, not fetches. */
export function importErrorReportUrl(companyId: number | null, jobId: number) {
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  return `${API_BASE}/admin/imports/${jobId}/errors.csv${qs}`;
}

export function productTemplateUrl(companyId: number | null) {
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  return `${API_BASE}/admin/products/import/template/${qs}`;
}

export function inventoryExportUrl(
  companyId: number | null,
  branchIds: number[],
  quantities: "current" | "blank",
) {
  const qs = new URLSearchParams({ quantities });
  if (companyId) qs.set("company", String(companyId));
  if (branchIds.length) qs.set("branches", branchIds.join(","));
  return `${API_BASE}/admin/inventory/export/?${qs}`;
}

// ---------------------------------------------------------------------------
// M12F — contenido comercial del escaparate
// ---------------------------------------------------------------------------

export type StorefrontCampaignRow = {
  id: number;
  slot: string;
  status: "draft" | "published" | "archived";
  badge: string;
  title: string;
  subtitle: string;
  body: string;
  image_url: string;
  cta_label: string;
  cta_url: string;
  secondary_cta_label: string;
  secondary_cta_url: string;
  priority: number;
  starts_at: string | null;
  ends_at: string | null;
  published_at: string | null;
  is_active: boolean;
  product_id: number | null;
  product: { slug: string; name: string } | null;
  updated_at: string;
};

export type StorefrontSlot = { value: string; label: string };

export type StorefrontCampaignList = {
  company: { id: number; slug: string; name: string };
  slots: StorefrontSlot[];
  results: StorefrontCampaignRow[];
};

export type StorefrontPageContent = {
  /** Cómo se ve el hero: losa oscura o claro con imagen. Lo decide la tienda. */
  hero_variant: "dark" | "light";
  hero_image_url: string;
  services_image_url: string;
  location_image_url: string;
  hero_eyebrow: string;
  hero_title: string;
  hero_subtitle: string;
  hero_primary_cta_label: string;
  hero_primary_cta_url: string;
  hero_secondary_cta_label: string;
  hero_secondary_cta_url: string;
  services_hero_title: string;
  services_hero_subtitle: string;
  services_warranty_note: string;
};

/** Errores por campo, tal como los devuelve el backend. */
export class ContentValidationError extends Error {
  constructor(message: string, readonly errors: Record<string, string[]>) {
    super(message);
    this.name = "ContentValidationError";
  }
}

function companyQuery(companyId?: number | null): string {
  return companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
}

async function handle<T>(res: Response, what: string): Promise<T> {
  if (res.status === 403) {
    throw new NoInternalAccessError(
      await readDetail(res, `No tienes permisos sobre ${what}.`),
    );
  }
  if (res.status === 404) throw new Error("Empresa no encontrada o sin acceso.");
  if (res.status === 400) {
    // Los mensajes por campo vienen del backend y se pintan bajo el campo al
    // que pertenecen. Tragárselos dejaría a alguien mirando un formulario que
    // no guarda sin decir por qué.
    const data = await res.json().catch(() => ({}));
    throw new ContentValidationError(
      data.detail || "Datos inválidos.", data.errors || {},
    );
  }
  if (!res.ok) throw new Error(await readDetail(res, `No se pudo cargar ${what}.`));
  return (await res.json()) as T;
}

export async function fetchStorefrontCampaigns(
  companyId?: number | null,
): Promise<StorefrontCampaignList> {
  const res = await fetchWithAuth(
    `${API_BASE}/admin/storefront/campaigns/${companyQuery(companyId)}`,
  );
  return handle<StorefrontCampaignList>(res, "el escaparate");
}

export async function createStorefrontCampaign(
  payload: Partial<StorefrontCampaignRow>, companyId?: number | null,
): Promise<StorefrontCampaignRow> {
  const res = await fetchWithAuth(
    `${API_BASE}/admin/storefront/campaigns/${companyQuery(companyId)}`,
    { method: "POST", body: JSON.stringify(payload) },
  );
  return handle<StorefrontCampaignRow>(res, "el escaparate");
}

export async function updateStorefrontCampaign(
  id: number, payload: Partial<StorefrontCampaignRow>, companyId?: number | null,
): Promise<StorefrontCampaignRow> {
  const res = await fetchWithAuth(
    `${API_BASE}/admin/storefront/campaigns/${id}/${companyQuery(companyId)}`,
    { method: "PATCH", body: JSON.stringify(payload) },
  );
  return handle<StorefrontCampaignRow>(res, "el escaparate");
}

/**
 * Publicar y archivar son acciones CON NOMBRE, no efectos de guardar.
 *
 * Por eso son un endpoint aparte y no un `status` que se pueda colar en un
 * PATCH: guardar un borrador tiene que dejarlo en borrador.
 */
export async function actOnStorefrontCampaign(
  id: number, action: "publish" | "archive", companyId?: number | null,
): Promise<StorefrontCampaignRow> {
  const res = await fetchWithAuth(
    `${API_BASE}/admin/storefront/campaigns/${id}/${action}/${companyQuery(companyId)}`,
    { method: "POST" },
  );
  return handle<StorefrontCampaignRow>(res, "el escaparate");
}

export async function fetchStorefrontPage(
  companyId?: number | null,
): Promise<{ page: StorefrontPageContent }> {
  const res = await fetchWithAuth(
    `${API_BASE}/admin/storefront/page/${companyQuery(companyId)}`,
  );
  return handle<{ page: StorefrontPageContent }>(res, "la portada");
}

export async function updateStorefrontPage(
  payload: Partial<StorefrontPageContent>, companyId?: number | null,
): Promise<{ page: StorefrontPageContent }> {
  const res = await fetchWithAuth(
    `${API_BASE}/admin/storefront/page/${companyQuery(companyId)}`,
    { method: "PATCH", body: JSON.stringify(payload) },
  );
  return handle<{ page: StorefrontPageContent }>(res, "la portada");
}

// ---------------------------------------------------------------------------
// Imágenes de la tienda — subir un archivo y recibir su dirección
// ---------------------------------------------------------------------------

export type StorefrontImage = {
  id: string;
  /** Ruta del propio sitio. Es lo que se guarda en el hueco. */
  url: string;
  mime_type: string;
  byte_size: number;
  width: number;
  height: number;
  /** PNG o WebP con transparencia: se conserva tal cual. */
  has_alpha: boolean;
};

/** Lo que el servidor acepta. El tipo lo decide él al decodificar, no el nombre. */
export const STOREFRONT_IMAGE_ACCEPT = "image/png,image/jpeg,image/webp";

export async function uploadStorefrontImage(
  file: File, companyId?: number | null,
): Promise<StorefrontImage> {
  const form = new FormData();
  form.append("file", file);
  // Sin `Content-Type`: con un `FormData` lo escribe el navegador, con el
  // boundary que separa las partes. Fijarlo a mano —aunque sea vacío— hace que
  // el servidor reciba un cuerpo que no sabe partir y responda 415.
  const res = await fetchWithAuth(
    `${API_BASE}/admin/storefront/images/${companyQuery(companyId)}`,
    { method: "POST", body: form },
  );
  if (res.ok) return res.json();
  throw new Error(await readDetail(res, "No se pudo subir la imagen."));
}

// ---------------------------------------------------------------------------
// M12F.1 — servicios, preguntas y métricas del escaparate
// ---------------------------------------------------------------------------

export type StorefrontListKind = "services" | "faqs" | "metrics";

export type StorefrontListRow = {
  id: number;
  is_active: boolean;
  sort_order: number;
  // servicios
  title?: string;
  description?: string;
  devices_text?: string;
  estimated_time_text?: string;
  highlight?: string;
  // preguntas
  question?: string;
  answer?: string;
  // métricas
  value?: string;
  label?: string;
};

export async function fetchStorefrontList(
  kind: StorefrontListKind, companyId?: number | null,
): Promise<{ results: StorefrontListRow[] }> {
  const res = await fetchWithAuth(
    `${API_BASE}/admin/storefront/${kind}/${companyQuery(companyId)}`,
  );
  return handle<{ results: StorefrontListRow[] }>(res, "el escaparate");
}

export async function saveStorefrontListRow(
  kind: StorefrontListKind,
  payload: Partial<StorefrontListRow>,
  companyId?: number | null,
): Promise<StorefrontListRow> {
  const id = payload.id;
  const url = id
    ? `${API_BASE}/admin/storefront/${kind}/${id}/${companyQuery(companyId)}`
    : `${API_BASE}/admin/storefront/${kind}/${companyQuery(companyId)}`;
  const res = await fetchWithAuth(url, {
    method: id ? "PATCH" : "POST",
    body: JSON.stringify(payload),
  });
  return handle<StorefrontListRow>(res, "el escaparate");
}

export async function deleteStorefrontListRow(
  kind: StorefrontListKind, id: number, companyId?: number | null,
): Promise<void> {
  const res = await fetchWithAuth(
    `${API_BASE}/admin/storefront/${kind}/${id}/${companyQuery(companyId)}`,
    { method: "DELETE" },
  );
  if (res.status === 204) return;
  await handle<unknown>(res, "el escaparate");
}

// ---------------------------------------------------------------------------
// DEVICE-COMMERCE-01 · DC2 — models, attributes, grades and line labels
// ---------------------------------------------------------------------------
//
// The company is a SELECTION, as everywhere in this file: `fetchWithAuth` adds
// the one chosen in the switcher, and the server re-validates it. Who may
// change what (`products.manage`) is decided by the server on every write.

export type CatalogLine = "new" | "used";

export type AdminCatalogModel = {
  id: number;
  name: string;
  slug: string;
  category_id: number | null;
  category_name: string | null;
  image_url: string;
  description: string;
  is_published_online: boolean;
  sort_order: number;
  variant_count: number;
  used_unit_count: number;
};

export type AdminVariantOption = {
  attribute_id: number;
  attribute_name: string;
  option_id: number;
  label: string;
  swatch_hex: string;
};

export type AdminModelVariant = {
  product_id: number;
  name: string;
  /** Shown when the server sends one; not part of the guaranteed contract. */
  sku?: string;
  code?: string;
  condition_line: CatalogLine;
  price: string;
  options: AdminVariantOption[];
  stock: number;
  /** CATALOG-VARIANTS-UX: the product's own publication in the shop. */
  is_published_online: boolean;
  /** What the variant already carries: order lines, devices received, devices set aside. */
  history: VariantHistory;
  /** How its model and options may change (see `VariantEditRule`). */
  identity_change: VariantEditRule;
  /** How its line (new / used) may change. */
  line_change: VariantEditRule;
  /** How it may be taken out of the model: blocked while it holds devices or stock. */
  removal?: VariantEditRule;
};

export type VariantHistory = {
  sold: number; units: number; reserved: number;
  /** Units on the shelves of a product counted by quantity; Kardex history; orders not handed over. */
  stock?: number; moved?: boolean; open_orders?: number;
};

/**
 * CATALOG-VARIANTS-UX — `free`: anyone with `products.manage`; `superior`: a
 * company administrator, with a reason that stays in the audit; `blocked`:
 * nobody, until what holds it (a reservation, its own devices) is gone.
 */
export type VariantEditRule = "free" | "superior" | "blocked";

export type AdminCatalogModelDetail = AdminCatalogModel & { variants: AdminModelVariant[] };

export type AdminCatalogModelWrite = Partial<{
  name: string;
  category_id: number | null;
  slug: string;
  description: string;
  image_url: string;
  is_published_online: boolean;
  sort_order: number;
}>;

export type AdminAttributeOption = { id: number; label: string; swatch_hex: string; sort_order: number };

export type AdminCatalogAttribute = {
  id: number;
  name: string;
  kind: "text" | "color";
  sort_order: number;
  options: AdminAttributeOption[];
};

export type CosmeticGradeRow = {
  id: number;
  code: string;
  label: string;
  description: string;
  sort_order: number;
  is_active: boolean;
};

export type CatalogSettings = { new_line_label: string; used_line_label: string };

/** The labels a line takes when the company leaves its own blank. */
export const DEFAULT_LINE_LABELS: Record<CatalogLine, string> = { new: "Nuevos", used: "Seminuevos" };

/** `#RRGGBB`, the only colour form the server stores for a swatch. */
export const SWATCH_HEX = /^#[0-9A-Fa-f]{6}$/;

export function isValidSwatchHex(value: string): boolean {
  return SWATCH_HEX.test(value);
}

/**
 * A refusal from the catalogue endpoints. `field` (or `fields`, for a 400 with
 * a message per input) says where the form should show it; `code` is the
 * server's machine-readable reason.
 */
export class CatalogApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string | null = null,
    readonly field: string | null = null,
    readonly fields: Record<string, string> = {},
  ) {
    super(message);
    this.name = "CatalogApiError";
  }
}

async function catalogCall<T>(path: string, init: RequestInit, fallback: string): Promise<T> {
  const res = await fetchWithAuth(`${API_BASE}${path}`, init);
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => null);
  if (res.ok) return body as T;
  const detail = body && typeof body.detail === "string" ? body.detail : "";
  const code = body && typeof body.code === "string" ? body.code : null;
  const field = body && typeof body.field === "string" ? body.field : null;
  const fields: Record<string, string> = {};
  if (res.status === 400 && body && typeof body === "object" && !detail) {
    for (const [key, value] of Object.entries(body as Record<string, unknown>)) {
      fields[key] = Array.isArray(value) ? String(value[0]) : String(value);
    }
  }
  const message =
    detail ||
    (Object.keys(fields).length ? "Revisa los campos marcados." : "") ||
    (res.status === 403 ? "No tienes permisos para esta operación." : fallback);
  throw new CatalogApiError(message, res.status, code, field, fields);
}

const jsonInit = (method: string, body?: unknown): RequestInit =>
  body === undefined ? { method } : { method, body: JSON.stringify(body) };

export function fetchAdminCatalogModels(companyId?: number | null): Promise<AdminCatalogModel[]> {
  return catalogCall<AdminCatalogModel[] | { results: AdminCatalogModel[] }>(
    `/admin/catalog/models/${companyQuery(companyId)}`, {}, "No se pudieron cargar los modelos.",
  ).then((data) => (Array.isArray(data) ? data : data?.results ?? []));
}

export function createAdminCatalogModel(body: AdminCatalogModelWrite, companyId?: number | null): Promise<AdminCatalogModel> {
  return catalogCall(`/admin/catalog/models/${companyQuery(companyId)}`, jsonInit("POST", body), "No se pudo crear el modelo.");
}

export function fetchAdminCatalogModel(id: number, companyId?: number | null): Promise<AdminCatalogModelDetail> {
  return catalogCall(`/admin/catalog/models/${id}/${companyQuery(companyId)}`, {}, "No se pudo cargar el modelo.");
}

export function updateAdminCatalogModel(
  id: number, body: AdminCatalogModelWrite, companyId?: number | null,
): Promise<AdminCatalogModelDetail> {
  return catalogCall(`/admin/catalog/models/${id}/${companyQuery(companyId)}`, jsonInit("PATCH", body), "No se pudo guardar el modelo.");
}

export function fetchCatalogAttributes(companyId?: number | null): Promise<AdminCatalogAttribute[]> {
  return catalogCall<AdminCatalogAttribute[] | { results: AdminCatalogAttribute[] }>(
    `/admin/catalog/attributes/${companyQuery(companyId)}`, {}, "No se pudieron cargar los atributos.",
  ).then((data) => (Array.isArray(data) ? data : data?.results ?? []));
}

export function createCatalogAttribute(
  body: { name: string; kind: "text" | "color" }, companyId?: number | null,
): Promise<AdminCatalogAttribute> {
  return catalogCall(`/admin/catalog/attributes/${companyQuery(companyId)}`, jsonInit("POST", body), "No se pudo crear el atributo.");
}

export function createAttributeOption(
  attributeId: number, body: { label: string; swatch_hex?: string }, companyId?: number | null,
): Promise<AdminAttributeOption> {
  return catalogCall(
    `/admin/catalog/attributes/${attributeId}/options/${companyQuery(companyId)}`,
    jsonInit("POST", body), "No se pudo crear la opción.",
  );
}

export function updateAttributeOption(
  optionId: number, body: Partial<{ label: string; swatch_hex: string; sort_order: number }>, companyId?: number | null,
): Promise<AdminAttributeOption> {
  return catalogCall(
    `/admin/catalog/options/${optionId}/${companyQuery(companyId)}`,
    jsonInit("PATCH", body), "No se pudo guardar la opción.",
  );
}

/**
 * Make a product a variant of a model (or take it out with `model_id: null`).
 * Editing a variant is this same call on the same product: it never creates
 * one. `reason` is what a superior writes when the variant already has history
 * (400 `history_reason_required` without it, 403 `history_needs_superior` for
 * anyone else; 409 `variant_reserved` / `line_change_with_units` when nobody may).
 */
export function setProductVariant(
  productId: number,
  body: {
    model_id: number | null; condition_line: CatalogLine; option_ids: number[]; reason?: string;
    /** CATALOG-EDITOR-FIX-01: saved in the same transaction as the variant (all or nothing). */
    price?: string; is_published_online?: boolean;
  },
  companyId?: number | null,
): Promise<unknown> {
  return catalogCall(
    `/admin/products/${productId}/variant/${companyQuery(companyId)}`,
    jsonInit("PUT", body), "No se pudo guardar la variante.",
  );
}

export function fetchCosmeticGrades(companyId?: number | null): Promise<CosmeticGradeRow[]> {
  return catalogCall<CosmeticGradeRow[] | { results: CosmeticGradeRow[] }>(
    `/admin/catalog/grades/${companyQuery(companyId)}`, {}, "No se pudo cargar la escala de estado.",
  ).then((data) => (Array.isArray(data) ? data : data?.results ?? []));
}

export function createCosmeticGrade(
  body: { code: string; label: string; description?: string; sort_order?: number }, companyId?: number | null,
): Promise<CosmeticGradeRow> {
  return catalogCall(`/admin/catalog/grades/${companyQuery(companyId)}`, jsonInit("POST", body), "No se pudo crear el grado.");
}

/** The suggested scale. Idempotent: pressing it twice creates nothing new. */
export function seedCosmeticGrades(companyId?: number | null): Promise<unknown> {
  return catalogCall(`/admin/catalog/grades/seed/${companyQuery(companyId)}`, jsonInit("POST", {}), "No se pudo crear la escala sugerida.");
}

export function fetchCatalogSettings(companyId?: number | null): Promise<CatalogSettings> {
  return catalogCall(`/admin/catalog/settings/${companyQuery(companyId)}`, {}, "No se pudieron cargar los nombres de las líneas.");
}

export function updateCatalogSettings(body: Partial<CatalogSettings>, companyId?: number | null): Promise<CatalogSettings> {
  return catalogCall(`/admin/catalog/settings/${companyQuery(companyId)}`, jsonInit("PATCH", body), "No se pudieron guardar los nombres de las líneas.");
}

// ---------------------------------------------------------------------------
// DEVICE-COMMERCE-01 · DC3 — suppliers, offers and «venta bajo pedido»
// ---------------------------------------------------------------------------
//
// Seeing needs `products.view`; every write `products.manage`. The agreed cost
// of an offer travels ONLY to whoever holds `costs.view`: the key is absent
// otherwise, and the screens show a cost only when the key is there — never a
// zero or a dash that pretends to be one.

export type SupplierDocumentType = "" | "dni" | "ruc" | "ce" | (string & {});

export const SUPPLIER_DOCUMENT_TYPE_LABELS: Record<string, string> = {
  "": "Sin documento",
  dni: "DNI",
  ruc: "RUC",
  ce: "Carné de extranjería",
};

export type SupplierRow = {
  id: number;
  name: string;
  document_type: SupplierDocumentType;
  document_number: string;
  contact_name: string;
  phone: string;
  email: string;
  notes: string;
  is_active: boolean;
  offer_count: number;
};

export type SupplierWrite = Partial<Omit<SupplierRow, "id" | "offer_count">>;

export type Paginated<T> = { count: number; page: number; page_size: number; results: T[] };

export type SupplierOfferState = "active" | "expired" | "inactive" | "scheduled" | "unconfirmed" | "exhausted";

export const SUPPLIER_OFFER_STATE_LABELS: Record<SupplierOfferState, string> = {
  active: "Vigente",
  scheduled: "Programada",
  unconfirmed: "Disponibilidad no confirmada",
  exhausted: "Agotada",
  expired: "Vencida",
  inactive: "Desactivada",
};

export type SupplierOffer = {
  id: number;
  supplier_id: number;
  supplier_name: string;
  product_id: number;
  product_name: string;
  product_slug: string;
  commercial_price: string;
  /** `null` = not confirmed yet: the offer sells nothing until it is. */
  confirmed_quantity: number | null;
  committed_quantity: number;
  remaining: number | null;
  /** INV-SNAPSHOT-01: false = the supplier did not confirm how many are left; nothing more sells. */
  availability_confirmed?: boolean;
  valid_from: string;
  valid_until: string;
  lead_time_days: number;
  is_active: boolean;
  deactivated_reason: string;
  state: SupplierOfferState;
  updated_at: string;
  /** Present ONLY for `costs.view`. Absent is «you may not see it», not zero. */
  agreed_cost?: string | null;
};

export type SupplierOfferWrite = Partial<{
  /** INV-SNAPSHOT-01: the person confirms the quantity as the supplier stated it (even the same number). */
  availability_confirmed: boolean;
  supplier_id: number;
  product_id: number;
  agreed_cost: string | null;
  commercial_price: string;
  confirmed_quantity: number | null;
  valid_from: string;
  valid_until: string;
  lead_time_days: number;
  is_active: boolean;
}>;

export type SupplierOfferHistoryEntry = {
  action: string;
  at: string;
  actor: string | null;
  metadata: Record<string, unknown>;
};

export const SUPPLIER_OFFER_ACTION_LABELS: Record<string, string> = {
  supplier_offer_created: "Creada",
  supplier_offer_updated: "Modificada",
  supplier_offer_deactivated: "Desactivada",
  supplier_offer_expired: "Vencida",
};

export type SupplierSettlementRow = {
  supplier_id: number;
  supplier_name: string;
  units: number;
  amount: string;
  units_without_cost: number;
};

export type SupplierImportRow = {
  row_number: number;
  action: "create" | "update" | "no_change" | "error" | "skip";
  errors: string[];
  warnings: string[];
  data: {
    supplier?: string;
    product_name?: string;
    commercial_price?: string | null;
    confirmed_quantity?: number | string | null;
    valid_from?: string | null;
    valid_until?: string | null;
    lead_time_days?: number | string | null;
    agreed_cost?: string | null;
  };
};

export type SupplierImportPreview = {
  id: number;
  status: string;
  rows_total: number;
  rows_create: number;
  rows_update: number;
  rows_no_change: number;
  rows_error: number;
  /** The file had a cost column and this person may not see costs: it was ignored, not read. */
  cost_column_ignored: boolean;
  rows: SupplierImportRow[];
};

export type SupplierImportResult = { status: "applied"; created: number; updated: number; unchanged: number };

export type ProductSaleMode = "stock" | "stock_or_order";

export const PRODUCT_SALE_MODE_LABELS: Record<ProductSaleMode, string> = {
  stock: "Sólo stock propio",
  stock_or_order: "Stock o bajo pedido",
};

function withQuery(path: string, params: Record<string, string | number | undefined | null>, companyId?: number | null) {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") qs.set(key, String(value));
  }
  if (companyId) qs.set("company", String(companyId));
  const query = qs.toString();
  return query ? `${path}?${query}` : path;
}

function listOf<T>(data: T[] | { results?: T[] } | null | undefined): T[] {
  return Array.isArray(data) ? data : data?.results ?? [];
}

export function fetchSuppliers(
  params: { search?: string; active?: "true" | "false" | ""; page?: number } = {},
  companyId?: number | null,
): Promise<Paginated<SupplierRow>> {
  return catalogCall<Paginated<SupplierRow> | SupplierRow[]>(
    withQuery("/admin/suppliers/", { search: params.search, active: params.active, page: params.page }, companyId),
    {}, "No se pudieron cargar los proveedores.",
  ).then((data) => (Array.isArray(data)
    ? { count: data.length, page: 1, page_size: data.length, results: data }
    : { ...data, results: data?.results ?? [] }));
}

export function createSupplier(body: SupplierWrite, companyId?: number | null): Promise<SupplierRow> {
  return catalogCall(withQuery("/admin/suppliers/", {}, companyId), jsonInit("POST", body), "No se pudo crear el proveedor.");
}

export function updateSupplier(id: number, body: SupplierWrite, companyId?: number | null): Promise<SupplierRow> {
  return catalogCall(withQuery(`/admin/suppliers/${id}/`, {}, companyId), jsonInit("PATCH", body), "No se pudo guardar el proveedor.");
}

export function fetchSupplierSettlement(companyId?: number | null): Promise<SupplierSettlementRow[]> {
  return catalogCall<{ results: SupplierSettlementRow[] } | SupplierSettlementRow[]>(
    withQuery("/admin/suppliers/settlement/", {}, companyId), {}, "No se pudo cargar lo pendiente de liquidar.",
  ).then(listOf);
}

export function fetchSupplierOffers(
  params: { supplier?: number | ""; product?: number | ""; state?: "active" | "expired" | "inactive" | ""; page?: number } = {},
  companyId?: number | null,
): Promise<SupplierOffer[]> {
  return catalogCall<{ results: SupplierOffer[] } | SupplierOffer[]>(
    withQuery("/admin/supplier-offers/", {
      supplier: params.supplier, product: params.product, state: params.state, page: params.page,
    }, companyId),
    {}, "No se pudieron cargar las ofertas.",
  ).then(listOf);
}

export function createSupplierOffer(body: SupplierOfferWrite, companyId?: number | null): Promise<SupplierOffer> {
  return catalogCall(withQuery("/admin/supplier-offers/", {}, companyId), jsonInit("POST", body), "No se pudo crear la oferta.");
}

export function updateSupplierOffer(id: number, body: SupplierOfferWrite, companyId?: number | null): Promise<SupplierOffer> {
  return catalogCall(withQuery(`/admin/supplier-offers/${id}/`, {}, companyId), jsonInit("PATCH", body), "No se pudo guardar la oferta.");
}

export function deactivateSupplierOffer(id: number, reason: string, companyId?: number | null): Promise<SupplierOffer> {
  return catalogCall(
    withQuery(`/admin/supplier-offers/${id}/deactivate/`, {}, companyId),
    jsonInit("POST", { reason }), "No se pudo desactivar la oferta.",
  );
}

export function fetchSupplierOfferHistory(id: number, companyId?: number | null): Promise<SupplierOfferHistoryEntry[]> {
  return catalogCall<{ results: SupplierOfferHistoryEntry[] }>(
    withQuery(`/admin/supplier-offers/${id}/history/`, {}, companyId), {}, "No se pudo cargar el historial.",
  ).then(listOf);
}

/** Absolute URLs for the browser to navigate to — downloads, not fetches. */
export function supplierOfferTemplateUrl(companyId?: number | null): string {
  return `${API_BASE}${withQuery("/admin/supplier-offers/import/template/", {}, companyId)}`;
}

export function supplierOfferExportUrl(companyId?: number | null): string {
  return `${API_BASE}${withQuery("/admin/supplier-offers/export/", {}, companyId)}`;
}

/** Multipart, WITHOUT a Content-Type: `fetchWithAuth` leaves it to the browser (IMPORT-UPLOAD-415). */
export function previewSupplierOfferImport(file: File, companyId?: number | null): Promise<SupplierImportPreview> {
  const form = new FormData();
  form.append("file", file);
  return catalogCall(
    withQuery("/admin/supplier-offers/import/preview/", {}, companyId),
    { method: "POST", body: form }, "No se pudo leer el archivo.",
  );
}

export function applySupplierOfferImport(id: number, companyId?: number | null): Promise<SupplierImportResult> {
  return catalogCall(
    withQuery(`/admin/supplier-offers/import/${id}/apply/`, {}, companyId),
    jsonInit("POST", {}), "No se pudo aplicar el archivo.",
  );
}

/** `products.manage`. A used variant cannot be sold on order: the server answers 400 and says so. */
export function updateProductSaleMode(
  productId: number, saleMode: ProductSaleMode, companyId?: number | null,
): Promise<{ sale_mode: ProductSaleMode }> {
  return catalogCall(
    withQuery(`/admin/products/${productId}/sale-mode/`, {}, companyId),
    jsonInit("PATCH", { sale_mode: saleMode }), "No se pudo cambiar el modo de venta.",
  );
}

// ── Supply of an order ──────────────────────────────────────────────────────

export type OrderSupplyLine = {
  id: number;
  supply_origin: "own" | "supplier";
  supply_status: string;
  supply_note: string;
  lead_time_days: number | null;
  supplier_offer_id: number | null;
  supplier_name: string;
  /** SUPPLIER-DEVICE-FLOW-02 — lo vendido y a qué sucursal. */
  order_id?: number;
  product_id?: number;
  product_name?: string;
  quantity?: number;
  price?: string;
  fulfillment_branch?: { id: number; name: string } | null;
  /** Sólo con `costs.view` (la clave ausente = no ve costos). */
  agreed_unit_cost?: string | null;
  received_unit_cost?: string | null;
  received_cost_source?: "" | "real" | "agreed";
  unit_cost_difference?: string | null;
};

/**
 * Un equipo que llegó. La regla es la del servidor: serie siempre, IMEI si el
 * producto lo exige, el segundo IMEI sólo con el primero. `cost` (costo real
 * de ese equipo) sólo lo toma el servidor de quien tiene `costs.view`.
 */
export type ReceivedUnitInput = { serial_number: string; imei: string; imei2?: string; notes?: string; cost?: string };

/** Lo que una línea recibida ya trajo (respuesta 409 `already_received`). */
export type ReceivedUnitRecord = { serial_number: string; imei: string; imei2: string };

/**
 * Una recepción rechazada. `line` (1 = primer equipo) y `unitField` dicen qué
 * campo de qué fila marcar; `code === "already_received"` trae lo ya recibido.
 */
export class SupplyReceiveError extends CatalogApiError {
  constructor(
    message: string,
    status: number,
    code: string | null,
    field: string | null,
    readonly line: number | null,
    readonly unitField: string | null,
    readonly units: ReceivedUnitRecord[],
  ) {
    super(message, status, code, field);
    this.name = "SupplyReceiveError";
  }
}

function supplyPath(orderId: number, itemId: number, action: "confirm" | "receive" | "fail") {
  return `/admin/orders/${orderId}/supply/${itemId}/${action}/`;
}

export function confirmOrderSupply(
  orderId: number, itemId: number, offerId?: number | null, companyId?: number | null,
): Promise<OrderSupplyLine> {
  return catalogCall(
    withQuery(supplyPath(orderId, itemId, "confirm"), {}, companyId),
    jsonInit("POST", offerId ? { offer_id: offerId } : {}), "No se pudo confirmar con el proveedor.",
  );
}

export async function receiveOrderSupply(
  orderId: number, itemId: number, units?: ReceivedUnitInput[], companyId?: number | null,
  options: { unitCost?: string } = {},
): Promise<OrderSupplyLine> {
  const body: Record<string, unknown> = {};
  if (units && units.length) body.units = units;
  if (options.unitCost) body.unit_cost = options.unitCost;
  const res = await fetchWithAuth(`${API_BASE}${withQuery(supplyPath(orderId, itemId, "receive"), {}, companyId)}`,
    jsonInit("POST", body));
  const answer = await res.json().catch(() => null);
  if (res.ok) return answer as OrderSupplyLine;
  const text = (key: string) => (answer && typeof answer[key] === "string" ? (answer[key] as string) : null);
  const message = text("detail")
    || (res.status === 403 ? "No tienes permisos para esta operación." : "No se pudo registrar la recepción.");
  throw new SupplyReceiveError(
    message, res.status, text("code"), text("field"),
    answer && typeof answer.line === "number" ? answer.line : null,
    text("unit_field"),
    answer && Array.isArray(answer.units) ? (answer.units as ReceivedUnitRecord[]) : [],
  );
}

/**
 * SUPPLIER-MARGIN (D2) — «Registrar costo real» de una línea recibida cuyo costo
 * real quedó pendiente. `costs.view` + lo que pide recibir. No toca el Kardex.
 */
export function recordOrderSupplyRealCost(
  orderId: number, itemId: number, unitCost: string, companyId?: number | null,
): Promise<OrderSupplyLine> {
  return catalogCall(
    withQuery(`/admin/orders/${orderId}/supply/${itemId}/real-cost/`, {}, companyId),
    jsonInit("POST", { unit_cost: unitCost }), "No se pudo registrar el costo real.",
  );
}

export function failOrderSupply(
  orderId: number, itemId: number, reason: string, companyId?: number | null,
): Promise<OrderSupplyLine> {
  return catalogCall(
    withQuery(supplyPath(orderId, itemId, "fail"), {}, companyId),
    jsonInit("POST", { reason }), "No se pudo registrar que no se abasteció.",
  );
}
