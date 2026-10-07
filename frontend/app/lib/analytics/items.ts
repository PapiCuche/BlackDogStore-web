import type { Item } from "./events";

/**
 * The shop's products and carts, as events may describe them: an id, a name, a
 * category, a price and a quantity. Whatever else a product record carries —
 * and a serialised unit carries an IMEI and a serial number — has no field to
 * go into.
 */

/** The currency every price in this shop is quoted in when a screen has not been told another. */
export const DEFAULT_CURRENCY = "PEN";

type ProductLike = {
  id: number | string;
  name: string;
  price?: number | string | null;
  category?: { name?: string | null } | string | null;
};

export function toItem(product: ProductLike, quantity?: number): Item {
  const category = typeof product.category === "string" ? product.category : product.category?.name;
  const price = product.price === undefined || product.price === null ? undefined : Number(product.price);
  return {
    id: String(product.id),
    name: product.name,
    ...(category ? { category } : {}),
    ...(price !== undefined && Number.isFinite(price) ? { price } : {}),
    ...(quantity !== undefined ? { quantity } : {}),
  };
}

type LineLike = { quantity: number; product: ProductLike };

export const toItems = (lines: LineLike[]): Item[] => lines.map((line) => toItem(line.product, line.quantity));

export const lineTotal = (lines: LineLike[]): number =>
  Math.round(lines.reduce((total, line) => total + Number(line.product.price ?? 0) * line.quantity, 0) * 100) / 100;
