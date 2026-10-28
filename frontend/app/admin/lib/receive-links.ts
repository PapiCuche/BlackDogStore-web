/**
 * INV-UX-A1 — where «Recibir mercadería» goes for a product.
 *
 * A device counted serial by serial is received in Equipos (one record per
 * device: serial, IMEI); anything counted by quantity is a purchase entry in
 * Movimientos. Both screens read `?product=` and preselect it.
 */
export type Receivable = { id: number; is_serialized?: boolean };

export function receiveHref(product: Receivable): string {
  return product.is_serialized
    ? `/admin/inventory/units?register=1&product=${product.id}`
    : `/admin/inventory/movements?receive=1&product=${product.id}`;
}

/** `?product=41` → 41; anything else → null. */
export function productIdFromSearch(search: string): number | null {
  const raw = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search).get("product");
  return raw && /^\d{1,9}$/.test(raw) && Number(raw) > 0 ? Number(raw) : null;
}

/** `?receive=1` / `?register=1`: the link asks to open the receiving form. */
export function wantsToReceive(search: string, key: "receive" | "register"): boolean {
  return new URLSearchParams(search.startsWith("?") ? search.slice(1) : search).get(key) === "1";
}
