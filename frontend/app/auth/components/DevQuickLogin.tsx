"use client";

/**
 * Development-only quick logins — TEMPORARY, REMOVE BEFORE PRODUCTION.
 *
 * This is a convenience widget, not an authentication path. Pressing "Usar
 * cuenta" only FILLS the username and password fields; the operator still has to
 * press "Iniciar sesión" and go through the real login() → JWT + HttpOnly cookie
 * + CSRF flow. No token is stored, nothing is bypassed.
 *
 * The whole component returns null outside development, so it is not merely
 * hidden with CSS — it never renders, and `next build` folds the branch away.
 *
 * The accounts come from:
 *     python manage.py seed_demo_users --company-slug <slug>
 * and are removed with:
 *     python manage.py seed_demo_users --purge
 *
 * The login screen belongs to the EXTERNAL PORTAL. This block is a development
 * tool sitting on it, not an administrative surface: nothing here implies that
 * /admin is the definitive Platform Control.
 */

const DEMO_PASSWORD = "Demo123!";

type DemoAccount = {
  username: string;
  label: string;
  destination: string;
  pending?: boolean;
};

const DEMO_ACCOUNTS: DemoAccount[] = [
  { username: "dev_customer", label: "Cliente", destination: "E-commerce" },
  { username: "dev_sales", label: "Ventas", destination: "Pedidos / ventas" },
  { username: "dev_inventory", label: "Inventario", destination: "Inventario" },
  {
    username: "dev_technician",
    label: "Técnico",
    destination: "Servicio Técnico",
    pending: true,
  },
  { username: "dev_admin", label: "Admin empresa", destination: "Control Interno" },
  {
    username: "dev_master",
    label: "MASTER",
    destination: "Control MASTER",
    pending: true,
  },
];

type Props = {
  onUse: (username: string, password: string) => void;
};

export function DevQuickLogin({ onUse }: Props) {
  // Not a CSS hide: in a production build this component renders nothing at all.
  if (process.env.NODE_ENV !== "development") return null;

  return (
    <section className="mt-8 rounded-xl border border-dashed border-bd-border bg-surface p-5">
      <div className="mb-1 flex items-center gap-2">
        <span className="rounded border border-bd-border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-widest text-foreground/85">
          Solo desarrollo
        </span>
        <h2 className="text-sm font-semibold text-foreground">Accesos de desarrollo</h2>
      </div>
      <p className="mb-4 text-xs text-muted">
        Usuarios temporales para probar roles durante el desarrollo. Rellenan el
        formulario; el inicio de sesión sigue siendo el real.
      </p>

      <div className="grid gap-2 sm:grid-cols-2">
        {DEMO_ACCOUNTS.map((account) => (
          <div
            key={account.username}
            className="rounded-lg border border-bd-border bg-background/30 p-3"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="text-sm font-medium text-foreground">{account.label}</p>
                <p className="truncate font-mono text-[11px] text-muted">
                  {account.username}
                </p>
                <p className="font-mono text-[11px] text-muted">{DEMO_PASSWORD}</p>
                <p className="mt-1 text-[11px] text-muted">
                  → {account.destination}
                  {account.pending ? (
                    <span className="ml-1 text-muted">(UI pendiente)</span>
                  ) : null}
                </p>
              </div>
              <button
                type="button"
                onClick={() => onUse(account.username, DEMO_PASSWORD)}
                className="shrink-0 rounded-lg border border-bd-border px-2.5 py-1.5 text-xs font-medium text-foreground/85 transition hover:border-bd-border hover:text-foreground"
              >
                Usar cuenta
              </button>
            </div>
          </div>
        ))}
      </div>

      <p className="mt-4 text-[11px] leading-relaxed text-muted">
        Crear con{" "}
        <code className="text-muted">
          python manage.py seed_demo_users --company-slug &lt;slug&gt;
        </code>
        , eliminar con{" "}
        <code className="text-muted">
          python manage.py seed_demo_users --purge
        </code>
        . El módulo de Servicio Técnico y la UI de Platform Control todavía no
        existen; <code className="text-muted">/admin</code> sigue siendo el
        panel legacy.
      </p>
    </section>
  );
}
