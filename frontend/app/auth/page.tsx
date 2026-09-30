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
    "mt-2 w-full rounded-xl border border-bd-border bg-surface px-4 py-3 text-sm text-foreground placeholder:text-muted-foreground focus:border-accent/60 focus:outline-none focus:ring-2 focus:ring-accent/20";
  const labelClass = "block text-xs font-bold uppercase tracking-[0.14em] text-muted-foreground";

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-white border-t-transparent" />
      </div>
    );
  }

  if (user) {
    return (
      <div className="min-h-screen bg-background px-6 py-12">
        <div className="mx-auto max-w-xl">
          <div className="rounded-2xl border border-bd-border bg-surface p-8">
            <div className="flex items-start justify-between">
              <div>
                <span className="section-label">Cuenta</span>
                <h1 className="font-display mt-2 text-4xl font-black uppercase text-foreground">Mi perfil</h1>
              </div>
              <button
                type="button"
                onClick={handleLogout}
                className="rounded-xl border border-bd-border bg-surface px-5 py-2.5 text-xs font-bold uppercase tracking-[0.12em] text-muted-foreground transition hover:border-accent/60 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
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
                <div key={field.label} className="rounded-xl border border-white/[0.06] bg-background px-4 py-3">
                  <span className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground">{field.label}</span>
                  <p className="mt-0.5 text-sm font-medium text-foreground">{field.value}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background">
      <div className="grid min-h-screen lg:grid-cols-2">

        {/* Left — brand panel */}
        <div className="relative hidden overflow-hidden border-r border-bd-border bg-background lg:flex lg:flex-col lg:justify-between lg:p-12">
          <div className="topo-bg absolute inset-0 pointer-events-none" />

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
              <span className="block font-display text-lg font-black uppercase tracking-tight text-foreground">
                {company.name}
              </span>
              {contact.city ? (
                <span className="block text-[9px] font-semibold uppercase tracking-[0.3em] text-muted-foreground">
                  {contact.city}
                </span>
              ) : null}
            </div>
          </div>

          {/* Main copy */}
          <div className="relative">
            <span className="section-label">{contact.city || "Cuenta"}</span>
            <h2 className="font-display mt-3 text-5xl font-black uppercase leading-none tracking-tight text-foreground sm:text-6xl">
              Tu cuenta,<br />tus compras,<br />tu seguimiento.
            </h2>
            <p className="mt-5 max-w-sm text-sm leading-7 text-muted-foreground">
              Inicia sesión para consultar pedidos y mantener tu información de cuenta en un solo lugar.
            </p>
          </div>

          <div className="relative grid grid-cols-3 gap-px overflow-hidden rounded-xl border border-bd-border bg-bd-border text-center">
            {[
              ["01", "Pedidos"],
              ["02", "Cuenta"],
              ["03", "Seguimiento"],
            ].map(([num, label]) => (
              <div key={num} className="bg-background px-3 py-4">
                <span className="block text-[9px] font-bold uppercase tracking-[0.18em] text-accent">{num}</span>
                <span className="mt-1 block text-[10px] uppercase tracking-[0.12em] text-muted-foreground">{label}</span>
              </div>
            ))}
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
              <span className="font-display text-base font-black uppercase tracking-tight text-foreground">
                {company.name}
              </span>
            </div>

            <div className="mb-8">
              <span className="section-label">{isLogin ? "Bienvenido" : "Nuevo usuario"}</span>
              <h1 className="font-display mt-2 text-4xl font-black uppercase text-foreground">
                {isLogin ? "Iniciar sesión" : "Crear cuenta"}
              </h1>
            </div>

            {error && (
              <div className="mb-5 rounded-xl border border-red-500/20 bg-red-500/10 p-4 text-sm text-red-300">
                {error}
              </div>
            )}
            {success && (
              <div className="mb-5 rounded-xl border border-bd-border bg-surface p-4 text-sm text-zinc-200">
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

              <button className="mt-2 w-full rounded-xl bg-primary px-6 py-3.5 text-sm font-black uppercase tracking-[0.12em] text-background transition hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
                {isLogin ? "Iniciar sesión" : "Registrarme"}
              </button>
            </form>

            <div className="mt-6 space-y-3 text-center text-sm text-muted-foreground">
              <div>
                {isLogin ? "¿No tienes cuenta?" : "¿Ya tienes cuenta?"}{" "}
                <button
                  type="button"
                  onClick={() => { setError(null); setSuccess(null); setIsLogin(!isLogin); }}
                  className="font-bold text-foreground transition hover:text-zinc-300"
                >
                  {isLogin ? "Crear una ahora" : "Iniciar sesión"}
                </button>
              </div>
              {isLogin && (
                <div>
                  <a href="/auth/forgot-password" className="text-muted-foreground transition hover:text-foreground">
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
