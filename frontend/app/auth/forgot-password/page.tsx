"use client";

import Link from "next/link";
import { useState } from "react";
import { requestPasswordReset } from "../../lib/auth";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
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
    <div className="min-h-[70vh] bg-background px-6 py-12">
      <div className="mx-auto max-w-md">
        <header className="mb-8">
          <span className="section-label">Cuenta</span>
          <h1 className="mt-2 font-display text-4xl font-black italic uppercase tracking-[-0.04em] text-foreground">
            Recuperar acceso
          </h1>
          <p className="mt-3 text-sm leading-6 text-muted">
            Ingresa el correo asociado a tu cuenta para solicitar un enlace de recuperación.
          </p>
        </header>

        <section className="rounded-2xl border border-bd-border bg-surface p-6 sm:p-8">
          {submitted ? (
            <div>
              <p className="font-semibold text-foreground">Solicitud recibida</p>
              <p className="mt-3 text-sm leading-6 text-muted">
                Si el correo está registrado, recibirás instrucciones para restablecer tu contraseña. Revisa también la carpeta de spam.
              </p>
              <Link href="/auth" className="mt-6 inline-flex rounded-xl border border-bd-border px-5 py-3 text-xs font-bold uppercase tracking-[0.06em] text-foreground transition hover:border-foreground/25">
                Volver al inicio de sesión
              </Link>
            </div>
          ) : (
            <>
              {error ? (
                <div className="mb-5 rounded-xl border border-danger-border bg-danger-surface p-4 text-sm text-danger" role="alert">
                  {error}
                </div>
              ) : null}

              <form onSubmit={handleSubmit} className="space-y-5">
                <label className="block text-xs font-bold uppercase tracking-[0.08em] text-muted">
                  Correo electrónico
                  <input
                    type="email"
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                    required
                    autoComplete="email"
                    className="mt-2 w-full rounded-xl border border-bd-border bg-background px-4 py-3 text-sm text-foreground placeholder:text-muted/60 focus:border-foreground/25 focus:outline-none"
                    placeholder="tu@correo.com"
                  />
                </label>
                <button
                  type="submit"
                  disabled={loading}
                  className="w-full rounded-xl bg-primary px-6 py-3.5 text-sm font-bold uppercase tracking-[0.08em] text-background transition hover:opacity-90 disabled:opacity-50"
                >
                  {loading ? "Enviando…" : "Enviar instrucciones"}
                </button>
              </form>

              <Link href="/auth" className="mt-6 inline-flex text-sm font-semibold text-muted transition hover:text-foreground">
                ← Volver al inicio de sesión
              </Link>
            </>
          )}
        </section>
      </div>
    </div>
  );
}
