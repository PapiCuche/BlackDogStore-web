/**
 * H2 — the Web client for the technical-service surface.
 *
 * IT CALLS THE SAME ENDPOINTS MOBILE DOES. `/api/v1/internal/<slug>/service/…`,
 * the ones M8 through M11 built and Mobile already consumes. There is no second
 * surface here and no `/api/admin/` variant: a parallel API is a parallel set of
 * rules, and the whole point of this phase is that Web and Mobile answer to one
 * authority.
 *
 * WHAT THIS FILE DOES NOT DO
 * --------------------------
 * · It defines NO state machine. The server sends `available_transitions` and
 *   this draws them. `received → diagnosing → …` is written down once, on the
 *   server, and a copy here would drift the first time that one changes.
 * · It computes NO money. Quote totals arrive calculated.
 * · It computes NO stock. Consuming a part is a request; the numbers come back.
 * · It computes NO quality verdict. `pass` is refused by the server if a
 *   required point is unanswered or any point failed.
 * · It decides NO authority. Capabilities come from the internal dashboard and
 *   the server re-checks every write regardless of what the UI drew.
 */

import { API_BASE } from "./api";
import { fetchWithAuth } from "./auth";

/** The service surface is slug-addressed, like the rest of `/api/v1/internal/`. */
function base(slug: string): string {
  return `${API_BASE}/v1/internal/${encodeURIComponent(slug)}/service`;
}

export class ServiceApiError extends Error {
  readonly status: number;
  /** The machine-readable discriminator the server attaches to a 409. */
  readonly code: string;

  constructor(message: string, status: number, code = "") {
    super(message);
    this.name = "ServiceApiError";
    this.status = status;
    this.code = code;
  }

  /** The capability is gone. The caller should refresh its context, not retry. */
  get isForbidden(): boolean {
    return this.status === 403;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetchWithAuth(path, init);
  if (res.status === 204) return undefined as T;

  const body = await res.json().catch(() => null);
  if (!res.ok) {
    // The server's own words when it has any. A domain refusal arrives as
    // {"detail": "..."} and is the most useful thing to show; inventing a
    // friendlier sentence here would hide what the shop was actually told.
    const detail =
      (body && typeof body === "object" && "detail" in body
        ? String((body as { detail?: unknown }).detail)
        : "") || `Error ${res.status}`;
    const code =
      body && typeof body === "object" && "code" in body
        ? String((body as { code?: unknown }).code ?? "")
        : "";
    throw new ServiceApiError(detail, res.status, code);
  }
  return body as T;
}

const get = <T,>(url: string) => request<T>(url);
const post = <T,>(url: string, payload: unknown = {}) =>
  request<T>(url, { method: "POST", body: JSON.stringify(payload) });
const patch = <T,>(url: string, payload: unknown) =>
  request<T>(url, { method: "PATCH", body: JSON.stringify(payload) });
const del = <T,>(url: string) => request<T>(url, { method: "DELETE" });

// ---------------------------------------------------------------------------
// Wire shapes — every field verified against a real response
// ---------------------------------------------------------------------------

export type ServiceStatusSetting = {
  code: string;
  label: string;
  is_customer_visible: boolean;
  sort_order: number;
};

export type ServiceContext = {
  statuses: ServiceStatusSetting[];
  available_branches: { id: number; name: string }[];
};

export type ServiceOrderRow = {
  id: number;
  number: string;
  status: string;
  status_label: string;
  customer_name: string;
  device_summary: string;
  branch_name: string;
  technician_name: string;
  received_at: string;
  updated_at: string;
};

export type ServiceTransition = { code: string; label: string };

export type ServiceOrderDetail = ServiceOrderRow & {
  reported_issue: string;
  physical_condition: string;
  received_accessories: string;
  internal_notes: string;
  received_by_name: string;
  closed_at: string | null;
  /** COMPUTED BY THE SERVER. This app draws these and nothing else. */
  available_transitions: ServiceTransition[];
};

export type ServiceHistoryEntry = {
  id: number;
  from_status: string;
  to_status: string;
  status_label: string;
  origin: string;
  comment: string;
  actor_name: string;
  is_customer_visible: boolean;
  created_at: string;
};

export type ServiceDiagnostic = {
  id: number;
  revision: number;
  status: string;
  status_label: string;
  description: string;
  root_cause: string;
  recommended_action: string;
  internal_notes: string;
  diagnosed_by_name: string;
  created_at: string;
  finalized_at: string | null;
};

export type ServiceQuoteItem = {
  id: number;
  item_type: string;
  item_type_label: string;
  description: string;
  quantity: string;
  unit_price: string;
  /** Server-computed. Never sent. */
  line_total: string;
  product: number | null;
};

export type ServiceQuote = {
  id: number;
  revision: number;
  status: string;
  status_label: string;
  currency: string;
  subtotal: string;
  discount_amount: string;
  tax_amount: string;
  total: string;
  valid_until: string | null;
  is_expired: boolean;
  is_editable: boolean;
  customer_notes: string;
  internal_notes: string;
  items: ServiceQuoteItem[];
  decision: { decision: string; reason: string; channel: string; decided_at: string } | null;
  created_by_name: string;
  sent_at: string | null;
};

export type ServicePartUsage = {
  id: number;
  quote_item_id: number;
  product_id: number;
  description: string;
  quantity: number;
  stock_movement_id: number;
  actor_name: string;
  created_at: string;
  is_reversed: boolean;
  reversed_at: string | null;
  reversed_by_name: string;
  reversal_reason: string;
};

export type ServiceExecution = {
  id: number;
  started_at: string;
  completed_at: string | null;
  is_completed: boolean;
  work_performed: string;
  result: string;
  result_label: string;
  internal_notes: string;
  started_by_name: string;
  completed_by_name: string;
  parts: ServicePartUsage[];
};

export type ServicePartCandidate = {
  quote_item_id: number;
  product_id: number;
  description: string;
  approved_quantity: number;
  used_quantity: number;
  outstanding_quantity: number;
  available_in_branch: number;
};

export type ServiceQualityItem = {
  id: number;
  code: string;
  label: string;
  is_required: boolean;
  result: string;
  notes: string;
  sort_order: number;
};

/**
 * A handover. Append-only on the server, so this app never edits one.
 *
 * There is deliberately no `idempotency_key` and no fingerprint: they are the
 * caller's own bookkeeping and echoing them back serves nothing.
 */
export type ServiceDelivery = {
  id: number;
  recipient_name: string;
  notes: string;
  delivered_by_name: string;
  delivered_at: string;
  created_at: string;
};

export type ServiceQualityCheck = {
  id: number;
  status: string;
  status_label: string;
  is_open: boolean;
  /** The list's name when it was COPIED. There is deliberately no template id. */
  template_name: string;
  notes: string;
  checked_by_name: string;
  completed_by_name: string;
  execution_id: number;
  started_at: string;
  completed_at: string | null;
  items: ServiceQualityItem[];
};

type Page<T> = { count: number; page?: number; page_size?: number; results: T[] };
type Rows<T> = { count: number; results: T[] };

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------

export const fetchServiceContext = (slug: string) =>
  get<ServiceContext>(`${base(slug)}/context/`);

export function fetchServiceOrders(
  slug: string,
  params: { status?: string; branch_id?: number | null; search?: string; page?: number } = {},
) {
  const qs = new URLSearchParams();
  if (params.status) qs.set("status", params.status);
  if (params.branch_id) qs.set("branch_id", String(params.branch_id));
  if (params.search) qs.set("search", params.search);
  if (params.page && params.page > 1) qs.set("page", String(params.page));
  const query = qs.toString();
  return get<Page<ServiceOrderRow>>(`${base(slug)}/orders/${query ? `?${query}` : ""}`);
}

const order = (slug: string, id: number) => `${base(slug)}/orders/${id}`;

export const fetchServiceOrder = (slug: string, id: number) =>
  get<ServiceOrderDetail>(`${order(slug, id)}/`);

export const fetchServiceHistory = (slug: string, id: number) =>
  get<Rows<ServiceHistoryEntry>>(`${order(slug, id)}/history/`);

export const fetchServiceDiagnostics = (slug: string, id: number) =>
  get<Rows<ServiceDiagnostic>>(`${order(slug, id)}/diagnostics/`);

export const fetchServiceQuotes = (slug: string, id: number) =>
  get<Rows<ServiceQuote>>(`${order(slug, id)}/quotes/`);

export const fetchServiceExecution = (slug: string, id: number) =>
  get<{ execution: ServiceExecution | null }>(`${order(slug, id)}/execution/`);

export const fetchServiceParts = (slug: string, id: number) =>
  get<Rows<ServicePartUsage>>(`${order(slug, id)}/parts/`);

export const fetchServicePartCandidates = (slug: string, id: number) =>
  get<Rows<ServicePartCandidate>>(`${order(slug, id)}/parts/candidates/`);

export const fetchServiceQuality = (slug: string, id: number) =>
  get<{ quality_check: ServiceQualityCheck | null }>(`${order(slug, id)}/quality/`);

export const fetchServiceQualityHistory = (slug: string, id: number) =>
  get<Rows<ServiceQualityCheck>>(`${order(slug, id)}/quality/history/`);

export const fetchServiceAssignmentOptions = (slug: string, id: number) =>
  get<{ current: { technician_name: string } | null; technicians: { id: number; name: string }[] }>(
    `${order(slug, id)}/assignment/`,
  );

// ---------------------------------------------------------------------------
// Writes — every one of them an INTENTION
// ---------------------------------------------------------------------------

/**
 * Move the order along an edge the SERVER offered.
 *
 * `status` here is always a code taken from `available_transitions`, never one
 * this app chose. The event-only states (`waiting_approval`, `approved`,
 * `rejected`, `in_repair`, `waiting_parts`, `repaired`, `quality_control`,
 * `ready_for_pickup`) are refused here by design — each has its own operation.
 */
export const transitionServiceOrder = (
  slug: string, id: number, status: string, comment: string,
) => post<ServiceOrderDetail>(`${order(slug, id)}/transition/`,
  comment.trim() ? { status, comment: comment.trim() } : { status });

export const assignTechnician = (slug: string, id: number, technicianId: number | null) =>
  post<unknown>(`${order(slug, id)}/assignment/`, { technician_id: technicianId });

export const createDiagnostic = (
  slug: string, id: number,
  body: { description: string; recommended_action: string; root_cause?: string; internal_notes?: string },
) => post<ServiceDiagnostic>(`${order(slug, id)}/diagnostics/`, body);

export const updateDiagnostic = (
  slug: string, id: number, diagnosticId: number,
  body: Partial<{ description: string; recommended_action: string; root_cause: string; internal_notes: string }>,
) => patch<ServiceDiagnostic>(`${order(slug, id)}/diagnostics/${diagnosticId}/`, body);

export const createQuote = (
  slug: string, id: number,
  body: { diagnostic_id?: number | null; customer_notes?: string; internal_notes?: string },
) => post<ServiceQuote>(`${order(slug, id)}/quotes/`, body);

/**
 * Add a line.
 *
 * `line_total` is absent on purpose: quantity × unit_price is the server's
 * multiplication, and a client that could post its own total could post one its
 * own numbers do not produce.
 */
export const addQuoteItem = (
  slug: string, id: number, quoteId: number,
  body: { item_type: string; description: string; quantity: string; unit_price: string; product_id?: number },
) => post<ServiceQuote>(`${order(slug, id)}/quotes/${quoteId}/items/`, body);

export const removeQuoteItem = (slug: string, id: number, quoteId: number, itemId: number) =>
  del<ServiceQuote>(`${order(slug, id)}/quotes/${quoteId}/items/${itemId}/`);

export const publishQuote = (slug: string, id: number, quoteId: number) =>
  post<ServiceQuote>(`${order(slug, id)}/quotes/${quoteId}/publish/`);

export const cancelQuote = (slug: string, id: number, quoteId: number) =>
  post<ServiceQuote>(`${order(slug, id)}/quotes/${quoteId}/cancel/`);

export const startRepair = (slug: string, id: number) =>
  post<ServiceExecution>(`${order(slug, id)}/execution/start/`);

export const updateExecution = (
  slug: string, id: number,
  body: Partial<{ work_performed: string; result: string; internal_notes: string }>,
) => patch<ServiceExecution>(`${order(slug, id)}/execution/`, body);

export const completeRepair = (
  slug: string, id: number, body: { work_performed: string; result: string; internal_notes?: string },
) => post<ServiceExecution>(`${order(slug, id)}/execution/complete/`, body);

export const pauseForParts = (slug: string, id: number, comment: string) =>
  post<ServiceOrderDetail>(`${order(slug, id)}/execution/pause/`,
    comment.trim() ? { comment: comment.trim() } : {});

export const resumeRepair = (slug: string, id: number) =>
  post<ServiceOrderDetail>(`${order(slug, id)}/execution/resume/`);

/**
 * Consume one approved part.
 *
 * Three fields. No branch — it is the order's, and there is no transfer in this
 * flow. No product — it is the quoted line's. No price, no stock figures, no
 * movement type. The idempotency key IS sent, because only the caller can mint
 * one that survives the caller's own retry.
 */
export const recordPartUsage = (
  slug: string, id: number,
  body: { quote_item_id: number; quantity: number; idempotency_key: string },
) => post<ServicePartUsage>(`${order(slug, id)}/parts/`, body);

export const reversePartUsage = (slug: string, id: number, usageId: number, reason: string) =>
  post<ServicePartUsage>(`${order(slug, id)}/parts/${usageId}/reverse/`,
    reason.trim() ? { reason: reason.trim() } : {});

export const startQualityCheck = (slug: string, id: number) =>
  post<ServiceQualityCheck>(`${order(slug, id)}/quality/`);

export const recordQualityResult = (
  slug: string, id: number, itemId: number, body: { result: string; notes?: string },
) => patch<ServiceQualityCheck>(`${order(slug, id)}/quality/items/${itemId}/`, body);

export const passQualityCheck = (slug: string, id: number, notes: string) =>
  post<ServiceQualityCheck>(`${order(slug, id)}/quality/pass/`,
    notes.trim() ? { notes: notes.trim() } : {});

export const failQualityCheck = (slug: string, id: number, notes: string) =>
  post<ServiceQualityCheck>(`${order(slug, id)}/quality/fail/`,
    notes.trim() ? { notes: notes.trim() } : {});

export const fetchDelivery = (slug: string, id: number) =>
  get<{ delivery: ServiceDelivery | null }>(`${order(slug, id)}/delivery/`);

/**
 * Hand the device over. Three fields, and none of them is a clock.
 *
 * `delivered_at` and `delivered_by` are the server's; sending them changes
 * nothing. The idempotency key IS sent, because only the caller can mint one
 * that survives the caller's own double-click — and a device handed over twice
 * is a record that says two different people took it.
 *
 * This does NOT record a payment. The platform has no way to charge for a
 * repair: `PaymentTransaction` is bound to an e-commerce order by a non-null
 * FK. A button here implying otherwise would be a lie the shop believes.
 */
export const recordDelivery = (
  slug: string, id: number,
  body: { recipient_name: string; notes?: string; idempotency_key: string },
) => post<ServiceDelivery>(`${order(slug, id)}/delivery/`, body);

// ---------------------------------------------------------------------------
// Capabilities — the SAME strings the backend enforces
// ---------------------------------------------------------------------------
//
// Not a Web permission model. These are the backend's own codes, and the server
// re-checks every one of them on every request. Hiding a button is courtesy; a
// 403 after a revocation is a normal outcome, not a bug.

export const CAP_ORDERS_VIEW = "service.orders.view";
export const CAP_ORDERS_CREATE = "service.orders.create";
export const CAP_ORDERS_MANAGE = "service.orders.manage";
export const CAP_DIAGNOSTIC_MANAGE = "service.diagnostic.manage";
export const CAP_REPAIR_MANAGE = "service.repair.manage";
export const CAP_QUALITY_MANAGE = "service.quality.manage";
export const CAP_DELIVERY_MANAGE = "service.delivery.manage";

/** A key that is stable for one intention and different for the next. */
export function makeIdempotencyKey(shape: string): string {
  const nonce = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
  let h = 2166136261;
  for (let i = 0; i < shape.length; i += 1) {
    h ^= shape.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return `${nonce}-${(h >>> 0).toString(36)}`.slice(0, 64);
}
