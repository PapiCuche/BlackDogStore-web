import type { MetadataRoute } from "next";
import { apiUrl } from "./lib/api";
import { siteOrigin } from "./lib/site-origin";
import { fetchStorefrontConfig } from "./lib/storefront";

/**
 * SEO-01 — sitemap.xml of THIS storefront: its public pages, its published products and its models, read from the
 * same public API the shop uses (nothing invented, nothing private). A failed read leaves those entries out; it
 * never breaks the sitemap.
 */
const PAGES = ["", "/product", "/services", "/about", "/contact", "/privacy", "/cookies", "/privacy/whatsapp"];

async function slugs(path: string, pick: (data: unknown) => unknown[]): Promise<string[]> {
  try {
    const res = await fetch(apiUrl(path), { cache: "no-store", signal: AbortSignal.timeout(5000) });
    if (!res.ok) return [];
    return pick(await res.json())
      .map((item) => (item && typeof item === "object" ? (item as { slug?: unknown }).slug : null))
      .filter((slug): slug is string => typeof slug === "string" && /^[a-z0-9-]+$/i.test(slug));
  } catch {
    return [];
  }
}

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const config = await fetchStorefrontConfig();
  const origin = await siteOrigin(config.contact?.website_url);
  if (!origin) return [];
  const [products, models] = await Promise.all([
    slugs("/products/", (data) => (Array.isArray(data) ? data : [])),
    slugs("/catalog/models/", (data) => {
      const results = (data as { results?: unknown })?.results;
      return Array.isArray(results) ? results : [];
    }),
  ]);
  return [
    ...PAGES.map((path) => ({ url: `${origin}${path}`, changeFrequency: "weekly" as const, priority: path ? 0.6 : 1 })),
    ...models.map((slug) => ({ url: `${origin}/modelo/${slug}`, changeFrequency: "daily" as const, priority: 0.8 })),
    ...products.map((slug) => ({ url: `${origin}/product/${slug}`, changeFrequency: "daily" as const, priority: 0.7 })),
  ];
}
