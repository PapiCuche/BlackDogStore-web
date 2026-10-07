"use client";

/**
 * Aceptar una invitación para trabajar en una empresa.
 *
 * RUTA PÚBLICA PARA LEER, AUTENTICADA PARA ACEPTAR. Se muestra a qué empresa se
 * invita —sin eso la persona no sabría qué está aceptando— y nada más: ni quién
 * más trabaja allí, ni qué permisos concede.
 *
 * EL TOKEN NO SE GUARDA. Vive en la barra de direcciones mientras dura la
 * pantalla. No va a `localStorage` ni a ninguna otra parte: un enlace de acceso
 * persistido en el navegador sobrevive a la sesión que lo necesitaba.
 *
 * LO QUE SE OFRECE ES EL CAMINO QUE TERMINA. El servidor dice qué pasa con la
 * cuenta de ese correo (`account_state`) y esta pantalla ofrece eso y no otra
 * cosa: crearla, iniciar sesión, o establecer la contraseña. Ofrecía «Crear
 * cuenta» a quien ya tenía una a medias, y esa persona acababa ante «ese correo
 * ya está registrado» sin poder entrar con nada.
 *
 * LA CONTRASEÑA ES DE LA PERSONA. Aquí nadie la escribe por ella: quien no la
 * conoce recibe en su correo el mismo enlace de recuperación que cualquiera, y
 * ese enlace la trae de vuelta a esta invitación.
 */

import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { API_BASE } from "../lib/api";
import { fetchWithAuth, getCurrentUser, logout, requestPasswordReset } from "../lib/auth";
import {
  accountStateOf, fetchInvitation, invitationPath, type InvitationInfo,
} from "../lib/invitation";

export default function InvitationPage() {
  return (
    <Suspense fallback={null}>
      <InvitationScreen />
    </Suspense>
  );
}

const primaryButton =
  "inline-flex min-h-11 items-center rounded-lg bg-foreground px-4 text-sm font-semibold text-background transition hover:bg-foreground/90 disabled:cursor-not-allowed disabled:opacity-60";
const secondaryButton =
  "inline-flex min-h-11 items-center rounded-lg border border-bd-border px-4 text-sm font-semibold text-foreground transition hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-60";

function InvitationScreen() {
  const params = useSearchParams();
  const token = params.get("token") ?? "";

  const [info, setInfo] = useState<InvitationInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [accepted, setAccepted] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  // Se pidió el enlace para establecer la contraseña. Se dice que se envió, no
  // si la cuenta existe: eso ya lo sabe quien tiene esta invitación.
  const [linkRequested, setLinkRequested] = useState(false);
  // El correo de la sesión abierta, si hay alguna. Sirve para NO ofrecer un
  // botón que sólo puede terminar en error: aceptar exige haber iniciado sesión
  // con el correo invitado, y el servidor lo comprueba de todas formas.
  // Comparar aquí no relaja nada; sólo evita el clic inútil.
  const [sessionEmail, setSessionEmail] = useState<string | null | undefined>(undefined);
  // Se vuelve a preguntar al regresar a la pestaña o al volver atrás: entre
  // tanto la persona pudo crear la cuenta o iniciar sesión en otra parte, y lo
  // que esta pantalla ofrecía dejó de ser cierto.
  const [asked, setAsked] = useState(0);

  useEffect(() => {
    const askAgain = () => setAsked((n) => n + 1);
    const whenVisible = () => { if (document.visibilityState === "visible") askAgain(); };
    window.addEventListener("pageshow", askAgain);
    document.addEventListener("visibilitychange", whenVisible);
    return () => {
      window.removeEventListener("pageshow", askAgain);
      document.removeEventListener("visibilitychange", whenVisible);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const user = await getCurrentUser();
      if (!cancelled) setSessionEmail(user?.email?.toLowerCase() ?? null);
    })();
    return () => { cancelled = true; };
  }, [asked]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const found = await fetchInvitation(token);
      if (cancelled) return;
      if (found) {
        setInfo(found);
      } else {
        // UN SOLO MENSAJE para inexistente, alterada, caducada y revocada:
        // distinguirlos diría a quien prueba enlaces si acertó el formato o
        // sólo el plazo.
        setInfo(null);
        setError("Esta invitación no es válida o ya expiró.");
      }
      setLoading(false);
    })();
    return () => { cancelled = true; };
  }, [token, asked]);

  const accept = useCallback(async () => {
    if (sending) return;
    setSending(true);
    setError(null);
    try {
      const res = await fetchWithAuth(`${API_BASE}/staff/invitations/accept/`, {
        method: "POST",
        body: JSON.stringify({ token }),
      });
      if (res.status === 401) {
        setError(
          "Inicia sesión con el correo al que se envió esta invitación y vuelve a intentarlo.",
        );
        return;
      }
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        setError(body?.detail ?? "No se pudo aceptar la invitación.");
        return;
      }
      const body = await res.json();
      setAccepted(body.company_name);
    } catch {
      setError("No se pudo aceptar la invitación.");
    } finally {
      setSending(false);
    }
  }, [token, sending]);

  const requestLink = useCallback(async () => {
    if (sending || !info) return;
    setSending(true);
    setError(null);
    try {
      await requestPasswordReset(info.email, invitationPath(token));
      setLinkRequested(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo enviar el enlace.");
    } finally {
      setSending(false);
    }
  }, [info, token, sending]);

  const leave = useCallback(async () => {
    if (sending) return;
    setSending(true);
    try {
      await logout().catch(() => {});
      window.dispatchEvent(new Event("authChange"));
      setSessionEmail(null);
    } finally {
      setSending(false);
    }
  }, [sending]);

  // `undefined` es «todavía no se sabe»; `null` es «no hay sesión». Mientras no
  // se sepa, no se ofrece aceptar: un botón que parpadea de estado es peor que
  // uno que aparece un momento después.
  const puedeAceptar =
    sessionEmail !== undefined &&
    sessionEmail !== null &&
    info !== null &&
    sessionEmail === info.email.toLowerCase();

  const account = info ? accountStateOf(info) : "none";
  const back = encodeURIComponent(invitationPath(token));

  return (
    <main className="mx-auto w-full max-w-md px-4 py-16">
      <div className="rounded-xl border border-bd-border bg-surface p-6">
        {loading ? (
          <div className="flex items-center gap-3 text-sm text-muted">
            <div className="h-4 w-4 animate-spin rounded-full border-2 border-bd-border border-t-transparent" />
            Comprobando la invitación…
          </div>
        ) : accepted ? (
          <>
            <h1 className="font-display text-xl text-foreground">
              Tu acceso ha sido configurado correctamente
            </h1>
            <p className="mt-2 text-sm text-muted">
              Ya formas parte de {accepted}. Puedes entrar al control interno.
            </p>
            <Link href="/admin" className={`mt-4 ${primaryButton}`}>
              Ir al panel
            </Link>
          </>
        ) : info ? (
          <>
            <h1 className="font-display text-xl text-foreground">
              Te han invitado a {info.company_name}
            </h1>
            <dl className="mt-4 space-y-1.5 text-sm">
              {info.role_name ? (
                <div className="flex gap-2">
                  <dt className="text-muted">Rol:</dt>
                  <dd className="text-foreground/85">{info.role_name}</dd>
                </div>
              ) : null}
              {info.area_name ? (
                <div className="flex gap-2">
                  <dt className="text-muted">Área:</dt>
                  <dd className="text-foreground/85">{info.area_name}</dd>
                </div>
              ) : null}
              <div className="flex gap-2">
                <dt className="text-muted">Correo:</dt>
                <dd className="break-all text-foreground/85">{info.email}</dd>
              </div>
            </dl>

            {error ? (
              <div
                role="alert"
                className="mt-4 rounded-lg border border-danger-border bg-danger-surface px-3 py-2.5"
              >
                <p className="text-sm text-danger">{error}</p>
              </div>
            ) : null}

            {/*
              QUÉ SE OFRECE DEPENDE DE QUIÉN ESTÉ CONECTADO Y DE QUÉ CUENTA
              HAYA. Aceptar sólo funciona si la sesión abierta es la del correo
              invitado, así que en los demás casos se dice qué falta y se ofrece
              lo único que lo resuelve.
            */}
            {puedeAceptar ? (
              <>
                <p className="mt-4 text-xs leading-relaxed text-muted">
                  Aceptar añade tu cuenta a {info.company_name} con el rol
                  indicado.
                </p>
                <div className="mt-4 flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => void accept()}
                    disabled={sending}
                    className={primaryButton}
                  >
                    {sending ? "Aceptando…" : "Aceptar invitación"}
                  </button>
                </div>
              </>
            ) : sessionEmail ? (
              <>
                <p className="mt-4 text-xs leading-relaxed text-muted">
                  Ahora mismo estás dentro como {sessionEmail}. Esta invitación
                  es para {info.email}, que es otra cuenta: cierra esta sesión y
                  entra con ese correo para aceptarla.
                </p>
                <div className="mt-4 flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => void leave()}
                    disabled={sending}
                    className={primaryButton}
                  >
                    Cerrar sesión
                  </button>
                </div>
              </>
            ) : linkRequested ? (
              <div className="mt-4 rounded-lg border border-bd-border bg-background px-3 py-2.5">
                <p className="text-sm font-semibold text-foreground">Te enviamos un enlace</p>
                <p className="mt-1 text-xs leading-relaxed text-muted">
                  Revisa el buzón de {info.email} (también el correo no deseado).
                  Ábrelo, elige tu contraseña y volverás a esta invitación para
                  aceptarla. El enlace dura una hora.
                </p>
              </div>
            ) : account === "none" ? (
              <>
                <p className="mt-4 text-sm font-semibold text-foreground">
                  Crea tu cuenta para continuar
                </p>
                <p className="mt-1 text-xs leading-relaxed text-muted">
                  Elegirás tu propia contraseña. Nadie más la ve ni la decide.
                </p>
                <div className="mt-4 flex flex-wrap gap-2">
                  <Link href={`/auth?mode=register&next=${back}`} className={primaryButton}>
                    Crear mi cuenta
                  </Link>
                </div>
              </>
            ) : account === "unverified" ? (
              <>
                <p className="mt-4 text-sm font-semibold text-foreground">
                  Ya tienes una cuenta con este correo
                </p>
                <p className="mt-1 text-xs leading-relaxed text-muted">
                  Está a medio terminar: todavía no puede iniciar sesión.
                  Establece tu contraseña con un enlace que te enviaremos a ese
                  correo y quedará lista.
                </p>
                <div className="mt-4 flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => void requestLink()}
                    disabled={sending}
                    className={primaryButton}
                  >
                    Establecer mi contraseña
                  </button>
                </div>
              </>
            ) : (
              <>
                <p className="mt-4 text-sm font-semibold text-foreground">
                  Ya tienes una cuenta con este correo
                </p>
                <p className="mt-1 text-xs leading-relaxed text-muted">
                  Inicia sesión con ella para aceptar: tener el enlace no basta
                  para vincular una cuenta. Si no conoces tu contraseña, te
                  enviamos un enlace a ese correo para que elijas una.
                </p>
                <div className="mt-4 flex flex-wrap gap-2">
                  <Link href={`/auth?next=${back}`} className={primaryButton}>
                    Iniciar sesión
                  </Link>
                  <button
                    type="button"
                    onClick={() => void requestLink()}
                    disabled={sending}
                    className={secondaryButton}
                  >
                    No conozco mi contraseña
                  </button>
                </div>
              </>
            )}
          </>
        ) : (
          <>
            <h1 className="font-display text-xl text-foreground">
              Invitación no válida
            </h1>
            <p className="mt-2 text-sm text-muted">
              {error ?? "Esta invitación no es válida o ya expiró."}
            </p>
            <p className="mt-3 text-xs text-muted">
              Pide a la empresa que te envíe una nueva.
            </p>
          </>
        )}
      </div>
    </main>
  );
}
