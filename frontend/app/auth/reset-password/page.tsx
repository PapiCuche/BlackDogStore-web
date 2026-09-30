"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { confirmPasswordReset } from "../../lib/auth";

const inputClass =
  "mt-2 w-full rounded-xl border border-bd-border bg-background px-4 py-3 text-foreground placeholder:text-muted-foreground focus:border-accent/60 focus:outline-none focus:ring-2 focus:ring-accent/20";

function ResetPasswordContent() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";

  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [success, setSuccess] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!token) {
    return (
      <div className="min-h-screen bg-background px-6 py-12 text-foreground">
        <div className="mx-auto max-w-md">
          <div className="rounded-2xl border border-bd-border bg-surface p-8 text-center">
            <span className="section-label">Cuenta</span>
            <h1 className="mt-2 font-display text-2xl font-black uppercase text-foreground">Enlace inválido</h1>
            <p className="mt-4 text-sm leading-6 text-muted-foreground">
              No se encontró el token. Usa el enlace recibido en el correo de recuperación.
            </p>
            <Link href="/auth/forgot-password" className="mt-6 inline-block text-sm text-muted-foreground transition hover:text-foreground">
              Solicitar nuevo enlace
            </Link>
          </div>
        </div>
      </div>
    );
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (newPassword !== confirmPassword) {
      setError("Las contraseñas no coinciden.");
      return;
    }
    setLoading(true);
    try {
      await confirmPasswordReset(token, newPassword);
      setSuccess(true);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "No se pudo restablecer la contraseña.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-background px-6 py-12 text-foreground">
      <div className="mx-auto max-w-md">
        <div className="mb-8 text-center">
          <span className="section-label">Cuenta</span>
          <h1 className="mt-2 font-display text-3xl font-black uppercase text-foreground">Nueva contraseña</h1>
        </div>

        <div className="rounded-2xl border border-bd-border bg-surface p-8">
          {success ? (
            <div className="text-center">
              <p className="font-display text-xl font-black uppercase text-foreground">Contraseña restablecida</p>
              <p className="mt-3 text-sm leading-6 text-muted-foreground">
                Tu contraseña fue actualizada. Ya puedes iniciar sesión con la nueva contraseña.
              </p>
              <Link
                href="/auth"
                className="mt-6 inline-flex min-h-12 items-center rounded-xl bg-primary px-6 py-3 text-sm font-bold text-background transition hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                Iniciar sesión
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
                    Nueva contraseña
                  </label>
                  <input
                    type="password"
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    required
                    autoComplete="new-password"
                    className={inputClass}
                    placeholder="Mínimo 8 caracteres"
                  />
                </div>
                <div>
                  <label className="block text-xs font-bold uppercase tracking-[0.12em] text-muted-foreground">
                    Confirmar contraseña
                  </label>
                  <input
                    type="password"
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    required
                    autoComplete="new-password"
                    className={inputClass}
                  />
                </div>
                <button
                  type="submit"
                  disabled={loading}
                  className="w-full rounded-xl bg-primary px-6 py-3.5 text-sm font-bold uppercase tracking-[0.1em] text-background transition hover:opacity-90 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                >
                  {loading ? "Guardando…" : "Guardar nueva contraseña"}
                </button>
              </form>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default function ResetPasswordPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-background">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-foreground border-t-transparent" />
        </div>
      }
    >
      <ResetPasswordContent />
    </Suspense>
  );
}
