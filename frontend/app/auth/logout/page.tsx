"use client";

import { useEffect } from "react";
import { logout } from "../../lib/auth";
import { useRouter } from "next/navigation";

export default function LogoutPage() {
  const router = useRouter();

  useEffect(() => {
    logout().finally(() => {
      router.replace("/");
      router.refresh();
    });
  }, [router]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-6 text-foreground">
      <div className="text-center" aria-live="polite">
        <div className="mx-auto mb-5 h-8 w-8 animate-spin rounded-full border-2 border-foreground border-t-transparent" />
        <span className="section-label">Cuenta</span>
        <h1 className="mt-2 font-display text-2xl font-black uppercase text-foreground">
          Cerrando sesión.
        </h1>
      </div>
    </div>
  );
}
