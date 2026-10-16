/**
 * «Stock changed» — a hint, never data.
 *
 * After an operation that moves stock (a sale at the till, a device set aside
 * or written off) the screen that did it says so, and screens that SHOW stock
 * («Consultar disponibilidad») re-ask the server. Same tab through a window
 * event, other tabs through a BroadcastChannel when the browser has one.
 *
 * Nothing travels but the fact: no product, no quantity. The server stays the
 * only source of what the shelves hold.
 */

const CHANNEL = "bd-stock-changed";
const EVENT = "bd:stock-changed";

function channel(): BroadcastChannel | null {
  if (typeof window === "undefined" || typeof BroadcastChannel === "undefined") return null;
  try {
    return new BroadcastChannel(CHANNEL);
  } catch {
    return null;
  }
}

export function notifyStockChanged(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(EVENT));
  const bc = channel();
  if (bc) {
    bc.postMessage("changed");
    bc.close();
  }
}

/** Calls `listener` whenever stock changed somewhere in this browser. Returns the unsubscribe. */
export function onStockChanged(listener: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  const handler = () => listener();
  window.addEventListener(EVENT, handler);
  const bc = channel();
  if (bc) bc.onmessage = handler;
  return () => {
    window.removeEventListener(EVENT, handler);
    if (bc) bc.close();
  };
}
