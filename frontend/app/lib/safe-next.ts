/**
 * Adónde volver después de iniciar sesión — si y sólo si es una ruta LOCAL.
 *
 * `?next=` lo escribe quien construye el enlace, y un enlace lo puede construir
 * cualquiera. Sin esta comprobación, `/auth?next=https://evil.example` convierte
 * el login de la tienda en un trampolín: la persona escribe su contraseña en el
 * sitio correcto y aterriza en otro que se le parece.
 *
 * Por eso no se hace `router.push(next)` con nada que no haya pasado por aquí.
 *
 * QUÉ SE ACEPTA: una ruta que empieza por una sola barra y que, resuelta contra
 * un origen ficticio, sigue en ese origen: `/admin`, `/admin/service`,
 * `/orders`, `/invitacion?token=…`.
 *
 * QUÉ SE RECHAZA, y por qué:
 *   · `https:`, `javascript:`, `data:` — otro origen, o código.
 *   · dos barras al principio — protocolo relativo: otro host.
 *   · barra invertida en cualquier sitio — los navegadores la leen como barra.
 *   · caracteres de control — también se normalizan hacia fuera del sitio.
 *   · codificaciones que al decodificarse producen lo anterior (`%2F%2F`,
 *     `%5C`, `%09`, y las mismas codificadas dos veces).
 *   · `/auth…` — volver al login después del login es un bucle.
 */

const LOCAL_ORIGIN = "https://local.invalid";
const SCHEME = /^[a-z][a-z0-9+.-]*:/i;
const MAX_LENGTH = 2048;
const BACKSLASH = 92;
const DELETE = 127;

/** Controles (0–31), DEL y la barra invertida. */
function hasUnsafeChars(value: string): boolean {
  for (let i = 0; i < value.length; i += 1) {
    const code = value.charCodeAt(i);
    if (code < 32 || code === DELETE || code === BACKSLASH) return true;
  }
  return false;
}

export function safeInternalNextPath(raw: string | null | undefined): string | null {
  if (typeof raw !== "string" || raw.length === 0 || raw.length > MAX_LENGTH) return null;
  if (hasUnsafeChars(raw)) return null;
  if (!raw.startsWith("/") || raw.startsWith("//")) return null;

  // Decodifica hasta que no cambie (con tope) y vuelve a comprobar: lo que
  // importa es lo que el navegador acabará interpretando, no lo que se ve.
  let decoded = raw;
  for (let i = 0; i < 3; i += 1) {
    let next: string;
    try {
      next = decodeURIComponent(decoded);
    } catch {
      return null;
    }
    if (next === decoded) break;
    decoded = next;
  }
  if (hasUnsafeChars(decoded) || decoded.startsWith("//") || SCHEME.test(decoded)) {
    return null;
  }

  let url: URL;
  try {
    url = new URL(raw, LOCAL_ORIGIN);
  } catch {
    return null;
  }
  if (url.origin !== LOCAL_ORIGIN) return null;
  if (url.pathname === "/auth" || url.pathname.startsWith("/auth/")) return null;

  return `${url.pathname}${url.search}${url.hash}`;
}
