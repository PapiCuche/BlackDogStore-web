/**
 * INV-RECEIVE-PRICE-03 — «Equipos seminuevos»: three authorities, apart. One never implies another, and the server
 * checks each again on every change:
 *
 *   · `inventory.adjust`  the device's data and photos (battery, cosmetic grade, details for the buyer);
 *   · `products.manage`   its SALE PRICE — the catalogue's decision. The price lives on the device's ficha, whose
 *                         route needs `inventory.adjust` too, so the price is editable only with both;
 *   · `costs.view`        its acquisition cost, shown apart and never edited on that screen.
 */
import type { InternalAccess } from "./internal-access";

export function usedUnitsAccess(access: Pick<InternalAccess, "can">) {
  const canEdit = access.can("inventory.adjust", ["inventory", "admin", "superadmin"]);
  return {
    canEdit,
    canManagePrice: canEdit && access.can("products.manage", ["admin", "superadmin"]),
    canSeeCost: access.can("costs.view", ["admin", "superadmin"]),
  };
}

/** The filters a link may preselect (`?product=<id>&precio=sin|con`). Anything else in the address is ignored. */
export function usedUnitsLinkFilters(search: string): { product: string; priced: "" | "yes" | "no" } {
  const params = new URLSearchParams(search);
  const product = (params.get("product") ?? "").trim();
  const precio = (params.get("precio") ?? "").trim();
  return {
    product: /^\d{1,10}$/.test(product) ? product : "",
    priced: precio === "sin" ? "no" : precio === "con" ? "yes" : "",
  };
}

/** The link to the devices of one article, optionally only those without a price. */
export const usedUnitsHref = (productId: number, onlyUnpriced = false) =>
  `/admin/inventory/used?product=${productId}${onlyUnpriced ? "&precio=sin" : ""}`;
