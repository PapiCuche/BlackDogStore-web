"use client";

import { useEffect, useState } from "react";
import { useStorefront } from "../components/StorefrontProvider";
import { useRouter } from "next/navigation";
import { login, logout, getCurrentUser, register, AuthUser } from "../lib/auth";
import { DevQuickLogin } from "./components/DevQuickLogin";

export default function AuthPage() {
  // The storefront this visitor arrived at. The ACCOUNT they log into is
  // global — one identity across every shop — but this page is the shop's.
  const { company, branding, contact } = useStorefront();
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
        const result = await register({ username, email, password, password_confirm: passwordConfirm });
        if (result.requires_verification) {
          setSuccess("Registro completado. Revisa tu correo para verificar tu cuenta antes de iniciar sesión.");
        } else {
          setSuccess("Registro completado. Ahora inicia sesión.");
          setIsLogin(true);
        }
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

  const inputClass =
    "mt-2 w-full rounded-xl border border-white/10 bg-white/[0.04] px-4 py-3 text-sm text-white placeholder-zinc-700 focus:border-white/25 focus:outline-none";
  const labelClass = "block text-xs font-bold uppercase tracking-widest text-zinc-500";

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#080808]">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-white border-t-transparent" />
      </div>
    );
  }

  if (user) {
    return (
      <div className="min-h-screen bg-[#080808] px-6 py-12">
        <div className="mx-auto max-w-xl">
          <div className="rounded-2xl border border-white/[0.08] bg-[#111] p-8">
            <div className="flex items-start justify-between">
              <div>
                <span className="section-label">Cuenta</span>
                <h1 className="font-display mt-2 text-4xl font-black uppercase text-white">Mi perfil</h1>
              </div>
              <button
                type="button"
                onClick={handleLogout}
                className="rounded-full border border-white/10 bg-white/[0.04] px-5 py-2.5 text-xs font-bold uppercase tracking-widest text-zinc-400 transition hover:border-white/25 hover:text-white"
              >
                Cerrar sesión
              </button>
            </div>

            <div className="mt-8 space-y-3">
              {[
                { label: "Usuario", value: user.username },
                { label: "Correo", value: user.email || "No registrado" },
                { label: "Nombre", value: user.first_name || "—" },
                { label: "Apellido", value: user.last_name || "—" },
              ].map((field) => (
                <div key={field.label} className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3">
                  <span className="text-[10px] font-bold uppercase tracking-widest text-zinc-700">{field.label}</span>
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
    <div className="min-h-screen bg-[#080808]">
      <div className="grid min-h-screen lg:grid-cols-2">

        {/* Left — brand panel */}
        <div className="relative hidden overflow-hidden border-r border-white/[0.06] bg-[#080808] lg:flex lg:flex-col lg:justify-between lg:p-12">
          <div className="topo-bg absolute inset-0 pointer-events-none" />
          <div className="dot-grid absolute right-0 top-0 h-64 w-64 opacity-20 pointer-events-none" />

          {/* Logo — this storefront's, resolved from the host. The login page
              belongs to the shop the customer came to, even though the ACCOUNT
              behind it is global; see store/emails.py for the other half of
              that distinction. */}
          <div className="relative flex items-center gap-3">
            {branding.logo_url ? (
              /* eslint-disable-next-line @next/next/no-img-element */
              <img
                src={branding.logo_url}
                alt={company.name}
                className="h-10 w-auto object-contain"
              />
            ) : null}
            <div>
              <span className="block font-display text-lg font-black uppercase tracking-tight text-white">
                {company.name}
              </span>
              {contact.city ? (
                <span className="block text-[9px] font-semibold uppercase tracking-[0.3em] text-zinc-600">
                  {contact.city}
                </span>
              ) : null}
            </div>
          </div>

          {/* Main copy */}
          <div className="relative">
            <span className="section-label">{contact.city}</span>
            <h2 className="font-display mt-3 text-6xl font-black uppercase leading-none tracking-tight text-white">
              Equipos<br />Apple<br />Originales
            </h2>
            <p className="mt-5 max-w-sm text-sm leading-7 text-zinc-500">
              Accede a tu cuenta para ver el estado de tus pedidos, guardar tu carrito y gestionar tu perfil.
            </p>
          </div>

          {/* Trust row */}
          <div className="relative flex flex-wrap gap-6 text-xs text-zinc-700">
            <span>✓ Garantía 6 meses</span>
            <span>✓ Envío a todo Perú</span>
            <span>✓ Repuestos originales</span>
          </div>
        </div>

        {/* Right — form panel */}
        <div className="flex flex-col items-center justify-center px-6 py-12 lg:px-12">
          <div className="w-full max-w-md">

            {/* Mobile logo */}
            <div className="mb-8 flex items-center gap-3 lg:hidden">
              {branding.logo_url ? (
                /* eslint-disable-next-line @next/next/no-img-element */
                <img
                  src={branding.logo_url}
                  alt={company.name}
                  className="h-8 w-auto object-contain"
                />
              ) : null}
              <span className="font-display text-base font-black uppercase tracking-tight text-white">
                {company.name}
              </span>
            </div>

            <div className="mb-8">
              <span className="section-label">{isLogin ? "Bienvenido" : "Nuevo usuario"}</span>
              <h1 className="font-display mt-2 text-4xl font-black uppercase text-white">
                {isLogin ? "Iniciar sesión" : "Crear cuenta"}
              </h1>
            </div>

            {error && (
              <div className="mb-5 rounded-xl border border-red-500/20 bg-red-500/10 p-4 text-sm text-red-300">
                {error}
              </div>
            )}
            {success && (
              <div className="mb-5 rounded-xl border border-white/10 bg-white/[0.04] p-4 text-sm text-zinc-200">
                {success}
              </div>
            )}

            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className={labelClass}>Usuario</label>
                <input
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  className={inputClass}
                  required
                  autoComplete="username"
                  placeholder="Tu nombre de usuario"
                />
              </div>

              {!isLogin && (
                <div>
                  <label className={labelClass}>Correo electrónico</label>
                  <input
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    className={inputClass}
                    required
                    autoComplete="email"
                    placeholder="correo@ejemplo.com"
                  />
                </div>
              )}

              <div>
                <label className={labelClass}>Contraseña</label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className={inputClass}
                  required
                  autoComplete={isLogin ? "current-password" : "new-password"}
                  placeholder="••••••••"
                />
              </div>

              {!isLogin && (
                <div>
                  <label className={labelClass}>Confirmar contraseña</label>
                  <input
                    type="password"
                    value={passwordConfirm}
                    onChange={(e) => setPasswordConfirm(e.target.value)}
                    className={inputClass}
                    required
                    autoComplete="new-password"
                    placeholder="••••••••"
                  />
                </div>
              )}

              <button className="mt-2 w-full rounded-full bg-white px-6 py-3.5 text-sm font-black uppercase tracking-widest text-[#080808] transition hover:bg-zinc-200">
                {isLogin ? "Iniciar sesión" : "Registrarme"}
              </button>
            </form>

            <div className="mt-6 space-y-3 text-center text-sm text-zinc-600">
              <div>
                {isLogin ? "¿No tienes cuenta?" : "¿Ya tienes cuenta?"}{" "}
                <button
                  type="button"
                  onClick={() => { setError(null); setSuccess(null); setIsLogin(!isLogin); }}
                  className="font-bold text-white transition hover:text-zinc-300"
                >
                  {isLogin ? "Crear una ahora" : "Iniciar sesión"}
                </button>
              </div>
              {isLogin && (
                <div>
                  <a href="/auth/forgot-password" className="text-zinc-600 transition hover:text-white">
                    ¿Olvidaste tu contraseña?
                  </a>
                </div>
              )}
            </div>

            {/* Development-only. Renders nothing outside `next dev`. */}
            {isLogin && (
              <DevQuickLogin
                onUse={(demoUsername, demoPassword) => {
                  // Fills the form only — the real login still has to be submitted.
                  setUsername(demoUsername);
                  setPassword(demoPassword);
                  setError(null);
                  setSuccess(null);
                }}
              />
            )}

          </div>
        </div>

      </div>
    </div>
  );
}
