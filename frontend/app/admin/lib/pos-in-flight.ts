import { storageGet, storageRemove, storageSet } from "../../lib/browser-storage";
/**
 * AUTH-C (review P2-6) — a counter sale that was SENT and has no definitive answer.
 *
 * THE DANGEROUS CASE. The sale reached the server and was made, the answer was
 * lost, and the session closed before the cashier could retry. Signing in again
 * is a full navigation: the basket and its idempotency key were only in memory.
 * Rebuilding the basket gets a NEW key, and charging it would sell twice.
 *
 * So, only while a sale is in flight, this tab keeps `{ key, basket, at }` in
 * `sessionStorage`: a random key, a hash of the lines and a time. Nothing about
 * the customer, nothing a stranger could use. Back in, the till ASKS the server
 * with the key (lookup only, `lookupPosSaleByKey`) and shows the answer; it
 * never sells by itself. The cashier confirms, and only then is the mark
 * forgotten.
 *
 * Forgotten as soon as the answer is definitive: a sale made, or a refusal
 * that says nothing was sold. Kept on a lost answer, a server error, or a 401.
 */

const STORAGE_KEY = "pos-sale-in-flight";
/** Older than this, the mark is not about the sale anybody remembers. */
const MAX_AGE_MS = 24 * 60 * 60 * 1000;

export type SaleInFlight = {
  key: string;
  basket: string;
  at: number;
  user: number;
  company: number | null;
};

/** FNV-1a over the lines: tells two baskets apart, says nothing about them. */
export function basketHash(lines: unknown): string {
  const text = JSON.stringify(lines ?? null);
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, "0");
}

export function rememberSaleInFlight(mark: Omit<SaleInFlight, "at">): void {
  try {
    storageSet("session", STORAGE_KEY, JSON.stringify({ ...mark, at: Date.now() }));
  } catch {
    // Without storage the sale is still protected by its key within this page.
  }
}

/** The mark of THIS cashier in THIS company, or null. */
export function readSaleInFlight(user: number, company: number | null): SaleInFlight | null {
  let raw: string | null = null;
  try {
    raw = storageGet("session", STORAGE_KEY);
  } catch {
    return null;
  }
  if (!raw) return null;
  try {
    const mark = JSON.parse(raw) as SaleInFlight;
    if (typeof mark?.key !== "string" || typeof mark.at !== "number") return null;
    if (Date.now() - mark.at > MAX_AGE_MS) {
      forgetSaleInFlight();
      return null;
    }
    if (mark.user !== user || (mark.company ?? null) !== (company ?? null)) return null;
    return mark;
  } catch {
    forgetSaleInFlight();
    return null;
  }
}

export function forgetSaleInFlight(): void {
  try {
    storageRemove("session", STORAGE_KEY);
  } catch {
    /* nothing to forget */
  }
}

/**
 * Whether an error from `posSale` is a DEFINITIVE answer (the server decided,
 * nothing more can come of this send) — as opposed to a lost one.
 */
export function isDefinitiveRefusal(error: unknown): boolean {
  const status = (error as { status?: unknown })?.status;
  return typeof status === "number" && status >= 400 && status < 500 && status !== 401;
}

/**
 * The idempotency key of a basket, from a SECURE source or none at all.
 *
 * `Math.random` is not one: two tills drawing the same key would answer a new
 * basket with somebody else's sale. With neither `randomUUID` nor
 * `getRandomValues` the till refuses to charge and says why.
 */
export function newSaleKey(): string | null {
  const source = (globalThis as { crypto?: Crypto }).crypto;
  if (typeof source?.randomUUID === "function") return source.randomUUID();
  if (typeof source?.getRandomValues === "function") {
    const bytes = source.getRandomValues(new Uint8Array(16));
    return "pos-" + Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  }
  return null;
}

/**
 * The key for the NEXT attempt after a failed one. A key the server reports as
 * spent on «no se cobró» (`key_reserved`) is never sent again: a new one, from
 * a secure source, or null — and with null the till refuses to charge. Any
 * other failure keeps the key, so a retry of the same basket stays idempotent.
 */
export function keyAfterFailure(error: unknown, current: string | null): string | null {
  return (error as { code?: unknown })?.code === "key_reserved" ? newSaleKey() : current;
}
