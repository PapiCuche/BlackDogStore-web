/**
 * DOC-DESIGN-03 · el diseño de los documentos INTERNOS de la empresa.
 *
 *   GET   /api/admin/document-design/?company=<id>   `company.view` o `company.manage`
 *   PATCH /api/admin/document-design/?company=<id>   `company.manage`
 *
 * Se aplica a la nota de venta (A4 y ticket 80 mm), la constancia de pedido y la
 * impresión térmica. NO a boletas ni facturas (pendiente de decisión). El servidor
 * valida todo; lo de aquí sólo evita enviar algo que va a rechazar y explica por qué.
 */
import { fetchWithAuth } from "../../lib/auth";
import { API_BASE } from "../../lib/api";

export type LogoSize = "small" | "medium" | "large";

export type DocumentDesign = {
  logo_size: LogoSize;
  show_phone: boolean;
  show_email: boolean;
  show_address: boolean;
  show_website: boolean;
  /** El correo en el ticket de 80 mm y la impresión térmica: aparte, y apagado (esos papeles nunca lo llevaron). */
  show_email_on_roll: boolean;
  footer_text: string;
  thank_you_message: string;
  hide_zero_columns: boolean;
};

export type DocumentDesignSettings = {
  design: DocumentDesign;
  logo_sizes: { value: LogoSize; label: string }[];
  limits: { footer_text: number; thank_you_message: number };
  /** Lo que imprimiría cada interruptor de contacto: los datos de la empresa, no una copia. */
  contact: { phone: string; email: string; address: string; website: string };
  can_manage: boolean;
};

/** Los valores de una empresa sin diseño guardado: los documentos como eran. El mensaje final está VACÍO. */
export const DEFAULT_DESIGN: DocumentDesign = {
  logo_size: "medium",
  show_phone: true,
  show_email: true,
  show_address: true,
  show_website: false,
  show_email_on_roll: false,
  footer_text: "",
  thank_you_message: "",
  hide_zero_columns: false,
};

export const DESIGN_FIELDS = Object.keys(DEFAULT_DESIGN) as (keyof DocumentDesign)[];

const LOGO_LABELS: Record<LogoSize, string> = { small: "Pequeño", medium: "Mediano", large: "Grande" };

/** Cómo se nombra cada campo al confirmar un cambio. */
export const FIELD_LABELS: Record<keyof DocumentDesign, string> = {
  logo_size: "Tamaño del logo",
  show_phone: "Teléfono",
  show_email: "Correo (A4)",
  show_email_on_roll: "Correo en el ticket de 80 mm y la impresión térmica",
  show_address: "Dirección",
  show_website: "Web",
  footer_text: "Pie de página",
  thank_you_message: "Mensaje final",
  hide_zero_columns: "Ocultar P. lista y Dscto. sin descuento",
};

/** Un 400: los mensajes del servidor por campo, tal cual. */
export class DocumentDesignFieldError extends Error {
  constructor(message: string, readonly fields: Record<string, string[]>) {
    super(message);
    this.name = "DocumentDesignFieldError";
  }
}

const query = (companyId: number | null) =>
  companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";

/** Igual que el servidor: un espacio entre palabras (saltos de línea incluidos), sin espacios a los lados. */
export function normaliseText(value: string): string {
  return value.normalize("NFC").split(/\s+/).filter(Boolean).join(" ");
}

/** Los caracteres que la fuente del papel (WinAnsi / cp1252) sí dibuja fuera de ASCII y Latin-1. */
const CP1252_EXTRA = new Set("€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ");

function drawable(char: string): boolean {
  const code = char.codePointAt(0) ?? 0;
  if (code >= 0x20 && code <= 0x7e) return true;
  if (code >= 0xa0 && code <= 0xff) return true;
  return CP1252_EXTRA.has(char);
}

/** Por qué un texto no se puede imprimir tal cual (las mismas reglas del servidor); `null` si se puede. */
export function textProblem(value: string, limit: number): string | null {
  const text = normaliseText(value);
  const length = Array.from(text).length;
  if (length > limit) return `Máximo ${limit} caracteres (tiene ${length}).`;
  if (/[<>]/.test(text)) return "Sólo texto plano: sin los signos < ni >.";
  const bad = Array.from(new Set(Array.from(text).filter((char) => !drawable(char))));
  if (bad.length) {
    return `El papel no puede imprimir: ${bad.join(" ")}. Escríbelo con letras o quítalo (saldría como «?»).`;
  }
  return null;
}

/** Los problemas del borrador por campo, antes de enviarlo. */
export function draftProblems(
  draft: DocumentDesign, limits: DocumentDesignSettings["limits"],
): Partial<Record<keyof DocumentDesign, string>> {
  const out: Partial<Record<keyof DocumentDesign, string>> = {};
  const footer = textProblem(draft.footer_text, limits.footer_text);
  if (footer) out.footer_text = footer;
  const thanks = textProblem(draft.thank_you_message, limits.thank_you_message);
  if (thanks) out.thank_you_message = thanks;
  return out;
}

function shown(field: keyof DocumentDesign, value: DocumentDesign[keyof DocumentDesign]): string {
  if (field === "logo_size") return LOGO_LABELS[value as LogoSize] ?? String(value);
  if (typeof value === "boolean") return value ? "sí" : "no";
  const text = normaliseText(String(value));
  return text ? `«${text}»` : "(vacío)";
}

export type DesignChange = { field: keyof DocumentDesign; label: string; from: string; to: string };

/** Qué cambia del diseño guardado al borrador, en palabras (lo que se confirma antes de guardar). */
export function designChanges(saved: DocumentDesign, draft: DocumentDesign): DesignChange[] {
  return DESIGN_FIELDS.flatMap((field) => {
    const before = saved[field];
    const after = draft[field];
    const same = typeof before === "string"
      ? normaliseText(before) === normaliseText(String(after))
      : before === after;
    return same ? [] : [{ field, label: FIELD_LABELS[field], from: shown(field, before), to: shown(field, after) }];
  });
}

/** Sólo los campos que cambian, con los textos ya normalizados: el cuerpo del PATCH y del borrador. */
export function changedFields(saved: DocumentDesign, draft: DocumentDesign): Partial<DocumentDesign> {
  const out: Record<string, unknown> = {};
  for (const change of designChanges(saved, draft)) {
    const value = draft[change.field];
    out[change.field] = typeof value === "string" ? normaliseText(value) : value;
  }
  return out as Partial<DocumentDesign>;
}

async function detail(res: Response, fallback: string): Promise<string> {
  const body = await res.json().catch(() => null);
  return body && typeof body.detail === "string" ? body.detail : fallback;
}

export async function fetchDocumentDesign(companyId: number | null): Promise<DocumentDesignSettings> {
  let res: Response;
  try {
    res = await fetchWithAuth(`${API_BASE}/admin/document-design/${query(companyId)}`);
  } catch {
    throw new Error("No se pudo conectar con el servidor. Revisa tu conexión.");
  }
  if (!res.ok) {
    throw new Error(await detail(res, res.status === 403
      ? "No tienes permiso para ver el diseño de los documentos de esta empresa."
      : "No se pudo cargar el diseño de los documentos."));
  }
  return res.json();
}

export async function updateDocumentDesign(
  companyId: number | null, change: Partial<DocumentDesign>,
): Promise<DocumentDesignSettings> {
  let res: Response;
  try {
    res = await fetchWithAuth(`${API_BASE}/admin/document-design/${query(companyId)}`, {
      method: "PATCH",
      body: JSON.stringify(change),
    });
  } catch {
    throw new Error("No se pudo conectar con el servidor. No se guardó nada.");
  }
  if (res.ok) return res.json();
  const body = await res.json().catch(() => null);
  if (res.status === 400 && body && typeof body === "object" && !("detail" in body)) {
    const fields = Object.fromEntries(
      Object.entries(body as Record<string, unknown>).map(([key, value]) => [
        key, Array.isArray(value) ? value.map(String) : [String(value)],
      ]),
    );
    throw new DocumentDesignFieldError("Revisa los campos marcados. No se guardó nada.", fields);
  }
  if (res.status === 403) {
    throw new Error("No tienes permiso para cambiar el diseño de los documentos (capacidad «company.manage»).");
  }
  if (res.status === 429) throw new Error("Demasiados intentos seguidos. Espera un momento y vuelve a guardar.");
  throw new Error(body && typeof body.detail === "string" ? body.detail : "No se pudo guardar el diseño.");
}
