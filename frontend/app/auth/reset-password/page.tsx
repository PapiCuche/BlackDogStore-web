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
      <div className="min-h-screen bg-zinc-950 px-6 py-12">
        <div className="mx-auto max-w-md text-center">
          <div className="rounded-2xl border border-white/10 bg-white/5 p-10">
            <h1 className="text-2xl font-bold text-white">Enlace inválido</h1>
            <p className="mt-4 text-slate-400">
              No se encontró el token. Usa el enlace del correo de recuperación.
            </p>
            <a
              href="/auth/forgot-password"
              className="mt-6 inline-block text-sm text-slate-400 hover:text-white transition"
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
    "mt-2 w-full rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-white placeholder-slate-500 focus:border-white/30 focus:outline-none";

  return (
    <div className="min-h-screen bg-zinc-950 px-6 py-12">
      <div className="mx-auto max-w-md">
        <div className="mb-8 text-center">
          <p className="text-sm uppercase tracking-[0.3em] font-semibold text-white/40">Cuenta</p>
          <h1 className="mt-2 text-3xl font-bold text-white">Nueva contraseña</h1>
        </div>

        <div className="rounded-2xl border border-white/10 bg-white/5 p-8">
          {success ? (
            <div className="text-center">
              <p className="font-semibold text-white">Contraseña restablecida</p>
              <p className="mt-3 text-sm text-slate-400">
                Tu contraseña fue actualizada. Ya puedes iniciar sesión con tu nueva contraseña.
              </p>
              <a
                href="/auth"
                className="mt-6 inline-block rounded-full bg-white px-6 py-3 text-sm font-semibold text-black transition hover:bg-zinc-200"
              >
                Iniciar sesión
              </a>
            </div>
          ) : (
            <>
              {error && (
                <div className="mb-5 rounded-xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300">
                  {error}
                </div>
              )}
              <form onSubmit={handleSubmit} className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-slate-300">Nueva contraseña</label>
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
                  <label className="block text-sm font-medium text-slate-300">
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
                  className="w-full rounded-full bg-white px-6 py-3 text-sm font-semibold text-black transition hover:bg-zinc-200 disabled:opacity-50"
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
        <div className="flex min-h-screen items-center justify-center bg-zinc-950">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-white border-t-transparent" />
        </div>
      }
    >
      <ResetPasswordContent />
    </Suspense>
  );
}
