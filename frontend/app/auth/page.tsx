"use client";

import { useEffect, useState } from "react";
import { useStorefront } from "../components/StorefrontProvider";
import { useRouter } from "next/navigation";
import { login, logout, getCurrentUser, register, AuthUser } from "../lib/auth";
import { DevQuickLogin } from "./components/DevQuickLogin";

export default function AuthPage() {
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
    "mt-2 w-full rounded-xl border border-bd-border bg-surface px-4 py-3 text-sm text-foreground placeholder:text-muted/60 focus:border-foreground/25 focus:outline-none";
  const labelClass = "block text-xs font-bold uppercase tracking-[0.08em] text-muted";

  if (loading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center bg-background">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" aria-label="Cargando cuenta" />
      </div>
    );
  }

  if (user) {
    return (
      <div className="min-h-screen bg-background px-6 py-12">
        <div className="mx-auto max-w-2xl">
          <div className="rounded-2xl border border-bd-border bg-surface p-6 sm:p-8">
            <div className="flex flex-col gap-5 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <span className="section-label">Cuenta</span>
                <h1 className="mt-2 font-display text-4xl font-black italic uppercase tracking-[-0.035em] text-foreground">Mi perfil</h1>
              </div>
              <button
                type="button"
                onClick={handleLogout}
                className="rounded-xl border border-bd-border px-4 py-2.5 text-xs font-bold uppercase tracking-[0.06em] text-muted transition hover:border-foreground/25 hover:text-foreground"
              >
                Cerrar sesión
              </button>
            </div>

            <dl className="mt-8 grid gap-px overflow-hidden rounded-xl border border-bd-border bg-bd-border sm:grid-cols-2">
              {[
                { label: "Usuario", value: user.username },
                { label: "Correo", value: user.email || "No registrado" },
                { label: "Nombre", value: user.first_name || "—" },
                { label: "Apellido", value: user.last_name || "—" },
              ].map((field) => (
                <div key={field.label} className="bg-background p-4">
                  <dt className="text-[10px] font-bold uppercase tracking-[0.1em] text-muted">{field.label}</dt>
                  <dd className="mt-1 text-sm font-medium text-foreground">{field.value}</dd>
                </div>
              ))}
            </dl>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background">
      <div className="grid min-h-[calc(100vh-64px)] lg:grid-cols-[0.9fr_1.1fr]">
        <aside className="relative hidden overflow-hidden border-r border-bd-border bg-surface lg:flex lg:flex-col lg:justify-between lg:p-12">
          <div className="topo-bg pointer-events-none absolute inset-0 opacity-70" />

          <div className="relative flex items-center gap-3">
            {branding.logo_url ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={branding.logo_url} alt="" className="h-10 max-w-40 object-contain" />
            ) : null}
            <div>
              <span className="block font-display text-lg font-extrabold uppercase text-foreground">
                {company.name || "Tienda"}
              </span>
              {contact.city ? (
                <span className="mt-1 block text-[9px] font-semibold uppercase tracking-[0.18em] text-muted">
                  {contact.city}
                </span>
              ) : null}
            </div>
          </div>

          <div className="relative">
            <span className="section-label">Tu cuenta</span>
            <h2 className="mt-3 max-w-md font-display text-5xl font-black italic uppercase leading-[0.9] tracking-[-0.045em] text-foreground">
              Pedidos, carrito y perfil en un solo lugar.
            </h2>
            <p className="mt-5 max-w-sm text-sm leading-7 text-muted">
              Accede para consultar tu actividad y continuar con tus compras.
            </p>
          </div>

          <p className="relative text-xs text-muted">
            La cuenta es personal. La información visible depende de la tienda que estás visitando.
          </p>
        </aside>

        <div className="flex items-center justify-center px-6 py-12 lg:px-12">
          <div className="w-full max-w-md">
            <div className="mb-8 flex items-center gap-3 lg:hidden">
              {branding.logo_url ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={branding.logo_url} alt="" className="h-8 max-w-32 object-contain" />
              ) : null}
              <span className="font-display text-base font-extrabold uppercase text-foreground">
                {company.name || "Tienda"}
              </span>
            </div>

            <div className="mb-8">
              <span className="section-label">{isLogin ? "Bienvenido" : "Nuevo usuario"}</span>
              <h1 className="mt-2 font-display text-4xl font-black italic uppercase tracking-[-0.035em] text-foreground">
                {isLogin ? "Iniciar sesión" : "Crear cuenta"}
              </h1>
            </div>

            {error ? (
              <div className="mb-5 rounded-xl border border-red-500/25 bg-red-500/10 p-4 text-sm text-red-200" role="alert">{error}</div>
            ) : null}
            {success ? (
              <div className="mb-5 rounded-xl border border-bd-border bg-surface p-4 text-sm text-foreground" role="status">{success}</div>
            ) : null}

            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className={labelClass}>Usuario</label>
                <input value={username} onChange={(e) => setUsername(e.target.value)} className={inputClass} required autoComplete="username" placeholder="Tu nombre de usuario" />
              </div>

              {!isLogin ? (
                <div>
                  <label className={labelClass}>Correo electrónico</label>
                  <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} className={inputClass} required autoComplete="email" placeholder="correo@ejemplo.com" />
                </div>
              ) : null}

              <div>
                <label className={labelClass}>Contraseña</label>
                <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} className={inputClass} required autoComplete={isLogin ? "current-password" : "new-password"} placeholder="••••••••" />
              </div>

              {!isLogin ? (
                <div>
                  <label className={labelClass}>Confirmar contraseña</label>
                  <input type="password" value={passwordConfirm} onChange={(e) => setPasswordConfirm(e.target.value)} className={inputClass} required autoComplete="new-password" placeholder="••••••••" />
                </div>
              ) : null}

              <button className="mt-2 w-full rounded-xl bg-primary px-6 py-3.5 text-sm font-extrabold uppercase tracking-[0.08em] text-background transition hover:opacity-90">
                {isLogin ? "Iniciar sesión" : "Registrarme"}
              </button>
            </form>

            <div className="mt-6 space-y-3 text-center text-sm text-muted">
              <div>
                {isLogin ? "¿No tienes cuenta?" : "¿Ya tienes cuenta?"}{" "}
                <button
                  type="button"
                  onClick={() => { setError(null); setSuccess(null); setIsLogin(!isLogin); }}
                  className="font-bold text-foreground transition hover:text-muted"
                >
                  {isLogin ? "Crear una ahora" : "Iniciar sesión"}
                </button>
              </div>
              {isLogin ? (
                <div>
                  <a href="/auth/forgot-password" className="transition hover:text-foreground">
                    ¿Olvidaste tu contraseña?
                  </a>
                </div>
              ) : null}
            </div>

            {isLogin ? (
              <DevQuickLogin
                onUse={(demoUsername, demoPassword) => {
                  setUsername(demoUsername);
                  setPassword(demoPassword);
                  setError(null);
                  setSuccess(null);
                }}
              />
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
