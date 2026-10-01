"use client";

import Link from "next/link";
import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { verifyEmail } from "../../lib/auth";

function VerifyEmailContent() {
  const searchParams = useSearchParams();
  const [state, setState] = useState<"loading" | "success" | "error">("loading");
  const [message, setMessage] = useState("");

  useEffect(() => {
    const token = searchParams.get("token");
    if (!token) {
      setState("error");
      setMessage("No se encontró el token en la URL. Usa el enlace recibido por correo.");
      return;
    }

    verifyEmail(token)
      .then((data) => {
        setState("success");
        setMessage(data.detail);
      })
      .catch((err: Error) => {
        setState("error");
        setMessage(err.message);
      });
  }, [searchParams]);

  return (
    <div className="min-h-[70vh] bg-background px-6 py-12">
      <div className="mx-auto max-w-md rounded-2xl border border-bd-border bg-surface p-7 sm:p-8">
        {state === "loading" ? (
          <div role="status">
            <span className="section-label">Cuenta</span>
            <h1 className="mt-2 font-display text-3xl font-black italic uppercase tracking-[-0.035em] text-foreground">
              Verificando correo
            </h1>
            <div className="mt-6 flex items-center gap-3 text-sm text-muted">
              <span className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" aria-hidden="true" />
              Comprobando el enlace…
            </div>
          </div>
        ) : state === "success" ? (
          <div>
            <span className="section-label">Verificación</span>
            <h1 className="mt-2 font-display text-3xl font-black italic uppercase tracking-[-0.035em] text-foreground">
              Correo verificado
            </h1>
            <p className="mt-4 text-sm leading-6 text-muted">{message}</p>
            <Link href="/auth" className="mt-6 inline-flex rounded-xl bg-primary px-5 py-3 text-xs font-bold uppercase tracking-[0.08em] text-background transition hover:opacity-90">
              Iniciar sesión
            </Link>
          </div>
        ) : (
          <div>
            <span className="section-label">Verificación</span>
            <h1 className="mt-2 font-display text-3xl font-black italic uppercase tracking-[-0.035em] text-foreground">
              No se pudo verificar
            </h1>
            <p className="mt-4 text-sm leading-6 text-muted">{message}</p>
            <Link href="/auth" className="mt-6 inline-flex rounded-xl border border-bd-border px-5 py-3 text-xs font-bold uppercase tracking-[0.06em] text-foreground transition hover:border-foreground/25">
              Volver al inicio de sesión
            </Link>
          </div>
        )}
      </div>
    </div>
  );
}

export default function VerifyEmailPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-[70vh] items-center justify-center bg-background">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" aria-label="Cargando" />
        </div>
      }
    >
      <VerifyEmailContent />
    </Suspense>
  );
}
