"use client";

import { useState } from "react";
import Link from "next/link";
import { requestPasswordReset } from "../../lib/auth";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await requestPasswordReset(email);
      setSubmitted(true);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "No se pudo enviar el correo.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-background px-6 py-12 text-foreground">
      <div className="mx-auto max-w-md">
        <div className="mb-8 text-center">
          <span className="section-label">Cuenta</span>
          <h1 className="mt-2 font-display text-3xl font-black uppercase text-foreground">
            Recupera tu acceso.
          </h1>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            Te enviaremos instrucciones si el correo está asociado a una cuenta.
          </p>
        </div>

        <div className="rounded-2xl border border-bd-border bg-surface p-8">
          {submitted ? (
            <div className="text-center">
              <div className="mx-auto mb-5 flex h-12 w-12 items-center justify-center rounded-xl border border-bd-border bg-background text-foreground" aria-hidden="true">
                <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.7} d="M5 13l4 4L19 7" />
                </svg>
              </div>
              <p className="font-display text-xl font-black uppercase text-foreground">Solicitud enviada</p>
              <p className="mt-3 text-sm leading-6 text-muted-foreground">
                Si el correo está registrado, recibirás instrucciones para restablecer tu contraseña.
                Revisa también la carpeta de spam.
              </p>
              <Link href="/auth" className="mt-6 inline-block text-sm text-muted-foreground transition hover:text-foreground">
                Volver al inicio de sesión
              </Link>
            </div>
          ) : (
            <>
              {error ? (
                <div className="mb-5 rounded-xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300">
                  {error}
                </div>
              ) : null}
              <form onSubmit={handleSubmit} className="space-y-4">
                <div>
                  <label className="block text-xs font-bold uppercase tracking-[0.12em] text-muted-foreground">
                    Correo electrónico
                  </label>
                  <input
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    required
                    autoComplete="email"
                    className="mt-2 w-full rounded-xl border border-bd-border bg-background px-4 py-3 text-foreground placeholder:text-muted-foreground focus:border-accent/60 focus:outline-none focus:ring-2 focus:ring-accent/20"
                    placeholder="tu@correo.com"
                  />
                </div>
                <button
                  type="submit"
                  disabled={loading}
                  className="w-full rounded-xl bg-primary px-6 py-3.5 text-sm font-bold uppercase tracking-[0.1em] text-background transition hover:opacity-90 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                >
                  {loading ? "Enviando…" : "Enviar instrucciones"}
                </button>
              </form>
              <div className="mt-6 text-center text-sm">
                <Link href="/auth" className="text-muted-foreground transition hover:text-foreground">
                  Volver al inicio de sesión
                </Link>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
