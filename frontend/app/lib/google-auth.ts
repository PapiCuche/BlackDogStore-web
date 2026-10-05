/**
 * GOOGLE-AUTH — «Continuar con Google», del lado del navegador.
 *
 * EL NAVEGADOR SÓLO TRANSPORTA. Recibe de Google un ID token y lo entrega al
 * backend, que es quien lo verifica (firma, aplicación, caducidad, intento) y
 * abre la sesión de siempre, en cookies HttpOnly. Aquí no se guarda nada: ni el
 * token ni una sesión, ni en `localStorage` ni en ninguna otra parte.
 *
 * Las peticiones llevan `credentials: "include"` porque el intento viaja también
 * en una cookie HttpOnly que el servidor compara con el token.
 */

import { API_BASE } from "./api";
import type { AuthUser } from "./auth";

export type GoogleConfig = { enabled: false } | { enabled: true; client_id: string; nonce: string };

export class GoogleAuthError extends Error {
  readonly status: number;
  /** `link_required`: ese correo ya tiene cuenta y hay que probar su contraseña. */
  readonly code: string;

  constructor(message: string, status: number, code = "") {
    super(message);
    this.name = "GoogleAuthError";
    this.status = status;
    this.code = code;
  }
}

const base = `${API_BASE}/auth/google`;

export async function fetchGoogleConfig(): Promise<GoogleConfig> {
  try {
    const res = await fetch(`${base}/config/`, { credentials: "include", cache: "no-store" });
    if (!res.ok) return { enabled: false };
    const body = await res.json();
    return body?.enabled && body.client_id && body.nonce
      ? { enabled: true, client_id: String(body.client_id), nonce: String(body.nonce) }
      : { enabled: false };
  } catch {
    // Sin respuesta no hay botón: el acceso con contraseña sigue ahí.
    return { enabled: false };
  }
}

async function send(path: string, payload: Record<string, string>): Promise<AuthUser> {
  const res = await fetch(`${base}${path}`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    throw new GoogleAuthError(
      (body && typeof body.detail === "string" && body.detail) || "No se pudo entrar con Google.",
      res.status,
      (body && typeof body.code === "string" && body.code) || "",
    );
  }
  return body.user as AuthUser;
}

export const signInWithGoogle = (credential: string) => send("/", { credential });

export const linkGoogleAccount = (credential: string, password: string) =>
  send("/link/", { credential, password });

const SCRIPT = "https://accounts.google.com/gsi/client";

type GoogleId = {
  initialize: (options: Record<string, unknown>) => void;
  renderButton: (element: HTMLElement, options: Record<string, unknown>) => void;
};

function current(): GoogleId | null {
  const api = (window as unknown as { google?: { accounts?: { id?: GoogleId } } }).google;
  return api?.accounts?.id ?? null;
}

let loading: Promise<GoogleId | null> | null = null;

/** La biblioteca de Google, cargada una vez y sólo si hace falta. */
export function loadGoogleIdentity(): Promise<GoogleId | null> {
  const ready = current();
  if (ready) return Promise.resolve(ready);
  if (!loading) {
    loading = new Promise((resolve) => {
      const script = document.createElement("script");
      script.src = SCRIPT;
      script.async = true;
      script.onload = () => resolve(current());
      script.onerror = () => { loading = null; resolve(null); };
      document.head.appendChild(script);
    });
  }
  return loading;
}
