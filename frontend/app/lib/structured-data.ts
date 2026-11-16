/**
 * SEO-02 — schema.org data (JSON-LD) built ONLY from what the shop really has: the company's identity and contact as
 * configured, a product's own price, stock and pictures. Nothing invented: no rating, no opening hours, no geo, no
 * address part that was not typed. An empty field is left out, never filled with a guess.
 */
import { DEFAULT_CURRENCY } from "./analytics/items";
import type { StorefrontConfig } from "./storefront";

type Json = Record<string, unknown>;

const httpsUrl = (value: string | undefined | null): string => {
  const text = String(value ?? "").trim();
  return /^https:\/\/[^\s"<>]+$/i.test(text) ? text : "";
};

/** An address as an absolute URL on this origin (a relative path is joined; anything else that is not https is dropped). */
export function absoluteUrl(value: string | undefined | null, origin: string | null): string {
  const text = String(value ?? "").trim();
  if (!text) return "";
  if (/^https:\/\//i.test(text)) return httpsUrl(text);
  if (origin && text.startsWith("/") && !text.startsWith("//")) return `${origin}${text}`;
  return "";
}

function compact(data: Json): Json {
  return Object.fromEntries(Object.entries(data).filter(([, value]) => {
    if (value === undefined || value === null || value === "") return false;
    if (Array.isArray(value)) return value.length > 0;
    if (typeof value === "object") return Object.keys(value as Json).length > 0;
    return true;
  }));
}

/** The shop as a `Store`, from the tenant's configuration. Null when the company has no name. */
export function storeJsonLd(config: StorefrontConfig, origin: string | null): Json | null {
  const name = config.company.name.trim();
  if (!name) return null;
  const address = compact({
    "@type": "PostalAddress",
    streetAddress: config.contact.address?.trim(),
    addressLocality: config.contact.city?.trim(),
  });
  return compact({
    "@context": "https://schema.org",
    "@type": "Store",
    name,
    legalName: config.company.legal_name?.trim(),
    taxID: config.company.tax_id?.trim(),
    url: origin ?? undefined,
    logo: absoluteUrl(config.branding.logo_url, origin),
    email: config.contact.email?.trim(),
    telephone: config.contact.phone?.trim(),
    address: Object.keys(address).length > 1 ? address : undefined,
    sameAs: [httpsUrl(config.contact.facebook_url), httpsUrl(config.contact.instagram_url)].filter(Boolean),
  });
}

export type ProductForJsonLd = {
  slug: string;
  name: string;
  price: number | string;
  inventory: number;
  description?: string;
  image_url?: string;
  images?: { url: string }[];
  on_order?: { price?: string } | null;
};

/**
 * A product as `Product` + `Offer`. The availability is the shop's own: stock → InStock; none but «bajo pedido» →
 * BackOrder; otherwise OutOfStock. No price (0 or unreadable) → no Offer at all, never an invented one.
 */
export function productJsonLd(product: ProductForJsonLd, origin: string | null, summary = ""): Json {
  const url = origin ? `${origin}/product/${encodeURIComponent(product.slug)}` : undefined;
  const images = [product.image_url, ...(product.images ?? []).map((image) => image.url)]
    .map((image) => absoluteUrl(image, origin)).filter(Boolean);
  const price = Number(product.price);
  const availability = product.inventory > 0 ? "https://schema.org/InStock"
    : product.on_order ? "https://schema.org/BackOrder" : "https://schema.org/OutOfStock";
  return compact({
    "@context": "https://schema.org",
    "@type": "Product",
    name: product.name,
    description: summary || undefined,
    image: [...new Set(images)],
    url,
    offers: Number.isFinite(price) && price > 0 ? compact({
      "@type": "Offer",
      price: price.toFixed(2),
      priceCurrency: DEFAULT_CURRENCY,
      availability,
      url,
    }) : undefined,
  });
}

/** A `BreadcrumbList` from (name, path) pairs; a crumb without an origin cannot be absolute and the list is dropped. */
export function breadcrumbJsonLd(items: { name: string; path: string }[], origin: string | null): Json | null {
  if (!origin || !items.length) return null;
  return {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: items.map((item, index) => ({
      "@type": "ListItem", position: index + 1, name: item.name, item: `${origin}${item.path}`,
    })),
  };
}

/** JSON for a `<script type="application/ld+json">`: «<», «>», «&» and the line separators escaped (no early `</script>`). */
export function serializeJsonLd(data: Json | Json[]): string {
  return JSON.stringify(data)
    .replace(/</g, "\\u003c").replace(/>/g, "\\u003e").replace(/&/g, "\\u0026")
    .replace(/\u2028/g, "\\u2028").replace(/\u2029/g, "\\u2029");
}
