/**
 * INV-UX-A1 — where «Recibir mercadería» goes for a product.
 *
 * A device counted serial by serial is received in Equipos (one record per
 * device: serial, IMEI); anything counted by quantity is a purchase entry in
 * Movimientos. Both screens read `?product=` and preselect it.
 */
export type Receivable = { id: number; is_serialized?: boolean; product_model_id?: number | null };

export function receiveHref(product: Receivable): string {
  // INV-INTAKE-GUARD-01: a variant of a device model is received in «Recibir mercadería», which starts it «por
  // equipo» and offers the state by its line. Every other door refuses the same things on the server anyway.
  if (typeof product.product_model_id === "number") return `/admin/inventory/receive?product=${product.id}`;
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
