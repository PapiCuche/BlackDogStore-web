import type { Consent } from "../../consent";
import type { Item, PurchaseSender } from "../events";
import { safeReferrer, safeSearchTerm } from "../privacy";
import { cookie, loadScript, type ProviderAdapter } from "./types";

/**
 * Google Analytics 4 (gtag.js).
 *   developers.google.com/analytics/devguides/collection/ga4
 *   developers.google.com/tag-platform/security/guides/consent
 */
const SCRIPT = "https://www.googletagmanager.com/gtag/js";
const MEASUREMENT_ID = /^G-[A-Z0-9]{4,20}$/;

declare global {
  interface Window {
    dataLayer?: unknown[];
    gtag?: (...args: unknown[]) => void;
  }
}

const item = (value: Item, extra: Record<string, unknown> = {}) => ({
  item_id: value.id, item_name: value.name,
  ...(value.category ? { item_category: value.category } : {}),
  ...(value.price !== undefined ? { price: value.price } : {}),
  ...(value.quantity !== undefined ? { quantity: value.quantity } : {}),
  ...extra,
});

/** Google's consent signals, from the two categories the shop asks about. */
const signals = (consent: Consent) => ({
  analytics_storage: consent.analytics ? "granted" : "denied",
  ad_storage: consent.marketing ? "granted" : "denied",
  ad_user_data: consent.marketing ? "granted" : "denied",
  ad_personalization: consent.marketing ? "granted" : "denied",
});

export function createGa4Adapter(measurementId: string, purchase: PurchaseSender): ProviderAdapter | null {
  if (!MEASUREMENT_ID.test(measurementId)) return null;
  const gtag = (...args: unknown[]) => window.gtag?.(...args);

  return {
    id: "google_analytics",
    consent: "analytics",
    purchase,

    load(consent) {
      window.dataLayer = window.dataLayer ?? [];
      // gtag.js reads `arguments` objects from the data layer, not arrays.
      window.gtag = window.gtag ?? function gtagShim() {
        // eslint-disable-next-line prefer-rest-params
        window.dataLayer?.push(arguments);
      };
      (window as unknown as Record<string, boolean>)[`ga-disable-${measurementId}`] = false;
      // Everything denied first, then what was actually accepted: no hit can leave before it.
      gtag("consent", "default", signals({ analytics: false, marketing: false }));
      gtag("consent", "update", signals(consent));
      gtag("js", new Date());
      // No automatic page view: the shop says its own, with an address it has checked.
      gtag("config", measurementId, { send_page_view: false });
      loadScript(`${SCRIPT}?id=${encodeURIComponent(measurementId)}`);
    },

    updateConsent(consent) {
      // Google's own switch for «send nothing more from this property».
      (window as unknown as Record<string, boolean>)[`ga-disable-${measurementId}`] = !consent.analytics;
      gtag("consent", "update", signals(consent));
    },

    track(event, page) {
      const send = (name: string, params: Record<string, unknown> = {}) => gtag("event", name, {
        ...params, page_location: page.location, page_path: page.path, send_to: measurementId,
      });
      switch (event.name) {
        case "PAGE_VIEW":
          return send("page_view", { page_title: page.title, page_referrer: safeReferrer(document.referrer) });
        case "VIEW_ITEM_LIST":
          return send("view_item_list", {
            item_list_name: event.listName,
            items: event.items.map((value, index) => item(value, { index, item_list_name: event.listName })),
          });
        case "SELECT_ITEM":
          return send("select_item", { item_list_name: event.listName, items: [item(event.item)] });
        case "VIEW_ITEM":
          return send("view_item", { currency: event.currency, value: event.value, items: [item(event.item)] });
        case "SEARCH":
          return send("search", { search_term: safeSearchTerm(event.term) });
        case "ADD_TO_CART":
          return send("add_to_cart", { currency: event.currency, value: event.value, items: [item(event.item)] });
        case "REMOVE_FROM_CART":
          return send("remove_from_cart", { currency: event.currency, value: event.value, items: [item(event.item)] });
        case "VIEW_CART":
          return send("view_cart", { currency: event.currency, value: event.value, items: event.items.map((v) => item(v)) });
        case "BEGIN_CHECKOUT":
          return send("begin_checkout", basket(event));
        case "ADD_SHIPPING_INFO":
          return send("add_shipping_info", { ...basket(event), shipping_tier: event.shippingTier });
        case "ADD_PAYMENT_INFO":
          return send("add_payment_info", { ...basket(event), payment_type: event.paymentType });
        case "PURCHASE":
          return send("purchase", {
            ...basket(event), transaction_id: event.transactionId,
            ...(event.tax !== undefined ? { tax: event.tax } : {}),
          });
        case "SIGN_UP":
          return send("sign_up", { method: event.method });
        case "LOGIN":
          return send("login", { method: event.method });
        case "LEAD":
          return send("generate_lead", { lead_source: event.source });
        case "CONTACT":
          // GA4 has no «contact» among its recommended events: reaching out is a lead.
          return send("generate_lead", { lead_source: event.channel });
      }
    },

    identifiers() {
      // `_ga` is GA1.1.<client id>; the stream's own cookie carries the session id.
      const client = cookie("_ga").split(".").slice(-2).join(".");
      const session = cookie(`_ga_${measurementId.slice(2)}`);
      const sessionId = session.match(/^GS\d\.\d\.s?(\d+)/)?.[1] ?? "";
      return {
        ...(/^\d+\.\d+$/.test(client) ? { ga_client_id: client } : {}),
        ...(sessionId ? { ga_session_id: sessionId } : {}),
      };
    },
  };
}

function basket(event: { currency: string; value: number; items: Item[]; coupon?: string }) {
  return {
    currency: event.currency, value: event.value, items: event.items.map((value) => item(value)),
    ...(event.coupon ? { coupon: event.coupon } : {}),
  };
}
