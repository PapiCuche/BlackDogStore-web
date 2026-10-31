/**
 * DOC-UX-02 · la vista previa de un documento impreso, con datos inventados.
 *
 *   POST /api/admin/document-preview/?company=<id>   {"document": "<tipo>"}   `company.manage`
 *
 * Responde un PDF (`application/pdf`, `Cache-Control: no-store`) dibujado con las
 * mismas funciones que los papeles reales. NO escribe nada. El servidor no dice si
 * está en «SUNAT BETA» (ni en una cabecera ni en el cuerpo), así que esta capa
 * tampoco lo afirma: lo que SÍ garantiza el servidor es «MUESTRA · NO FISCAL» en
 * cada página.
 */
import { fetchWithAuth } from "../../lib/auth";
import { API_BASE } from "../../lib/api";

export type PreviewKind =
  | "sales_note" | "order_receipt" | "ticket80_sales_note"
  | "boleta" | "factura" | "ticket80_boleta" | "ticket80_factura";

export type PreviewOption = {
  kind: PreviewKind;
  label: string;
  /** Papel en que se imprime. */
  paper: "A4" | "Ticket 80 mm";
  /** Comprobante electrónico (boleta o factura): el papel imita uno, sin validez tributaria. */
  fiscal: boolean;
};

/** Los siete que el servidor acepta, en el orden en que se ofrecen. */
export const PREVIEW_OPTIONS: readonly PreviewOption[] = [
  { kind: "sales_note", label: "Nota de venta interna", paper: "A4", fiscal: false },
  { kind: "order_receipt", label: "Constancia de pedido", paper: "A4", fiscal: false },
  { kind: "ticket80_sales_note", label: "Ticket de nota de venta", paper: "Ticket 80 mm", fiscal: false },
  { kind: "boleta", label: "Boleta de venta electrónica", paper: "A4", fiscal: true },
  { kind: "factura", label: "Factura electrónica", paper: "A4", fiscal: true },
  { kind: "ticket80_boleta", label: "Ticket de boleta", paper: "Ticket 80 mm", fiscal: true },
  { kind: "ticket80_factura", label: "Ticket de factura", paper: "Ticket 80 mm", fiscal: true },
];

export class DocumentPreviewError extends Error {
  constructor(message: string, readonly status: number | null) {
    super(message);
    this.name = "DocumentPreviewError";
  }
}

export type DocumentPreviewResult = { blob: Blob; fileName: string };

export const FALLBACK_FILE_NAME = "MUESTRA-documento.pdf";

/** El nombre lo pone el servidor (`MUESTRA-….pdf`); sólo se acepta si no trae rutas. */
export function fileNameFrom(disposition: string | null): string {
  const served = /filename="([^"\\/]+)"/.exec(disposition ?? "")?.[1];
  return served || FALLBACK_FILE_NAME;
}

async function messageFor(res: Response): Promise<string> {
  let data: unknown = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  const body = (data && typeof data === "object" ? data : {}) as Record<string, unknown>;
  const detail = typeof body.detail === "string" ? body.detail : "";

  if (res.status === 403) {
    return "No tienes permiso para ver la vista previa de documentos de esta empresa. "
      + "Pídela a quien administra la empresa (capacidad «company.manage»).";
  }
  if (res.status === 429) {
    const wait = Number(res.headers.get("Retry-After"));
    return Number.isFinite(wait) && wait > 0
      ? `Pediste demasiadas vistas previas seguidas. Vuelve a intentarlo en unos ${Math.ceil(wait)} segundos.`
      : "Pediste demasiadas vistas previas seguidas. Espera un momento y vuelve a intentarlo.";
  }
  if (detail) return detail;
  // 400: el servidor responde `{"document": ["Elige uno de: …"]}`.
  const doc = body.document;
  if (Array.isArray(doc) && doc.length) return doc.map(String).join(" ");
  return "No se pudo generar la vista previa. Inténtalo de nuevo.";
}

/**
 * Pide la muestra de un documento. `signal` permite descartar una petición lenta
 * cuando ya se eligió otro: un `AbortError` se propaga tal cual (no es un fallo).
 */
export async function fetchDocumentPreview(
  kind: PreviewKind,
  companyId: number | null,
  signal?: AbortSignal,
): Promise<DocumentPreviewResult> {
  const query = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  let res: Response;
  try {
    res = await fetchWithAuth(`${API_BASE}/admin/document-preview/${query}`, {
      method: "POST",
      body: JSON.stringify({ document: kind }),
      signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    if (signal?.aborted) throw err;
    throw new DocumentPreviewError("No se pudo conectar con el servidor. Revisa tu conexión.", null);
  }
  if (!res.ok) throw new DocumentPreviewError(await messageFor(res), res.status);

  const type = (res.headers.get("Content-Type") ?? "").toLowerCase();
  if (!type.startsWith("application/pdf")) {
    throw new DocumentPreviewError("El servidor no devolvió un PDF.", res.status);
  }
  const blob = await res.blob();
  return { blob, fileName: fileNameFrom(res.headers.get("Content-Disposition")) };
}
