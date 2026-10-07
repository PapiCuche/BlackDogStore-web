/**
 * CONSENT — what a visitor allowed, and nothing a visitor did not say.
 *
 *     necessary   always: the session, the cart, the theme. Not asked.
 *     analytics   Google Analytics
 *     marketing   Meta and TikTok
 *
 * Until a visitor answers, both optional categories are OFF. Not «pending», not
 * «implied by scrolling»: off. The measurement code reads this module and loads
 * a provider's script only for a category that is `true` here.
 *
 * The answer lives in this browser (`localStorage`): it is a preference of the
 * device, not of an account, and it has to be readable before anybody signs in.
 * It carries no identifier. The SERVER learns of it only at checkout, where it
 * travels with the order so that a refusal here is a refusal there too.
 */

export type ConsentCategory = "analytics" | "marketing";
export type Consent = Record<ConsentCategory, boolean>;
export type StoredConsent = Consent & { version: 2; decidedAt: string };

export const CONSENT_KEY = "bd.consent.v2";
export const NO_CONSENT: Consent = { analytics: false, marketing: false };
export const FULL_CONSENT: Consent = { analytics: true, marketing: true };

const CHANGED = "bd:consent";
const OPEN = "bd:consent:open";

/** The stored answer, or null when the visitor has not given one (or it is not one). */
export function readConsent(): StoredConsent | null {
  if (typeof window === "undefined") return null;
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(CONSENT_KEY) ?? "null");
    if (!parsed || typeof parsed !== "object") return null;
    const value = parsed as Record<string, unknown>;
    // Strict on purpose: a value somebody edited to "yes" is not a consent.
    if (value.version !== 2 || typeof value.analytics !== "boolean" || typeof value.marketing !== "boolean") return null;
    return {
      version: 2, analytics: value.analytics, marketing: value.marketing,
      decidedAt: typeof value.decidedAt === "string" ? value.decidedAt : "",
    };
  } catch {
    return null;
  }
}

/** What may be done right now: the stored answer, or nothing at all. */
export function currentConsent(): Consent {
  const stored = readConsent();
  return stored ? { analytics: stored.analytics, marketing: stored.marketing } : NO_CONSENT;
}

export function writeConsent(consent: Consent): StoredConsent {
  const stored: StoredConsent = {
    version: 2, analytics: consent.analytics === true, marketing: consent.marketing === true,
    decidedAt: new Date().toISOString(),
  };
  try {
    window.localStorage.setItem(CONSENT_KEY, JSON.stringify(stored));
  } catch {
    // A browser that stores nothing keeps the answer for this page only.
  }
  window.dispatchEvent(new CustomEvent<Consent>(CHANGED, { detail: { analytics: stored.analytics, marketing: stored.marketing } }));
  return stored;
}

/** Called with the new answer whenever it changes, in this tab or in another one. */
export function subscribeConsent(listener: (consent: Consent) => void): () => void {
  const changed = (event: Event) => listener((event as CustomEvent<Consent>).detail);
  const stored = (event: StorageEvent) => { if (event.key === CONSENT_KEY) listener(currentConsent()); };
  window.addEventListener(CHANGED, changed);
  window.addEventListener("storage", stored);
  return () => {
    window.removeEventListener(CHANGED, changed);
    window.removeEventListener("storage", stored);
  };
}

/** «Preferencias de cookies» in the footer: ask the banner to open its settings. */
export function openConsentPreferences(): void {
  window.dispatchEvent(new Event(OPEN));
}

export function onOpenConsentPreferences(listener: () => void): () => void {
  window.addEventListener(OPEN, listener);
  return () => window.removeEventListener(OPEN, listener);
}
