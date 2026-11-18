/**
 * DOC-DESIGN-03 · el diseño de los documentos INTERNOS de la empresa.
 *
 *   GET   /api/admin/document-design/?company=<id>   `company.view` o `company.manage`
 *   PATCH /api/admin/document-design/?company=<id>   `company.manage`
 *
 * Se aplica a la nota de venta (A4 y ticket 80 mm), la constancia de pedido y la
 * impresión térmica. DOC-DESIGN-04 añade, sólo para la nota de venta: título, logo en mm y
 * alineación, datos opcionales, mensajes posventa y comercial, garantía, desglose de
 * impuestos, espaciado, énfasis del total y orden de los bloques. NO a boletas ni facturas (pendiente de decisión). El servidor
 * valida todo; lo de aquí sólo evita enviar algo que va a rechazar y explica por qué.
 */
import { fetchWithAuth } from "../../lib/auth";
import { API_BASE } from "../../lib/api";

export type LogoSize = "small" | "medium" | "large";
export type LogoAlign = "left" | "center" | "right";
export type WarrantyMode = "full" | "summary";
export type TaxBreakdownMode = "detailed" | "summary" | "hidden";
export type Density = "compact" | "normal" | "airy";
export type BlockKey = "customer" | "sale" | "notes" | "warranty" | "thank_you" | "post_sale" | "commercial";

export type DocumentDesign = {
  logo_size: LogoSize;
  /** DOC-DESIGN-04: ancho del logo en mm por formato; 0 = según `logo_size`. */
  logo_a4_width_mm: number;
  logo_ticket_width_mm: number;
  logo_a4_align: LogoAlign;
  logo_ticket_align: LogoAlign;
  show_phone: boolean;
  show_email: boolean;
  show_address: boolean;
  show_website: boolean;
  /** El correo en el ticket de 80 mm y la impresión térmica: aparte, y apagado (esos papeles nunca lo llevaron). */
  show_email_on_roll: boolean;
  /** DOC-DESIGN-04: datos opcionales de la nota de venta, todos encendidos (el papel de siempre). */
  show_seller: boolean;
  show_order: boolean;
  show_channel: boolean;
  show_requested_receipt: boolean;
  show_counts: boolean;
  show_amount_words: boolean;
  show_customer_contact: boolean;
  show_delivery_address: boolean;
  document_title: string;
  footer_text: string;
  thank_you_message: string;
  post_sale_message: string;
  commercial_message: string;
  warranty_summary: string;
  warranty_mode: WarrantyMode;
  tax_breakdown_mode: TaxBreakdownMode;
  density: Density;
  emphasize_total: boolean;
  block_order: BlockKey[];
  hide_zero_columns: boolean;
};

export type TextField =
  | "document_title" | "footer_text" | "thank_you_message" | "post_sale_message" | "commercial_message"
  | "warranty_summary";

type Option<T extends string> = { value: T; label: string };

export type DocumentDesignSettings = {
  design: DocumentDesign;
  logo_sizes: Option<LogoSize>[];
  logo_aligns?: Option<LogoAlign>[];
  warranty_modes?: Option<WarrantyMode>[];
  tax_breakdown_modes?: Option<TaxBreakdownMode>[];
  densities?: Option<Density>[];
  blocks?: Option<BlockKey>[];
  logo_width_mm?: { a4: [number, number]; ticket: [number, number] };
  max_lines?: number;
  limits: Partial<Record<TextField, number>> & { footer_text: number; thank_you_message: number };
  /** Lo que imprimiría cada interruptor de contacto: los datos de la empresa, no una copia. */
  contact: { phone: string; email: string; address: string; website: string; warranty_url?: string };
  can_manage: boolean;
};

/** Los bloques informativos de la nota en el orden de hoy. La identificación legal NO es un bloque. */
export const BLOCKS: BlockKey[] = ["customer", "sale", "notes", "warranty", "thank_you", "post_sale", "commercial"];

/** Los valores de una empresa sin diseño guardado: los documentos como eran. Los mensajes están VACÍOS. */
export const DEFAULT_DESIGN: DocumentDesign = {
  logo_size: "medium",
  logo_a4_width_mm: 0,
  logo_ticket_width_mm: 0,
  logo_a4_align: "left",
  logo_ticket_align: "center",
  show_phone: true,
  show_email: true,
  show_address: true,
  show_website: false,
  show_email_on_roll: false,
  show_seller: true,
  show_order: true,
  show_channel: true,
  show_requested_receipt: true,
  show_counts: true,
  show_amount_words: true,
  show_customer_contact: true,
  show_delivery_address: true,
  document_title: "Nota de venta",
  footer_text: "",
  thank_you_message: "",
  post_sale_message: "",
  commercial_message: "",
  warranty_summary: "",
  warranty_mode: "full",
  tax_breakdown_mode: "detailed",
  density: "normal",
  emphasize_total: true,
  block_order: [...BLOCKS],
  hide_zero_columns: false,
};

export const DESIGN_FIELDS = Object.keys(DEFAULT_DESIGN) as (keyof DocumentDesign)[];

/** Los textos que conservan sus renglones (cada uno normalizado). Los demás son de una línea. */
export const MULTILINE_FIELDS: readonly TextField[] = ["thank_you_message", "post_sale_message", "commercial_message"];
export const MESSAGE_MAX_LINES = 6;
export const LOGO_WIDTH_MM = { a4: [20, 90] as [number, number], ticket: [20, 72] as [number, number] };

/** Las opciones cerradas, por si el servidor no las envía (son las mismas). */
export const OPTIONS = {
  logo_aligns: [{ value: "left", label: "Izquierda" }, { value: "center", label: "Centro" }, { value: "right", label: "Derecha" }] as Option<LogoAlign>[],
  warranty_modes: [{ value: "full", label: "Texto completo" }, { value: "summary", label: "Resumen y enlace" }] as Option<WarrantyMode>[],
  tax_breakdown_modes: [{ value: "detailed", label: "Detallado" }, { value: "summary", label: "Resumido" }, { value: "hidden", label: "Oculto" }] as Option<TaxBreakdownMode>[],
  densities: [{ value: "compact", label: "Compacto" }, { value: "normal", label: "Normal" }, { value: "airy", label: "Amplio" }] as Option<Density>[],
  blocks: [
    { value: "customer", label: "Cliente" }, { value: "sale", label: "Datos de la venta" },
    { value: "notes", label: "Observaciones" }, { value: "warranty", label: "Garantía" },
    { value: "thank_you", label: "Mensaje de agradecimiento" }, { value: "post_sale", label: "Mensaje posventa" },
    { value: "commercial", label: "Mensaje comercial" },
  ] as Option<BlockKey>[],
};

const LOGO_LABELS: Record<LogoSize, string> = { small: "Pequeño", medium: "Mediano", large: "Grande" };

/** Cómo se nombra cada campo al confirmar un cambio. */
export const FIELD_LABELS: Record<keyof DocumentDesign, string> = {
  logo_size: "Tamaño del logo",
  logo_a4_width_mm: "Ancho del logo en A4",
  logo_ticket_width_mm: "Ancho del logo en el ticket",
  logo_a4_align: "Alineación del logo en A4",
  logo_ticket_align: "Alineación del logo en el ticket",
  show_phone: "Teléfono",
  show_email: "Correo (A4)",
  show_email_on_roll: "Correo en el ticket de 80 mm y la impresión térmica",
  show_address: "Dirección",
  show_website: "Web",
  show_seller: "Vendedor",
  show_order: "Número de pedido",
  show_channel: "Canal de venta",
  show_requested_receipt: "Comprobante solicitado",
  show_counts: "Productos y unidades",
  show_amount_words: "Importe en letras",
  show_customer_contact: "Contacto del cliente",
  show_delivery_address: "Dirección de entrega",
  document_title: "Título del documento",
  footer_text: "Pie de página",
  thank_you_message: "Mensaje final",
  post_sale_message: "Mensaje posventa",
  commercial_message: "Mensaje comercial",
  warranty_summary: "Resumen de la garantía",
  warranty_mode: "Garantía",
  tax_breakdown_mode: "Desglose de impuestos",
  density: "Espaciado",
  emphasize_total: "Destacar el total",
  block_order: "Orden de los bloques",
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

/**
 * Igual que el servidor para un texto de varios renglones: cada renglón normalizado, como mucho
 * un renglón vacío entre dos escritos y ninguno al principio ni al final.
 */
export function normaliseMessage(value: string): string {
  const lines: string[] = [];
  for (const raw of value.normalize("NFC").split(/\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]/)) {
    const line = raw.split(/\s+/).filter(Boolean).join(" ");
    if (line || (lines.length && lines[lines.length - 1])) lines.push(line);
  }
  while (lines.length && !lines[lines.length - 1]) lines.pop();
  return lines.join("\n");
}

const isMultiline = (field: string) => (MULTILINE_FIELDS as readonly string[]).includes(field);

/** Un texto del diseño como lo guarda el servidor. */
export function normaliseField(field: TextField, value: string): string {
  return isMultiline(field) ? normaliseMessage(value) : normaliseText(value);
}

/** Los caracteres que la fuente del papel (WinAnsi / cp1252) sí dibuja fuera de ASCII y Latin-1. */
const CP1252_EXTRA = new Set("€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ");

function drawable(char: string): boolean {
  const code = char.codePointAt(0) ?? 0;
  if (code >= 0x20 && code <= 0x7e) return true;
  if (code >= 0xa0 && code <= 0xff) return true;
  return CP1252_EXTRA.has(char);
}

/** Los mismos rangos que el servidor (`document_design._is_emoji`). */
function isEmoji(char: string): boolean {
  const code = char.codePointAt(0) ?? 0;
  return code >= 0x1f000 || (code >= 0x2600 && code <= 0x27bf) || (code >= 0x2b00 && code <= 0x2bff)
    || code === 0xfe0f || code === 0x200d || code === 0x20e3;
}

/** Por qué un texto no se puede imprimir tal cual (las mismas reglas del servidor); `null` si se puede. */
export function textProblem(value: string, limit: number, multiline = false): string | null {
  const text = multiline ? normaliseMessage(value) : normaliseText(value);
  const length = Array.from(text).length;
  if (length > limit) return `Máximo ${limit} caracteres (tiene ${length}).`;
  if (multiline) {
    const lines = text.split("\n").length;
    if (lines > MESSAGE_MAX_LINES) return `Máximo ${MESSAGE_MAX_LINES} renglones (tiene ${lines}).`;
  }
  if (/[<>]/.test(text)) return "Sólo texto plano: sin los signos < ni >.";
  const chars = Array.from(text);
  if (chars.some(isEmoji)) {
    const shown = Array.from(new Set(chars.filter((c) => isEmoji(c) && !["\ufe0f", "\u200d", "\u20e3"].includes(c))));
    return `Los emoji no se pueden imprimir${shown.length ? ` ${shown.join(" ")}` : ""}. Quítalos: la impresora los cambiaría por «?».`;
  }
  const bad = Array.from(new Set(chars.filter((char) => !(multiline && char === "\n") && !drawable(char))));
  if (bad.length) {
    return `El papel no puede imprimir: ${bad.join(" ")}. Escríbelo con letras o quítalo (saldría como «?»).`;
  }
  return null;
}

/**
 * Nombres de documentos tributarios que ningún texto de la nota lleva (regla del propietario). Se
 * comparan tras `fold`, así que «Fáctura», «B.O.L.E.T.A», «B0LETA», «Bøleta» o «Bolet@» también.
 * Las mismas reglas que el servidor (`document_design._FISCAL_NAMES`, `_LOOKALIKES`).
 */
const FISCAL_NAMES = [
  "factura", "boleta", "comprobante", "reciboporhonorarios", "reciboxhonorarios", "recibohonorarios",
  "ticketfiscal", "liquidaciondecompra", "liquidacioncompra", "notadecredito", "notacredito",
  "notadedebito", "notadebito",
];

/** Lo que puede hacerse pasar por cada letra entre los caracteres que el papel admite. «1» vale por «i» Y por «l». */
const LOOKALIKES: Record<string, string> = {
  a: "a4@ª", b: "b8ß", c: "c¢©", e: "e3€", f: "fƒ", i: "i1l|!¡¦", l: "l1i|!¡¦",
  o: "o0øðº°", s: "s5$§", t: "t7+†", u: "uµμ", r: "r®", x: "x×",
};
const LOOKALIKE_SYMBOLS = new Set(
  Object.values(LOOKALIKES).flatMap((chars) => Array.from(chars)).filter((c) => !/^[a-z0-9]$/.test(c)),
);

/** Minúsculas, sin tildes; letras, dígitos y parecidos se quedan, lo demás es un espacio. */
function fold(text: string): string {
  const plain = text.toLowerCase().normalize("NFKD").replace(/\p{M}/gu, "");
  return Array.from(plain).map((c) => (/^[a-z0-9]$/.test(c) || LOOKALIKE_SYMBOLS.has(c) ? c : " ")).join("");
}

const escapeClass = (chars: string) => chars.replace(/[\\\]\[^-]/g, "\\$&");
/** Cada nombre empieza una palabra («manufactura» no es «factura») y admite espacios o signos entre letras. */
const FISCAL_PATTERNS = FISCAL_NAMES.map((name) => new RegExp(
  "(?<![a-z0-9])" + Array.from(name).map((c) => `[${escapeClass(LOOKALIKES[c] ?? c)}]`).join(" *"), "u",
));

/** Si el texto nombra un documento tributario, se escriba como se escriba. */
export function namesFiscalDocument(text: string): boolean {
  const folded = fold(text);
  return FISCAL_PATTERNS.some((pattern) => pattern.test(folded));
}

/** Cómo se llama cada texto en los mensajes (los mismos que el servidor). */
export const FIELD_NAMES: Record<TextField, string> = {
  document_title: "El título", footer_text: "El pie de página", thank_you_message: "El mensaje final",
  post_sale_message: "El mensaje posventa", commercial_message: "El mensaje comercial",
  warranty_summary: "El resumen de la garantía",
};

/** Un texto del diseño (las mismas reglas que `design_text_problem` del servidor); `null` si sirve. */
export function designTextProblem(field: TextField, value: string, limit: number): string | null {
  const problem = textProblem(value, limit, isMultiline(field));
  if (problem) return problem;
  if (field === "document_title" && !normaliseText(value)) {
    return "Escribe el título del documento (por ejemplo, «Nota de venta»).";
  }
  if (namesFiscalDocument(value)) {
    return `${FIELD_NAMES[field]} no puede nombrar un comprobante de pago ni otro documento tributario (factura, `
      + "boleta, comprobante, recibo por honorarios, ticket fiscal, liquidación de compra, nota de crédito o "
      + "débito): la nota de venta no lleva esas palabras."
      + (field === "document_title" ? " Usa, por ejemplo, «Nota de venta»." : "");
  }
  return null;
}

/** Por qué un título no sirve (las mismas reglas del servidor); `null` si sirve. */
export function titleProblem(value: string, limit: number): string | null {
  return designTextProblem("document_title", value, limit);
}

function widthProblem(value: number, [low, high]: [number, number]): string | null {
  return Number.isInteger(value) && (value === 0 || (value >= low && value <= high))
    ? null
    : `Escribe 0 (según el tamaño elegido) o un ancho entero entre ${low} y ${high} mm.`;
}

const TEXT_FIELDS: TextField[] = [
  "document_title", "footer_text", "thank_you_message", "post_sale_message", "commercial_message", "warranty_summary",
];

/** Lo que el servidor admite por campo cuando no lo dice (los mismos números). */
export const DEFAULT_LIMITS: Record<TextField, number> = {
  document_title: 40, footer_text: 200, thank_you_message: 300, post_sale_message: 400, commercial_message: 300,
  warranty_summary: 300,
};

/** Los problemas del borrador por campo, antes de enviarlo. */
export function draftProblems(
  draft: DocumentDesign, limits: DocumentDesignSettings["limits"],
  widths: { a4: [number, number]; ticket: [number, number] } = LOGO_WIDTH_MM,
): Partial<Record<keyof DocumentDesign, string>> {
  const out: Partial<Record<keyof DocumentDesign, string>> = {};
  for (const field of TEXT_FIELDS) {
    const value = draft[field];
    if (typeof value !== "string") continue;
    const limit = limits[field] ?? DEFAULT_LIMITS[field];
    const problem = designTextProblem(field, value, limit);
    if (problem) out[field] = problem;
  }
  const a4 = widthProblem(draft.logo_a4_width_mm, widths.a4);
  if (a4) out.logo_a4_width_mm = a4;
  const ticket = widthProblem(draft.logo_ticket_width_mm, widths.ticket);
  if (ticket) out.logo_ticket_width_mm = ticket;
  return out;
}

const CHOICE_LABELS: Partial<Record<keyof DocumentDesign, Record<string, string>>> = {
  logo_size: LOGO_LABELS,
  logo_a4_align: Object.fromEntries(OPTIONS.logo_aligns.map((o) => [o.value, o.label])),
  logo_ticket_align: Object.fromEntries(OPTIONS.logo_aligns.map((o) => [o.value, o.label])),
  warranty_mode: Object.fromEntries(OPTIONS.warranty_modes.map((o) => [o.value, o.label])),
  tax_breakdown_mode: Object.fromEntries(OPTIONS.tax_breakdown_modes.map((o) => [o.value, o.label])),
  density: Object.fromEntries(OPTIONS.densities.map((o) => [o.value, o.label])),
};
const BLOCK_LABELS = Object.fromEntries(OPTIONS.blocks.map((o) => [o.value, o.label])) as Record<BlockKey, string>;

function shown(field: keyof DocumentDesign, value: DocumentDesign[keyof DocumentDesign]): string {
  const labels = CHOICE_LABELS[field];
  if (labels) return labels[String(value)] ?? String(value);
  if (field === "block_order") return (value as BlockKey[]).map((key) => BLOCK_LABELS[key] ?? key).join(" → ");
  if (field === "logo_a4_width_mm" || field === "logo_ticket_width_mm") {
    return value === 0 ? "según el tamaño" : `${value} mm`;
  }
  if (typeof value === "boolean") return value ? "sí" : "no";
  const text = normaliseField(field as TextField, String(value)).replace(/\n/g, " / ");
  return text ? `«${text}»` : "(vacío)";
}

function same(field: keyof DocumentDesign, before: unknown, after: unknown): boolean {
  if (Array.isArray(before) || Array.isArray(after)) {
    return Array.isArray(before) && Array.isArray(after) && before.join(",") === after.join(",");
  }
  if (typeof before === "string") return normaliseField(field as TextField, before) === normaliseField(field as TextField, String(after));
  return before === after;
}

export type DesignChange = { field: keyof DocumentDesign; label: string; from: string; to: string };

/** Qué cambia del diseño guardado al borrador, en palabras (lo que se confirma antes de guardar). */
export function designChanges(saved: DocumentDesign, draft: DocumentDesign): DesignChange[] {
  return DESIGN_FIELDS.flatMap((field) => {
    const before = saved[field] ?? DEFAULT_DESIGN[field];
    const after = draft[field] ?? DEFAULT_DESIGN[field];
    return same(field, before, after)
      ? [] : [{ field, label: FIELD_LABELS[field], from: shown(field, before), to: shown(field, after) }];
  });
}

/** Sólo los campos que cambian, con los textos ya normalizados: el cuerpo del PATCH y del borrador. */
export function changedFields(saved: DocumentDesign, draft: DocumentDesign): Partial<DocumentDesign> {
  const out: Record<string, unknown> = {};
  for (const change of designChanges(saved, draft)) {
    const value = draft[change.field];
    out[change.field] = typeof value === "string" ? normaliseField(change.field as TextField, value) : value;
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
