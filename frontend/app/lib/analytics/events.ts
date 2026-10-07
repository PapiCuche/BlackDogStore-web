/**
 * The shop's OWN vocabulary of what happens in it.
 *
 * Pages and components say one of these. They do not say `gtag`, `fbq` or
 * `ttq`, and they do not know which providers exist: each adapter translates
 * (or ignores) an event for its provider.
 *
 * WHAT AN EVENT MAY CARRY IS WRITTEN HERE, AS TYPES. A product is an id, a
 * name, a category, a price and a quantity. There is no field for a person —
 * no name, e-mail, phone, document or address — and none for an IMEI, a serial
 * number or a token, so none can be sent by mistake.
 */

export type Item = {
  /** The product's id in the shop. */
  id: string;
  name: string;
  category?: string;
  price?: number;
  quantity?: number;
};

type Money = { currency: string; value: number };
type Basket = Money & { items: Item[]; coupon?: string };

export type AnalyticsEvent =
  | { name: "PAGE_VIEW" }
  | { name: "VIEW_ITEM_LIST"; listName: string; items: Item[] }
  | { name: "SELECT_ITEM"; listName: string; item: Item }
  | ({ name: "VIEW_ITEM"; item: Item } & Money)
  | { name: "SEARCH"; term: string }
  | ({ name: "ADD_TO_CART"; item: Item } & Money)
  | ({ name: "REMOVE_FROM_CART"; item: Item } & Money)
  | ({ name: "VIEW_CART" } & Basket)
  | ({ name: "BEGIN_CHECKOUT" } & Basket)
  | ({ name: "ADD_SHIPPING_INFO"; shippingTier: string } & Basket)
  | ({ name: "ADD_PAYMENT_INFO"; paymentType: string } & Basket)
  | ({ name: "PURCHASE"; eventId: string; transactionId: string; tax?: number } & Basket)
  | { name: "SIGN_UP"; method: string }
  | { name: "LOGIN"; method: string }
  | { name: "LEAD"; source: string }
  | { name: "CONTACT"; channel: "whatsapp" | "email" | "phone" };

export type AnalyticsEventName = AnalyticsEvent["name"];

/** Every event the shop can say. A test checks each adapter decides about each of them. */
export const EVENT_NAMES: AnalyticsEventName[] = [
  "PAGE_VIEW", "VIEW_ITEM_LIST", "SELECT_ITEM", "VIEW_ITEM", "SEARCH", "ADD_TO_CART", "REMOVE_FROM_CART",
  "VIEW_CART", "BEGIN_CHECKOUT", "ADD_SHIPPING_INFO", "ADD_PAYMENT_INFO", "PURCHASE", "SIGN_UP", "LOGIN",
  "LEAD", "CONTACT",
];

/** Where the page is, as it may be told to a provider: see `privacy.ts`. */
export type PageContext = { location: string; path: string; title: string };

/** What the server says the storefront may measure with (GET /api/measurement/config/). */
export type MeasurementConfig = {
  providers: {
    google_analytics?: { measurement_id: string; purchase: PurchaseSender };
    meta?: { pixel_id: string; purchase: PurchaseSender };
    tiktok?: { pixel_code: string; purchase: PurchaseSender };
  };
};

/** Who sends the purchase: the browser, the server, or both with a shared event id. */
export type PurchaseSender = "browser" | "server" | "both";
