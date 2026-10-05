"use client";

/**
 * «Continuar con Google».
 *
 * Aparece sólo donde la tienda lo tiene configurado. El botón lo dibuja Google
 * (es su marca y su flujo); lo que pasa después es de aquí: el token va al
 * backend, y si ese correo ya tenía cuenta se pide su contraseña antes de
 * vincular nada.
 */

import { useEffect, useRef, useState } from "react";

import type { AuthUser } from "../lib/auth";
import {
  fetchGoogleConfig, GoogleAuthError, linkGoogleAccount, loadGoogleIdentity, signInWithGoogle,
} from "../lib/google-auth";

export function GoogleSignIn({ onSignedIn }: { onSignedIn: (user: AuthUser) => void }) {
  const slot = useRef<HTMLDivElement>(null);
  const done = useRef(onSignedIn);
  const library = useRef<Awaited<ReturnType<typeof loadGoogleIdentity>>>(null);
  const [available, setAvailable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<{ credential: string; message: string } | null>(null);
  const [password, setPassword] = useState("");
  const [working, setWorking] = useState(false);

  useEffect(() => { done.current = onSignedIn; }, [onSignedIn]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const config = await fetchGoogleConfig();
      if (cancelled || !config.enabled) return;
      const google = await loadGoogleIdentity();
      if (cancelled || !google) return;

      google.initialize({
        client_id: config.client_id,
        nonce: config.nonce,
        ux_mode: "popup",
        callback: (response: { credential?: string }) => {
          const credential = response.credential;
          if (!credential) return;
          setError(null);
          signInWithGoogle(credential).then(
            (user) => done.current(user),
            (err) => {
              if (err instanceof GoogleAuthError && err.code === "link_required") {
                setPending({ credential, message: err.message });
              } else {
                setError(err instanceof Error ? err.message : "No se pudo entrar con Google.");
              }
            },
          );
        },
      });
      library.current = google;
      setAvailable(true);
    })();
    return () => { cancelled = true; };
  }, []);

  // El botón se dibuja cada vez que su hueco existe: al cargar, y otra vez al
  // volver del formulario de vinculación (el hueco de antes ya no está).
  useEffect(() => {
    if (!available || pending || !slot.current || !library.current) return;
    library.current.renderButton(slot.current, {
      type: "standard", theme: "outline", size: "large", shape: "pill",
      text: "continue_with", logo_alignment: "center", locale: "es",
      width: Math.min(360, slot.current.clientWidth || 320),
    });
  }, [available, pending]);

  async function link(event: React.FormEvent) {
    event.preventDefault();
    if (!pending) return;
    setWorking(true);
    setError(null);
    try {
      done.current(await linkGoogleAccount(pending.credential, password));
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo vincular la cuenta.");
    } finally {
      setWorking(false);
    }
  }

  if (!available) return null;

  return (
    <div className="mt-6">
      <div className="flex items-center gap-3 text-xs uppercase tracking-wide text-muted" aria-hidden="true">
        <span className="h-px flex-1 bg-bd-border" />o<span className="h-px flex-1 bg-bd-border" />
      </div>

      {pending ? (
        <form onSubmit={link} className="mt-5 space-y-3 rounded-xl border border-bd-border bg-background p-4 text-left">
          <p className="text-sm text-foreground">{pending.message}</p>
          <label className="block text-xs font-bold uppercase tracking-wide text-muted">
            Contraseña de tu cuenta
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
              className="mt-2 w-full rounded-xl border border-bd-border bg-surface px-4 py-3 text-sm text-foreground focus:outline-none"
            />
          </label>
          <div className="flex flex-wrap gap-2">
            <button
              type="submit"
              disabled={working || !password}
              className="rounded-full bg-foreground px-5 py-2.5 text-xs font-semibold uppercase tracking-wide text-background transition-opacity hover:opacity-90 disabled:opacity-40"
            >
              Vincular y entrar
            </button>
            <button
              type="button"
              disabled={working}
              onClick={() => { setPending(null); setPassword(""); setError(null); }}
              className="rounded-full border border-bd-border px-5 py-2.5 text-xs font-semibold uppercase tracking-wide text-muted transition-colors hover:text-foreground"
            >
              Cancelar
            </button>
          </div>
        </form>
      ) : (
        // El botón es de Google: se dibuja dentro de este hueco.
        <div ref={slot} className="mt-5 flex min-h-[44px] justify-center" data-google-button />
      )}

      {error ? (
        <p role="alert" className="mt-3 rounded-xl border border-danger-border bg-danger-surface px-4 py-3 text-sm text-danger">
          {error}
        </p>
      ) : null}
    </div>
  );
}
