/**
 * Personal: personas, no membresías.
 *
 * Los identificadores viajan porque el servidor los necesita. Lo que no hacen
 * es aparecer en pantalla: quien administra una tienda piensa en «Carlos», no
 * en «membresía 18».
 */

import { API_BASE } from "./api";
import { fetchWithAuth } from "./auth";

export type StaffPerson = {
  /** Viaja para las acciones. No se muestra. */
  id: number;
  full_name: string;
  first_name: string;
  last_name: string;
  email: string;
  is_active: boolean;
  /** La ficha de quien está mirando. Nadie se desactiva a sí mismo. */
  is_self: boolean;
  areas: { id: number; name: string }[];
  roles: { id: number; name: string }[];
  branch_access_mode: string;
  branches: string[];
  /** «Todas las sucursales» o la lista, ya redactada por el servidor. */
  branch_scope_label: string;
};

export type StaffInvitation = {
  id: number;
  email: string;
  full_name: string;
  first_name: string;
  last_name: string;
  role_id: number | null;
  role_name: string;
  area_id: number | null;
  area_name: string;
  branch_access_mode: string;
  branch_ids: number[];
  /** `pending`, `accepted`, `revoked` o `expired` — ya calculado. */
  status: string;
  invited_by: string;
  created_at: string;
  expires_at: string;
  accepted_at: string | null;
  is_usable: boolean;
};

export type StaffFilters = {
  search?: string;
  status?: string;
  area?: number | "";
  role?: number | "";
  branch?: number | "";
};

async function readDetail(res: Response, fallback: string): Promise<string> {
  try {
    const body = await res.json();
    return typeof body?.detail === "string" ? body.detail : fallback;
  } catch {
    return fallback;
  }
}

function query(company: number, extra: Record<string, unknown> = {}): string {
  const params = new URLSearchParams({ company: String(company) });
  for (const [key, value] of Object.entries(extra)) {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  }
  return params.toString();
}

/**
 * El personal de la empresa.
 *
 * Los filtros van al SERVIDOR. Traerlo todo para filtrar aquí funciona con seis
 * personas y deja de funcionar con seiscientas.
 */
export async function fetchStaff(
  company: number,
  filters: StaffFilters = {},
): Promise<StaffPerson[]> {
  const res = await fetchWithAuth(
    `${API_BASE}/admin/staff/?${query(company, filters)}`,
  );
  if (!res.ok) throw new Error(await readDetail(res, "No se pudo cargar el personal."));
  return (await res.json()).results ?? [];
}

export async function setStaffActive(
  company: number,
  membershipId: number,
  isActive: boolean,
): Promise<StaffPerson> {
  const res = await fetchWithAuth(`${API_BASE}/admin/staff/${membershipId}/`, {
    method: "PATCH",
    body: JSON.stringify({ company, is_active: isActive }),
  });
  if (!res.ok) throw new Error(await readDetail(res, "No se pudo cambiar el acceso."));
  return res.json();
}

export async function fetchInvitations(
  company: number,
  onlyPending = true,
): Promise<StaffInvitation[]> {
  const res = await fetchWithAuth(
    `${API_BASE}/admin/staff/invitations/?${query(company, {
      status: onlyPending ? "pending" : "",
    })}`,
  );
  if (!res.ok) throw new Error(await readDetail(res, "No se pudieron cargar las invitaciones."));
  return (await res.json()).results ?? [];
}

export type InviteInput = {
  first_name: string;
  last_name: string;
  email: string;
  role: number;
  area?: number | null;
  branch_access_mode: "all" | "selected";
  branch_ids?: number[];
};

/**
 * Invita a alguien. NO crea acceso: hasta que la persona acepte no puede entrar.
 *
 * Repetir la llamada con el mismo correo devuelve la MISMA invitación sin rotar
 * su token — el enlace que ya va camino del buzón sigue sirviendo. Rotarlo es
 * `resendInvitation`, que es un acto deliberado.
 */
export async function createInvitation(
  company: number,
  input: InviteInput,
): Promise<StaffInvitation> {
  const res = await fetchWithAuth(`${API_BASE}/admin/staff/invitations/`, {
    method: "POST",
    body: JSON.stringify({ company, ...input }),
  });
  if (!res.ok) throw new Error(await readDetail(res, "No se pudo enviar la invitación."));
  return res.json();
}

async function invitationAction(
  company: number,
  invitationId: number,
  action: "resend" | "revoke",
): Promise<StaffInvitation> {
  const res = await fetchWithAuth(
    `${API_BASE}/admin/staff/invitations/${invitationId}/${action}/`,
    { method: "POST", body: JSON.stringify({ company }) },
  );
  if (!res.ok) throw new Error(await readDetail(res, "No se pudo completar la acción."));
  return res.json();
}

export const resendInvitation = (company: number, id: number) =>
  invitationAction(company, id, "resend");
export const revokeInvitation = (company: number, id: number) =>
  invitationAction(company, id, "revoke");

// --- áreas -----------------------------------------------------------------

export type CompanyAreaRow = {
  id: number;
  name: string;
  slug: string;
  description: string;
  is_active: boolean;
  sort_order: number;
  /** Cuántas personas están asignadas hoy. Lo cuenta el servidor. */
  member_count?: number;
};

export async function fetchAreas(company: number): Promise<CompanyAreaRow[]> {
  const res = await fetchWithAuth(`${API_BASE}/admin/areas/?${query(company)}`);
  if (!res.ok) throw new Error(await readDetail(res, "No se pudieron cargar las áreas."));
  return (await res.json()).results ?? [];
}

export async function createArea(
  company: number,
  input: { name: string; description?: string; sort_order?: number },
): Promise<CompanyAreaRow> {
  const res = await fetchWithAuth(`${API_BASE}/admin/areas/`, {
    method: "POST",
    body: JSON.stringify({ company, ...input }),
  });
  if (!res.ok) throw new Error(await readDetail(res, "No se pudo crear el área."));
  return res.json();
}

export async function updateArea(
  areaId: number,
  patch: Partial<Pick<CompanyAreaRow, "name" | "description" | "is_active" | "sort_order">>,
): Promise<CompanyAreaRow> {
  const res = await fetchWithAuth(`${API_BASE}/admin/areas/${areaId}/`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(await readDetail(res, "No se pudo actualizar el área."));
  return res.json();
}

// --- catálogos para los selectores ------------------------------------------

export type NamedOption = { id: number; name: string };

/**
 * Roles y sucursales por NOMBRE, para que nadie tenga que escribir un id.
 *
 * Los identificadores siguen viajando en la petición; lo que cambia es que la
 * persona elige «Técnico», no «7».
 */
export async function fetchRoles(company: number): Promise<NamedOption[]> {
  const res = await fetchWithAuth(`${API_BASE}/admin/roles/?${query(company)}`);
  if (!res.ok) return [];
  return ((await res.json()).results ?? [])
    .filter((r: { is_active?: boolean }) => r.is_active !== false)
    .map((r: { id: number; name: string }) => ({ id: r.id, name: r.name }));
}

export async function fetchBranches(company: number): Promise<NamedOption[]> {
  const res = await fetchWithAuth(`${API_BASE}/admin/branches/?${query(company)}`);
  if (!res.ok) return [];
  const body = await res.json();
  return (body.results ?? body ?? [])
    .filter((b: { is_active?: boolean }) => b.is_active !== false)
    .map((b: { id: number; name: string }) => ({ id: b.id, name: b.name }));
}
