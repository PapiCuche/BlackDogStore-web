"use client";

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

  if (!token) {
    return (
      <div className="min-h-screen bg-background px-6 py-12">
        <div className="mx-auto max-w-md text-center">
          <div className="rounded-2xl border border-bd-border bg-surface p-10">
            <h1 className="text-2xl font-bold text-foreground">Enlace inválido</h1>
            <p className="mt-4 text-muted">
              No se encontró el token. Usa el enlace del correo de recuperación.
            </p>
            <a
              href="/auth/forgot-password"
              className="mt-6 inline-block text-sm text-muted hover:text-foreground transition"
            >
              Solicitar nuevo enlace
            </a>
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

  const inputClass =
    "mt-2 w-full rounded-xl border border-bd-border bg-surface px-4 py-3 text-foreground placeholder-muted focus:border-bd-border focus:outline-none";

  return (
    <div className="min-h-screen bg-background px-6 py-12">
      <div className="mx-auto max-w-md">
        <div className="mb-8 text-center">
          <p className="text-sm uppercase tracking-[0.3em] font-semibold text-muted">Cuenta</p>
          <h1 className="mt-2 text-3xl font-bold text-foreground">Nueva contraseña</h1>
        </div>

        <div className="rounded-2xl border border-bd-border bg-surface p-8">
          {success ? (
            <div className="text-center">
              <p className="font-semibold text-foreground">Contraseña restablecida</p>
              <p className="mt-3 text-sm text-muted">
                Tu contraseña fue actualizada. Ya puedes iniciar sesión con tu nueva contraseña.
              </p>
              <a
                href="/auth"
                className="mt-6 inline-block rounded-full bg-foreground px-6 py-3 text-sm font-semibold text-background transition hover:bg-foreground/90"
              >
                Iniciar sesión
              </a>
            </div>
          ) : (
            <>
              {error && (
                <div className="mb-5 rounded-xl border border-danger-border bg-danger-surface p-4 text-sm text-danger">
                  {error}
                </div>
              )}
              <form onSubmit={handleSubmit} className="space-y-4">
                <div>
                  <label htmlFor="auth-reset-password-page-nueva-contrasena" className="block text-sm font-medium text-foreground/85">Nueva contraseña</label>
                  <input id="auth-reset-password-page-nueva-contrasena"
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
                  <label htmlFor="auth-reset-password-page-confirmar-contrasena" className="block text-sm font-medium text-foreground/85">
                    Confirmar contraseña
                  </label>
                  <input id="auth-reset-password-page-confirmar-contrasena"
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
                  className="w-full rounded-full bg-foreground px-6 py-3 text-sm font-semibold text-background transition hover:bg-foreground/90 disabled:opacity-50"
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
