import type { Consent } from "../../consent";
import type { Item, PageContext, PurchaseSender } from "../events";
import { safeSearchTerm } from "../privacy";
import { cookie, type ProviderAdapter } from "./types";

/**
 * TikTok Pixel.
 *   business-api.tiktok.com/portal/docs?id=1739585702922241   (install with code, standard events)
 *   business-api.tiktok.com/portal/docs?id=1771100965992450   (deduplication with Events API)
 *
 * The purchase event is `Purchase`: TikTok's current list of standard events has
 * no `CompletePayment`. NO `ttq.identify`: no e-mail, phone or external id.
 */
const SCRIPT = "https://analytics.tiktok.com/i18n/pixel/events.js";
const PIXEL_CODE = /^[A-Z0-9]{16,24}$/;
const METHODS = [
  "page", "track", "identify", "instances", "debug", "on", "off", "once", "ready", "alias", "group",
  "enableCookie", "disableCookie", "holdConsent", "revokeConsent", "grantConsent",
];

type Ttq = unknown[] & Record<string, unknown> & {
  methods?: string[];
  page: () => void;
  track: (name: string, params?: Record<string, unknown>, options?: Record<string, unknown>) => void;
  enableCookie: () => void;
  disableCookie: () => void;
  grantConsent: () => void;
  revokeConsent: () => void;
};

declare global {
  interface Window {
    ttq?: Ttq;
    TiktokAnalyticsObject?: string;
  }
}

const contents = (items: Item[]) => ({
  content_type: "product",
  contents: items.map((value) => ({
    content_id: value.id, content_name: value.name,
    ...(value.category ? { content_category: value.category } : {}),
    ...(value.price !== undefined ? { price: value.price } : {}),
    quantity: value.quantity ?? 1,
  })),
});

export function createTikTokAdapter(pixelCode: string, purchase: PurchaseSender): ProviderAdapter | null {
  if (!PIXEL_CODE.test(pixelCode)) return null;
  // Both of TikTok's switches: its consent state, and its first-party cookie.
  const cookies = (consent: Consent) => {
    if (consent.marketing) {
      window.ttq?.grantConsent();
      window.ttq?.enableCookie();
    } else {
      window.ttq?.revokeConsent();
      window.ttq?.disableCookie();
    }
  };

  return {
    id: "tiktok",
    consent: "marketing",
    purchase,

    load(consent) {
      if (!window.ttq) {
        // TikTok's own base code: every method queues its call until the script arrives.
        window.TiktokAnalyticsObject = "ttq";
        const ttq = [] as unknown as Ttq;
        ttq.methods = METHODS;
        for (const method of METHODS) {
          (ttq as Record<string, unknown>)[method] = (...args: unknown[]) => { ttq.push([method, ...args]); };
        }
        ttq._i = { [pixelCode]: Object.assign([], { _u: SCRIPT }) };
        ttq._t = { [pixelCode]: Date.now() };
        ttq._o = { [pixelCode]: {} };
        window.ttq = ttq;
        const script = document.createElement("script");
        script.type = "text/javascript";
        script.async = true;
        script.src = `${SCRIPT}?sdkid=${encodeURIComponent(pixelCode)}&lib=ttq`;
        document.head.appendChild(script);
      }
      cookies(consent);
    },

    updateConsent(consent) {
      cookies(consent);
    },

    track(event, _page: PageContext, eventId) {
      const send = (name: string, params: Record<string, unknown> = {}) => window.ttq?.track(name, params, { event_id: eventId });
      switch (event.name) {
        case "PAGE_VIEW":
          return window.ttq?.page();
        case "VIEW_ITEM":
          return send("ViewContent", { ...contents([event.item]), currency: event.currency, value: event.value });
        case "SEARCH":
          return send("Search", { search_string: safeSearchTerm(event.term) });
        case "ADD_TO_CART":
          return send("AddToCart", { ...contents([event.item]), currency: event.currency, value: event.value });
        case "BEGIN_CHECKOUT":
          return send("InitiateCheckout", { ...contents(event.items), currency: event.currency, value: event.value });
        case "ADD_PAYMENT_INFO":
          return send("AddPaymentInfo", { ...contents(event.items), currency: event.currency, value: event.value });
        case "PURCHASE":
          return send("Purchase", { ...contents(event.items), currency: event.currency, value: event.value, order_id: event.transactionId });
        case "SIGN_UP":
          return send("CompleteRegistration");
        case "LEAD":
          return send("Lead");
        case "CONTACT":
          return send("Contact");
        default:
          // TikTok has no standard web event for a list view, a selection, a removal, the cart, shipping or a login.
          return undefined;
      }
    },

    identifiers() {
      const ttp = cookie("_ttp");
      const ttclid = cookie("ttclid");
      return { ...(ttp ? { ttp } : {}), ...(ttclid ? { ttclid } : {}) };
    },
  };
}
