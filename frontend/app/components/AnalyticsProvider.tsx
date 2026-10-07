"use client";

/**
 * Connects the shop to its analytics service. Renders nothing.
 *
 *   · asks the server, once per page load, which measurement providers a master
 *     activated (their PUBLIC ids — nothing else is in that answer);
 *   · keeps the service told of the visitor's consent;
 *   · reports a page view when the route changes.
 *
 * No provider is named here, and none is loaded here: `lib/analytics/service`
 * decides that, from consent.
 */

import { usePathname } from "next/navigation";
import { useEffect } from "react";

import { configure, pageView, setConsent, track } from "../lib/analytics/service";
import type { MeasurementConfig } from "../lib/analytics/events";
import { API_BASE } from "../lib/api";
import { subscribeConsent } from "../lib/consent";

/** Which way of reaching the shop a link is. Only the scheme and the host are looked at. */
export function contactChannel(href: string): "whatsapp" | "email" | "phone" | null {
  if (/^https:\/\/(wa\.me|api\.whatsapp\.com)\//i.test(href)) return "whatsapp";
  if (/^mailto:/i.test(href)) return "email";
  if (/^tel:/i.test(href)) return "phone";
  return null;
}

export function AnalyticsProvider() {
  const pathname = usePathname();

  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE}/measurement/config/`, { credentials: "omit" })
      .then((response) => (response.ok ? response.json() : { providers: {} }))
      .catch(() => ({ providers: {} }))
      .then((config: MeasurementConfig) => { if (!cancelled) configure(config); });
    const unsubscribe = subscribeConsent(setConsent);
    return () => { cancelled = true; unsubscribe(); };
  }, []);

  useEffect(() => {
    pageView();
  }, [pathname]);

  useEffect(() => {
    // Reaching out — WhatsApp, e-mail, phone — is one event wherever the link is:
    // the footer, the floating button, a product page. Read from the link itself.
    const contacted = (event: MouseEvent) => {
      const href = (event.target as Element | null)?.closest?.("a[href]")?.getAttribute("href") ?? "";
      const channel = contactChannel(href);
      if (channel) track({ name: "CONTACT", channel });
    };
    document.addEventListener("click", contacted, true);
    return () => document.removeEventListener("click", contacted, true);
  }, []);

  return null;
}
