/**
 * The shop's analytics service: the ONE thing pages talk to.
 *
 *     track({ name: "ADD_TO_CART", item, currency, value })
 *
 * What it guarantees, in this order:
 *
 *   1. CONSENT DECIDES. A provider's script is loaded only when its category was
 *      accepted, and an event reaches only providers whose category is accepted
 *      at that moment. Before an answer, and after a refusal, nothing is loaded
 *      and nothing is sent. Withdrawing consent stops the next event.
 *   2. NO PROVIDER ON A PRIVATE ADDRESS. Where the address itself is private —
 *      a repair's tracking link, a password reset, the panel — no script is
 *      loaded and no event is sent (`privacy.ts`).
 *   3. IT CANNOT BREAK THE SHOP. Every call into a provider is wrapped: a script
 *      that failed to load, or throws, costs a measurement and nothing else.
 *   4. A PAGE IS VIEWED ONCE, AND A SALE IS BOUGHT ONCE. Re-renders do not repeat
 *      a page view; a purchase is emitted once per order in this browser, and
 *      not at all for a provider whose server sends it alone.
 *
 * Which providers exist comes from the server at run time
 * (`/api/measurement/config/`): changing an ID in the console changes the next
 * visit, with no rebuild.
 */

import { currentConsent, NO_CONSENT, type Consent } from "../consent";
import { createGa4Adapter } from "./adapters/ga4";
import { createMetaAdapter } from "./adapters/meta";
import { createTikTokAdapter } from "./adapters/tiktok";
import type { ProviderAdapter } from "./adapters/types";
import type { AnalyticsEvent, MeasurementConfig, PageContext } from "./events";
import { mayMeasure, pageLocation, sanitizePath } from "./privacy";

const PURCHASED = "bd.purchase.";
const MAX_QUEUE = 20;

let adapters: ProviderAdapter[] | null = null;      // null until the server said what exists
let consent: Consent = NO_CONSENT;
let loaded = new Set<string>();
let queue: AnalyticsEvent[] = [];
let lastPage = "";

function here(): { pathname: string; search: string } {
  return { pathname: window.location.pathname, search: window.location.search };
}

function page(): PageContext {
  const { pathname } = here();
  return { location: pageLocation(window.location.origin, pathname), path: sanitizePath(pathname), title: document.title };
}

function newEventId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}.${Math.random().toString(36).slice(2)}`;
}

/** Never lets a provider's failure become the shop's. */
function guarded(action: () => void): void {
  try {
    action();
  } catch {
    // A measurement was lost. That is all that may happen here.
  }
}

function allowed(adapter: ProviderAdapter): boolean {
  return consent[adapter.consent] === true;
}

/** Bring in the scripts of the providers that are allowed here and now, and tell the others the answer. */
function reconcile(): void {
  if (!adapters) return;
  const { pathname, search } = here();
  for (const adapter of adapters) {
    if (allowed(adapter) && !loaded.has(adapter.id)) {
      if (!mayMeasure(pathname, search)) continue;          // not on this address; maybe on the next one
      guarded(() => adapter.load(consent));
      loaded.add(adapter.id);
    } else if (loaded.has(adapter.id)) {
      guarded(() => adapter.updateConsent(consent));        // a script cannot be unloaded: it is told to stop
    }
  }
}

function alreadyPurchased(eventId: string): boolean {
  try {
    if (window.localStorage.getItem(PURCHASED + eventId)) return true;
    window.localStorage.setItem(PURCHASED + eventId, "1");
  } catch {
    // Without storage the provider's own deduplication by event id still holds.
  }
  return false;
}

function dispatch(event: AnalyticsEvent): void {
  if (!adapters) return;
  const { pathname, search } = here();
  if (!mayMeasure(pathname, search)) return;
  if (event.name === "PAGE_VIEW") {
    const key = sanitizePath(pathname);
    if (key === lastPage) return;                           // a re-render, not a visit
    lastPage = key;
  }
  if (event.name === "PURCHASE" && alreadyPurchased(event.eventId)) return;

  // One id per event, shared by every provider: the server's copy of a purchase carries the same one.
  const eventId = event.name === "PURCHASE" ? event.eventId : newEventId();
  const context = page();
  for (const adapter of adapters) {
    if (!allowed(adapter) || !loaded.has(adapter.id)) continue;
    if (event.name === "PURCHASE" && adapter.purchase === "server") continue;      // the server sends it alone
    guarded(() => adapter.track(event, context, eventId));
  }
}

// -- what the rest of the shop calls ----------------------------------------------------

/** Say that something happened. Safe to call anywhere, any number of times, with or without consent. */
export function track(event: AnalyticsEvent): void {
  if (typeof window === "undefined") return;
  if (!adapters) {
    // The server has not answered yet. Kept briefly, and still subject to consent when it does.
    if (queue.length < MAX_QUEUE) queue.push(event);
    return;
  }
  dispatch(event);
}

/** The server said which providers exist. Called once per page load by `AnalyticsProvider`. */
export function configure(config: MeasurementConfig): void {
  const providers = config?.providers ?? {};
  adapters = [
    providers.google_analytics && createGa4Adapter(providers.google_analytics.measurement_id, providers.google_analytics.purchase),
    providers.meta && createMetaAdapter(providers.meta.pixel_id, providers.meta.purchase),
    providers.tiktok && createTikTokAdapter(providers.tiktok.pixel_code, providers.tiktok.purchase),
  ].filter((adapter): adapter is ProviderAdapter => Boolean(adapter));
  consent = currentConsent();
  reconcile();
  const waiting = queue;
  queue = [];
  waiting.forEach(dispatch);
}

/** The visitor answered, or changed their answer. */
export function setConsent(next: Consent): void {
  const before = consent;
  consent = { analytics: next.analytics === true, marketing: next.marketing === true };
  reconcile();
  if (!adapters) return;
  // A provider that has just been allowed learns of the page the visitor is on. Nothing earlier.
  const newly = adapters.filter((adapter) => allowed(adapter) && !before[adapter.consent] && loaded.has(adapter.id));
  if (newly.length && mayMeasure(here().pathname, here().search)) {
    const context = page();
    const eventId = newEventId();
    newly.forEach((adapter) => guarded(() => adapter.track({ name: "PAGE_VIEW" }, context, eventId)));
    lastPage = sanitizePath(here().pathname);
  }
}

/** The route changed. Loads what could not be loaded on a private address, then counts the view. */
export function pageView(): void {
  if (typeof window === "undefined") return;
  reconcile();
  track({ name: "PAGE_VIEW" });
}

/**
 * What travels with a checkout so that the server's copy of the purchase follows
 * this visitor's answer: the consent itself, and — only for accepted categories —
 * the identifiers the providers' own scripts left in this browser.
 */
export function checkoutContext(): { consent: Consent } & Record<string, unknown> {
  const context: { consent: Consent } & Record<string, unknown> = { consent: { ...consent } };
  for (const adapter of adapters ?? []) {
    if (!allowed(adapter) || !loaded.has(adapter.id)) continue;
    guarded(() => Object.assign(context, adapter.identifiers()));
  }
  return context;
}

/** For tests: forget everything this module learnt. */
export function resetAnalyticsForTests(): void {
  adapters = null;
  consent = NO_CONSENT;
  loaded = new Set();
  queue = [];
  lastPage = "";
}
