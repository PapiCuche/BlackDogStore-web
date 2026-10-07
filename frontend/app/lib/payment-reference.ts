/**
 * The reference of the payment a buyer has just made, kept for the page that
 * asks the server whether it landed.
 *
 * It used to travel in the address (`/checkout/success?reference=…`). Whoever
 * holds it can read that payment's status, and an address is read by every
 * third-party script on the page and by the browser's history. It now stays in
 * this tab's `sessionStorage`: it survives a reload of the success page and
 * dies with the tab.
 */
const KEY = "bd.checkout.reference";
const SHAPE = /^[A-Za-z0-9_-]{6,64}$/;

export function rememberPaymentReference(reference: string): void {
  try {
    if (SHAPE.test(reference)) window.sessionStorage.setItem(KEY, reference);
  } catch {
    // Without storage the success page says it has no reference; the order page still shows the order.
  }
}

/**
 * The reference for this visit. An address that still carries one (an old link,
 * a gateway that returns with it) is honoured — and cleaned at once.
 */
export function takePaymentReference(): string | null {
  if (typeof window === "undefined") return null;
  const fromAddress = new URLSearchParams(window.location.search).get("reference");
  if (fromAddress && SHAPE.test(fromAddress)) {
    rememberPaymentReference(fromAddress);
    window.history.replaceState(window.history.state, "", window.location.pathname);
    return fromAddress;
  }
  try {
    const stored = window.sessionStorage.getItem(KEY);
    return stored && SHAPE.test(stored) ? stored : null;
  } catch {
    return null;
  }
}
