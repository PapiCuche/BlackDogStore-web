import { API_BASE } from "./api";

export type AuthUser = {
  id: number;
  username: string;
  email: string;
  first_name: string;
  last_name: string;
  role: string;
  is_staff: boolean;
};

export type BusinessRole =
  | 'customer'
  | 'sales'
  | 'inventory'
  | 'technician'
  | 'admin'
  | 'superadmin';

export function isAdminRole(user: AuthUser | null): boolean {
  return user?.role === 'admin' || user?.role === 'superadmin';
}

export function isStaffRole(user: AuthUser | null): boolean {
  return (
    user?.role === 'inventory' ||
    user?.role === 'sales' ||
    user?.role === 'admin' ||
    user?.role === 'superadmin'
  );
}

export function isSuperAdmin(user: AuthUser | null): boolean {
  return user?.role === 'superadmin';
}

const ROLE_LABELS: Record<string, string> = {
  customer: 'Cliente',
  sales: 'Vendedor',
  inventory: 'Inventario',
  technician: 'Técnico',
  admin: 'Administrador',
  superadmin: 'Superadministrador',
};

export function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role;
}

function getCsrfTokenFromCookie(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

async function ensureCsrfToken(): Promise<string | null> {
  let token = getCsrfTokenFromCookie();
  if (!token) {
    try {
      await fetch(`${API_BASE}/auth/csrf/`, { credentials: "include" });
      token = getCsrfTokenFromCookie();
    } catch {
      return null;
    }
  }
  return token;
}

async function tryRefresh(): Promise<boolean> {
  try {
    const csrf = await ensureCsrfToken();
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (csrf) headers["X-CSRFToken"] = csrf;
    const res = await fetch(`${API_BASE}/auth/refresh/`, {
      method: "POST",
      headers,
      credentials: "include",
    });
    return res.ok;
  } catch {
    return false;
  }
}

export async function fetchWithAuth(
  url: string,
  options: RequestInit = {}
): Promise<Response> {
  const method = ((options.method as string) || "GET").toUpperCase();
  const needsCsrf = ["POST", "PUT", "PATCH", "DELETE"].includes(method);

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((options.headers as Record<string, string>) ?? {}),
  };

  if (needsCsrf) {
    const csrf = await ensureCsrfToken();
    if (csrf) headers["X-CSRFToken"] = csrf;
  }

  const response = await fetch(url, { ...options, headers, credentials: "include" });

  if (response.status === 401) {
    const isAuthEndpoint =
      url.includes("/auth/login") ||
      url.includes("/auth/refresh") ||
      url.includes("/auth/logout");

    if (!isAuthEndpoint) {
      const refreshed = await tryRefresh();
      if (refreshed) {
        if (needsCsrf) {
          const freshCsrf = getCsrfTokenFromCookie();
          if (freshCsrf) headers["X-CSRFToken"] = freshCsrf;
        }
        return fetch(url, { ...options, headers, credentials: "include" });
      }
    }
  }

  return response;
}

export async function login(
  username: string,
  password: string
): Promise<{ detail: string; user: AuthUser }> {
  const csrf = await ensureCsrfToken();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (csrf) headers["X-CSRFToken"] = csrf;

  const res = await fetch(`${API_BASE}/auth/login/`, {
    method: "POST",
    headers,
    credentials: "include",
    body: JSON.stringify({ username, password }),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => null);
    throw new Error(err?.detail || "Credenciales inválidas.");
  }
  return res.json();
}

export async function logout(): Promise<void> {
  const csrf = await ensureCsrfToken();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (csrf) headers["X-CSRFToken"] = csrf;
  await fetch(`${API_BASE}/auth/logout/`, {
    method: "POST",
    headers,
    credentials: "include",
  });
}

export async function getCurrentUser(): Promise<AuthUser | null> {
  try {
    const res = await fetchWithAuth(`${API_BASE}/auth/me/`);
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

export async function register(data: {
  username: string;
  email: string;
  password: string;
  password_confirm: string;
  first_name?: string;
  last_name?: string;
}): Promise<{ detail: string; requires_verification: boolean; user?: AuthUser }> {
  const res = await fetch(`${API_BASE}/auth/register/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => null);
    const msg =
      err?.detail ||
      err?.password_confirm?.[0] ||
      err?.password?.[0] ||
      err?.email?.[0] ||
      err?.username?.[0] ||
      "Error en el registro.";
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return res.json();
}

export async function verifyEmail(token: string): Promise<{ detail: string }> {
  const csrf = await ensureCsrfToken();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (csrf) headers["X-CSRFToken"] = csrf;
  const res = await fetch(`${API_BASE}/auth/verify-email/`, {
    method: "POST",
    headers,
    credentials: "include",
    body: JSON.stringify({ token }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => null);
    throw new Error(err?.detail || "No se pudo verificar el correo.");
  }
  return res.json();
}

export async function resendVerification(email: string): Promise<{ detail: string }> {
  const csrf = await ensureCsrfToken();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (csrf) headers["X-CSRFToken"] = csrf;
  const res = await fetch(`${API_BASE}/auth/resend-verification/`, {
    method: "POST",
    headers,
    credentials: "include",
    body: JSON.stringify({ email }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => null);
    throw new Error(err?.detail || "No se pudo enviar el correo.");
  }
  return res.json();
}

export async function requestPasswordReset(email: string): Promise<{ detail: string }> {
  const csrf = await ensureCsrfToken();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (csrf) headers["X-CSRFToken"] = csrf;
  const res = await fetch(`${API_BASE}/auth/password-reset/request/`, {
    method: "POST",
    headers,
    credentials: "include",
    body: JSON.stringify({ email }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => null);
    throw new Error(err?.detail || "No se pudo procesar la solicitud.");
  }
  return res.json();
}

export async function confirmPasswordReset(
  token: string,
  new_password: string
): Promise<{ detail: string }> {
  const csrf = await ensureCsrfToken();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (csrf) headers["X-CSRFToken"] = csrf;
  const res = await fetch(`${API_BASE}/auth/password-reset/confirm/`, {
    method: "POST",
    headers,
    credentials: "include",
    body: JSON.stringify({ token, new_password }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => null);
    throw new Error(err?.detail || "No se pudo restablecer la contraseña.");
  }
  return res.json();
}

export async function changePassword(
  current_password: string,
  new_password: string
): Promise<{ detail: string }> {
  const res = await fetchWithAuth(`${API_BASE}/auth/change-password/`, {
    method: "POST",
    body: JSON.stringify({ current_password, new_password }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => null);
    throw new Error(err?.detail || "No se pudo cambiar la contraseña.");
  }
  return res.json();
}
