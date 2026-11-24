/**
 * The orders list filters that an address can carry. The dashboard's alerts link with `?alert=` (or `?supply=`);
 * NAV-SALES-01's «Pedidos y bajo pedido» and «Documentos» open the same list with `?supply=pending`,
 * `?status=cancelled` and `?receipt=pending`. A value the list does not know is ignored.
 */
import { PAYMENT_STATUS_LABELS, SUPPLY_ALERT_KINDS } from "../../lib/admin";

export const SUPPLY_FILTERS = ["to_confirm", "pending", "received", "failed"];

export type AddressFilters = { alert: string; supply: string; receipt: string; status: string };

export function filtersFromSearch(search: string): AddressFilters {
  const params = new URLSearchParams(search);
  const alert = params.get("alert") ?? "";
  const supply = params.get("supply") ?? "";
  const status = params.get("status") ?? "";
  return {
    alert: (SUPPLY_ALERT_KINDS as readonly string[]).includes(alert) ? alert : "",
    supply: SUPPLY_FILTERS.includes(supply) ? supply : "",
    receipt: params.get("receipt") === "pending" ? "pending" : "",
    status: Object.prototype.hasOwnProperty.call(PAYMENT_STATUS_LABELS, status) ? status : "",
  };
}

/** One spelling per query (with or without «?»), to tell whether the address really changed. */
export function addressQueryKey(search: string): string {
  return new URLSearchParams(search).toString();
}
