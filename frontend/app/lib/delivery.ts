import { API_BASE } from "./api";
import { fetchWithAuth } from "./auth";

/**
 * DELIVERY-01 — the panel's side: a company's carriers and the delivery record
 * of an order. What may be read or written, and by whom, is the server's
 * answer (`can_manage`, `can_edit`, `allowed_modes`); nothing here decides it.
 */

export type CarrierKind = "agency" | "courier";

export type Carrier = {
  id: number;
  name: string;
  kind: CarrierKind;
  kind_label: string;
  /** https, with `{guia}` where the waybill number goes. May be empty. */
  tracking_url_template: string;
  is_active: boolean;
};

export type CarrierList = { results: Carrier[]; can_manage: boolean };

export type DeliveryMode = "" | "pickup_store" | "local_delivery" | "national_address" | "agency_pickup";

export const DELIVERY_MODE_LABELS: Record<string, string> = {
  pickup_store: "Recojo en tienda",
  local_delivery: "Delivery local",
  national_address: "Envío nacional a domicilio",
  agency_pickup: "Recojo en agencia",
};

export const FREIGHT_PAID_BY_LABELS: Record<string, string> = {
  customer_at_destination: "El cliente, al recibir",
  store: "La tienda",
};

/** An order's delivery as staff read it; `exists: false` is the same shape, empty. */
export type OrderDelivery = {
  exists: boolean;
  can_edit: boolean;
  mode: DeliveryMode;
  mode_label: string;
  allowed_modes: DeliveryMode[];
  recipient_name: string;
  recipient_document_type: "" | "dni" | "ruc" | "ce";
  recipient_document_number: string;
  recipient_phone: string;
  address_line: string;
  district: string;
  city: string;
  department: string;
  reference: string;
  carrier: number | null;
  carrier_name: string;
  agency_name: string;
  agency_locality: string;
  /** Information only: never part of the order's total. */
  freight_amount: string | null;
  freight_paid_by: "" | "customer_at_destination" | "store";
  waybill_number: string;
  tracking_code: string;
  tracking_url: string;
  shipped_at: string | null;
  updated_by: string | null;
  updated_at: string | null;
};

export type OrderDeliveryChanges = Partial<Pick<OrderDelivery,
  "mode" | "recipient_name" | "recipient_document_type" | "recipient_document_number" | "recipient_phone"
  | "carrier" | "agency_name" | "agency_locality" | "freight_amount" | "freight_paid_by"
  | "waybill_number" | "tracking_code" | "tracking_url" | "shipped_at">>;

/** A write the server refused: `fields` is what it said, field by field. */
export class DeliveryError extends Error {
  readonly status: number;
  readonly fields: Record<string, string>;

  constructor(message: string, status: number, fields: Record<string, string> = {}) {
    super(message);
    this.name = "DeliveryError";
    this.status = status;
    this.fields = fields;
  }
}

async function refusal(res: Response, fallback: string): Promise<never> {
  const body = await res.json().catch(() => null);
  const fields: Record<string, string> = {};
  if (body && typeof body === "object") {
    for (const [key, said] of Object.entries(body as Record<string, unknown>)) {
      if (key === "detail" || key === "code") continue;
      if (Array.isArray(said) && said.length > 0) fields[key] = String(said[0]);
      else if (typeof said === "string" && said) fields[key] = said;
    }
  }
  const detail = body && typeof (body as { detail?: unknown }).detail === "string" ? (body as { detail: string }).detail : "";
  if (res.status === 403) throw new DeliveryError(detail || "No tienes permisos para esto.", 403);
  throw new DeliveryError(
    detail || (Object.keys(fields).length > 0 ? "Revisa los datos marcados." : fallback),
    res.status,
    fields,
  );
}

export async function fetchCarriers(): Promise<CarrierList> {
  const res = await fetchWithAuth(`${API_BASE}/admin/carriers/`);
  if (!res.ok) return refusal(res, "No se pudieron cargar los transportistas.");
  return res.json();
}

export async function createCarrier(data: Pick<Carrier, "name" | "kind" | "tracking_url_template">): Promise<Carrier> {
  const res = await fetchWithAuth(`${API_BASE}/admin/carriers/`, { method: "POST", body: JSON.stringify(data) });
  if (!res.ok) return refusal(res, "No se pudo guardar el transportista.");
  return res.json();
}

export async function updateCarrier(
  id: number,
  data: Partial<Pick<Carrier, "name" | "kind" | "tracking_url_template" | "is_active">>,
): Promise<Carrier> {
  const res = await fetchWithAuth(`${API_BASE}/admin/carriers/${id}/`, { method: "PATCH", body: JSON.stringify(data) });
  if (!res.ok) return refusal(res, "No se pudo guardar el transportista.");
  return res.json();
}

export async function updateOrderDelivery(orderId: number, changes: OrderDeliveryChanges): Promise<OrderDelivery> {
  const res = await fetchWithAuth(`${API_BASE}/admin/orders/${orderId}/delivery/`, {
    method: "PATCH",
    body: JSON.stringify(changes),
  });
  if (!res.ok) return refusal(res, "No se pudo guardar la entrega.");
  return res.json();
}

/** Only an https address is ever a link. Anything else is shown as nothing. */
export function safeTrackingUrl(url: string | null | undefined): string {
  return typeof url === "string" && url.startsWith("https://") ? url : "";
}
