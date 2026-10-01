import { API_BASE } from "./api";

const SESSION_KEY = "storefront_session_key";
const LEGACY_SESSION_KEY = "blackdog_session_key";

const COUPON_KEY = "storefront_coupon";
const LEGACY_COUPON_KEY = "blackdog_coupon";

export type StoredCoupon = {
  code: string;
  discount_percent: number;
};

export function emitCartChange() {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event("cartChange"));
}

export function getSessionKey(): string {
  if (typeof window === "undefined") {
    return "guest-session";
  }

  const existing = window.localStorage.getItem(SESSION_KEY);
  if (existing) {
    // Keep the legacy mirror during the migration window so an already-open
    // pre-migration tab continues to address the same guest cart.
    if (!window.localStorage.getItem(LEGACY_SESSION_KEY)) {
      window.localStorage.setItem(LEGACY_SESSION_KEY, existing);
    }
    return existing;
  }

  const legacy = window.localStorage.getItem(LEGACY_SESSION_KEY);
  if (legacy) {
    window.localStorage.setItem(SESSION_KEY, legacy);
    return legacy;
  }

  const newKey = `guest-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  window.localStorage.setItem(SESSION_KEY, newKey);
  // Transitional compatibility only. Remove this mirror after the old
  // storefront version can no longer coexist with the new one.
  window.localStorage.setItem(LEGACY_SESSION_KEY, newKey);
  return newKey;
}

/**
 * El resultado de leer el carrito, donde «vacío» e «ilegible» NO son lo mismo.
 *
 * Son dos hechos distintos sobre el mundo —no hay nada que comprar / no sabemos
 * qué hay que comprar— y quien los confunde acaba diciéndole al comprador que
 * su carrito está vacío cuando lo que pasó es que el servidor no contestó.
 */
export type CartReadResult<T> =
  | { status: "ok"; items: T[] }
  | { status: "unreadable" };

/** Tope duro de espera. Un `Retry-After` absurdo no deja la pantalla colgada. */
const MAX_WAIT_MS = 120_000;

function waitSecondsFrom(res: Response, body: string): number | null {
  // `Retry-After` primero: es la cabecera estándar y no depende del idioma del
  // mensaje. El cuerpo de DRF —«…disponible en 60 segundos»— es el respaldo.
  const header = Number(res.headers.get("Retry-After"));
  if (Number.isFinite(header) && header > 0) return header;
  const fromBody = Number(body.match(/(\d+)\s*segundo/)?.[1]);
  return Number.isFinite(fromBody) && fromBody > 0 ? fromBody : null;
}

/**
 * Lee el carrito del servidor, respetando el limitador en vez de esquivarlo.
 *
 * EL LÍMITE ES UNA DEFENSA, NO UN ESTORBO. `cart` admite 60 lecturas por minuto
 * y por IP; superarlo devuelve 429 diciendo cuántos segundos faltan. Eso le pasa
 * a una oficina entera detrás de una misma salida a internet, y le pasa a la
 * suite de navegador, que comparte IP con todo lo demás. La respuesta correcta
 * no es pedir más cupo ni apagar el límite: es esperar lo que el servidor pide y
 * volver a intentarlo UNA vez. Si sigue sin poder leerse, se dice.
 */
export async function readCart<T>(
  sessionKey: string,
  opts: {
    fetchImpl?: typeof fetch;
    wait?: (ms: number) => Promise<void>;
    retries?: number;
  } = {},
): Promise<CartReadResult<T>> {
  const doFetch = opts.fetchImpl ?? fetch;
  const wait =
    opts.wait ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  const retries = opts.retries ?? 1;

  const url = `${API_BASE}/cart/?session_key=${encodeURIComponent(sessionKey)}`;

  for (let intento = 0; ; intento++) {
    let res: Response;
    try {
      res = await doFetch(url);
    } catch {
      // Sin red no hay nada que reintentar aquí: quien no puede abrir la
      // conexión tampoco va a poder en un segundo, y la pantalla ya ofrece
      // reintentar a mano.
      return { status: "unreadable" };
    }

    if (res.ok) {
      const data = await res.json().catch(() => null);
      return { status: "ok", items: Array.isArray(data) ? (data as T[]) : [] };
    }

    if (res.status === 429 && intento < retries) {
      const body = await res.text().catch(() => "");
      const segundos = waitSecondsFrom(res, body);
      if (segundos !== null) {
        await wait(Math.min((segundos + 1) * 1000, MAX_WAIT_MS));
        continue;
      }
    }

    return { status: "unreadable" };
  }
}

export function readStoredCoupon(): StoredCoupon | null {
  if (typeof window === "undefined") return null;

  const raw =
    window.sessionStorage.getItem(COUPON_KEY) ??
    window.sessionStorage.getItem(LEGACY_COUPON_KEY);

  if (!raw) return null;

  try {
    const parsed = JSON.parse(raw) as Partial<StoredCoupon>;
    if (
      typeof parsed.code !== "string" ||
      typeof parsed.discount_percent !== "number"
    ) {
      clearStoredCoupon();
      return null;
    }

    const coupon: StoredCoupon = {
      code: parsed.code,
      discount_percent: parsed.discount_percent,
    };

    window.sessionStorage.setItem(COUPON_KEY, JSON.stringify(coupon));
    window.sessionStorage.removeItem(LEGACY_COUPON_KEY);
    return coupon;
  } catch {
    clearStoredCoupon();
    return null;
  }
}

export function writeStoredCoupon(coupon: StoredCoupon) {
  if (typeof window === "undefined") return;
  window.sessionStorage.setItem(COUPON_KEY, JSON.stringify(coupon));
  window.sessionStorage.removeItem(LEGACY_COUPON_KEY);
}

export function clearStoredCoupon() {
  if (typeof window === "undefined") return;
  window.sessionStorage.removeItem(COUPON_KEY);
  window.sessionStorage.removeItem(LEGACY_COUPON_KEY);
}
