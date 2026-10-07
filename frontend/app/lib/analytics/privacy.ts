/**
 * What a measurement provider may be told about WHERE a visitor is and WHAT they typed.
 *
 * A URL in this shop can be a credential: the link to follow a repair, to reset
 * a password, to verify an account or to accept an invitation carries a token
 * that opens that thing for whoever holds it. A provider's script reads
 * `location.href` by itself and sends it home, and nothing passed to it can
 * stop that. So the rule has two halves:
 *
 *   1. ON A SENSITIVE PAGE NO PROVIDER SCRIPT IS PRESENT AT ALL — and a document
 *      that STARTED on one never loads any, even after navigating away
 *      (`service.ts`). The only safe script on such a page is none.
 *   2. Everywhere else, what we say explicitly is a path with no query string,
 *      and free text a visitor typed is checked before it is repeated.
 */

/** Routes whose address is, or may carry, something private. No measurement here. */
const SENSITIVE_PREFIXES = [
  "/seguimiento",          // the token that opens a repair order
  "/auth/reset-password",  // ?token=
  "/auth/verify-email",    // ?token=
  "/invitacion",           // ?token=
  "/admin",                // the panel is not the shop: staff are not measured
  "/orders",               // a customer's own orders
  "/repairs",              // a customer's own repairs, with their tracking links
];

const EMAIL = /[^\s@/]+@[^\s@/]+\.[a-z]{2,}/i;
const LONG_NUMBER = /\d{8,}/;                       // an IMEI, a document, a phone, a card
const TOKEN = /eyJ[A-Za-z0-9_-]{10,}|[A-Za-z0-9_-]{32,}/;
const SERIAL = /\b(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{10,}\b/;   // F2LXK1ABC9

export function isSensitivePath(pathname: string): boolean {
  const path = pathname.toLowerCase();
  return SENSITIVE_PREFIXES.some((prefix) => path === prefix || path.startsWith(`${prefix}/`));
}

/** Free text that looks like something private rather than like a product name. */
export function looksPrivate(text: string): boolean {
  return EMAIL.test(text) || LONG_NUMBER.test(text.replace(/[\s.-]/g, "")) || TOKEN.test(text) || SERIAL.test(text);
}

/**
 * Whether measurement may run on this address at all. False on a sensitive
 * route, and false when the query string carries something private — a provider
 * script would read it straight from the address bar.
 */
export function mayMeasure(pathname: string, search: string): boolean {
  if (isSensitivePath(pathname)) return false;
  let query = search;
  try {
    query = decodeURIComponent(search);
  } catch {
    // An address that does not decode is not one to describe to anybody.
    return false;
  }
  return !looksPrivate(query);
}

/** The path as it may be said: a token route collapses to its name. Belt and braces for rule 1. */
export function sanitizePath(pathname: string): string {
  const path = pathname.split("?")[0].split("#")[0] || "/";
  for (const prefix of SENSITIVE_PREFIXES) {
    if (path.toLowerCase().startsWith(`${prefix}/`)) return `${prefix}/[REDACTED]`;
  }
  return path;
}

/** The address a provider is told: origin and sanitised path. Never a query string or a fragment. */
export function pageLocation(origin: string, pathname: string): string {
  return `${origin}${sanitizePath(pathname)}`;
}

/** What a visitor searched for, or a placeholder when it is not a product search. */
export function safeSearchTerm(term: string): string {
  const trimmed = term.trim().slice(0, 80);
  return looksPrivate(trimmed) ? "[REDACTED]" : trimmed;
}

/** A referrer reduced to its origin: where a visitor came from, never which page of it. */
export function safeReferrer(referrer: string): string {
  try {
    return referrer ? new URL(referrer).origin : "";
  } catch {
    return "";
  }
}
