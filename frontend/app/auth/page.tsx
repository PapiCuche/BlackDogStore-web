"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { login, logout, getCurrentUser, register, AuthUser } from "../lib/auth";

export default function AuthPage() {
  const [isLogin, setIsLogin] = useState(true);
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirm, setPasswordConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  useEffect(() => {
    getCurrentUser().then((u) => {
      setUser(u);
      setLoading(false);
    });
  }, []);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSuccess(null);
    try {
      if (isLogin) {
        const data = await login(username, password);
        setUser(data.user);
        setSuccess("Inicio de sesión correcto.");
        window.dispatchEvent(new Event("authChange"));
        router.push("/");
      } else {
        await register({ username, email, password, password_confirm: passwordConfirm });
        setSuccess("Registro completado. Ahora inicia sesión.");
        setIsLogin(true);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "No se pudo completar la acción.");
    }
  }

  async function handleLogout() {
    await logout().catch(() => {});
    setUser(null);
    window.dispatchEvent(new Event("authChange"));
    router.push("/");
  }

  const inputClass = "mt-2 w-full rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-white placeholder-slate-500 focus:border-emerald-500/50 focus:outline-none";
  const labelClass = "block text-sm font-medium text-slate-300";

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-zinc-950">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-emerald-500 border-t-transparent" />
      </div>
    );
  }

  if (user) {
    return (
      <div className="min-h-screen bg-zinc-950 px-6 py-12">
        <div className="mx-auto max-w-xl">
          <div className="rounded-2xl border border-white/10 bg-white/5 p-8">
            <div className="flex items-start justify-between">
              <div>
                <p className="text-sm uppercase tracking-[0.3em] font-semibold text-emerald-400/60">Cuenta</p>
                <h1 className="mt-1 text-3xl font-bold text-white">Mi perfil</h1>
              </div>
              <button
                type="button"
                onClick={handleLogout}
                className="rounded-full border border-red-500/40 bg-red-500/10 px-5 py-2.5 text-sm font-semibold text-red-300 transition hover:bg-red-500/20"
              >
                Cerrar sesión
              </button>
            </div>

            <div className="mt-8 space-y-4">
              {[
                { label: "Usuario", value: user.username },
                { label: "Correo", value: user.email || "No registrado" },
                { label: "Nombre", value: user.first_name || "—" },
                { label: "Apellido", value: user.last_name || "—" },
              ].map((field) => (
                <div key={field.label} className="rounded-xl border border-white/5 bg-white/5 px-4 py-3">
                  <span className="text-xs font-medium text-slate-500">{field.label}</span>
                  <p className="mt-0.5 text-sm font-medium text-white">{field.value}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-zinc-950 px-6 py-12">
      <div className="mx-auto max-w-md">
        <div className="mb-8 text-center">
          <p className="text-sm uppercase tracking-[0.3em] font-semibold text-emerald-400/60">
            {isLogin ? "Bienvenido" : "Nuevo usuario"}
          </p>
          <h1 className="mt-2 text-3xl font-bold text-white">
            {isLogin ? "Iniciar sesión" : "Crear cuenta"}
          </h1>
        </div>

        <div className="rounded-2xl border border-white/10 bg-white/5 p-8">
          {error && (
            <div className="mb-5 rounded-xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300">{error}</div>
          )}
          {success && (
            <div className="mb-5 rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-4 text-sm text-emerald-300">{success}</div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className={labelClass}>Usuario</label>
              <input value={username} onChange={(e) => setUsername(e.target.value)} className={inputClass} required autoComplete="username" />
            </div>

            {!isLogin && (
              <div>
                <label className={labelClass}>Correo electrónico</label>
                <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} className={inputClass} required autoComplete="email" />
              </div>
            )}

            <div>
              <label className={labelClass}>Contraseña</label>
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} className={inputClass} required autoComplete={isLogin ? "current-password" : "new-password"} />
            </div>

            {!isLogin && (
              <div>
                <label className={labelClass}>Confirmar contraseña</label>
                <input type="password" value={passwordConfirm} onChange={(e) => setPasswordConfirm(e.target.value)} className={inputClass} required autoComplete="new-password" />
              </div>
            )}

            <button className="mt-2 w-full rounded-full bg-emerald-500 px-6 py-3 text-sm font-semibold text-white shadow-lg transition hover:bg-emerald-600">
              {isLogin ? "Iniciar sesión" : "Registrarme"}
            </button>
          </form>

          <div className="mt-6 text-center text-sm text-slate-400">
            {isLogin ? "¿No tienes cuenta?" : "¿Ya tienes cuenta?"}{" "}
            <button
              type="button"
              onClick={() => { setError(null); setSuccess(null); setIsLogin(!isLogin); }}
              className="font-semibold text-emerald-400 hover:text-emerald-300"
            >
              {isLogin ? "Crear una ahora" : "Iniciar sesión"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
