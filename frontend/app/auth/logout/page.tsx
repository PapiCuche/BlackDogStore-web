"use client";

import { useEffect } from "react";
import { logout } from "../../lib/auth";
import { useRouter } from "next/navigation";

export default function LogoutPage() {
  const router = useRouter();

  useEffect(() => {
    logout().finally(() => router.replace("/"));
  }, [router]);

  return (
    <div className="flex min-h-[60vh] items-center justify-center bg-background px-6 py-12">
      <div className="w-full max-w-md rounded-2xl border border-bd-border bg-surface p-7 text-center" role="status" aria-live="polite">
        <div className="mx-auto h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" aria-hidden="true" />
        <span className="section-label mt-5">Cuenta</span>
        <h1 className="mt-2 font-display text-3xl font-black italic uppercase tracking-[-0.035em] text-foreground">
          Cerrando sesión
        </h1>
        <p className="mt-3 text-sm leading-6 text-muted">
          Estamos cerrando tu sesión y volverás al inicio automáticamente.
        </p>
      </div>
    </div>
  );
}
