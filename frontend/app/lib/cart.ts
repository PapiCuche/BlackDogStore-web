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
