/**
 * INV-FAST-B2 — «Revisar y registrar»: la lista revisada, por el importador OFICIAL de equipos.
 *
 * Las filas del borrador viajan como JSON a `…/text-preview/units/preview/`, que las escribe en la plantilla oficial
 * en memoria y corre `preview_units`; registrar es el aplicar oficial `…/units/import/<id>/apply/`, sin cambios.
 * No hay reglas nuevas aquí: cada error es el del importador. Series, IMEI y costos viajan sólo a esa ruta oficial
 * de la empresa y nunca se guardan en el navegador.
 */
import { API_BASE } from "../../lib/api";
import { fetchWithAuth } from "../../lib/auth";
import { identityExtras } from "./inventory-catalog-proposal";
import type { DraftCandidate, DraftPlan } from "./inventory-text-draft";

export type ReceiveSettings = { branch: string; branchId: number | null; reason: string };

export type ReceiveRowBody = {
  key: string; product_id: number; serial_number: string; imei: string; imei2: string; condition: "new" | "used";
  battery_health?: string; cost?: string;
};
export type ReceivePayload = { branch: number; reason: string; rows: ReceiveRowBody[] };

export type ReceiveJobRow = {
  key: string | null; row: number; action: string; errors: string[]; warnings: string[]; data: Record<string, unknown>;
};
export type ReceiveJob = {
  id: number; status: string; import_type: string; cost_column_ignored: boolean;
  counts: { total: number; create: number; update: number; no_change: number; skip: number; error: number };
  rows: ReceiveJobRow[];
  summary: { applied?: { units?: number; movements?: number } } & Record<string, unknown>;
  /** LABEL-A: present on the official apply's answer. */
  label_batches?: unknown[];
  label_error?: boolean;
};

/** The request body: the fields of the official template, nothing else from the candidate. */
export function receivePayload(plan: DraftPlan, settings: ReceiveSettings, canSeeCosts: boolean): ReceivePayload {
  return {
    branch: settings.branchId as number, reason: settings.reason.trim(),
    rows: plan.included.map(row => ({
      key: String(row.id), product_id: row.productId as number, serial_number: row.serial, imei: row.imei,
      imei2: row.imei2, condition: row.line === "used" ? "used" : "new",
      ...(row.batteryPercent !== null ? { battery_health: String(row.batteryPercent) } : {}),
      ...(canSeeCosts && row.supplierCost ? { cost: row.supplierCost } : {}),
    })),
  };
}

export type RowStatus = "draft" | "ready" | "review" | "registered";
export type ReceiveRowView = {
  id: number; product: string; serialTail: string; status: RowStatus; reason: string; notes: string[];
};

export const STATUS_LABELS: Record<RowStatus, string> = {
  draft: "Borrador", ready: "Listo", review: "Necesita revisión", registered: "Registrado",
};

const tail = (value: string) => (value ? "••••" + value.slice(-4) : "—");

/** Every pasted row with where it stands. A row that stays out says why: nothing is dropped silently. */
export function receiveRows(rows: DraftCandidate[], plan: DraftPlan, job: ReceiveJob | null, applied: boolean): ReceiveRowView[] {
  const local = new Map(plan.review.map(item => [item.id, item.reason]));
  const staged = new Map((job?.rows ?? []).filter(item => item.key !== null).map(item => [String(item.key), item]));
  return rows.map(row => {
    const base = { id: row.id, product: row.productName || row.model, serialTail: tail(row.serial) };
    const reason = local.get(row.id);
    if (reason) return { ...base, status: "review", reason, notes: [] };
    if (!job) return { ...base, status: "draft", reason: "", notes: [] };
    const found = staged.get(String(row.id));
    if (!found) return { ...base, status: "review", reason: "El importador no devolvió esta fila.", notes: [] };
    if (found.action === "error") return { ...base, status: "review", reason: found.errors.join(" "), notes: [] };
    // Only a row the importer will CREATE is ready or registered; a skipped one (or any other action) is not.
    if (found.action !== "create") {
      return { ...base, status: "review", reason: [...found.errors, ...found.warnings].join(" ") || "El importador omitió esta fila.", notes: [] };
    }
    const extras = identityExtras(row);
    const notes = [...found.warnings, ...(extras ? [`Del texto y sin reflejo en el catálogo: ${extras}. Confírmalo antes de registrar.`] : [])];
    return { ...base, status: applied ? "registered" : "ready", reason: "", notes };
  });
}

function withCompany(path: string, companyId?: number | null): string {
  return companyId ? `${path}?company=${encodeURIComponent(String(companyId))}` : path;
}

async function post(path: string, body: unknown, companyId: number | null | undefined, fallback: string) {
  const res = await fetchWithAuth(`${API_BASE}${withCompany(path, companyId)}`, { method: "POST", body: JSON.stringify(body) });
  const answer = await res.json().catch(() => null);
  if (res.ok) return answer;
  throw new Error(answer && typeof answer.detail === "string" ? answer.detail
    : res.status === 403 ? "No tienes permiso para registrar equipos en esta sucursal." : fallback);
}

/** The official preview of the draft: stages a job, moves no stock. */
export function requestReceivePreview(body: ReceivePayload, companyId?: number | null): Promise<ReceiveJob> {
  return post("/admin/inventory/text-preview/units/preview/", body, companyId, "No se pudo revisar la lista.");
}

/** The official apply of the staged job: all of the rows or none. A second call changes nothing. */
export function applyReceiveJob(jobId: number, companyId?: number | null): Promise<ReceiveJob> {
  return post(`/admin/inventory/units/import/${jobId}/apply/`, {}, companyId, "No se pudieron registrar los equipos.");
}
