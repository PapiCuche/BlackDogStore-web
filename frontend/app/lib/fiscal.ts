/**
 * El comprobante electrónico, desde el navegador.
 *
 * Ninguna de estas funciones decide si un documento es fiscalmente válido: eso
 * lo dice el backend en `status`. Aquí sólo se transporta.
 */

import { API_BASE } from "./api";
import { fetchWithAuth } from "./auth";

export type FiscalDocument = {
  id: number;
  order_id: number;
  document_type: string;
  document_type_label: string;
  /** `F001-1`, tal como va en el XML y en el nombre del archivo. */
  identifier: string;
  series: string;
  number: number;
  environment: string;
  environment_label: string;
  status: string;
  status_label: string;
  issued_at: string;
  currency: string;
  /** Cadenas, no números: un importe que pasa por `number` deja de ser exacto. */
  taxable_amount: string;
  tax_amount: string;
  total: string;
  customer_doc_number: string;
  customer_legal_name: string;
  response_code: string;
  response_message: string;
  is_accepted: boolean;
  has_xml: boolean;
  has_cdr: boolean;
  attempts: number;
  /** Pistas para la interfaz. El backend las vuelve a comprobar. */
  can_submit: boolean;
  can_retry: boolean;
  can_download_pdf: boolean;
};

async function readDetail(res: Response, fallback: string): Promise<string> {
  try {
    const body = await res.json();
    return typeof body?.detail === "string" ? body.detail : fallback;
  } catch {
    return fallback;
  }
}

/** El comprobante de esta venta, o `null` si todavía no existe. */
export async function fetchFiscalDocument(
  orderId: number,
): Promise<FiscalDocument | null> {
  const res = await fetchWithAuth(
    `${API_BASE}/admin/orders/${orderId}/fiscal-document/`,
  );
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(await readDetail(res, "No se pudo consultar el comprobante."));
  return res.json();
}

/** Emite y firma. NO habla con SUNAT: eso es `submitFiscalDocument`. */
export async function issueFiscalDocument(orderId: number): Promise<FiscalDocument> {
  const res = await fetchWithAuth(
    `${API_BASE}/admin/orders/${orderId}/fiscal-document/`,
    { method: "POST" },
  );
  if (!res.ok) throw new Error(await readDetail(res, "No se pudo emitir la factura."));
  return res.json();
}

/**
 * Envía o reintenta. Siempre el MISMO documento: misma serie, mismo correlativo
 * y mismo XML firmado. Un reintento no emite nada nuevo.
 */
export async function submitFiscalDocument(
  documentId: number,
): Promise<FiscalDocument> {
  const res = await fetchWithAuth(
    `${API_BASE}/admin/fiscal-documents/${documentId}/submit/`,
    { method: "POST" },
  );
  if (!res.ok) {
    // 409 significa que ya hay un envío en curso, no que algo haya fallado.
    throw new Error(await readDetail(res, "No se pudo enviar el comprobante."));
  }
  return res.json();
}

const ARTIFACT_PATH: Record<string, string> = {
  pdf: "pdf/",
  ticket: "pdf/?formato=ticket80",
  xml: "xml/",
  cdr: "cdr/",
};

/**
 * Descarga un artefacto y deja que el navegador lo guarde.
 *
 * EL NOMBRE LO PONE EL SERVIDOR. Componerlo aquí acabaría produciendo un nombre
 * distinto del que SUNAT espera, que es exactamente el defecto que ya apareció
 * una vez con la nota interna.
 */
export async function downloadFiscalArtifact(
  documentId: number,
  kind: "pdf" | "ticket" | "xml" | "cdr",
): Promise<void> {
  const res = await fetchWithAuth(
    `${API_BASE}/admin/fiscal-documents/${documentId}/${ARTIFACT_PATH[kind]}`,
  );
  if (!res.ok) throw new Error(await readDetail(res, "No se pudo descargar el archivo."));

  const disposition = res.headers.get("Content-Disposition") ?? "";
  const served = /filename="([^"]+)"/.exec(disposition)?.[1];

  const url = URL.createObjectURL(await res.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = served ?? `comprobante-${documentId}.${kind === "ticket" ? "pdf" : kind}`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
}
