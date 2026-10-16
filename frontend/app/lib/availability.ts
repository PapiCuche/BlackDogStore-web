/**
 * COMMERCE-ADMIN-UX-01 — «Ventas › Consultar disponibilidad». Read only.
 *
 * The server answers everything: what the shelves hold, what each device is
 * doing, which supplier offers are valid. This layer adds nothing up — in
 * particular it never adds a supplier's quantity to the shop's own stock.
 * There is no cost in any of these types: the endpoint never sends one.
 */

import { API_BASE } from "./api";
import { fetchWithAuth } from "./auth";

export type AvailabilityCounts = {
  /** What the shelf can sell now (`BranchStock`). */
  available: number;
  /** Devices a web buyer's cart or checkout holds. */
  reserved_web: number;
  /** Devices staff set aside («apartado»). */
  reserved_staff: number;
  sold: number;
  written_off: number;
};

export type AvailabilityBranchStock = AvailabilityCounts & { branch_id: number; branch_name: string };

export type SupplierOfferSummary = {
  /** Only present when the caller may see suppliers. */
  supplier_name?: string;
  /** What the customer pays when it is sold on order. */
  commercial_price: string;
  /** What the supplier still confirms (confirmed − committed). NOT own stock. */
  remaining: number;
  valid_until: string;
  lead_time_days: number;
};

export type AvailabilityProduct = {
  id: number;
  name: string;
  barcode: string;
  model: { id: number; name: string } | null;
  category: { id: number; name: string } | null;
  line: "new" | "used";
  line_label: string;
  options: { attribute: string; kind: "text" | "color"; label: string; swatch_hex: string }[];
  is_serialized: boolean;
  price: string;
  /** Own price range of the AVAILABLE devices that carry one (used devices). */
  unit_price_range: { min: string; max: string } | null;
  sale_mode: "stock" | "stock_or_order";
  /** Whether it may actually be sold «bajo pedido» (mode + line). */
  sells_on_order: boolean;
  is_published_online: boolean;
  stock: { by_branch: AvailabilityBranchStock[]; total: AvailabilityCounts };
  supplier_offers: SupplierOfferSummary[];
};

export type AvailabilityFacets = {
  categories: { id: number; name: string }[];
  models: { id: number; name: string }[];
  attributes: { id: number; name: string; kind: "text" | "color"; options: { id: number; label: string; swatch_hex: string }[] }[];
};

export type AvailabilityPage = {
  count: number;
  page: number;
  page_size: number;
  scope: { branch: { id: number; name: string } | null; branches: { id: number; name: string }[]; is_aggregate: boolean };
  can_see_suppliers: boolean;
  results: AvailabilityProduct[];
  facets?: AvailabilityFacets;
};

export type AvailabilityUnit = {
  id: number;
  branch_id: number;
  branch_name: string;
  status: "available" | "reserved" | "sold" | "written_off";
  status_label: string;
  hold_kind: "web" | "staff" | null;
  hold_until: string | null;
  /** The last four digits only. The screen never receives the whole IMEI. */
  imei_last4: string;
  serial_last4: string;
  condition: string;
  condition_label: string;
  battery_health: number | null;
  cosmetic_grade_label: string;
  public_notes: string;
  sale_price: string | null;
  received_at: string;
  sold_at: string | null;
};

export type AvailabilityUnitsPage = {
  product: AvailabilityProduct;
  count: number;
  truncated: boolean;
  results: AvailabilityUnit[];
};

export type AvailabilityQuery = {
  q?: string;
  branch?: number | "all";
  category?: number | "";
  line?: "" | "new" | "used";
  model?: number | "";
  /** Option ids (capacity, colour…): every one must hold. */
  options?: number[];
  page?: number;
  pageSize?: number;
  facets?: boolean;
};

export type UnitStatusFilter = "live" | "all" | "available" | "reserved" | "sold" | "written_off";

export class AvailabilityError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "AvailabilityError";
    this.status = status;
  }
}

const BASE = `${API_BASE}/admin/availability/`;

async function call<T>(qs: URLSearchParams): Promise<T> {
  const query = qs.toString();
  const res = await fetchWithAuth(`${BASE}${query ? `?${query}` : ""}`);
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? String(body.detail) : "";
    throw new AvailabilityError(
      detail || (res.status === 403 ? "No tienes permiso para consultar la disponibilidad." : "No se pudo consultar la disponibilidad."),
      res.status,
    );
  }
  return body as T;
}

export function availabilityQueryString(params: AvailabilityQuery): URLSearchParams {
  const qs = new URLSearchParams();
  const q = params.q?.trim();
  if (q) qs.set("q", q);
  if (params.branch !== undefined) qs.set("branch", String(params.branch));
  if (params.category) qs.set("category", String(params.category));
  if (params.line) qs.set("line", params.line);
  if (params.model) qs.set("model", String(params.model));
  for (const option of params.options ?? []) qs.append("option", String(option));
  if (params.page && params.page > 1) qs.set("page", String(params.page));
  if (params.pageSize) qs.set("page_size", String(params.pageSize));
  if (params.facets) qs.set("facets", "1");
  return qs;
}

export const fetchAvailability = (params: AvailabilityQuery) =>
  call<AvailabilityPage>(availabilityQueryString(params));

export function fetchAvailabilityUnits(
  productId: number,
  params: { branch?: number | "all"; unitStatus?: UnitStatusFilter } = {},
): Promise<AvailabilityUnitsPage> {
  const qs = new URLSearchParams({ product: String(productId), units: "1" });
  if (params.branch !== undefined) qs.set("branch", String(params.branch));
  if (params.unitStatus && params.unitStatus !== "live") qs.set("unit_status", params.unitStatus);
  return call<AvailabilityUnitsPage>(qs);
}
