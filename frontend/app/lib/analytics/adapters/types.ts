import type { Consent, ConsentCategory } from "../../consent";
import type { AnalyticsEvent, PageContext, PurchaseSender } from "../events";

/**
 * One measurement provider, as the shop's analytics service sees it.
 *
 * AN ADAPTER IS THE ONLY PLACE ITS PROVIDER'S SCRIPT IS NAMED. The address the
 * script is loaded from is a constant of the adapter — it does not come from the
 * server, from the console or from an environment variable. What comes from the
 * server is an identifier, and the adapter checks its shape again before it
 * puts it anywhere near a URL.
 */
export interface ProviderAdapter {
  /** The id this provider has in the integrations console. */
  readonly id: "google_analytics" | "meta" | "tiktok";
  /** What a visitor must have accepted for this provider to exist on the page. */
  readonly consent: ConsentCategory;
  /** Who sends the purchase. The browser's copy is skipped when the server sends it alone. */
  readonly purchase: PurchaseSender;
  /** Put the provider's script on the page and initialise it. Called once, and only with consent. */
  load(consent: Consent): void;
  /** The visitor changed their mind (either way) after the script was loaded. */
  updateConsent(consent: Consent): void;
  /** Translate one of the shop's events for this provider, or ignore it. */
  track(event: AnalyticsEvent, page: PageContext, eventId: string): void;
  /** The identifiers this provider's own script left in this browser, for the server's copy of a purchase. */
  identifiers(): Record<string, string>;
}

/** Add an async script. The only way a third-party script enters the page. */
export function loadScript(src: string): void {
  if (document.querySelector(`script[src="${src}"]`)) return;
  const script = document.createElement("script");
  script.async = true;
  script.src = src;
  document.head.appendChild(script);
}

export function cookie(name: string): string {
  const match = document.cookie.match(new RegExp(`(?:^|;\\s*)${name.replace(/[.$?*|{}()[\]\\/+^]/g, "\\$&")}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : "";
}
