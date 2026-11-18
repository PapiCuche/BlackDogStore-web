import type { ComponentProps } from "react";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { JsonLd } from "../../components/JsonLd";
import ProductDetail from "../../components/ProductDetail";
import { readPublicProduct } from "../../lib/catalog-models-server";
import { markdownToPlainText } from "../../lib/markdown-lite";
import { siteOrigin } from "../../lib/site-origin";
import { canonical } from "../../lib/canonical";
import { breadcrumbJsonLd, productJsonLd } from "../../lib/structured-data";

/**
 * The page of one product.
 *
 * SEO-AUDIT #4 — «the shop answered and there is no such product» is `notFound()`, here and in
 * `generateMetadata`. The root `app/loading.tsx` streams the shell first, so that alone would be a 200 with
 * noindex; the real 404 is decided before the render, in `proxy.ts`. A failed read (an error status, the network,
 * an odd body) is NOT «no such product»: the page says it could not load it and offers to try again, and the
 * metadata asks not to be indexed meanwhile. `generateMetadata` and the page share ONE read per request
 * (`readPublicProduct` is `cache()`d), with the slug escaped (#18).
 */

type Props = {
  params: Promise<{ slug: string }>;
};

/** A failed read: not indexed, and not called «no encontrado» — the product may well exist. */
const FAILED_READ: Metadata = { title: "Producto", robots: { index: false, follow: true } };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  const read = await readPublicProduct(slug);
  if (read.status === "missing") notFound();
  if (read.status === "error") return FAILED_READ;
  const { product } = read;
  // Phase 3: the shop's name is NOT repeated here. The root layout's title
  // template (`%s | <tenant>`) appends it, and it knows which tenant owns this
  // host; hardcoding one here would have branded every storefront's product
  // pages as the same business.
  // CATALOG-RICH-DESC-01: search engines and link previews get the text, never the Markdown marks.
  const summary = markdownToPlainText(product.description, 160);
  return {
    title: product.name,
    description: summary || `Compra ${product.name} en nuestra tienda.`,
    // SEO-01: one address per product («?…» variants are the same page).
    alternates: await canonical(`/product/${encodeURIComponent(slug)}`),
    openGraph: {
      title: product.name,
      description: summary || undefined,
      ...(product.image_url ? { images: [{ url: product.image_url }] } : {}),
    },
  };
}

/** The read failed: say so, and offer the same address again (a full load, so the server reads it again). */
function ReadFailed({ slug }: { slug: string }) {
  return (
    <div className="flex min-h-[60vh] items-center justify-center bg-background px-6">
      <div role="alert" className="max-w-md rounded-2xl border border-dashed border-bd-border px-6 py-12 text-center">
        <p className="font-display text-2xl font-extrabold uppercase text-foreground">No pudimos cargar el producto</p>
        <p className="mt-2 text-sm leading-6 text-muted">Hubo un problema al consultar la tienda. Vuelve a intentarlo en unos segundos.</p>
        <a href={`/product/${encodeURIComponent(slug)}`} className="mt-6 inline-flex rounded-xl border border-bd-border px-5 py-3 text-xs font-bold uppercase tracking-[0.06em] text-foreground transition hover:border-foreground/25">
          Reintentar
        </a>
      </div>
    </div>
  );
}

export default async function ProductPage({ params }: Props) {
  const { slug } = await params;
  const read = await readPublicProduct(slug);
  if (read.status === "missing") notFound();
  if (read.status === "error") return <ReadFailed slug={slug} />;
  const { product } = read;

  // SEO-02: the product as schema.org data, from its own price, stock and pictures (no rating, nothing invented).
  const origin = await siteOrigin();
  const structured = [
    productJsonLd(product, origin, markdownToPlainText(product.description, 300)),
    breadcrumbJsonLd([{ name: "Inicio", path: "/" }, { name: "Productos", path: "/product" },
      { name: product.name, path: `/product/${encodeURIComponent(product.slug)}` }], origin),
  ].filter((item): item is Record<string, unknown> => item !== null);
  return (
    <>
      <JsonLd data={structured} />
      <ProductDetail product={product as unknown as ComponentProps<typeof ProductDetail>["product"]} />
    </>
  );
}
