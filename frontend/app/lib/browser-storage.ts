/**
 * AUTH-C (review of #184) — THE only door to `localStorage` and `sessionStorage`.
 *
 * Whatever survives in the browser after a session closes is here, by name,
 * with what it holds. Nothing else in the app may touch the storage objects —
 * not directly, not through an alias, not as `window["localStorage"]` — and an
 * ESLint rule plus a source test (`session-guards.test.ts`) enforce it.
 *
 * NEVER HERE: tokens or cookies (the session is HttpOnly), keys or secrets,
 * fiscal credentials, and a customer's private data (documents, phones,
 * addresses, a service order's notes). A key that is not listed is refused:
 * adding one is a visible line in this file, reviewed for what it holds.
 *
 * Errors of the storage itself (private mode, blocked site data, quota) are
 * passed through unchanged: callers already decide what an unavailable
 * storage means for them.
 */

export type StorageArea = "local" | "session";

type Entry = { area: StorageArea; holds: string; prefix?: boolean };

export const STORAGE_KEYS = {
  "internal-selected-company": { area: "local", holds: "the company a master is looking at (an id)" },
  "bds-inventory-mode": { area: "local", holds: "a display preference (quick or advanced)" },
  "ui-theme": { area: "local", holds: "light or dark" },
  "bd.consent.v2": { area: "local", holds: "the measurement consent answer" },
  "storefront_session_key": { area: "local", holds: "this browser's cart key (random)" },
  "blackdog_session_key": { area: "local", holds: "this browser's cart key, legacy name (random)" },
  "bd.purchase.": { area: "local", holds: "which purchases were already measured (event ids)", prefix: true },
  "bd.auth.username": { area: "session", holds: "the username to prefill on the sign-in screen after a reset" },
  "pos-sale-in-flight": { area: "session", holds: "a sale in flight: random key, basket hash, time, user and company ids" },
  "bd.checkout.reference": { area: "session", holds: "the payment reference of the success page" },
  "storefront_coupon": { area: "session", holds: "the coupon typed in the cart" },
  "blackdog_coupon": { area: "session", holds: "the coupon typed in the cart, legacy name" },
  "bd.checkout.begun": { area: "session", holds: "whether the checkout was already measured" },
} as const satisfies Record<string, Entry>;

function entryFor(area: StorageArea, key: string): Entry | null {
  const exact = (STORAGE_KEYS as Record<string, Entry>)[key];
  if (exact) return exact.area === area && !exact.prefix ? exact : null;
  for (const [name, entry] of Object.entries(STORAGE_KEYS as Record<string, Entry>)) {
    if (entry.prefix && entry.area === area && key.startsWith(name)) return entry;
  }
  return null;
}

function area(which: StorageArea): Storage {
  // The one reference to the storage objects in the whole app.
  return which === "local" ? window.localStorage : window.sessionStorage;
}

function allowed(which: StorageArea, key: string): void {
  if (!entryFor(which, key)) {
    throw new Error(`browser-storage: «${key}» is not a listed ${which} storage key`);
  }
}

export function storageGet(which: StorageArea, key: string): string | null {
  allowed(which, key);
  return area(which).getItem(key);
}

export function storageSet(which: StorageArea, key: string, value: string): void {
  allowed(which, key);
  area(which).setItem(key, value);
}

export function storageRemove(which: StorageArea, key: string): void {
  allowed(which, key);
  area(which).removeItem(key);
}
