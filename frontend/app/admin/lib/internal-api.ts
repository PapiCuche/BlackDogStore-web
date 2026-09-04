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
  constructor(message = "No tienes acceso al control interno.") {
    super(message);
    this.name = "NoInternalAccessError";
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
    throw new NoInternalAccessError(
      await readDetail(res, "No tienes acceso al control interno."),
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
  data: Record<string, string>,
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

export type PosContext = {
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
};

export type PosSaleLine = { product: number; quantity: number };

export type PosSaleResult = {
  order_id: number;
  created: boolean;
  subtotal: string;
  discount: string;
  discount_source: string;
  discount_reason: string;
  total: string;
  paid_at: string | null;
  payment_method: string;
  amount_received: string | null;
  change_amount: string | null;
  payment_reference: string;
  branch: PosBranch;
  seller: string;
  customer: string;
  commission: string | null;
  items: { product: number; name: string; quantity: number; price: string }[];
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
  const res = await fetchWithAuth(`${API_BASE}/admin/pos/products/lookup/?${qs}`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(await readDetail(res, "No se pudo leer el código."));
  return res.json();
}

export async function posSearch(
  companyId: number | null,
  branchId: number,
  q: string,
): Promise<PosProduct[]> {
  const qs = new URLSearchParams({ branch: String(branchId), q });
  if (companyId) qs.set("company", String(companyId));
  const res = await fetchWithAuth(`${API_BASE}/admin/pos/products/search/?${qs}`);
  if (!res.ok) throw new Error(await readDetail(res, "No se pudo buscar."));
  return (await res.json()).results ?? [];
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
  throw new Error(detail);
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

export type SalesDashboard = {
  company: { id: number; name: string };
  branches: PosBranch[];
  today: string;
  kpis: Record<string, SalesKpi>;
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
  seller: { id: number | null; name: string };
  customer: { id: number; name: string } | null;
  /** null unless the caller may see earnings. */
  commission: { rate_percent: string; base_amount: string; amount: string } | null;
  lines: { product: number; name: string; quantity: number; price: string }[];
};

export type PosSaleInput = {
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
  /** Shown BESIDE the historical total, never used to compute it. */
  current_rate_percent: string | null;
};

export type CommissionReport = {
  window_days: number;
  today: string;
  branches: PosBranch[];
  total_commission: string;
  note: string;
  results: CommissionRow[];
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

export type ImportJob = {
  id: number;
  import_type: "products" | "stock";
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
    unmapped?: { column: string; reason: string }[];
    branches?: { column: string; branch_id: number; branch_name: string }[];
    sheets?: string[];
    applied?: Record<string, number>;
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
};

export type InspectResult = {
  import_type: string;
  reader_notes: string[];
  sheets: InspectedSheet[];
  fields: Record<string, { label: string; required: boolean }>;
  branches: { id: number; name: string }[];
};

/**
 * POST multipart WITHOUT forcing a Content-Type.
 *
 * `fetchWithAuth` always sets `application/json`, which is right for every other
 * call in this file and fatal here: a multipart body needs the boundary the
 * browser generates, and a hand-written Content-Type has no boundary in it, so
 * the server receives a body it cannot split into parts. Deleting the header
 * lets the browser write the correct one.
 */
async function postForm<T>(path: string, form: FormData, fallback: string): Promise<T> {
  const res = await fetchWithAuth(`${API_BASE}${path}`, {
    method: "POST",
    body: form,
    headers: { "Content-Type": "" },
  });
  if (res.ok) return res.json();
  throw new Error(await readDetail(res, fallback));
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
  } = {},
): Promise<ImportJob> {
  const form = new FormData();
  form.append("file", file);
  if (opts.sheetName) form.append("sheet_name", opts.sheetName);
  if (opts.headerRow) form.append("header_row", String(opts.headerRow));
  if (opts.mapping) form.append("mapping", JSON.stringify(opts.mapping));
  if (opts.options) form.append("options", JSON.stringify(opts.options));
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
  importType?: "products" | "stock",
): Promise<ImportJob[]> {
  const qs = new URLSearchParams();
  if (companyId) qs.set("company", String(companyId));
  if (importType) qs.set("type", importType);
  const res = await fetchWithAuth(`${API_BASE}/admin/imports/?${qs}`);
  if (!res.ok) throw new Error(await readDetail(res, "No se pudo cargar el historial."));
  return (await res.json()).results ?? [];
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
