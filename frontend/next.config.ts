import type { NextConfig } from "next";

/**
 * Image Optimization — FASE 0.3 / P0-A.
 *
 * WHAT WAS HERE, AND WHY IT MATTERED
 * ----------------------------------
 * This file used to say:
 *
 *     { protocol: "https", hostname: "**" }
 *
 * Next's own documentation for this version is explicit that `**` matches any
 * number of subdomains at the beginning, and that omitting `pathname` and
 * `search` implies a wildcard for those too — "this is not recommended because
 * it may allow malicious actors to optimize urls you did not intend".
 *
 * The app renders product images through `next/image` (ProductCard,
 * ProductDetail), so `/_next/image` is a live optimizer. Combined, that made
 * the endpoint an OPEN IMAGE PROXY: anyone could ask this server to fetch any
 * HTTPS URL on the internet, with our IP as the client and our bandwidth paying
 * for it. It is also the reachable surface for the Image Optimization DoS fixed
 * in Next 16.2.11.
 *
 * WHY AN ENV VAR AND NOT A HARDCODED LIST
 * ---------------------------------------
 * `Product.image_url` is a URLField that each TENANT fills in. This is a
 * multi-tenant platform, so the set of legitimate image hosts is a deployment
 * fact, not something that belongs in source — and hardcoding the pilot's hosts
 * here is exactly the "no hardcodees Black Dog Store" rule.
 *
 * WHY NO WILDCARD FALLBACK
 * ------------------------
 * An unset variable yields the local development hosts and nothing else. A
 * missing configuration therefore breaks a remote image visibly — a 400 on that
 * one image, which somebody notices and fixes — instead of silently restoring
 * the open proxy. Failing closed is the whole point of the change.
 */

function remoteImagePatterns() {
  // Comma-separated hostnames. A leading `*.` is allowed for subdomains of a
  // domain you control (e.g. `*.cdn.example.com`); a bare `**` is not, because
  // that is the configuration this change exists to remove.
  const raw = process.env.NEXT_PUBLIC_IMAGE_HOSTS ?? "";
  const hosts = raw
    .split(",")
    .map((h) => h.trim())
    .filter(Boolean)
    .filter((h) => h !== "*" && h !== "**");

  return [
    ...hosts.map((hostname) => ({ protocol: "https" as const, hostname })),
    // Local development only. These are unreachable from the internet, so they
    // are not an open-proxy surface.
    { protocol: "http" as const, hostname: "localhost" },
    { protocol: "http" as const, hostname: "127.0.0.1" },
  ];
}

/**
 * Security response headers — FE-AUTH-04.
 *
 * The frontend sent none. Two things were concretely open: the storefront and
 * the panel could be framed by another site (clickjacking against a signed-in
 * panel), and the pages that receive a one-time token in the URL handed it over
 * in `Referer` to any external resource they loaded.
 *
 * The Content-Security-Policy here is deliberately narrow: who may frame us and
 * where `<base>` may point. A script or style policy would need a nonce on
 * every inline script Next emits and is a change of its own. There is no
 * `object-src` either: the till prints its ticket as a PDF inside a hidden
 * frame, a document created by this page inherits its policy, and forbidding
 * objects can stop the browser's PDF viewer.
 *
 * In the production topology the reverse proxy adds its own headers; these also
 * hold when Next is published without one in front.
 */
const SECURITY_HEADERS = [
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Content-Security-Policy", value: "frame-ancestors 'none'; base-uri 'self'" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  // The app uses none of these. Giving them up stops a third-party script (the
  // payment SDK, say) from asking for them in the shop's name. Evidence photos
  // come from a file input, which this does not affect. `payment` is left
  // alone on purpose: the gateway's SDK may need it.
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

// Pages whose URL carries a one-time token. Listed AFTER the general rule:
// when two rules set the same header, the last one wins.
const TOKEN_IN_URL_PAGES = ["/auth/verify-email", "/auth/reset-password", "/invitacion"];

const nextConfig: NextConfig = {
  async headers() {
    return [
      { source: "/:path*", headers: SECURITY_HEADERS },
      ...TOKEN_IN_URL_PAGES.map((source) => ({
        source,
        headers: [{ key: "Referrer-Policy", value: "no-referrer" }],
      })),
    ];
  },
  // No rewrites needed — /api/* is handled by app/api/[...path]/route.ts (Route Handler proxy).
  images: {
    remotePatterns: remoteImagePatterns(),
    // SVGs are not rasterised safely by the optimizer and were the vector for
    // the Image Optimization DoS. Nothing in this catalogue needs them.
    dangerouslyAllowSVG: false,
  },
};

export default nextConfig;
