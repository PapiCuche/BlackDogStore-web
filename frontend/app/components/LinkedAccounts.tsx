"use client";

/**
 * «Cuentas vinculadas» de «Mi cuenta» (SOCIAL-AUTH-01).
 *
 * Qué proveedores abren esta cuenta, y vincular o desvincular uno. Vincular
 * pide la contraseña actual —es la re-autenticación— y lleva al proveedor; al
 * volver, el servidor ya lo dejó vinculado y esta pantalla lo cuenta con
 * `?linked=` o `?link_error=`. Una cuenta sin contraseña (hecha con un
 * proveedor) primero crea una con «¿Olvidaste tu contraseña?».
 *
 * Desvincular nunca deja la cuenta sin forma de entrar: lo decide el servidor.
 * Google se vincula desde la pantalla de acceso, como siempre.
 */

import { useCallback, useEffect, useState } from "react";

import {
  PROVIDER_LABELS, fetchLinkedIdentities, socialErrorMessage, startLinking, unlinkProvider,
  type LinkedIdentities, type LinkedProvider, type RedirectProvider, type SocialProvider,
} from "../lib/social-auth";

const BUTTON =
  "rounded-full border border-bd-border px-4 py-2 text-xs font-semibold uppercase tracking-wide text-foreground " +
  "transition-colors hover:bg-background disabled:opacity-40";

export function LinkedAccounts({ navigate }: { navigate?: (url: string) => void }) {
  const [data, setData] = useState<LinkedIdentities | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [linking, setLinking] = useState<RedirectProvider | null>(null);
  const [password, setPassword] = useState("");
  const [working, setWorking] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await fetchLinkedIdentities());
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudieron cargar tus cuentas vinculadas.");
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    const search = new URLSearchParams(window.location.search);
    void fetchLinkedIdentities().then(
      (found) => { if (!cancelled) setData(found); },
      (err) => { if (!cancelled) setError(err instanceof Error ? err.message : "No se pudieron cargar tus cuentas vinculadas."); },
    ).then(() => {
      if (cancelled) return;
      // Lo que el servidor dejó dicho al volver del proveedor.
      const linked = search.get("linked") as SocialProvider | null;
      if (linked && PROVIDER_LABELS[linked]) setNotice(`${PROVIDER_LABELS[linked]} quedó vinculado a tu cuenta.`);
      const refused = search.get("link_error");
      if (refused) setError(socialErrorMessage(refused, search.get("provider")));
    });
    return () => { cancelled = true; };
  }, []);

  async function link(event: React.FormEvent) {
    event.preventDefault();
    if (!linking) return;
    setWorking(true);
    setError(null);
    try {
      const url = await startLinking(linking, password);
      (navigate ?? ((to: string) => window.location.assign(to)))(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo vincular.");
      setWorking(false);
    }
  }

  async function unlink(provider: SocialProvider) {
    setWorking(true);
    setError(null);
    setNotice(null);
    try {
      await unlinkProvider(provider);
      setNotice(`${PROVIDER_LABELS[provider]} ya no abre tu cuenta.`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo desvincular.");
    } finally {
      setWorking(false);
    }
  }

  function row(item: LinkedProvider) {
    const canLink = item.linkable && item.enabled && !item.linked && data?.has_password;
    return (
      <li key={item.provider} className="flex flex-wrap items-center justify-between gap-3 py-3">
        <div>
          <p className="text-sm font-semibold text-foreground">{item.label}</p>
          <p className="text-xs text-muted">{item.linked ? "Vinculada" : "Sin vincular"}</p>
        </div>
        {item.linked ? (
          <button type="button" disabled={working} onClick={() => void unlink(item.provider)} className={BUTTON}
            aria-label={`Desvincular ${item.label}`}>
            Desvincular
          </button>
        ) : canLink ? (
          <button type="button" disabled={working}
            onClick={() => { setLinking(item.provider as RedirectProvider); setPassword(""); setError(null); }}
            className={BUTTON} aria-label={`Vincular ${item.label}`}>
            Vincular
          </button>
        ) : null}
      </li>
    );
  }

  return (
    <section aria-labelledby="linked-accounts-title" className="rounded-2xl border border-bd-border bg-surface p-5 sm:p-6">
      <h2 id="linked-accounts-title" className="text-base font-semibold text-foreground">Cuentas vinculadas</h2>
      <p className="mt-1 text-sm text-muted">Formas de entrar a tu cuenta además de tu usuario y contraseña.</p>

      {notice ? (
        <p role="status" className="mt-4 rounded-xl border border-bd-border bg-background px-4 py-3 text-sm text-foreground">{notice}</p>
      ) : null}
      {error ? (
        <p role="alert" className="mt-4 rounded-xl border border-danger-border bg-danger-surface px-4 py-3 text-sm text-danger">{error}</p>
      ) : null}

      {data ? (
        <>
          <ul className="mt-3 divide-y divide-bd-border">{data.providers.map(row)}</ul>
          {!data.has_password ? (
            <p className="mt-3 text-xs text-muted">
              Para vincular otra forma de entrar, primero crea una contraseña desde «¿Olvidaste tu contraseña?».
            </p>
          ) : null}
          {data.email_is_private_relay ? (
            <p className="mt-3 text-xs text-muted">
              Tu correo es una dirección de reenvío privado de Apple: lo que te enviemos llega a tu correo real.
            </p>
          ) : null}
        </>
      ) : null}

      {linking ? (
        <form onSubmit={link} className="mt-4 space-y-3 rounded-xl border border-bd-border bg-background p-4">
          <label htmlFor="linked-accounts-password" className="block text-xs font-bold uppercase tracking-wide text-muted">
            Contraseña actual
          </label>
          <input id="linked-accounts-password" type="password" value={password} autoComplete="current-password" required
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-xl border border-bd-border bg-surface px-4 py-3 text-sm text-foreground focus:outline-none" />
          <div className="flex flex-wrap gap-2">
            <button type="submit" disabled={working || !password}
              className="rounded-full bg-foreground px-5 py-2.5 text-xs font-semibold uppercase tracking-wide text-background transition-opacity hover:opacity-90 disabled:opacity-40">
              {`Continuar con ${PROVIDER_LABELS[linking]}`}
            </button>
            <button type="button" disabled={working} onClick={() => { setLinking(null); setPassword(""); }}
              className="rounded-full border border-bd-border px-5 py-2.5 text-xs font-semibold uppercase tracking-wide text-muted transition-colors hover:text-foreground">
              Cancelar
            </button>
          </div>
        </form>
      ) : null}
    </section>
  );
}
