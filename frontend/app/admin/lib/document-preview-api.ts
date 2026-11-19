/**
 * DOC-UX-02 · la vista previa de un documento impreso, con datos inventados.
 *
 *   POST /api/admin/document-preview/?company=<id>   {"document": "<tipo>", "design"?: {…}}   `company.manage`
 *
 * `design` (DOC-DESIGN-03) es un BORRADOR del diseño de los documentos internos: el
 * servidor lo valida como un guardado, lo dibuja en la muestra y NO lo guarda.
 *
 * Responde un PDF (`application/pdf`, `Cache-Control: no-store`) dibujado con las
 * mismas funciones que los papeles reales. NO escribe nada. El servidor no dice si
 * está en «SUNAT BETA» (ni en una cabecera ni en el cuerpo), así que esta capa
 * tampoco lo afirma: lo que SÍ garantiza el servidor es «MUESTRA · NO FISCAL» en
 * cada página.
 *
 * DOC-PREVIEW-ESCPOS · la térmica:
 *
 *   POST /api/admin/document-preview/thermal/?company=<id>   {"document": "sales_note", "design"?: {…},
 *                                                             "print_width_dots"?: 576 | 512 | 384}
 *
 * Mismas reglas (capacidad, borrador, límite del cuerpo y cupo). Responde JSON: el rollo de la nota de
 * muestra leído de los MISMOS bytes ESC/POS que recibiría la impresora (columnas, puntos y renglones con su
 * alineación, negrita y doble tamaño; el logo como PNG de un bit del mapa de puntos).
 */
import { fetchWithAuth } from "../../lib/auth";
import { API_BASE } from "../../lib/api";
import type { DocumentDesign } from "./document-design-api";

/** Los PDF que acepta `/admin/document-preview/`. */
export type PdfPreviewKind =
  | "sales_note" | "order_receipt" | "ticket80_sales_note"
  | "boleta" | "factura" | "ticket80_boleta" | "ticket80_factura";

/** DOC-PREVIEW-ESCPOS: la nota de venta en la térmica (`/admin/document-preview/thermal/`). */
export type ThermalPreviewKind = "thermal_sales_note";

export type PreviewKind = PdfPreviewKind | ThermalPreviewKind;

export type PreviewOption = {
  kind: PreviewKind;
  label: string;
  /** Papel en que se imprime. */
  paper: "A4" | "Ticket 80 mm" | "Térmica · 48, 42 o 32 columnas";
  /** Comprobante electrónico (boleta o factura): el papel imita uno, sin validez tributaria. */
  fiscal: boolean;
  /** `pdf`: un PDF dibujado por el servidor. `escpos`: el rollo que imprimiría la térmica, leído de sus bytes. */
  format: "pdf" | "escpos";
};

/** Los siete PDF y la térmica que el servidor acepta, en el orden en que se ofrecen. */
export const PREVIEW_OPTIONS: readonly PreviewOption[] = [
  { kind: "sales_note", label: "Nota de venta interna", paper: "A4", fiscal: false, format: "pdf" },
  { kind: "order_receipt", label: "Constancia de pedido", paper: "A4", fiscal: false, format: "pdf" },
  { kind: "ticket80_sales_note", label: "Ticket de nota de venta", paper: "Ticket 80 mm", fiscal: false, format: "pdf" },
  {
    kind: "thermal_sales_note", label: "Nota de venta en la térmica (ESC/POS)", paper: "Térmica · 48, 42 o 32 columnas",
    fiscal: false, format: "escpos",
  },
  { kind: "boleta", label: "Boleta de venta electrónica", paper: "A4", fiscal: true, format: "pdf" },
  { kind: "factura", label: "Factura electrónica", paper: "A4", fiscal: true, format: "pdf" },
  { kind: "ticket80_boleta", label: "Ticket de boleta", paper: "Ticket 80 mm", fiscal: true, format: "pdf" },
  { kind: "ticket80_factura", label: "Ticket de factura", paper: "Ticket 80 mm", fiscal: true, format: "pdf" },
];

/** Un renglón del rollo térmico, tal como lo imprime la impresora. */
export type ThermalLine =
  | { type: "text"; text: string; align: ThermalAlign; bold: boolean; large: boolean }
  | { type: "image"; align: ThermalAlign; width: number; height: number; png: string }
  | { type: "qr"; align: ThermalAlign; payload: string }
  | { type: "feed"; lines: number }
  | { type: "cut" };

export type ThermalAlign = "left" | "center" | "right";

/**
 * THERMAL-512 · los anchos que imprime una térmica, en puntos: 576 (80 mm, 48 columnas), 512 (80 mm que imprime
 * 64 mm, 42 columnas) y 384 (58 mm, 32 columnas). Sin elegir, el servidor dibuja 576. Etiquetas cortas a
 * propósito: el selector cabe en un teléfono de 320 px (AGENTS.md §7).
 */
export const THERMAL_WIDTHS: readonly { dots: number; label: string }[] = [
  { dots: 576, label: "80 mm · 48 col" },
  { dots: 512, label: "80 mm · 42 col (512 puntos)" },
  { dots: 384, label: "58 mm · 32 col" },
];

export type ThermalPreview = {
  document: "sales_note";
  paper_width_mm: number;
  /** Caracteres por renglón con la letra normal (la doble ocupa dos). */
  columns: number;
  /** Puntos de ancho del cabezal. */
  dots: number;
  /** Puntos por milímetro del cabezal (8 en uno de 203 ppp). */
  dots_per_mm: number;
  sample: string;
  lines: ThermalLine[];
};

export class DocumentPreviewError extends Error {
  /** Segundos que el servidor pide esperar (`Retry-After` de un 429); `null` si no lo dijo. */
  readonly retryAfter: number | null;

  constructor(message: string, readonly status: number | null, retryAfter: number | null = null) {
    super(message);
    this.name = "DocumentPreviewError";
    this.retryAfter = retryAfter;
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
  // 400 de un borrador: `{"design": {"campo": ["…"]}}`.
  const design = body.design;
  if (design && typeof design === "object") {
    const messages = Object.values(design as Record<string, unknown>).flatMap((v) => (Array.isArray(v) ? v : [v]));
    if (messages.length) return `El diseño tiene un error: ${messages.map(String).join(" ")}`;
  }
  return "No se pudo generar la vista previa. Inténtalo de nuevo.";
}

async function post(path: string, body: unknown, companyId: number | null, signal?: AbortSignal): Promise<Response> {
  const query = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  let res: Response;
  try {
    res = await fetchWithAuth(`${API_BASE}/admin/document-preview/${path}${query}`, {
      method: "POST",
      body: JSON.stringify(body),
      signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    if (signal?.aborted) throw err;
    throw new DocumentPreviewError("No se pudo conectar con el servidor. Revisa tu conexión.", null);
  }
  if (!res.ok) {
    const wait = Number(res.headers.get("Retry-After"));
    throw new DocumentPreviewError(await messageFor(res), res.status, Number.isFinite(wait) && wait > 0 ? wait : null);
  }
  return res;
}

/**
 * Pide la muestra de un documento. `signal` permite descartar una petición lenta
 * cuando ya se eligió otro: un `AbortError` se propaga tal cual (no es un fallo).
 */
export async function fetchDocumentPreview(
  kind: PdfPreviewKind,
  companyId: number | null,
  signal?: AbortSignal,
  design?: Partial<DocumentDesign>,
): Promise<DocumentPreviewResult> {
  const res = await post("", design ? { document: kind, design } : { document: kind }, companyId, signal);
  const type = (res.headers.get("Content-Type") ?? "").toLowerCase();
  if (!type.startsWith("application/pdf")) {
    throw new DocumentPreviewError("El servidor no devolvió un PDF.", res.status);
  }
  const blob = await res.blob();
  return { blob, fileName: fileNameFrom(res.headers.get("Content-Disposition")) };
}

const ALIGNS: readonly string[] = ["left", "center", "right"];

/** Un renglón que esta pantalla sabe dibujar; cualquier otra cosa invalida la respuesta. */
function isThermalLine(value: unknown): value is ThermalLine {
  if (!value || typeof value !== "object") return false;
  const line = value as Record<string, unknown>;
  switch (line.type) {
    case "text":
      return typeof line.text === "string" && ALIGNS.includes(String(line.align))
        && typeof line.bold === "boolean" && typeof line.large === "boolean";
    case "image":
      return ALIGNS.includes(String(line.align)) && Number.isInteger(line.width) && Number.isInteger(line.height)
        && typeof line.png === "string" && /^[A-Za-z0-9+/]+=*$/.test(line.png);
    case "qr":
      return ALIGNS.includes(String(line.align)) && typeof line.payload === "string";
    case "feed":
      return Number.isInteger(line.lines);
    case "cut":
      return true;
    default:
      return false;
  }
}

/**
 * DOC-PREVIEW-ESCPOS · el rollo térmico de la nota de muestra, con el borrador del diseño si lo hay.
 * Mismos errores que `fetchDocumentPreview`; una respuesta que no es el rollo esperado también es un error.
 */
export async function fetchThermalPreview(
  companyId: number | null,
  signal?: AbortSignal,
  design?: Partial<DocumentDesign>,
  /** THERMAL-512: el ancho en puntos (`THERMAL_WIDTHS`); sin él, el del servidor (576). */
  dots?: number,
): Promise<ThermalPreview> {
  const body = {
    document: "sales_note", ...(design ? { design } : {}), ...(dots !== undefined ? { print_width_dots: dots } : {}),
  };
  const res = await post("thermal/", body, companyId, signal);
  let data: unknown = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  const roll = (data && typeof data === "object" ? data : {}) as Partial<ThermalPreview>;
  if (!Number.isInteger(roll.columns) || !Number.isInteger(roll.dots) || !Number.isInteger(roll.dots_per_mm)
      || !Array.isArray(roll.lines)
      || !roll.lines.every(isThermalLine)) {
    throw new DocumentPreviewError("El servidor no devolvió la vista previa de la térmica.", res.status);
  }
  return roll as ThermalPreview;
}
