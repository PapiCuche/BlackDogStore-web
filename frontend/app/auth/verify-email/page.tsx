"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
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
      setMessage("No se encontró el token en la URL. Usa el enlace de tu correo.");
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
    <div className="min-h-screen bg-background px-6 py-12 text-foreground">
      <div className="mx-auto max-w-md">
        <div className="rounded-2xl border border-bd-border bg-surface p-8 text-center sm:p-10">
          {state === "loading" ? (
            <>
              <div className="mx-auto mb-5 h-8 w-8 animate-spin rounded-full border-2 border-foreground border-t-transparent" />
              <span className="section-label">Cuenta</span>
              <h1 className="mt-2 font-display text-2xl font-black uppercase text-foreground">
                Verificando tu correo.
              </h1>
              <p className="mt-3 text-sm text-muted-foreground">Validando el enlace recibido.</p>
            </>
          ) : null}

          {state === "success" ? (
            <>
              <div className="mx-auto mb-5 flex h-12 w-12 items-center justify-center rounded-xl border border-bd-border bg-background" aria-hidden="true">
                <svg className="h-6 w-6 text-foreground" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M5 13l4 4L19 7" />
                </svg>
              </div>
              <span className="section-label">Verificación</span>
              <h1 className="mt-2 font-display text-2xl font-black uppercase text-foreground">Correo verificado</h1>
              <p className="mt-4 text-sm leading-6 text-muted-foreground">{message}</p>
              <Link
                href="/auth"
                className="mt-6 inline-flex min-h-12 items-center rounded-xl bg-primary px-6 py-3 text-sm font-bold text-background transition hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                Iniciar sesión
              </Link>
            </>
          ) : null}

          {state === "error" ? (
            <>
              <div className="mx-auto mb-5 flex h-12 w-12 items-center justify-center rounded-xl border border-red-500/30 bg-red-500/10 text-red-300" aria-hidden="true">
                <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </div>
              <span className="section-label">Error</span>
              <h1 className="mt-2 font-display text-2xl font-black uppercase text-foreground">No se pudo verificar</h1>
              <p className="mt-4 text-sm leading-6 text-muted-foreground">{message}</p>
              <Link
                href="/auth"
                className="mt-6 inline-flex min-h-12 items-center rounded-xl border border-bd-border bg-background px-6 py-3 text-sm font-bold text-foreground transition hover:border-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                Volver al inicio de sesión
              </Link>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export default function VerifyEmailPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-background">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-foreground border-t-transparent" />
        </div>
      }
    >
      <VerifyEmailContent />
    </Suspense>
  );
}
