/**
 * M12C — comunicados internos.
 *
 * El backend sigue siendo la autoridad. Este módulo describe las formas que
 * viajan y nada más: no decide quién puede publicar, no filtra audiencias y no
 * construye URLs de destino. Una comprobación aquí es una comodidad de la
 * interfaz; la de verdad ocurre en el servidor, en cada petición.
 */

import { API_BASE } from "./api";
import { fetchWithAuth } from "./auth";

export type AnnouncementStatus = "draft" | "published" | "cancelled";

export type AudienceKind =
  | "all_company"
  | "branch"
  | "role"
  | "capability"
  | "user";

export type AudienceRule = {
  kind: AudienceKind;
  company?: string;
  branch?: string | null;
  role?: string | null;
  capability_code?: string | null;
  user?: string | null;
};

export type Announcement = {
  id: number;
  title: string;
  body?: string;
  priority: string;
  status: AnnouncementStatus;
  author: string;
  created_at: string;
  published_at: string | null;
  recipient_count: number;
  audience?: AudienceRule[];
};

export type AnnouncementStats = {
  recipients: number;
  read: number;
  unread: number;
  read_pct: number;
};

export type PreviewCompany = {
  slug: string;
  name: string;
  recipient_count: number;
};

export type AnnouncementPreview = {
  companies: PreviewCompany[];
  company_count: number;
  recipient_count: number;
};

/**
 * El literal que hay que escribir para llegar a todas las empresas.
 *
 * No existe forma corta ni implícita. Una lista vacía es un error, nunca un
 * envío global — que es exactamente el default peligroso que este proyecto no
 * puede permitirse.
 */
export const ALL_ACTIVE_COMPANIES = "ALL_ACTIVE_COMPANIES";

export const AUDIENCE_LABELS: Record<AudienceKind, string> = {
  all_company: "Toda la empresa",
  branch: "Sucursal",
  role: "Rol",
  capability: "Capacidad",
  user: "Personas",
};

export const PRIORITY_LABELS: Record<string, string> = {
  info: "Informativa",
  action: "Requiere acción",
  warning: "Advertencia",
  critical: "Crítica",
};

export function tenantBase(slug: string) {
  return `${API_BASE}/v1/internal/${slug}/communications`;
}

async function readJson(res: Response) {
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    throw new Error(
      (body && typeof body.detail === "string" && body.detail) ||
        "No se pudo completar la operación.",
    );
  }
  return body;
}

export async function listAnnouncements(
  slug: string,
  status?: AnnouncementStatus,
) {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  return readJson(await fetchWithAuth(`${tenantBase(slug)}/${qs}`)) as Promise<{
    count: number;
    results: Announcement[];
  }>;
}

export async function createDraft(
  slug: string,
  input: { title: string; body: string; priority: string },
) {
  return readJson(
    await fetchWithAuth(`${tenantBase(slug)}/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),
  ) as Promise<Announcement>;
}

export async function updateDraft(
  slug: string,
  id: number,
  input: Record<string, unknown>,
) {
  return readJson(
    await fetchWithAuth(`${tenantBase(slug)}/${id}/`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),
  ) as Promise<Announcement>;
}

export async function previewAnnouncement(slug: string, id: number) {
  return readJson(
    await fetchWithAuth(`${tenantBase(slug)}/${id}/preview/`, { method: "POST" }),
  ) as Promise<AnnouncementPreview>;
}

export async function publishAnnouncement(slug: string, id: number) {
  return readJson(
    await fetchWithAuth(`${tenantBase(slug)}/${id}/publish/`, { method: "POST" }),
  ) as Promise<Announcement>;
}

export async function cancelAnnouncement(slug: string, id: number) {
  return readJson(
    await fetchWithAuth(`${tenantBase(slug)}/${id}/cancel/`, { method: "POST" }),
  ) as Promise<Announcement>;
}

export async function announcementStats(slug: string, id: number) {
  return readJson(
    await fetchWithAuth(`${tenantBase(slug)}/${id}/stats/`),
  ) as Promise<AnnouncementStats>;
}

/** Lo que abre un DESTINATARIO desde su bandeja. Sin capability. */
export async function readAnnouncement(slug: string, id: number) {
  return readJson(
    await fetchWithAuth(`${API_BASE}/v1/internal/${slug}/announcements/${id}/`),
  ) as Promise<Announcement>;
}

/**
 * Resume una audiencia en una frase.
 *
 * Sólo para confirmar antes de publicar. Nada de esto se le enseña a quien
 * recibe el comunicado: cómo se eligió la lista es la nota de trabajo de quien
 * lo escribió.
 */
export function describeAudience(rules: AudienceRule[] | undefined) {
  if (!rules || rules.length === 0) return "Sin destinatarios";
  return rules
    .map((r) => {
      const label = AUDIENCE_LABELS[r.kind] ?? r.kind;
      const target =
        r.branch ?? r.role ?? r.capability_code ?? r.user ?? null;
      return target ? `${label}: ${target}` : label;
    })
    .join(" · ");
}

/**
 * ¿Hace falta la segunda confirmación?
 *
 * No por el número de personas, sino porque el envío cruza empresas. Un
 * comunicado que sale del tenant propio es un tipo de acto distinto, y la
 * interfaz debe decirlo antes y no después.
 */
export function needsGlobalConfirmation(preview: AnnouncementPreview | null) {
  return !!preview && preview.company_count > 1;
}
