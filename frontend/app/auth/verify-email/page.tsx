"use client";

import Link from "next/link";
import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { verifyEmail } from "../../lib/auth";
import { VerificationCode } from "../VerificationCode";

function VerifyEmailContent() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token");
  // Sin enlace no hay nada que comprobar solo: se ofrece escribir el código.
  const [state, setState] = useState<"loading" | "success" | "error" | "code">(
    token ? "loading" : "code",
  );
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (!token) return;

    verifyEmail(token)
      .then((data) => {
        setState("success");
        setMessage(data.detail);
      })
      .catch((err: Error) => {
        setState("error");
        setMessage(err.message);
      });
  }, [token]);

  return (
    <div className="v3-account-page min-h-[65vh] bg-surface px-4 py-12 sm:px-6 sm:py-16">
      <div className="mx-auto max-w-md rounded-xl border border-bd-border bg-background p-6 text-center sm:p-10">
        {state === "loading" ? (
          <div role="status">
            <span className="section-label">Cuenta</span>
            <h1 className="mt-2 text-2xl font-semibold text-foreground">
              Verificando correo
            </h1>
            <div className="mt-6 flex items-center justify-center gap-3 text-sm text-muted">
              <span className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" aria-hidden="true" />
              Comprobando el enlace…
            </div>
          </div>
        ) : state === "code" ? (
          <div className="text-left">
            <div className="text-center">
              <span className="section-label">Verificación</span>
              <h1 className="mt-2 text-2xl font-semibold text-foreground">
                Verifica tu correo
              </h1>
            </div>
            <div className="mt-6">
              <VerificationCode
                onVerified={(detail) => {
                  setMessage(detail);
                  setState("success");
                }}
              />
            </div>
          </div>
        ) : state === "success" ? (
          <div>
            <span className="section-label">Verificación</span>
            <h1 className="mt-2 text-2xl font-semibold text-foreground">
              Correo verificado
            </h1>
            <p className="mt-4 text-sm leading-6 text-muted">{message}</p>
            <Link href="/auth" className="mt-6 inline-flex rounded-full bg-foreground px-6 py-3 text-sm font-semibold text-background transition hover:bg-foreground/90">
              Iniciar sesión
            </Link>
          </div>
        ) : (
          <div>
            <span className="section-label">Verificación</span>
            <h1 className="mt-2 text-2xl font-semibold text-foreground">
              No se pudo verificar
            </h1>
            <p className="mt-4 text-sm leading-6 text-muted">{message}</p>
            <Link href="/auth" className="mt-6 inline-flex rounded-full border border-bd-border px-6 py-3 text-sm font-semibold text-foreground transition hover:bg-surface">
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
