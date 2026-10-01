"use client";

import Link from "next/link";
import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { confirmPasswordReset } from "../../lib/auth";

function ResetPasswordContent() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";

  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [success, setSuccess] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
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

  if (!token) {
    return (
      <div className="min-h-[70vh] bg-background px-6 py-12">
        <div className="mx-auto max-w-md rounded-2xl border border-bd-border bg-surface p-7 text-center">
          <span className="section-label">Cuenta</span>
          <h1 className="mt-2 font-display text-3xl font-black italic uppercase tracking-[-0.035em] text-foreground">
            Enlace inválido
          </h1>
          <p className="mt-4 text-sm leading-6 text-muted">
            No se encontró un token de recuperación. Usa el enlace recibido por correo o solicita uno nuevo.
          </p>
          <Link href="/auth/forgot-password" className="mt-6 inline-flex rounded-xl border border-bd-border px-5 py-3 text-xs font-bold uppercase tracking-[0.06em] text-foreground transition hover:border-foreground/25">
            Solicitar nuevo enlace
          </Link>
        </div>
      </div>
    );
  }

  const inputClass =
    "mt-2 w-full rounded-xl border border-bd-border bg-background px-4 py-3 text-sm text-foreground placeholder:text-muted/60 focus:border-foreground/25 focus:outline-none";

  return (
    <div className="min-h-[70vh] bg-background px-6 py-12">
      <div className="mx-auto max-w-md">
        <header className="mb-8">
          <span className="section-label">Cuenta</span>
          <h1 className="mt-2 font-display text-4xl font-black italic uppercase tracking-[-0.04em] text-foreground">
            Nueva contraseña
          </h1>
        </header>

        <section className="rounded-2xl border border-bd-border bg-surface p-6 sm:p-8">
          {success ? (
            <div>
              <p className="font-semibold text-foreground">Contraseña restablecida</p>
              <p className="mt-3 text-sm leading-6 text-muted">
                Ya puedes iniciar sesión con la nueva contraseña.
              </p>
              <Link href="/auth" className="mt-6 inline-flex rounded-xl bg-primary px-5 py-3 text-xs font-bold uppercase tracking-[0.08em] text-background transition hover:opacity-90">
                Iniciar sesión
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
                  Nueva contraseña
                  <input
                    type="password"
                    value={newPassword}
                    onChange={(event) => setNewPassword(event.target.value)}
                    required
                    autoComplete="new-password"
                    className={inputClass}
                    placeholder="Mínimo 8 caracteres"
                  />
                </label>
                <label className="block text-xs font-bold uppercase tracking-[0.08em] text-muted">
                  Confirmar contraseña
                  <input
                    type="password"
                    value={confirmPassword}
                    onChange={(event) => setConfirmPassword(event.target.value)}
                    required
                    autoComplete="new-password"
                    className={inputClass}
                  />
                </label>
                <button
                  type="submit"
                  disabled={loading}
                  className="w-full rounded-xl bg-primary px-6 py-3.5 text-sm font-bold uppercase tracking-[0.08em] text-background transition hover:opacity-90 disabled:opacity-50"
                >
                  {loading ? "Guardando…" : "Guardar nueva contraseña"}
                </button>
              </form>
            </>
          )}
        </section>
      </div>
    </div>
  );
}

export default function ResetPasswordPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-[70vh] items-center justify-center bg-background">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" aria-label="Cargando" />
        </div>
      }
    >
      <ResetPasswordContent />
    </Suspense>
  );
}
