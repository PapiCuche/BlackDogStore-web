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
