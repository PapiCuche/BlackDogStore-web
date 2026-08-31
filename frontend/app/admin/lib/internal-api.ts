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
