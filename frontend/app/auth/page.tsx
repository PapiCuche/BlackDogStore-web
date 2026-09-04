"use client";

import { useEffect, useState } from "react";
import { BrandLogo } from "../components/BrandLogo";
import { useStorefront } from "../components/StorefrontProvider";
import { useRouter } from "next/navigation";
import { login, logout, getCurrentUser, register, AuthUser } from "../lib/auth";
import { DevQuickLogin } from "./components/DevQuickLogin";

export default function AuthPage() {
  // The storefront this visitor arrived at. The ACCOUNT they log into is
  // global — one identity across every shop — but this page is the shop's.
  const { contact } = useStorefront();
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
    "mt-2 w-full rounded-xl border border-bd-border bg-surface px-4 py-3 text-sm text-foreground placeholder-muted focus:border-bd-border focus:outline-none";
  const labelClass = "block text-xs font-bold uppercase tracking-widest text-muted";

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-foreground border-t-transparent" />
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
                className="rounded-full border border-bd-border bg-surface px-5 py-2.5 text-xs font-bold uppercase tracking-widest text-muted transition hover:border-bd-border hover:text-foreground"
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
                <div key={field.label} className="rounded-xl border border-bd-border bg-surface px-4 py-3">
                  <span className="text-[10px] font-bold uppercase tracking-widest text-muted">{field.label}</span>
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
          <div className="dot-grid absolute right-0 top-0 h-64 w-64 opacity-20 pointer-events-none" />

          {/* Logo — this storefront's, resolved from the host. The login page
              belongs to the shop the customer came to, even though the ACCOUNT
              behind it is global; see store/emails.py for the other half of
              that distinction. */}
          {/* Este panel es `bg-background`, que SIGUE al tema: la superficie
              cambia, y el logotipo con ella. El comentario anterior decía
              «panel oscuro fijo» y era cierto cuando el fondo era un negro
              literal; la migración de M12F lo convirtió en token y dejó atrás
              la declaración de superficie. El nombre en tipografía sólo
              aparece si no hay variante — el lockup ya lo contiene. */}
          <div className="relative flex items-center gap-3">
            <BrandLogo
              placement="header"
              surface="theme"
              className="h-11 w-auto object-contain"
              wordmarkClassName="font-display text-lg font-black uppercase tracking-tight text-foreground"
            />
            <div>
              {contact.city ? (
                <span className="block text-[9px] font-semibold uppercase tracking-[0.3em] text-muted">
                  {contact.city}
                </span>
              ) : null}
            </div>
          </div>

          {/* Main copy */}
          <div className="relative">
            <span className="section-label">{contact.city}</span>
            <h2 className="font-display mt-3 text-6xl font-black uppercase leading-none tracking-tight text-foreground">
              Equipos<br />Apple<br />Originales
            </h2>
            <p className="mt-5 max-w-sm text-sm leading-7 text-muted">
              Accede a tu cuenta para ver el estado de tus pedidos, guardar tu carrito y gestionar tu perfil.
            </p>
          </div>

          {/* Trust row */}
          <div className="relative flex flex-wrap gap-6 text-xs text-muted">
            <span>✓ Servicio especializado</span>
            <span>✓ Envío a todo Perú</span>
            <span>✓ Condiciones claras</span>
          </div>
        </div>

        {/* Right — form panel */}
        <div className="flex flex-col items-center justify-center px-6 py-12 lg:px-12">
          <div className="w-full max-w-md">

            {/* Mobile logo */}
            <div className="mb-8 flex items-center gap-3 lg:hidden">
              <BrandLogo
                placement="compact"
                surface="theme"
                className="h-10 w-auto object-contain"
                wordmarkClassName="font-display text-base font-black uppercase tracking-tight text-foreground"
              />
            </div>

            <div className="mb-8">
              <span className="section-label">{isLogin ? "Bienvenido" : "Nuevo usuario"}</span>
              <h1 className="font-display mt-2 text-4xl font-black uppercase text-foreground">
                {isLogin ? "Iniciar sesión" : "Crear cuenta"}
              </h1>
            </div>

            {error && (
              <div className="mb-5 rounded-xl border border-danger-border bg-danger-surface p-4 text-sm text-danger">
                {error}
              </div>
            )}
            {success && (
              <div className="mb-5 rounded-xl border border-bd-border bg-surface p-4 text-sm text-foreground">
                {success}
              </div>
            )}

            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label htmlFor="auth-page-usuario" className={labelClass}>Usuario</label>
                <input id="auth-page-usuario"
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
                  <label htmlFor="auth-page-correo-electronico" className={labelClass}>Correo electrónico</label>
                  <input id="auth-page-correo-electronico"
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
                <label htmlFor="auth-page-contrasena" className={labelClass}>Contraseña</label>
                <input id="auth-page-contrasena"
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
                  <label htmlFor="auth-page-confirmar-contrasena" className={labelClass}>Confirmar contraseña</label>
                  <input id="auth-page-confirmar-contrasena"
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

              <button className="mt-2 w-full rounded-full bg-foreground px-6 py-3.5 text-sm font-black uppercase tracking-widest text-background transition hover:bg-foreground/90">
                {isLogin ? "Iniciar sesión" : "Registrarme"}
              </button>
            </form>

            <div className="mt-6 space-y-3 text-center text-sm text-muted">
              <div>
                {isLogin ? "¿No tienes cuenta?" : "¿Ya tienes cuenta?"}{" "}
                <button
                  type="button"
                  onClick={() => { setError(null); setSuccess(null); setIsLogin(!isLogin); }}
                  className="font-bold text-foreground transition hover:text-foreground/85"
                >
                  {isLogin ? "Crear una ahora" : "Iniciar sesión"}
                </button>
              </div>
              {isLogin && (
                <div>
                  <a href="/auth/forgot-password" className="text-muted transition hover:text-foreground">
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
