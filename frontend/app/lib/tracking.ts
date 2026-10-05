/**
 * TRACKING — el seguimiento de una reparación, visto por su cliente.
 *
 * Dos puertas, y no se mezclan:
 *
 *  · POR ENLACE, sin sesión. La credencial es el propio enlace, así que las
 *    cookies NO viajan (`credentials: "omit"`): una sesión abierta en el mismo
 *    navegador no añade nada y no debe poder cambiar lo que se ve.
 *  · DESDE LA CUENTA, con la sesión de la web: la lista de mis reparaciones,
 *    cada una con su enlace. El detalle se lee siempre por el enlace.
 *
 * Esta capa no calcula estados ni importes: dibuja lo que el servidor manda.
 */

import { API_BASE } from "./api";
import { fetchWithAuth } from "./auth";

export type TrackingQuoteItem = {
  id: number;
  description: string;
  quantity: string;
  unit_price: string;
  line_total: string;
  item_type_label: string;
};

export type TrackingQuote = {
  id: number;
  revision: number;
  status: string;
  status_label: string;
  currency: string;
  subtotal: string;
  discount_amount: string;
  total: string;
  valid_until: string | null;
  is_expired: boolean;
  can_be_decided: boolean;
  customer_notes: string;
  items: TrackingQuoteItem[];
  decision: { decision: string; decided_at: string } | null;
};

export type TrackingEvidence = {
  id: number;
  stage: string;
  stage_label: string;
  caption: string;
  width: number | null;
  height: number | null;
  created_at: string;
};

export type Tracking = {
  company: {
    name: string;
    phone: string;
    whatsapp_link: string;
    logo_url: string;
    warranty_policy_text: string;
    warranty_policy_url: string;
  };
  order: {
    number: string;
    status: string;
    status_label: string;
    device_summary: string;
    reported_issue: string;
    received_at: string;
    closed_at: string | null;
    updated_at: string;
  };
  device: {
    type_label: string;
    brand: string;
    model: string;
    /** Enmascarados por el servidor. Aquí nunca llega el valor completo. */
    serial_number: string;
    imei: string;
  } | null;
  timeline: { id: number; status: string; status_label: string; occurred_at: string }[];
  quote: TrackingQuote | null;
  can_decide: boolean;
  payments: {
    currency: string;
    quoted_total: string | null;
    paid: string;
    outstanding: string | null;
    status: string;
  };
  evidence: TrackingEvidence[];
};

export class TrackingError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "TrackingError";
    this.status = status;
  }

  /** El enlace no resuelve. No se sabe —ni se dice— por qué. */
  get isNotFound(): boolean {
    return this.status === 404;
  }
}

const link = (token: string) => `${API_BASE}/v1/tracking/${encodeURIComponent(token)}`;

async function open<T>(url: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(url, {
    ...init,
    credentials: "omit",
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
  });
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? String(body.detail) : "";
    throw new TrackingError(detail || "No se pudo consultar el seguimiento.", res.status);
  }
  return body as T;
}

export const fetchTracking = (token: string) => open<Tracking>(`${link(token)}/`);

export const decideTrackingQuote = (token: string, quoteId: number, decision: "approve" | "reject") =>
  open<{ quote: TrackingQuote }>(`${link(token)}/quotes/${quoteId}/decision/`, {
    method: "POST",
    body: JSON.stringify({ decision }),
  });

/** La foto se pide con el mismo enlace: sin él no hay imagen. */
export const trackingEvidenceUrl = (token: string, evidenceId: number) =>
  `${link(token)}/evidence/${evidenceId}/content/`;

// ---------------------------------------------------------------------------
// Desde la cuenta
// ---------------------------------------------------------------------------

export type AccountRepair = {
  number: string;
  status: string;
  status_label: string;
  device_summary: string;
  received_at: string;
  closed_at: string | null;
  updated_at: string;
  tracking_path: string | null;
};

export async function fetchMyRepairs(): Promise<AccountRepair[]> {
  const res = await fetchWithAuth(`${API_BASE}/account/repairs/`);
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new TrackingError(body?.detail || "No se pudieron cargar tus reparaciones.", res.status);
  return (body?.results ?? []) as AccountRepair[];
}

/** Saca el token de lo que la persona pegó: el enlace entero o sólo el código. */
export function tokenFromInput(value: string): string {
  const trimmed = value.trim();
  const match = /\/seguimiento\/([A-Za-z0-9_-]+)/.exec(trimmed);
  return match ? match[1] : trimmed;
}

/**
 * El enlace abre UNA orden; sumarla a la cuenta entrega todo el historial de ese
 * cliente. Por eso hace falta además el documento con el que se registró en la
 * tienda. Lo compara el servidor.
 */
export async function claimRepair(token: string, documentNumber: string): Promise<void> {
  const res = await fetchWithAuth(`${API_BASE}/account/repairs/claim/`, {
    method: "POST",
    body: JSON.stringify({ token, document_number: documentNumber }),
  });
  if (res.ok) return;
  const body = await res.json().catch(() => null);
  throw new TrackingError(
    res.status === 404
      ? "Ese enlace no está disponible. Revisa que esté completo."
      : body?.detail || "No se pudo agregar la orden a tu cuenta.",
    res.status,
  );
}
