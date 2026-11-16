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
};

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
  data: { branch: number; name: string; host?: string; auto_print: boolean; connection?: PrinterConnection },
): Promise<Printer> {
  const res = await fetchWithAuth(
    `${API_BASE}/admin/printing/printers/${query(companyId)}`, json("POST", data));
  return handle<Printer>(res, "No se pudo añadir la impresora.");
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
  companyId: number | null, id: number, confirmNotPrinted = false,
): Promise<PrintJob> {
  const res = await fetchWithAuth(
    `${API_BASE}/admin/printing/jobs/${id}/retry/${query(companyId)}`,
    json("POST", confirmNotPrinted ? { confirm_not_printed: true } : {}));
  return handle<PrintJob>(res, "No se pudo reenviar el trabajo.");
}
