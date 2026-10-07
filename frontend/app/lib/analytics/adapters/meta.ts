import type { Item, PageContext, PurchaseSender } from "../events";
import { safeSearchTerm } from "../privacy";
import { cookie, loadScript, type ProviderAdapter } from "./types";

/**
 * Meta Pixel.
 *   developers.facebook.com/docs/meta-pixel
 *   developers.facebook.com/docs/marketing-api/conversions-api/deduplicate-pixel-and-server-events
 *
 * NO ADVANCED MATCHING: `init` is called with the pixel id and nothing else — no
 * e-mail, no phone, hashed or not. And `autoConfig` is off: left on, the pixel
 * reads the page's buttons and form fields by itself to «enrich» events.
 */
const SCRIPT = "https://connect.facebook.net/en_US/fbevents.js";
const PIXEL_ID = /^\d{10,20}$/;

type Fbq = ((...args: unknown[]) => void) & {
  callMethod?: (...args: unknown[]) => void;
  queue: unknown[]; push: unknown; loaded: boolean; version: string; disablePushState?: boolean;
};

declare global {
  interface Window {
    fbq?: Fbq;
    _fbq?: Fbq;
  }
}

const contents = (items: Item[]) => ({
  content_type: "product",
  content_ids: items.map((value) => value.id),
  contents: items.map((value) => ({ id: value.id, quantity: value.quantity ?? 1, ...(value.price !== undefined ? { item_price: value.price } : {}) })),
});

export function createMetaAdapter(pixelId: string, purchase: PurchaseSender): ProviderAdapter | null {
  if (!PIXEL_ID.test(pixelId)) return null;
  const fbq = (...args: unknown[]) => window.fbq?.(...args);

  return {
    id: "meta",
    consent: "marketing",
    purchase,

    load(consent) {
      if (!window.fbq) {
        // Meta's own bootstrap: a queue that the real script drains when it arrives.
        const queue = function fbqShim(...args: unknown[]) {
          if (queue.callMethod) queue.callMethod(...args);
          else queue.queue.push(args);
        } as Fbq;
        queue.push = queue;
        queue.loaded = true;
        queue.version = "2.0";
        queue.queue = [];
        window.fbq = queue;
        window._fbq = window._fbq ?? queue;
      }
      // The pixel would otherwise send a PageView with the raw address on every route change.
      window.fbq.disablePushState = true;
      fbq("consent", consent.marketing ? "grant" : "revoke");
      fbq("set", "autoConfig", false, pixelId);
      fbq("init", pixelId);
      loadScript(SCRIPT);
    },

    updateConsent(consent) {
      fbq("consent", consent.marketing ? "grant" : "revoke");
    },

    track(event, _page: PageContext, eventId) {
      // The fourth argument is how the server's copy of an event is recognised as the same one.
      const send = (name: string, params: Record<string, unknown> = {}) => fbq("track", name, params, { eventID: eventId });
      switch (event.name) {
        case "PAGE_VIEW":
          return send("PageView");
        case "VIEW_ITEM":
          return send("ViewContent", { ...contents([event.item]), content_name: event.item.name, currency: event.currency, value: event.value });
        case "SEARCH":
          return send("Search", { search_string: safeSearchTerm(event.term) });
        case "ADD_TO_CART":
          return send("AddToCart", { ...contents([event.item]), currency: event.currency, value: event.value });
        case "BEGIN_CHECKOUT":
          return send("InitiateCheckout", { ...contents(event.items), currency: event.currency, value: event.value, num_items: count(event.items) });
        case "ADD_PAYMENT_INFO":
          return send("AddPaymentInfo", { ...contents(event.items), currency: event.currency, value: event.value });
        case "PURCHASE":
          return send("Purchase", { ...contents(event.items), currency: event.currency, value: event.value, order_id: event.transactionId, num_items: count(event.items) });
        case "SIGN_UP":
          return send("CompleteRegistration");
        case "LEAD":
          return send("Lead");
        case "CONTACT":
          return send("Contact");
        default:
          // Meta has no standard event for a list view, a selection, a removal, the cart, shipping or a login.
          return undefined;
      }
    },

    identifiers() {
      const fbp = cookie("_fbp");
      const fbc = cookie("_fbc");
      return { ...(fbp ? { fbp } : {}), ...(fbc ? { fbc } : {}) };
    },
  };
}

const count = (items: Item[]) => items.reduce((total, value) => total + (value.quantity ?? 1), 0);
