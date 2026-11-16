import { headers } from "next/headers";

/**
 * SEO-01 — the origin («https://host») of THIS storefront, from the request: a multi-tenant site never hardcodes a
 * domain. Caddy forwards the Host it routed (only the configured domains reach Next), and the value is still checked
 * to be a plain host name before it is written into a canonical URL, a sitemap or an image address.
 * `fallback` is the company's `website_url`, used only when the request names no host.
 */
export async function siteOrigin(fallback?: string): Promise<string | null> {
  let host = "";
  let proto = "";
  try {
    const h = await headers();
    // Only `Host`: Caddy sets it to the domain it routed. `X-Forwarded-Host` is never trusted (review of SEO-01).
    host = (h.get("host") ?? "").trim().toLowerCase().replace(/\.(?=:|$)/, "");
    proto = (h.get("x-forwarded-proto") ?? "").split(",")[0].trim().toLowerCase();
  } catch {
    host = "";
  }
  if (host && /^[a-z0-9.-]+(:\d{1,5})?$/.test(host)) {
    const local = /^(localhost|127\.0\.0\.1|backend|frontend)(:|$)/.test(host);
    return `${proto === "http" || (local && proto !== "https") ? "http" : "https"}://${host}`;
  }
  if (fallback && /^https?:\/\/[^/\s]+/i.test(fallback)) {
    try {
      return new URL(fallback).origin;
    } catch {
      return null;
    }
  }
  return null;
}

/**
 * Routes a crawler never fetches. Only these two: the other private routes carry `noindex` in their layout, and a
 * crawler must be ALLOWED to fetch them to read it (review of SEO-01).
 */
export const PRIVATE_ROUTES = ["/admin", "/api"];
