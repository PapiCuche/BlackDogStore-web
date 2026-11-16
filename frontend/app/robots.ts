import type { MetadataRoute } from "next";
import { PRIVATE_ROUTES, siteOrigin } from "./lib/site-origin";
import { fetchStorefrontConfig } from "./lib/storefront";

/** SEO-01 — robots.txt of THIS storefront: the shop is open, the private routes are not, and the sitemap is named. */
export default async function robots(): Promise<MetadataRoute.Robots> {
  const config = await fetchStorefrontConfig();
  const origin = await siteOrigin(config.contact?.website_url);
  return {
    rules: [{ userAgent: "*", allow: "/", disallow: PRIVATE_ROUTES.map((route) => `${route}/`).concat(PRIVATE_ROUTES) }],
    ...(origin ? { sitemap: `${origin}/sitemap.xml` } : {}),
  };
}
