/**
 * Impresión en la tienda: impresoras, agentes y cola.
 *
 * La empresa viaja como `?company=`, que el servidor trata como una selección
 * entre las empresas que quien pide ya alcanza. Nada de aquí habla con una
 * impresora: eso lo hace el agente del local.
 */
import { fetchWithAuth } from "../../lib/auth";
import { API_BASE } from "../../lib/api";

/**
 * `network`: térmica en la red del local, con dirección. `local_queue`: conectada
 * (USB) al equipo del agente; no tiene dirección y el agente la tiene en su
 * propia configuración por el id de la impresora (WIN-003C).
 */
export type PrinterConnection = "network" | "local_queue";

export const PRINTER_CONNECTION_LABEL: Record<PrinterConnection, string> = {
  network: "Red del local", local_queue: "Equipo del agente (USB)",
};

export type Printer = {
  id: number; branch: number; branch_name: string; name: string; host: string; port: number;
  paper_width_mm: number; encoding: string; auto_print: boolean; is_active: boolean;
  /** Ausentes en un servidor anterior a WIN-003C: entonces es de red. */
  connection?: PrinterConnection;
  /** ¿Hay un agente de su sucursal en línea que atienda esta clase de impresora? */
  agent_online?: boolean;
  /**
   * THERMAL-512, ausentes en un servidor anterior: el ancho declarado en puntos (0 = según el rollo), el que se
   * imprime y sus columnas de letra normal.
   */
  print_width_dots?: number;
  printable_dots?: number;
  columns?: number;
};

/**
 * THERMAL-512 · lo que la impresora imprime de ancho. No todas las de 80 mm imprimen 72 mm: algunas imprimen
 * 64 mm (512 puntos, 42 columnas). `print_width_dots` 0 es «según el rollo».
 */
export type PrintWidth = { paper_width_mm: number; print_width_dots: number };

/** Etiquetas cortas a propósito: el selector cabe en un teléfono de 320 px (AGENTS.md §7). */
export const PRINT_WIDTH_OPTIONS: readonly (PrintWidth & { key: string; label: string })[] = [
  { key: "80-576", paper_width_mm: 80, print_width_dots: 0, label: "80 mm · 48 col" },
  { key: "80-512", paper_width_mm: 80, print_width_dots: 512, label: "80 mm · 42 col (512 puntos)" },
  { key: "58-384", paper_width_mm: 58, print_width_dots: 0, label: "58 mm · 32 col" },
];

/** Los puntos que imprime: los que dice el servidor o, si es anterior a THERMAL-512, los de su rollo. */
export function printableDots(printer: Pick<Printer, "paper_width_mm" | "printable_dots">): number {
  return printer.printable_dots ?? (printer.paper_width_mm === 58 ? 384 : 576);
}

/** Columnas de letra normal: 12 puntos cada una. */
export function printerColumns(printer: Pick<Printer, "paper_width_mm" | "printable_dots" | "columns">): number {
  return printer.columns ?? Math.floor(printableDots(printer) / 12);
}

/** La opción de `PRINT_WIDTH_OPTIONS` en que está la impresora. */
export function printWidthKey(printer: Pick<Printer, "paper_width_mm" | "printable_dots">): string {
  const dots = printableDots(printer);
  return dots === 512 ? "80-512" : dots === 384 ? "58-384" : "80-576";
}

export type PrintAgent = {
  id: number; branch: number; branch_name: string; name: string; token_hint: string;
  is_active: boolean; last_seen_at: string | null;
  online?: boolean; connections?: PrinterConnection[];
};

/** Sólo la respuesta de crear un agente lleva el token, y sólo esa vez. */
export type CreatedPrintAgent = PrintAgent & { token: string };

export type PrintJobStatus = "pending" | "printing" | "printed" | "failed" | "cancelled";

export type PrintJob = {
  id: number; kind: string; reason: string; status: PrintJobStatus; order: number; branch: number;
  printer: number; printer_name: string; attempts: number; last_error: string;
  created_at: string; printed_at: string | null;
  /** Falló sin saberse si el papel salió: reenviarlo pide confirmar que NO salió. */
  uncertain?: boolean;
  /** El identificador que se ve; la confirmación «no salió» lo devuelve. */
  uid?: string;
};

/**
 * «Entregado» y no «Impreso»: el agente sabe que la impresora (o Windows) recibió
 * el ticket, no que el papel salió.
 */
export const PRINT_JOB_STATUS_LABEL: Record<PrintJobStatus, string> = {
  pending: "En cola", printing: "Enviándose", printed: "Entregado a la impresora",
  failed: "Falló", cancelled: "Cancelado",
};

export class PrintingFieldError extends Error {
  constructor(message: string, readonly fields: Record<string, string[]>) {
    super(message);
    this.name = "PrintingFieldError";
  }
}

const query = (companyId: number | null) =>
  companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";

async function handle<T>(res: Response, fallback: string): Promise<T> {
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => ({}));
  if (res.ok) return data as T;
  if (res.status === 400 && data && typeof data === "object" && !("detail" in data)) {
    throw new PrintingFieldError("Revisa los datos.", data as Record<string, string[]>);
  }
  throw new Error((data as { detail?: string }).detail || fallback);
}

const json = (method: string, body?: unknown, headers: Record<string, string> = {}) => ({
  method,
  headers: { "Content-Type": "application/json", ...headers },
  body: body === undefined ? undefined : JSON.stringify(body),
});

export async function fetchPrinters(companyId: number | null): Promise<Printer[]> {
  const res = await fetchWithAuth(`${API_BASE}/admin/printing/printers/${query(companyId)}`);
  return (await handle<{ results: Printer[] }>(res, "No se pudieron cargar las impresoras.")).results;
}

export async function createPrinter(
  companyId: number | null,
  data: {
    branch: number; name: string; host?: string; auto_print: boolean; connection?: PrinterConnection;
  } & Partial<PrintWidth>,
): Promise<Printer> {
  const res = await fetchWithAuth(
    `${API_BASE}/admin/printing/printers/${query(companyId)}`, json("POST", data));
  return handle<Printer>(res, "No se pudo añadir la impresora.");
}

/** THERMAL-512: cambia lo que imprime de ancho una impresora ya dada de alta. */
export async function updatePrinter(
  companyId: number | null, id: number, data: Partial<PrintWidth>,
): Promise<Printer> {
  const res = await fetchWithAuth(
    `${API_BASE}/admin/printing/printers/${id}/${query(companyId)}`, json("PATCH", data));
  return handle<Printer>(res, "No se pudo cambiar la impresora.");
}

export async function deactivatePrinter(companyId: number | null, id: number): Promise<void> {
  const res = await fetchWithAuth(
    `${API_BASE}/admin/printing/printers/${id}/${query(companyId)}`, { method: "DELETE" });
  return handle<void>(res, "No se pudo desactivar la impresora.");
}

export async function fetchPrintAgents(companyId: number | null): Promise<PrintAgent[]> {
  const res = await fetchWithAuth(`${API_BASE}/admin/printing/agents/${query(companyId)}`);
  return (await handle<{ results: PrintAgent[] }>(res, "No se pudieron cargar los agentes.")).results;
}

export async function createPrintAgent(
  companyId: number | null, data: { branch: number; name: string },
): Promise<CreatedPrintAgent> {
  const res = await fetchWithAuth(
    `${API_BASE}/admin/printing/agents/${query(companyId)}`, json("POST", data));
  return handle<CreatedPrintAgent>(res, "No se pudo crear el agente.");
}

export async function revokePrintAgent(companyId: number | null, id: number): Promise<void> {
  const res = await fetchWithAuth(
    `${API_BASE}/admin/printing/agents/${id}/${query(companyId)}`, { method: "DELETE" });
  return handle<void>(res, "No se pudo revocar el agente.");
}

export async function fetchPrintJobs(companyId: number | null): Promise<PrintJob[]> {
  const res = await fetchWithAuth(`${API_BASE}/admin/printing/jobs/${query(companyId)}`);
  return (await handle<{ results: PrintJob[] }>(res, "No se pudo cargar la cola de impresión.")).results;
}

/**
 * Reenvía un trabajo fallido. Si era incierto, el servidor exige
 * `confirmNotPrinted`: quien lo pide miró el mostrador y el ticket NO salió.
 */
export async function retryPrintJob(
  companyId: number | null, id: number, confirmNotPrinted = false, expectedUid?: string,
): Promise<PrintJob> {
  // La confirmación vale para el ticket que se vio: si ya se reenvió, el
  // servidor responde 409 en vez de reimprimir.
  const body = confirmNotPrinted
    ? { confirm_not_printed: true, ...(expectedUid ? { expected_uid: expectedUid } : {}) }
    : {};
  const res = await fetchWithAuth(
    `${API_BASE}/admin/printing/jobs/${id}/retry/${query(companyId)}`, json("POST", body));
  return handle<PrintJob>(res, "No se pudo reenviar el trabajo.");
}
