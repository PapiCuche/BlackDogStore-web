/**
 * SOCIAL-AUTH-01 — «Continuar con Facebook / Apple» y las cuentas vinculadas.
 *
 * EL NAVEGADOR SÓLO NAVEGA. Facebook y Apple son flujos de redirección que el
 * servidor empieza y termina: el botón es un enlace a `/api/auth/<p>/start`, el
 * proveedor vuelve al servidor, y el servidor deja la sesión de siempre en
 * cookies HttpOnly y manda a la tienda con `?social=` o `?social_error=`. Aquí no
 * se guarda nada: ni un token ni una sesión, ni en `localStorage` ni en otra parte.
 *
 * Google sigue su propio camino (`google-auth.ts`).
 */

import { API_BASE, apiUrl } from "./api";
import { fetchWithAuth } from "./auth";
import { safeInternalNextPath } from "./safe-next";

export type SocialProvider = "google" | "facebook" | "apple";
export type RedirectProvider = "facebook" | "apple";

export const PROVIDER_LABELS: Record<SocialProvider, string> = {
  google: "Google",
  facebook: "Facebook",
  apple: "Apple",
};

export type SocialConfig = Record<SocialProvider, boolean>;

const OFF: SocialConfig = { google: false, facebook: false, apple: false };

/** Qué proveedores ofrece este sitio. Sin respuesta, ninguno: la contraseña sigue ahí. */
export async function fetchSocialConfig(): Promise<SocialConfig> {
  try {
    const res = await fetch(`${API_BASE}/auth/social/config/`, { credentials: "include", cache: "no-store" });
    if (!res.ok) return OFF;
    const body = await res.json();
    const providers = body?.providers ?? {};
    return {
      google: providers.google?.enabled === true,
      facebook: providers.facebook?.enabled === true,
      apple: providers.apple?.enabled === true,
    };
  } catch {
    return OFF;
  }
}

/** Donde empieza el intento. `next` sólo si es una ruta de este sitio. */
export function socialStartUrl(provider: RedirectProvider, next?: string | null): string {
  const safe = safeInternalNextPath(next ?? null);
  return apiUrl(`/auth/${provider}/start/${safe ? `?next=${encodeURIComponent(safe)}` : ""}`);
}

const MESSAGES: Record<string, (label: string) => string> = {
  refused: (label) => `No se pudo verificar tu cuenta de ${label}. Vuelve a intentarlo.`,
  cancelled: (label) => `Cancelaste el acceso con ${label}.`,
  link_required: (label) =>
    `Ya existe una cuenta con ese correo. Inicia sesión con tu usuario y contraseña y vincula ${label} desde «Mi cuenta».`,
  not_allowed: (label) =>
    `Esta cuenta no puede entrar con ${label}. Las cuentas del equipo entran con usuario y contraseña.`,
  inactive: () => "Esta cuenta está desactivada.",
  already_linked: (label) => `Esa cuenta de ${label} ya está vinculada a otra cuenta de la tienda.`,
};

export function socialErrorMessage(code: string | null | undefined, provider: string | null | undefined): string {
  const label = PROVIDER_LABELS[(provider ?? "") as SocialProvider] ?? "el proveedor";
  return (MESSAGES[code ?? ""] ?? MESSAGES.refused)(label);
}

// ── «Mi cuenta» ──────────────────────────────────────────────────────────────

export type LinkedProvider = {
  provider: SocialProvider;
  label: string;
  enabled: boolean;
  /** Se vincula desde aquí (Facebook, Apple). Google, desde la pantalla de acceso. */
  linkable: boolean;
  linked: boolean;
  linked_at: string | null;
};

export type LinkedIdentities = {
  has_password: boolean;
  email_is_private_relay: boolean;
  providers: LinkedProvider[];
};

export class SocialLinkError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(message: string, status: number, code = "") {
    super(message);
    this.name = "SocialLinkError";
    this.status = status;
    this.code = code;
  }
}

async function failure(res: Response, fallback: string): Promise<SocialLinkError> {
  const body = await res.json().catch(() => null);
  if (res.status === 429) return new SocialLinkError("Demasiados intentos. Espera un minuto y vuelve a probar.", 429);
  return new SocialLinkError(
    (body && typeof body.detail === "string" && body.detail) || fallback,
    res.status,
    (body && typeof body.code === "string" && body.code) || "",
  );
}

export async function fetchLinkedIdentities(): Promise<LinkedIdentities> {
  const res = await fetchWithAuth(`${API_BASE}/auth/social/identities/`);
  if (!res.ok) throw await failure(res, "No se pudieron cargar tus cuentas vinculadas.");
  return (await res.json()) as LinkedIdentities;
}

/** Empieza a vincular: devuelve la dirección del proveedor a la que hay que ir. */
export async function startLinking(provider: RedirectProvider, password: string): Promise<string> {
  const res = await fetchWithAuth(`${API_BASE}/auth/social/${provider}/link/`, {
    method: "POST",
    body: JSON.stringify({ password }),
  });
  if (!res.ok) throw await failure(res, `No se pudo vincular ${PROVIDER_LABELS[provider]}.`);
  const body = await res.json();
  const url = typeof body?.authorize_url === "string" ? body.authorize_url : "";
  if (!url.startsWith("https://")) throw new SocialLinkError(`No se pudo vincular ${PROVIDER_LABELS[provider]}.`, 500);
  return url;
}

export async function unlinkProvider(provider: SocialProvider): Promise<void> {
  const res = await fetchWithAuth(`${API_BASE}/auth/social/identities/${provider}/`, { method: "DELETE" });
  if (!res.ok) throw await failure(res, `No se pudo desvincular ${PROVIDER_LABELS[provider]}.`);
}
