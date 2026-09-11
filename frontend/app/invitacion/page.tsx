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
 */

import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { API_BASE } from "../lib/api";
import { fetchWithAuth, getCurrentUser } from "../lib/auth";

type InvitationInfo = {
  company_name: string;
  email: string;
  first_name: string;
  last_name: string;
  role_name: string;
  area_name: string;
  expires_at: string;
  /** La cuenta ya existe: hay que demostrar que es suya. */
  requires_authentication: boolean;
};

export default function InvitationPage() {
  return (
    <Suspense fallback={null}>
      <InvitationScreen />
    </Suspense>
  );
}

function InvitationScreen() {
  const params = useSearchParams();
  const token = params.get("token") ?? "";

  const [info, setInfo] = useState<InvitationInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [accepted, setAccepted] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  // El correo de la sesión abierta, si hay alguna. Sirve para NO ofrecer un
  // botón que sólo puede terminar en error: aceptar exige haber iniciado sesión
  // con el correo invitado, y el servidor lo comprueba de todas formas.
  // Comparar aquí no relaja nada; sólo evita el clic inútil.
  const [sessionEmail, setSessionEmail] = useState<string | null | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const user = await getCurrentUser();
      if (!cancelled) setSessionEmail(user?.email?.toLowerCase() ?? null);
    })();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await fetch(
          `${API_BASE}/staff/invitations/accept/?token=${encodeURIComponent(token)}`,
        );
        if (!res.ok) throw new Error("inválida");
        const body = (await res.json()) as InvitationInfo;
        if (!cancelled) setInfo(body);
      } catch {
        if (!cancelled) {
          // UN SOLO MENSAJE para inexistente, alterada, caducada y revocada:
          // distinguirlos diría a quien prueba enlaces si acertó el formato o
          // sólo el plazo.
          setError("Esta invitación no es válida o ya expiró.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [token]);

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

  // `undefined` es «todavía no se sabe»; `null` es «no hay sesión». Mientras no
  // se sepa, no se ofrece aceptar: un botón que parpadea de estado es peor que
  // uno que aparece un momento después.
  const puedeAceptar =
    sessionEmail !== undefined &&
    sessionEmail !== null &&
    info !== null &&
    sessionEmail === info.email.toLowerCase();

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
              Ya formas parte de {accepted}
            </h1>
            <p className="mt-2 text-sm text-muted">
              Tu acceso está activo. Puedes entrar al control interno.
            </p>
            <Link
              href="/admin"
              className="mt-4 inline-flex min-h-11 items-center rounded-lg bg-foreground px-4 text-sm font-semibold text-background"
            >
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
              QUÉ SE OFRECE DEPENDE DE QUIÉN ESTÉ CONECTADO. Aceptar sólo
              funciona si la sesión abierta es la del correo invitado, así que
              en los demás casos se dice qué falta en vez de ofrecer un botón
              que acaba en un error.
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
                    className="min-h-11 rounded-lg bg-foreground px-4 text-sm font-semibold text-background transition hover:bg-foreground/90 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    {sending ? "Aceptando…" : "Aceptar invitación"}
                  </button>
                </div>
              </>
            ) : (
              <>
                <p className="mt-4 text-xs leading-relaxed text-muted">
                  {sessionEmail
                    ? `Ahora mismo estás dentro como ${sessionEmail}. Esta invitación es para ${info.email}: cierra sesión y entra con ese correo para aceptarla.`
                    : info.requires_authentication
                      ? "Ya existe una cuenta con este correo. Inicia sesión con ella para aceptar: tener el enlace no basta para vincular una cuenta."
                      : "Crea tu cuenta con este correo y vuelve a este enlace para aceptar."}
                </p>
                <div className="mt-4 flex flex-wrap gap-2">
                  <Link
                    href={`/auth?next=${encodeURIComponent(`/invitacion?token=${token}`)}`}
                    className="inline-flex min-h-11 items-center rounded-lg bg-foreground px-4 text-sm font-semibold text-background transition hover:bg-foreground/90"
                  >
                    {info.requires_authentication || sessionEmail
                      ? "Iniciar sesión"
                      : "Crear cuenta"}
                  </Link>
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
