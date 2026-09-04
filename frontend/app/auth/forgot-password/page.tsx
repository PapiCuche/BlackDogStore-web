"use client";

import { useState } from "react";
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
    <div className="min-h-screen bg-background px-6 py-12">
      <div className="mx-auto max-w-md">
        <div className="mb-8 text-center">
          <p className="text-sm uppercase tracking-[0.3em] font-semibold text-muted">Cuenta</p>
          <h1 className="mt-2 text-3xl font-bold text-foreground">Olvidé mi contraseña</h1>
        </div>

        <div className="rounded-2xl border border-bd-border bg-surface p-8">
          {submitted ? (
            <div className="text-center">
              <p className="text-foreground font-semibold">Correo enviado</p>
              <p className="mt-3 text-sm text-muted">
                Si el correo está registrado, recibirás instrucciones para restablecer tu contraseña.
                Revisa también la carpeta de spam.
              </p>
              <a
                href="/auth"
                className="mt-6 inline-block text-sm text-muted hover:text-foreground transition"
              >
                Volver al inicio de sesión
              </a>
            </div>
          ) : (
            <>
              {error && (
                <div className="mb-5 rounded-xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300">
                  {error}
                </div>
              )}
              <p className="mb-6 text-sm text-muted">
                Ingresa el correo asociado a tu cuenta. Si existe, recibirás un enlace para restablecer
                tu contraseña.
              </p>
              <form onSubmit={handleSubmit} className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-foreground/85">
                    Correo electrónico
                  </label>
                  <input
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    required
                    autoComplete="email"
                    className="mt-2 w-full rounded-xl border border-bd-border bg-surface px-4 py-3 text-foreground placeholder-muted focus:border-bd-border focus:outline-none"
                    placeholder="tu@correo.com"
                  />
                </div>
                <button
                  type="submit"
                  disabled={loading}
                  className="w-full rounded-full bg-foreground px-6 py-3 text-sm font-semibold text-background transition hover:bg-foreground/90 disabled:opacity-50"
                >
                  {loading ? "Enviando…" : "Enviar instrucciones"}
                </button>
              </form>
              <div className="mt-6 text-center text-sm">
                <a href="/auth" className="text-muted hover:text-foreground transition">
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
