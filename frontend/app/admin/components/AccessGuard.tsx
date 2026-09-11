"use client";

/**
 * La puerta de una pantalla interna — H4.1.2A (RBAC-LEGACY-UI-01).
 *
 * Sustituye a `StaffGuard` y `AdminGuard`, que decidían con el rol global.
 * Pregunta al servidor qué capacidades tiene quien mira DENTRO de su empresa y
 * abre la pantalla con la misma regla que el endpoint que hay detrás; para el
 * operador del puente legacy —sin Membership— conserva su rol como autoridad,
 * porque es lo que el backend sigue comprobando en ese camino.
 *
 * Sigue sin ser autorización: cada petición se vuelve a decidir en el servidor.
 * Esto sólo evita ofrecer una pantalla que iba a responder 403, y dejar de
 * ofrecer una a la que sí se tenía derecho.
 *
 * El contexto que publica lo consume `AdminShell`, para que el panel no vuelva a
 * pedir lo que este guard acaba de traer.
 */

import Link from "next/link";
import { createContext, useEffect, useState } from "react";
import { getCurrentUser, type AuthUser } from "../../lib/auth";
import {
  NoInternalAccessError,
  fetchInternalDashboard,
  type InternalDashboard,
} from "../lib/internal-api";
import { buildInternalAccess, type InternalAccess } from "../lib/internal-access";

export const InternalAccessContext = createContext<InternalAccess | null>(null);

type Props = {
  /** La capacidad que abre la pantalla: la misma que exige su endpoint. */
  capability: string;
  /** Los roles que el backend acepta en el puente legacy para esa pantalla. */
  legacyRoles: readonly string[];
  children: (access: InternalAccess) => React.ReactNode;
};

function Spinner() {
  return (
    <div className="flex min-h-[60vh] items-center justify-center">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-foreground border-t-transparent" />
    </div>
  );
}

function Notice({
  message,
  linkLabel,
  linkHref,
}: {
  message: string;
  linkLabel: string;
  linkHref: string;
}) {
  return (
    <div className="flex min-h-[60vh] items-center justify-center px-6">
      <div className="mx-auto max-w-md text-center">
        <div className="rounded-2xl border border-bd-border bg-surface p-10">
          <p className="text-sm uppercase tracking-[0.3em] font-semibold text-muted">Acceso</p>
          <h1 className="mt-2 text-2xl font-bold text-foreground">Acceso restringido</h1>
          <p className="mt-4 text-muted">{message}</p>
          <Link
            href={linkHref}
            className="mt-6 inline-block rounded-full bg-foreground px-6 py-3 text-sm font-semibold text-background transition hover:bg-foreground/90"
          >
            {linkLabel}
          </Link>
        </div>
      </div>
    </div>
  );
}

export function AccessGuard({ capability, legacyRoles, children }: Props) {
  const [user, setUser] = useState<AuthUser | null | undefined>(undefined);
  const [dashboard, setDashboard] = useState<InternalDashboard | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

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
    if (!user) return;
    let cancelled = false;
    void (async () => {
      try {
        const data = await fetchInternalDashboard();
        if (!cancelled) setDashboard(data);
      } catch (err) {
        if (cancelled) return;
        if (err instanceof NoInternalAccessError) {
          // No es un fallo: es el operador legacy, sin empresa que consultar.
          setDashboard(null);
        } else {
          // Un fallo de red NO se resuelve tirando de rol: eso decidiría con
          // menos información de la que hay, en la dirección de abrir de más.
          setError(err instanceof Error ? err.message : "No se pudo comprobar tu acceso.");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [user]);

  if (user === undefined) return <Spinner />;

  if (user === null) {
    return (
      <Notice
        message="Necesitas iniciar sesión para acceder a esta sección."
        linkLabel="Iniciar sesión"
        linkHref="/auth"
      />
    );
  }

  if (error !== null) {
    return (
      <Notice
        message={`No se pudo comprobar tu acceso: ${error}`}
        linkLabel="Volver al panel"
        linkHref="/admin"
      />
    );
  }

  if (dashboard === undefined) return <Spinner />;

  const access = buildInternalAccess(user, dashboard);

  if (!access.can(capability, legacyRoles)) {
    const needsCompany = dashboard !== null && !access.hasCompanyContext;
    return (
      <Notice
        message={
          needsCompany
            ? "Elige una empresa en el control interno para abrir esta sección."
            : "Tu cuenta no tiene permiso para esta sección en esta empresa."
        }
        linkLabel="Volver al panel"
        linkHref="/admin"
      />
    );
  }

  return (
    <InternalAccessContext.Provider value={access}>
      {children(access)}
    </InternalAccessContext.Provider>
  );
}
