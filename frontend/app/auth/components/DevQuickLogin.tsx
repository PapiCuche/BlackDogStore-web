"use client";

/**
 * Accesos rápidos de desarrollo — TEMPORAL, RETIRAR ANTES DE PRODUCCIÓN.
 *
 * NO ES UNA VÍA DE AUTENTICACIÓN. «Usar cuenta» sólo RELLENA los campos; quien
 * los use sigue pulsando «Iniciar sesión» y pasando por el mismo `login()` →
 * JWT + cookie HttpOnly + CSRF que cualquiera. No se guarda ningún token y no
 * se salta nada.
 *
 * Fuera de desarrollo el componente devuelve `null`, así que no está meramente
 * oculto con CSS: no llega a existir, y `next build` pliega la rama.
 *
 * EL DEFECTO QUE ESTE FICHERO TENÍA
 * ---------------------------------
 * Traía su PROPIA lista de seis cuentas y la contraseña escritas a mano, y las
 * anunciaba incondicionalmente. Si nadie había ejecutado `seed_demo_users` —el
 * caso normal en un entorno recién clonado— la pantalla ofrecía seis
 * credenciales que el backend rechazaba con «No active account found with the
 * given credentials». Prometer una credencial que no existe es peor que no
 * ofrecer ninguna: manda a depurar el login, que funciona.
 *
 * Y eran DOS listas —ésta y la del comando— que podían separarse en silencio.
 *
 * Ahora hay UNA. `/api/dev/demo-accounts/` pregunta al propio comando qué
 * cuentas existen y cuáles sirven, y esto pinta esa respuesta. Una cuenta que
 * no se puede usar se muestra apagada y sin botón, con el comando exacto para
 * crearla — con el slug real de una empresa de esta base, no un marcador.
 */

import { useEffect, useState } from "react";
import { API_BASE } from "../../lib/api";

type DemoAccount = {
  username: string;
  label: string;
  destination: string;
  authority: string;
  exists: boolean;
  /** Existe Y está activa. Existir sin poder entrar no sirve de nada. */
  usable: boolean;
};

type DemoAccountsResponse = {
  password: string;
  accounts: DemoAccount[];
  ready: boolean;
  seed_command: string;
};

type Props = {
  onUse: (username: string, password: string) => void;
};

export function DevQuickLogin({ onUse }: Props) {
  const [data, setData] = useState<DemoAccountsResponse | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (process.env.NODE_ENV === "production") return;
    let cancelled = false;
    void (async () => {
      try {
        const res = await fetch(`${API_BASE}/dev/demo-accounts/`, { cache: "no-store" });
        // 404 es la respuesta normal cuando el backend corre con DEBUG=False:
        // esta superficie no existe allí, y no hay nada que enseñar.
        if (!res.ok) throw new Error(String(res.status));
        const json = (await res.json()) as DemoAccountsResponse;
        if (!cancelled) setData(json);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  if (process.env.NODE_ENV === "production") return null;
  if (failed || !data) return null;

  return (
    <section className="mt-8 rounded-xl border border-bd-border bg-surface p-4">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-xs font-bold uppercase tracking-widest text-muted">
          Accesos de desarrollo
        </h2>
        <p className="font-mono text-[11px] text-muted">{data.password}</p>
      </header>

      {!data.ready ? (
        <div className="mt-3 rounded-lg border border-warning-border bg-warning-surface px-3 py-2.5">
          <p className="text-xs text-warning">
            Faltan cuentas por crear. Las apagadas no se pueden usar todavía.
          </p>
          <code className="mt-1.5 block break-all font-mono text-[11px] text-muted">
            {data.seed_command}
          </code>
        </div>
      ) : null}

      <ul className="mt-3 space-y-1.5">
        {data.accounts.map((account) => (
          <li
            key={account.username}
            className="flex items-center justify-between gap-3 rounded-lg border border-bd-border px-3 py-2"
          >
            <div className="min-w-0">
              <p className="truncate text-xs font-semibold text-foreground">
                {account.label}
                {!account.usable ? (
                  <span className="ml-1.5 font-normal text-muted">
                    {account.exists ? "(inactiva)" : "(sin crear)"}
                  </span>
                ) : null}
              </p>
              <p className="truncate font-mono text-[11px] text-muted">
                {account.username}
              </p>
              <p className="mt-0.5 truncate text-[11px] text-muted">
                → {account.destination}
              </p>
            </div>
            {/*
              Sin botón cuando la cuenta no sirve. Un botón que rellena unas
              credenciales que van a fallar es la promesa falsa otra vez, sólo
              que con un clic de por medio.
            */}
            {account.usable ? (
              <button
                type="button"
                onClick={() => onUse(account.username, data.password)}
                className="min-h-11 shrink-0 rounded-lg border border-bd-border px-2.5 text-xs font-medium text-foreground transition-colors hover:bg-surface-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
              >
                Usar cuenta
              </button>
            ) : null}
          </li>
        ))}
      </ul>

      <p className="mt-3 text-[11px] leading-relaxed text-muted">
        Sólo desarrollo. Se eliminan con{" "}
        <code className="font-mono">python manage.py seed_demo_users --purge</code>.
      </p>
    </section>
  );
}
