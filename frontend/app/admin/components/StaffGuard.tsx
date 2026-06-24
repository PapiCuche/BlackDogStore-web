"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { getCurrentUser, isStaffRole, type AuthUser } from "../../lib/auth";

type Props = {
  children: (user: AuthUser) => React.ReactNode;
};

function Spinner() {
  return (
    <div className="flex min-h-[60vh] items-center justify-center">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-white border-t-transparent" />
    </div>
  );
}

function AccessDenied({ message, linkLabel, linkHref }: { message: string; linkLabel: string; linkHref: string }) {
  return (
    <div className="flex min-h-[60vh] items-center justify-center px-6">
      <div className="mx-auto max-w-md text-center">
        <div className="rounded-2xl border border-white/10 bg-white/5 p-10">
          <p className="text-sm uppercase tracking-[0.3em] font-semibold text-white/40">Acceso</p>
          <h1 className="mt-2 text-2xl font-bold text-white">Acceso restringido</h1>
          <p className="mt-4 text-slate-400">{message}</p>
          <Link
            href={linkHref}
            className="mt-6 inline-block rounded-full bg-white px-6 py-3 text-sm font-semibold text-black transition hover:bg-zinc-200"
          >
            {linkLabel}
          </Link>
        </div>
      </div>
    </div>
  );
}

export function StaffGuard({ children }: Props) {
  const [user, setUser] = useState<AuthUser | null | undefined>(undefined);

  useEffect(() => {
    getCurrentUser().then(setUser);
  }, []);

  if (user === undefined) return <Spinner />;

  if (!user) {
    return (
      <AccessDenied
        message="Necesitas iniciar sesión para acceder a esta sección."
        linkLabel="Iniciar sesión"
        linkHref="/auth"
      />
    );
  }

  if (!isStaffRole(user)) {
    return (
      <AccessDenied
        message="Esta sección requiere rol de inventario, ventas, administrador o superior."
        linkLabel="Volver al inicio"
        linkHref="/"
      />
    );
  }

  return <>{children(user)}</>;
}
