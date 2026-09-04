"use client";

/**
 * Gate for the internal control surface — Phase 2A.2.
 *
 * Replaces AdminGuard on the SHELL and DASHBOARD only. Entering the internal
 * control means: an active Membership in an active Company, or platform master.
 * It explicitly does NOT mean UserProfile.role === "admin", which is why a
 * salesperson or a technician can now open the dashboard at all.
 *
 * Opening the dashboard is not opening every module: each business page keeps
 * its own guard, and every endpoint enforces its own permissions server-side.
 *
 * LEGACY FALLBACK — deliberate.
 * A user with a legacy staff role but no Membership (the state of every existing
 * operator until companies adopt memberships) must keep working. For them the
 * guard passes with `dashboard === null`, and the shell renders in legacy mode:
 * no company header, sidebar driven by the legacy role. Requiring a Membership
 * here would lock existing operators out of a panel they use today.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  NoInternalAccessError,
  fetchInternalDashboard,
  type InternalDashboard,
} from "../lib/internal-api";
import { getCurrentUser, isStaffRole, type AuthUser } from "../../lib/auth";

export type InternalContext = {
  user: AuthUser;
  /** null when the caller reaches the panel through the legacy role only. */
  dashboard: InternalDashboard | null;
  selectedCompanyId: number | null;
  selectCompany: (companyId: number | null) => void;
  reload: () => void;
};

type Props = {
  children: (ctx: InternalContext) => React.ReactNode;
};

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-[70vh] items-center justify-center px-6">
      <div className="w-full max-w-md text-center">{children}</div>
    </div>
  );
}

function Spinner({ label }: { label: string }) {
  return (
    <Centered>
      <div
        className="mx-auto h-8 w-8 animate-spin rounded-full border-2 border-foreground border-t-transparent"
        role="status"
        aria-label={label}
      />
      <p className="mt-4 text-sm text-muted">{label}</p>
    </Centered>
  );
}

function Denied({ title, message }: { title: string; message: string }) {
  return (
    <Centered>
      <div className="rounded-2xl border border-bd-border bg-surface p-10">
        <p className="text-xs font-semibold uppercase tracking-[0.3em] text-muted">
          Control interno
        </p>
        <h1 className="mt-2 text-2xl font-bold text-foreground">{title}</h1>
        <p className="mt-4 text-sm text-muted">{message}</p>
        <Link
          href="/"
          className="mt-6 inline-block rounded-full bg-foreground px-6 py-3 text-sm font-semibold text-background transition hover:bg-foreground/90"
        >
          Volver a la tienda
        </Link>
      </div>
    </Centered>
  );
}

/** Clave neutra: el panel es multiempresa y esto no nombra a ninguna. */
const SELECTED_COMPANY_KEY = "internal-selected-company";

export function InternalControlGuard({ children }: Props) {
  const [user, setUser] = useState<AuthUser | null | undefined>(undefined);
  const [dashboard, setDashboard] = useState<InternalDashboard | null>(null);
  /*
    LA EMPRESA ELEGIDA SOBREVIVE A LA NAVEGACIÓN.

    Estaba en `useState` a secas, y este guard se monta UNA VEZ POR PÁGINA: al
    pasar de /admin a /admin/products la elección se perdía y el master veía
    «No tienes permisos» en cada módulo. Tenía que volver a elegir empresa en
    cada pantalla del panel.

    `localStorage` y no `sessionStorage`: el segundo es POR PESTAÑA, así que
    abrir el panel en otra perdía la empresa otra vez. Cuál administras es una
    preferencia del puesto de trabajo, no de una ventana.

    Y NO ES AUTENTICACIÓN. Este id no concede nada: el backend lo trata como
    untrusted y `resolve_company_for_user` sólo lo usa para SELECCIONAR entre
    las empresas que quien llama ya alcanza. Escribirlo aquí es recordar qué
    estaba mirando, no quién es.
  */
  const [selectedCompanyId, setSelectedCompanyId] = useState<number | null>(null);
  /*
    SE LEE TRAS MONTAR, no en el inicializador.

    Un inicializador perezoso corre TAMBIÉN en el servidor, donde no hay
    `sessionStorage`: devuelve null, React hidrata con ese valor y no vuelve a
    mirar. El primer intento de este arreglo hacía exactamente eso y la empresa
    se seguía perdiendo — el defecto sobrevivía a su propia corrección.
  */
  const [restored, setRestored] = useState(false);
  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(SELECTED_COMPANY_KEY);
      const parsed = stored ? Number(stored) : NaN;
      if (Number.isInteger(parsed) && parsed > 0) setSelectedCompanyId(parsed);
    } catch {
      // Ventana privada o almacenamiento bloqueado: se elige otra vez y ya.
    }
    setRestored(true);
  }, []);
  const [loading, setLoading] = useState(true);
  const [denied, setDenied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    void getCurrentUser().then((u) => {
      if (!cancelled) setUser(u);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (user === undefined || user === null) return;
    // Sin esperar a `restored` se pediría el panel con `null` y el master
    // volvería a ver «selecciona una empresa» aunque ya la hubiera elegido.
    if (!restored) return;
    let cancelled = false;

    void (async () => {
      try {
        const data = await fetchInternalDashboard(selectedCompanyId);
        if (cancelled) return;
        setDashboard(data);
        setDenied(false);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        if (err instanceof NoInternalAccessError) {
          // No company access. A legacy staff role still gets in (see docstring).
          setDashboard(null);
          setDenied(!isStaffRole(user));
          setError(null);
        } else {
          setError(err instanceof Error ? err.message : "Error inesperado.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [user, selectedCompanyId, reloadKey, restored]);

  const selectCompany = useCallback((companyId: number | null) => {
    setLoading(true);
    setSelectedCompanyId(companyId);
    try {
      if (companyId === null) window.localStorage.removeItem(SELECTED_COMPANY_KEY);
      else window.localStorage.setItem(SELECTED_COMPANY_KEY, String(companyId));
    } catch {
      // Una preferencia de interfaz no puede tumbar el panel.
    }
  }, []);

  const reload = useCallback(() => {
    setLoading(true);
    setReloadKey((k) => k + 1);
  }, []);

  if (user === undefined) return <Spinner label="Verificando sesión…" />;

  if (user === null) {
    return (
      <Denied
        title="Inicia sesión"
        message="Necesitas iniciar sesión para acceder al control interno."
      />
    );
  }

  if (loading) return <Spinner label="Cargando control interno…" />;

  if (error) {
    return (
      <Centered>
        <div className="rounded-2xl border border-danger-border bg-red-500/[0.07] p-8">
          <h1 className="text-lg font-semibold text-foreground">No se pudo cargar</h1>
          <p className="mt-3 text-sm text-danger">{error}</p>
          <button
            type="button"
            onClick={reload}
            className="mt-6 rounded-full bg-foreground px-5 py-2.5 text-sm font-semibold text-background transition hover:bg-foreground/90"
          >
            Reintentar
          </button>
        </div>
      </Centered>
    );
  }

  if (denied) {
    return (
      <Denied
        title="Sin acceso interno"
        message="Tu cuenta no pertenece a ninguna empresa activa. Si trabajas en una,
                 pide a su administración que te dé de alta."
      />
    );
  }

  return (
    <>
      {children({ user, dashboard, selectedCompanyId, selectCompany, reload })}
    </>
  );
}
