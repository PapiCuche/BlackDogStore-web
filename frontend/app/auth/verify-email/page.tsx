"use client";

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
    <div className="min-h-screen bg-background px-6 py-12">
      <div className="mx-auto max-w-md text-center">
        <div className="rounded-2xl border border-bd-border bg-surface p-10">
          {state === "loading" && (
            <>
              <div className="mx-auto mb-4 h-8 w-8 animate-spin rounded-full border-2 border-foreground border-t-transparent" />
              <p className="text-muted">Verificando tu correo…</p>
            </>
          )}
          {state === "success" && (
            <>
              <p className="text-sm uppercase tracking-widest text-muted">Verificación</p>
              <h1 className="mt-2 text-2xl font-bold text-foreground">Correo verificado</h1>
              <p className="mt-4 text-muted">{message}</p>
              <a
                href="/auth"
                className="mt-6 inline-block rounded-full bg-foreground px-6 py-3 text-sm font-semibold text-background transition hover:bg-foreground/90"
              >
                Iniciar sesión
              </a>
            </>
          )}
          {state === "error" && (
            <>
              <p className="text-sm uppercase tracking-widest text-muted">Error</p>
              <h1 className="mt-2 text-2xl font-bold text-foreground">No se pudo verificar</h1>
              <p className="mt-4 text-muted">{message}</p>
              <div className="mt-6 space-y-2">
                <a
                  href="/auth"
                  className="block rounded-full border border-bd-border px-6 py-3 text-sm font-semibold text-foreground transition hover:bg-surface"
                >
                  Volver al inicio de sesión
                </a>
              </div>
            </>
          )}
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
