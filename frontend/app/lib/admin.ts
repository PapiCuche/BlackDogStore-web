import { API_BASE } from "./api";
import { fetchWithAuth } from "./auth";

export type AdminUser = {
  id: number;
  username: string;
  email: string;
  is_active: boolean;
  role: string;
  date_joined: string;
};

export type PaginatedResponse<T> = {
  count: number;
  page: number;
  page_size: number;
  results: T[];
};

export type AuditLogEntry = {
  id: number;
  actor: string | null;
  action: string;
  target_type: string;
  target_id: string;
  metadata: Record<string, unknown>;
  ip_address: string | null;
  created_at: string;
};

function buildQs(params: Record<string, string | number | undefined>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && String(v) !== '') p.set(k, String(v));
  }
  return p.size > 0 ? `?${p.toString()}` : '';
}

export async function fetchAdminUsers(params?: {
  search?: string;
  role?: string;
  page?: number;
  page_size?: number;
}): Promise<PaginatedResponse<AdminUser>> {
  const qs = buildQs({
    search: params?.search,
    role: params?.role,
    page: params?.page,
    page_size: params?.page_size,
  });
  const res = await fetchWithAuth(`${API_BASE}/admin/users/${qs}`);
  if (res.status === 403) throw new Error('No tienes permisos para ver usuarios.');
  if (res.status === 401) throw new Error('Sesión expirada. Vuelve a iniciar sesión.');
  if (!res.ok) throw new Error('No se pudieron cargar los usuarios.');
  return res.json();
}

export async function changeUserRole(userId: number, newRole: string): Promise<void> {
  const res = await fetchWithAuth(`${API_BASE}/admin/users/${userId}/role/`, {
    method: 'PATCH',
    body: JSON.stringify({ role: newRole }),
  });
  if (res.status === 403) throw new Error('Solo el superadministrador puede cambiar roles.');
  if (res.status === 400) {
    const err = await res.json().catch(() => null);
    throw new Error(err?.detail ?? 'Solicitud inválida.');
  }
  if (!res.ok) throw new Error('No se pudo actualizar el rol.');
}

export async function fetchAuditLogs(params?: {
  action?: string;
  actor?: string;
  target_type?: string;
  page?: number;
  page_size?: number;
}): Promise<PaginatedResponse<AuditLogEntry>> {
  const qs = buildQs({
    action: params?.action,
    actor: params?.actor,
    target_type: params?.target_type,
    page: params?.page,
    page_size: params?.page_size,
  });
  const res = await fetchWithAuth(`${API_BASE}/admin/audit-logs/${qs}`);
  if (res.status === 403) throw new Error('No tienes permisos para ver el registro de auditoría.');
  if (res.status === 401) throw new Error('Sesión expirada. Vuelve a iniciar sesión.');
  if (!res.ok) throw new Error('No se pudieron cargar los registros.');
  return res.json();
}

export const ACTION_LABELS: Record<string, string> = {
  role_change: 'Cambio de rol',
};

export function actionLabel(action: string): string {
  return ACTION_LABELS[action] ?? action;
}

export function formatAdminDate(iso: string): string {
  return new Date(iso).toLocaleString('es-PE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export const ALL_ROLES = [
  { value: 'customer', label: 'Cliente' },
  { value: 'sales', label: 'Vendedor' },
  { value: 'inventory', label: 'Inventario' },
  { value: 'technician', label: 'Técnico' },
  { value: 'admin', label: 'Administrador' },
  { value: 'superadmin', label: 'Superadministrador' },
];
