"use client";

import Link from "next/link";
import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { confirmPasswordReset } from "../../lib/auth";
import { invitationPath, invitationTokenFromNext } from "../../lib/invitation";

function ResetPasswordContent() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";
  // El enlace de recuperación trae de vuelta a una invitación cuando la persona
  // lo pidió desde una. A una invitación y a nada más: lo demás se ignora.
  const invitationToken = invitationTokenFromNext(searchParams.get("next"));
  const loginHref = invitationToken
    ? `/auth?next=${encodeURIComponent(invitationPath(invitationToken))}`
    : "/auth";

  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [success, setSuccess] = useState(false);
  const [username, setUsername] = useState("");
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
      const result = await confirmPasswordReset(token, newPassword);
      // Quien nunca eligió una contraseña —una cuenta de Google, alguien
      // invitado— puede no saber con qué usuario se entra. Se le dice, y se le
      // deja escrito en la pantalla siguiente.
      const name = typeof result?.username === "string" ? result.username : "";
      setUsername(name);
      if (name) {
        try {
          window.sessionStorage.setItem("bd.auth.username", name);
        } catch {
          // Sin almacenamiento de sesión la persona lo escribe: lo tiene delante.
        }
      }
      setSuccess(true);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "No se pudo restablecer la contraseña.");
    } finally {
      setLoading(false);
    }
  }

  if (!token) {
    return (
      <div className="v3-account-page min-h-[65vh] bg-surface px-4 py-12 sm:px-6 sm:py-16">
        <div className="mx-auto max-w-md rounded-xl border border-bd-border bg-background p-6 text-center sm:p-10">
          <span className="section-label">Cuenta</span>
          <h1 className="mt-2 text-2xl font-semibold text-foreground">
            Enlace inválido
          </h1>
          <p className="mt-4 text-sm leading-6 text-muted">
            No se encontró un token de recuperación. Usa el enlace recibido por correo o solicita uno nuevo.
          </p>
          <Link href="/auth/forgot-password" className="mt-6 inline-flex rounded-full border border-bd-border px-6 py-3 text-sm font-semibold text-foreground transition hover:bg-surface">
            Solicitar nuevo enlace
          </Link>
        </div>
      </div>
    );
  }

  const inputClass =
    "mt-2 w-full rounded-xl border border-bd-border bg-background px-4 py-3 text-sm text-foreground placeholder:text-muted/60 focus:border-foreground/25 focus:outline-none";

  return (
    <div className="v3-account-page min-h-[65vh] bg-surface px-4 py-12 sm:px-6 sm:py-16">
      <div className="mx-auto max-w-md">
        <header className="mb-8 text-center">
          <span className="section-label">Cuenta</span>
          <h1 className="mt-2 text-3xl font-semibold text-foreground">
            Nueva contraseña
          </h1>
        </header>

        <section className="rounded-xl border border-bd-border bg-background p-6 sm:p-8">
          {success ? (
            <div>
              <p className="font-semibold text-foreground">Contraseña restablecida</p>
              <p className="mt-3 text-sm leading-6 text-muted">
                Ya puedes iniciar sesión con la nueva contraseña.
                {invitationToken ? " Al entrar volverás a tu invitación para aceptarla." : ""}
              </p>
              {username ? (
                <p className="mt-3 text-sm leading-6 text-muted">
                  Tu usuario es <strong className="font-semibold text-foreground">{username}</strong>
                </p>
              ) : null}
              <Link href={loginHref} className="mt-6 inline-flex rounded-full bg-foreground px-6 py-3 text-sm font-semibold text-background transition hover:bg-foreground/90">
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
                <label className="block text-sm font-medium text-foreground/85">
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
                <label className="block text-sm font-medium text-foreground/85">
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
                  className="w-full rounded-full bg-foreground px-6 py-3 text-sm font-semibold text-background transition hover:bg-foreground/90 disabled:opacity-50"
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
