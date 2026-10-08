/**
 * La invitación de una persona a trabajar en una empresa, vista desde las
 * pantallas que la acompañan hasta aceptarla: `/invitacion`, y las de cuenta
 * (`/auth`, recuperar y restablecer contraseña) cuando alguien llega a ellas
 * desde una invitación.
 *
 * Esas pantallas se pasan la invitación por la DIRECCIÓN DE VUELTA
 * (`?next=/invitacion?token=…`), que ya existía para volver tras iniciar sesión.
 * No se guarda en `localStorage`: un enlace de acceso persistido en el navegador
 * sobrevive a la sesión que lo necesitaba.
 */
import { API_BASE } from "./api";
import { safeInternalNextPath } from "./safe-next";

/**
 * Qué tiene que hacer la persona para poder aceptar. Lo dice el servidor, y
 * sólo a quien trae un enlace válido para ese correo.
 *
 *   none        no hay cuenta con ese correo: la crea.
 *   active      hay cuenta: inicia sesión, o recupera su contraseña.
 *   unverified  hay cuenta, registrada y nunca confirmada: no puede iniciar
 *               sesión con nada; sólo le sirve establecer su contraseña.
 */
export type AccountState = "none" | "active" | "unverified";

export type InvitationInfo = {
  company_name: string;
  email: string;
  first_name: string;
  last_name: string;
  role_name: string;
  area_name: string;
  expires_at: string;
  /** La cuenta ya existe: hay que demostrar que es suya. */
  requires_authentication: boolean;
  /** Ausente en un servidor anterior a esta respuesta: ver `accountStateOf`. */
  account_state?: AccountState;
};

const LOCAL_ORIGIN = "https://local.invalid";
/** La forma de nuestros tokens: base64 seguro para URL, ni corto ni interminable. */
const TOKEN_SHAPE = /^[A-Za-z0-9_-]{16,128}$/;

export function invitationPath(token: string): string {
  return `/invitacion?token=${encodeURIComponent(token)}`;
}

/**
 * El token de la invitación a la que apunta una dirección de vuelta, o `null`
 * si esa dirección no es la de una invitación.
 *
 * Pasa primero por `safeInternalNextPath`: una dirección que no es local no
 * llega a mirarse. Y exige la ruta exacta y un token con la forma de los
 * nuestros: «volver a una invitación» no es «volver a donde diga el enlace».
 */
export function invitationTokenFromNext(raw: string | null | undefined): string | null {
  const next = safeInternalNextPath(raw);
  if (!next) return null;
  let url: URL;
  try {
    url = new URL(next, LOCAL_ORIGIN);
  } catch {
    return null;
  }
  if (url.pathname !== "/invitacion") return null;
  const token = url.searchParams.get("token") ?? "";
  return TOKEN_SHAPE.test(token) ? token : null;
}

/** La dirección de vuelta de esta página, si es la de una invitación. */
export function invitationReturnFromLocation(): string | null {
  if (typeof window === "undefined") return null;
  const token = invitationTokenFromNext(new URLSearchParams(window.location.search).get("next"));
  return token ? invitationPath(token) : null;
}

/**
 * Lo que se supo al leer una invitación.
 *
 *   found    sirve, y esto es lo que dice.
 *   invalid  inexistente, alterada, caducada, revocada o usada: el servidor
 *            responde lo mismo a todas, y aquí no se distingue más.
 *   busy     no se pudo saber: demasiadas lecturas desde esta red en un minuto,
 *            o no hubo respuesta. NO es «inválida»: decirle eso a alguien cuyo
 *            enlace sirve lo manda a pedir otro que no necesita.
 */
export type InvitationRead =
  | { state: "found"; info: InvitationInfo }
  | { state: "invalid" }
  | { state: "busy" };

export async function readInvitation(token: string): Promise<InvitationRead> {
  try {
    const res = await fetch(
      `${API_BASE}/staff/invitations/accept/?token=${encodeURIComponent(token)}`,
      { cache: "no-store" },
    );
    if (res.ok) return { state: "found", info: (await res.json()) as InvitationInfo };
    return res.status === 404 ? { state: "invalid" } : { state: "busy" };
  } catch {
    return { state: "busy" };
  }
}

export function accountStateOf(info: InvitationInfo): AccountState {
  if (info.account_state === "none" || info.account_state === "active" || info.account_state === "unverified") {
    return info.account_state;
  }
  return info.requires_authentication ? "active" : "none";
}
