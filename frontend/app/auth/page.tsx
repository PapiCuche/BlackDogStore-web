"use client";

import { useEffect, useState } from "react";
import { track } from "../lib/analytics/service";
import { BrandLogo } from "../components/BrandLogo";
import { GoogleSignIn } from "../components/GoogleSignIn";
import { useStorefront } from "../components/StorefrontProvider";
import { useRouter } from "next/navigation";
import {
  login, logout, getCurrentUser, register, AuthUser,
  forgetInternalAccess, hasInternalAccess,
} from "../lib/auth";
import { safeInternalNextPath } from "../lib/safe-next";
import {
  accountStateOf, invitationPath, invitationTokenFromNext, readInvitation,
} from "../lib/invitation";
import { DevQuickLogin } from "./components/DevQuickLogin";
import { VerificationCode } from "./VerificationCode";

/**
 * Adónde lleva un login correcto — H4.1.1.
 *
 *   1. `?next=` si es una ruta local. Es lo que permite volver a la invitación
 *      que pidió iniciar sesión, en lugar de perderla en la portada.
 *   2. Sin `next`: al control interno si el SERVIDOR dice que hay acceso; si
 *      no, a la tienda.
 *
 * Quien es cliente y trabajador aterriza en el panel y conserva la tienda: la
 * cabecera le ofrece «Pedidos» y «Control interno», y el panel tiene «Volver a
 * la tienda». Ser trabajador no le quita ser cliente.
 *
 * `next` se lee de `window.location` al enviar, no con `useSearchParams`: así
 * la página no necesita un límite de Suspense sólo para esto.
 */
async function destinationAfterLogin(): Promise<string> {
  const next = safeInternalNextPath(new URLSearchParams(window.location.search).get("next"));
  if (next) return next;
  return (await hasInternalAccess()) ? "/admin" : "/";
}

/**
 * El usuario que la pantalla de recuperación acaba de decirle a la persona. Va
 * de una pantalla a la siguiente en la misma pestaña y se borra al leerlo: no es
 * una credencial, pero tampoco algo que dejar escrito en la dirección.
 */
const USERNAME_HANDOFF = "bd.auth.username";

function handedUsername(): string {
  try {
    return window.sessionStorage.getItem(USERNAME_HANDOFF) ?? "";
  } catch {
    return "";
  }
}

function forgetHandedUsername(): void {
  try {
    window.sessionStorage.removeItem(USERNAME_HANDOFF);
  } catch {
    // Sin almacenamiento de sesión no había nada que olvidar.
  }
}

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
  // El correo que acaba de registrarse y espera su verificación. Mientras lo
  // haya, la pantalla pide el código en vez del formulario.
  const [verifying, setVerifying] = useState<string | null>(null);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  // Quien llega desde una invitación (`?next=/invitacion?token=…`). Crear la
  // cuenta usa entonces el correo de la invitación, y no otro: la invitación es
  // para una persona, no para quien tenga el enlace y escriba cualquier correo.
  const [invitation, setInvitation] = useState<{ token: string; email: string } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // Se llegó desde una invitación y no se pudo leer. Registrarse ahora crearía
  // la cuenta SIN la invitación —sin verificar, que es el estado del que no se
  // salía—, así que no se deja: se dice que hay que esperar.
  const [invitationUnread, setInvitationUnread] = useState(false);
  const [forgotHref, setForgotHref] = useState("/auth/forgot-password");
  const router = useRouter();

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const search = new URLSearchParams(window.location.search);
      const wantsRegister = search.get("mode") === "register";
      const token = invitationTokenFromNext(search.get("next"));
      const handed = handedUsername();
      const read = token ? await readInvitation(token) : null;
      if (cancelled) return;
      const info = read?.state === "found" ? read.info : null;
      if (read?.state === "busy") setInvitationUnread(true);

      // Se olvida DESPUÉS de usarlo, no al leerlo: en desarrollo React ejecuta
      // este efecto dos veces y la primera se cancela; borrarlo al leer dejaba
      // el campo vacío (lo encontró la prueba en navegador real).
      if (handed) {
        setUsername(handed);
        forgetHandedUsername();
      }
      if (token) setForgotHref(`/auth/forgot-password?next=${encodeURIComponent(invitationPath(token))}`);
      if (!token || !info) {
        if (wantsRegister) setIsLogin(false);
        return;
      }
      if (accountStateOf(info) === "none") {
        setInvitation({ token, email: info.email });
        setEmail(info.email);
        if (wantsRegister) setIsLogin(false);
      } else if (wantsRegister) {
        // El registro sólo podía terminar en «ese correo ya está registrado».
        setNotice("Ya tienes una cuenta con este correo. Inicia sesión para aceptar tu invitación.");
      }
    })();
    return () => { cancelled = true; };
  }, []);

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
      if (!isLogin && invitationUnread) {
        setError("No pudimos comprobar tu invitación ahora mismo. Espera un minuto y vuelve a cargar esta página antes de crear la cuenta.");
        return;
      }
      if (isLogin) {
        const data = await login(username, password);
        setUser(data.user);
        track({ name: "LOGIN", method: "password" });
        setSuccess("Inicio de sesión correcto.");
        forgetInternalAccess();
        window.dispatchEvent(new Event("authChange"));
        router.push(await destinationAfterLogin());
      } else {
        const result = await register(
          invitation
            ? { username, email: invitation.email, password, password_confirm: passwordConfirm, invitation_token: invitation.token }
            : { username, email, password, password_confirm: passwordConfirm },
        );
        track({ name: "SIGN_UP", method: "password" });
        if (invitation && !result.requires_verification) {
          // La cuenta nació activa: la invitación ya probó el buzón. Se entra
          // con lo que la persona acaba de escribir y se vuelve a la
          // invitación, que es adonde iba.
          const data = await login(username, password);
          setUser(data.user);
          forgetInternalAccess();
          window.dispatchEvent(new Event("authChange"));
          router.push(await destinationAfterLogin());
          return;
        }
        if (result.requires_verification) {
          setVerifying(invitation ? invitation.email : email);
        } else {
          setSuccess("Registro completado. Ahora inicia sesión.");
          setIsLogin(true);
        }
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "No se pudo completar la acción.");
    }
  }

  async function handleGoogleSignedIn(signedIn: AuthUser) {
    track({ name: "LOGIN", method: "google" });
    // La sesión ya está en sus cookies; desde aquí es un inicio como cualquiera.
    setUser(signedIn);
    setError(null);
    setSuccess("Inicio de sesión correcto.");
    forgetInternalAccess();
    window.dispatchEvent(new Event("authChange"));
    router.push(await destinationAfterLogin());
  }

  async function handleLogout() {
    await logout().catch(() => {});
    forgetInternalAccess();
    setUser(null);
    window.dispatchEvent(new Event("authChange"));
    router.push("/");
  }

  const inputClass =
    "mt-2 w-full rounded-xl border border-bd-border bg-surface px-4 py-3 text-sm text-foreground placeholder-muted focus:border-bd-border focus:outline-none";
  const labelClass = "block text-xs font-bold uppercase tracking-wide text-muted";

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
          <div className="rounded-xl border border-bd-border bg-surface p-8">
            <div className="flex items-start justify-between">
              <div>
                <span className="section-label">Cuenta</span>
                <h1 className="font-display mt-2 text-4xl font-semibold uppercase text-foreground">Mi perfil</h1>
              </div>
              <button
                type="button"
                onClick={handleLogout}
                className="rounded-full border border-bd-border bg-surface px-5 py-2.5 text-xs font-bold uppercase tracking-wide text-muted transition hover:border-bd-border hover:text-foreground"
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
                  <span className="text-[10px] font-bold uppercase tracking-wide text-muted">{field.label}</span>
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
              wordmarkClassName="font-display text-lg font-semibold uppercase tracking-tight text-foreground"
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
            <h2 className="font-display mt-3 text-6xl font-semibold uppercase leading-none tracking-tight text-foreground">
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
        <div className="flex min-w-0 flex-col items-center justify-center px-6 py-12 lg:px-12">
          {/* `min-w-0`: un ítem flex no baja de su ancho intrínseco por defecto, así
              que cualquier contenido ancho —la tarjeta de accesos, por ejemplo—
              estiraba esta columna y con ella TODO lo que lleva `w-full`. A 320 px
              el formulario entero medía 330. */}
          <div className="w-full min-w-0 max-w-md">

            {/* Mobile logo */}
            <div className="mb-8 flex items-center gap-3 lg:hidden">
              <BrandLogo
                placement="compact"
                surface="theme"
                className="h-10 w-auto object-contain"
                wordmarkClassName="font-display text-base font-semibold uppercase tracking-tight text-foreground"
              />
            </div>

            {verifying !== null ? (
              <div>
                <div className="mb-8">
                  <span className="section-label">Un paso más</span>
                  <h1 className="font-display mt-2 text-4xl font-semibold uppercase text-foreground">
                    Verifica tu correo
                  </h1>
                </div>
                <VerificationCode
                  email={verifying}
                  onVerified={() => {
                    // Verificar no inicia sesión: la persona entra con lo que eligió.
                    setVerifying(null);
                    setPassword("");
                    setPasswordConfirm("");
                    setError(null);
                    setIsLogin(true);
                    setSuccess("Correo verificado. Ya puedes iniciar sesión.");
                  }}
                />
                <button
                  type="button"
                  onClick={() => { setVerifying(null); setIsLogin(true); setError(null); setSuccess(null); }}
                  className="mt-6 text-sm text-muted transition hover:text-foreground"
                >
                  Volver al inicio de sesión
                </button>
              </div>
            ) : (
            <>
            <div className="mb-8">
              <span className="section-label">{isLogin ? "Bienvenido" : "Nuevo usuario"}</span>
              <h1 className="font-display mt-2 text-4xl font-semibold uppercase text-foreground">
                {isLogin ? "Iniciar sesión" : "Crear cuenta"}
              </h1>
            </div>

            {notice && isLogin && (
              <div className="mb-5 rounded-xl border border-bd-border bg-surface p-4 text-sm text-foreground">
                {notice}
              </div>
            )}
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
                {!isLogin && (
                  <p className="mt-1.5 text-xs text-muted">Con este nombre iniciarás sesión.</p>
                )}
              </div>

              {!isLogin && (
                <div>
                  <label htmlFor="auth-page-correo-electronico" className={labelClass}>Correo electrónico</label>
                  <input id="auth-page-correo-electronico"
                    type="email"
                    value={invitation ? invitation.email : email}
                    onChange={(e) => setEmail(e.target.value)}
                    readOnly={invitation !== null}
                    className={inputClass}
                    required
                    autoComplete="email"
                    placeholder="correo@ejemplo.com"
                  />
                  {invitation && (
                    <p className="mt-1.5 text-xs text-muted">
                      Es el correo al que se envió tu invitación. La cuenta se crea con él.
                    </p>
                  )}
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

              <button className="mt-2 w-full rounded-full bg-foreground px-6 py-3.5 text-sm font-semibold uppercase tracking-wide text-background transition hover:bg-foreground/90">
                {isLogin ? "Iniciar sesión" : "Registrarme"}
              </button>
            </form>

            {/* Entrar y registrarse con Google son lo mismo: la primera vez crea la cuenta. */}
            <GoogleSignIn onSignedIn={handleGoogleSignedIn} />

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
                  <a href={forgotHref} className="text-muted transition hover:text-foreground">
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
            </>
            )}

          </div>
        </div>

      </div>
    </div>
  );
}
