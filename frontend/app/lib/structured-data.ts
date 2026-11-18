/**
 * SEO-02 — schema.org data (JSON-LD) built ONLY from what the shop really has: the company's identity and contact as
 * configured, a product's own price, stock and pictures. Nothing invented: no rating, no opening hours, no geo, no
 * address part that was not typed. An empty field is left out, never filled with a guess.
 */
import { DEFAULT_CURRENCY } from "./analytics/items";
import type { CatalogModel, CatalogModelCard } from "./catalog-models";
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
  /** DC2: a used variant sells device by device, at each device's own price — it gets no product-level Offer. */
  condition_line?: string;
};

/**
 * A product as `Product` + `Offer`. The availability is the shop's own: stock → InStock; none but «bajo pedido» →
 * BackOrder, at the «bajo pedido» price the page shows (review of SEO-02); otherwise OutOfStock. No price (0 or
 * unreadable) → no Offer at all, never an invented one. A used variant gets no Offer: each device has its own price.
 */
export function productJsonLd(product: ProductForJsonLd, origin: string | null, summary = ""): Json {
  const url = origin ? `${origin}/product/${encodeURIComponent(product.slug)}` : undefined;
  const images = [product.image_url, ...(product.images ?? []).map((image) => image.url)]
    .map((image) => absoluteUrl(image, origin)).filter(Boolean);
  const backOrder = product.inventory <= 0 && Boolean(product.on_order);
  const availability = product.inventory > 0 ? IN_STOCK : backOrder ? BACK_ORDER : OUT_OF_STOCK;
  return compact({
    "@context": "https://schema.org",
    "@type": "Product",
    name: product.name,
    description: summary || undefined,
    image: [...new Set(images)],
    url,
    offers: product.condition_line !== "used"
      ? offerJsonLd(backOrder ? product.on_order?.price : product.price, availability, url) : undefined,
  });
}

const IN_STOCK = "https://schema.org/InStock";
const BACK_ORDER = "https://schema.org/BackOrder";
const OUT_OF_STOCK = "https://schema.org/OutOfStock";

/** One `Offer` in soles, or `undefined` when there is no real price (0 or unreadable): never an invented one. */
function offerJsonLd(value: unknown, availability: string, url: string | undefined, extra: Json = {}): Json | undefined {
  const price = Number(value);
  if (!Number.isFinite(price) || price <= 0) return undefined;
  return compact({ "@type": "Offer", price: price.toFixed(2), priceCurrency: DEFAULT_CURRENCY, availability, ...extra, url });
}

/** SEO-AUDIT #2 — the address of a model's V3 page (`/modelo/<slug>`), the shop's public page while it is in V3. */
export function v3ModelPath(slug: string): string {
  return `/modelo/${encodeURIComponent(slug)}`;
}

/** V4-SEO — the address of a model's V4 page (`/v4/modelo/<slug>`). */
export function v4ModelPath(slug: string): string {
  return `/v4/modelo/${encodeURIComponent(slug)}`;
}

/**
 * V4-VARIANT-URL — the address of ONE new variant on its model's V4 page (`/v4/modelo/<slug>?variante=<variant>`).
 * The page opens with that variant chosen; its canonical stays the model's address.
 */
export function v4VariantPath(modelSlug: string, variantSlug: string): string {
  return `${v4ModelPath(modelSlug)}?variante=${encodeURIComponent(variantSlug)}`;
}

export type ModelForJsonLd = Pick<CatalogModel, "slug" | "name" | "image_url" | "attributes" | "variants">;

/**
 * Where a model's page and its variants live. V4 (the default): `/v4/modelo/<slug>` and one address per new variant
 * (`?variante=`). V3 (SEO-AUDIT #2): `/modelo/<slug>` and no address per variant — the V3 page has none, so every
 * variant points at the model's page.
 */
export type ModelPaths = {
  model: (slug: string) => string;
  variant: ((modelSlug: string, variantSlug: string) => string) | null;
};

export const V4_MODEL_PATHS: ModelPaths = { model: v4ModelPath, variant: v4VariantPath };
export const V3_MODEL_PATHS: ModelPaths = { model: v3ModelPath, variant: null };

/**
 * V4-SEO — a model of the V4 storefront as a `ProductGroup` (schema.org; Google's product-variant markup), built only
 * from the public model:
 *
 *  - each NEW variant is a `Product` (its own name, slug as `sku`, pictures, colour when the model has a colour
 *    attribute) with ONE `Offer` in soles and the shop's real availability, read the way the page reads it: own
 *    stock → InStock at its price; «bajo pedido» → BackOrder at the «bajo pedido» price; nothing → OutOfStock;
 *  - USED units are never variants and never get an Offer: each device has its own price (as SEO-02);
 *  - no brand (the public model has none), no rating, no review.
 *
 * V4-VARIANT-URL — each variant `Product` and its `Offer` point at the variant's own address (`v4VariantPath`),
 * which opens the page with it chosen: one URL per variant, as Google's variant markup asks. The group keeps the
 * model's address (the canonical).
 *
 * SEO-AUDIT #2 — the V3 model page uses the same builder with `V3_MODEL_PATHS`: same rules, V3 addresses, and every
 * variant at the model's address (the V3 page has no address per variant).
 */
export function modelGroupJsonLd(model: ModelForJsonLd, origin: string | null, summary = "", paths: ModelPaths = V4_MODEL_PATHS): Json {
  const url = origin ? `${origin}${paths.model(model.slug)}` : undefined;
  // Review: a payload from an older or broken backend may lack a list; it is read as empty, never thrown on.
  const color = (model.attributes ?? []).find((attribute) => (attribute.role ? attribute.role === "color" : attribute.kind === "color"));
  const colorOf = (options: Record<string, number> | undefined) => {
    if (!color) return undefined;
    const id = (options ?? {})[String(color.id)];
    return (color.options ?? []).find((option) => option.id === id)?.label?.trim() || undefined;
  };
  const variants = (model.variants ?? []).map((variant) => {
    const images = [variant.image_url, ...(variant.images ?? []).map((image) => image.url)]
      .map((image) => absoluteUrl(image, origin)).filter(Boolean);
    const onOrder = variant.availability === "supplier" && variant.on_order ? variant.on_order : null;
    const availability = variant.availability === "own_stock" ? IN_STOCK : onOrder ? BACK_ORDER : OUT_OF_STOCK;
    const variantUrl = origin && variant.slug && paths.variant ? `${origin}${paths.variant(model.slug, variant.slug)}` : url;
    return compact({
      "@type": "Product",
      name: variant.name,
      sku: variant.slug,
      image: [...new Set(images)],
      color: colorOf(variant.options),
      url: variantUrl,
      offers: offerJsonLd(onOrder ? onOrder.price : variant.price, availability, variantUrl,
        { itemCondition: "https://schema.org/NewCondition" }),
    });
  });
  return compact({
    "@context": "https://schema.org",
    "@type": "ProductGroup",
    name: model.name,
    description: summary || undefined,
    image: [absoluteUrl(model.image_url, origin)].filter(Boolean),
    url,
    productGroupID: model.slug,
    variesBy: variants.some((variant) => "color" in variant) ? ["https://schema.org/color"] : undefined,
    hasVariant: variants,
  });
}

/** V4-SEO — the models listed on the V4 home as an `ItemList` of their V4 pages; without an origin or models, none. */
export function modelListJsonLd(models: Pick<CatalogModelCard, "slug" | "name">[], origin: string | null): Json | null {
  const listed = models.filter((model) => typeof model.slug === "string" && model.slug.trim());
  if (!origin || !listed.length) return null;
  return {
    "@context": "https://schema.org",
    "@type": "ItemList",
    itemListElement: listed.map((model, index) => ({
      "@type": "ListItem", position: index + 1, name: model.name, url: `${origin}${v4ModelPath(model.slug)}`,
    })),
  };
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
