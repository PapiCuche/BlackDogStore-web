/**
 * NOTE-NOT-SUBSTITUTE — la boleta o factura emitida FUERA del sistema.
 *
 * Con la facturación electrónica del sistema desactivada, cada venta recibe su
 * comprobante por otro canal de SUNAT. Aquí se registra cuál fue, para conciliar.
 * El sistema no lo verifica en SUNAT: la venta queda como «comprobante externo
 * registrado», nunca «aceptado por SUNAT». Nada de esto se imprime ni se envía.
 */
import { API_BASE } from "./api";
import { fetchWithAuth } from "./auth";
import type { ReceiptCoverage } from "./admin";

export type ExternalReceipt = {
  id: number;
  document_type: "01" | "03";
  document_type_label: string;
  series: string;
  number: number;
  document_id: string;
  issue_date: string;
  channel: string;
  channel_label: string;
  reference: string;
  status: "registered" | "void";
  status_label: string;
  /** Emitido después del día del cobro: el comprobante correspondía al cobrar o entregar. */
  issued_after_payment: boolean;
  registered_by: string | null;
  registered_at: string | null;
  voided_by: string | null;
  voided_at: string | null;
  void_reason: string;
};

export type ExternalReceiptBlock = {
  registrations: ExternalReceipt[];
  coverage: ReceiptCoverage;
  /** Pistas para la interfaz; el servidor vuelve a decidir. */
  can_register: boolean;
  can_void: boolean;
  document_types: { value: string; label: string }[];
  channels: { value: string; label: string }[];
  notice: string;
};

export type ExternalReceiptInput = {
  document_type: string;
  series: string;
  number: string;
  issue_date: string;
  channel: string;
  reference: string;
};

async function readDetail(res: Response, fallback: string): Promise<string> {
  try {
    const body = await res.json();
    return typeof body?.detail === "string" ? body.detail : fallback;
  } catch {
    return fallback;
  }
}

const url = (orderId: number) => `${API_BASE}/admin/orders/${orderId}/external-receipt/`;

/** `null` cuando quien consulta no puede ver comprobantes (403) o el pedido no existe para él (404). */
export async function fetchExternalReceipts(orderId: number): Promise<ExternalReceiptBlock | null> {
  const res = await fetchWithAuth(url(orderId));
  if (res.status === 403 || res.status === 404) return null;
  if (!res.ok) throw new Error(await readDetail(res, "No se pudo consultar el comprobante externo."));
  return res.json();
}

export async function registerExternalReceipt(
  orderId: number, input: ExternalReceiptInput,
): Promise<ExternalReceiptBlock> {
  const res = await fetchWithAuth(url(orderId), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) throw new Error(await readDetail(res, "No se pudo registrar el comprobante."));
  return res.json();
}

export async function voidExternalReceipt(
  orderId: number, receiptId: number, reason: string,
): Promise<ExternalReceiptBlock> {
  const res = await fetchWithAuth(`${url(orderId)}${receiptId}/void/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  });
  if (!res.ok) throw new Error(await readDetail(res, "No se pudo anular el registro."));
  return res.json();
}
