/**
 * Impresión en la tienda: impresoras, agentes y cola.
 *
 * La empresa viaja como `?company=`, que el servidor trata como una selección
 * entre las empresas que quien pide ya alcanza. Nada de aquí habla con una
 * impresora: eso lo hace el agente del local.
 */
import { fetchWithAuth } from "../../lib/auth";
import { API_BASE } from "../../lib/api";

export type Printer = {
  id: number; branch: number; branch_name: string; name: string; host: string; port: number;
  paper_width_mm: number; encoding: string; auto_print: boolean; is_active: boolean;
};

export type PrintAgent = {
  id: number; branch: number; branch_name: string; name: string; token_hint: string;
  is_active: boolean; last_seen_at: string | null;
};

/** Sólo la respuesta de crear un agente lleva el token, y sólo esa vez. */
export type CreatedPrintAgent = PrintAgent & { token: string };

export type PrintJobStatus = "pending" | "printing" | "printed" | "failed" | "cancelled";

export type PrintJob = {
  id: number; kind: string; reason: string; status: PrintJobStatus; order: number; branch: number;
  printer: number; printer_name: string; attempts: number; last_error: string;
  created_at: string; printed_at: string | null;
};

export const PRINT_JOB_STATUS_LABEL: Record<PrintJobStatus, string> = {
  pending: "En cola", printing: "Enviado a la impresora", printed: "Impreso",
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
  data: { branch: number; name: string; host: string; auto_print: boolean },
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

export async function retryPrintJob(companyId: number | null, id: number): Promise<PrintJob> {
  const res = await fetchWithAuth(
    `${API_BASE}/admin/printing/jobs/${id}/retry/${query(companyId)}`, json("POST", {}));
  return handle<PrintJob>(res, "No se pudo reenviar el trabajo.");
}
