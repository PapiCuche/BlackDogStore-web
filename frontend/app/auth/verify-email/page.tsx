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
    <div className="min-h-screen bg-zinc-950 px-6 py-12">
      <div className="mx-auto max-w-md text-center">
        <div className="rounded-2xl border border-white/10 bg-white/5 p-10">
          {state === "loading" && (
            <>
              <div className="mx-auto mb-4 h-8 w-8 animate-spin rounded-full border-2 border-white border-t-transparent" />
              <p className="text-slate-400">Verificando tu correo…</p>
            </>
          )}
          {state === "success" && (
            <>
              <p className="text-sm uppercase tracking-widest text-white/40">Verificación</p>
              <h1 className="mt-2 text-2xl font-bold text-white">Correo verificado</h1>
              <p className="mt-4 text-slate-400">{message}</p>
              <a
                href="/auth"
                className="mt-6 inline-block rounded-full bg-white px-6 py-3 text-sm font-semibold text-black transition hover:bg-zinc-200"
              >
                Iniciar sesión
              </a>
            </>
          )}
          {state === "error" && (
            <>
              <p className="text-sm uppercase tracking-widest text-white/40">Error</p>
              <h1 className="mt-2 text-2xl font-bold text-white">No se pudo verificar</h1>
              <p className="mt-4 text-slate-400">{message}</p>
              <div className="mt-6 space-y-2">
                <a
                  href="/auth"
                  className="block rounded-full border border-white/20 px-6 py-3 text-sm font-semibold text-white transition hover:bg-white/5"
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
        <div className="flex min-h-screen items-center justify-center bg-zinc-950">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-white border-t-transparent" />
        </div>
      }
    >
      <VerifyEmailContent />
    </Suspense>
  );
}
