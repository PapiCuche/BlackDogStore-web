import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { JsonLd } from "../../components/JsonLd";
import ModelDetail from "../../components/ModelDetail";
import type { CatalogModel } from "../../lib/catalog-models";
import { readCatalogModel, type ServerModelRead } from "../../lib/catalog-models-server";
import { isE2eMockedModel } from "../../lib/e2e-mocked-models";
import { markdownToPlainText } from "../../lib/markdown-lite";
import { canonical } from "../../lib/canonical";
import { siteOrigin } from "../../lib/site-origin";
import { V3_MODEL_PATHS, breadcrumbJsonLd, modelGroupJsonLd, v3ModelPath, variantShareImage } from "../../lib/structured-data";
// CATALOG-REFERENCE-01 · PR D: the configurator block's tokens. Every rule of this sheet is scoped to
// `[data-design="v4"]` (`storefront-v4-route.test.tsx`), so loading it here restyles nothing outside the block.
import "../../v4/v4.css";

/**
 * DC2 — the page of a model (its variants and its used devices).
 *
 * SEO-AUDIT #2 — painted on the SERVER, as the V4 page (P2 · F2). The model is read here and handed to
 * `ModelDetail` (the configurator block) as its first data, so the first HTML already has the name (h1), the price, the main photo and
 * the model as schema.org data. The browser still reads it again: prices, availability and which used device is
 * still there change while somebody looks, and it is re-read after a refusal. `generateMetadata` and the page
 * share ONE read per request (`readCatalogModel` is `cache()`d).
 *
 * SEO-AUDIT #4 — «no such model» is `notFound()`, here and in `generateMetadata`. Because the root
 * `app/loading.tsx` streams the shell first, that alone would be a 200 with noindex; the real 404 is decided
 * before the render, in `proxy.ts`. A failed read is NOT «no such model»: the page is sent without data and the
 * browser tries again (the state it always had), and the metadata asks not to be indexed meanwhile.
 *
 * CATALOG-REFERENCE-01 · PR D (ADR §4.3) — the configurator is the premium one, in the identity of the Manual V4, but
 * ONLY inside its block (`data-design="v4" data-v4-scope="block"`, Montserrat by class): the V3 header, footer and
 * every other page keep their design, and the block follows the visitor's light/dark theme as they do. The same
 * component serves the `/v4` preview. `?variante=<variant slug>` opens the page with that new variant chosen,
 * already in the first HTML; the canonical stays the model's address (D-D-1).
 */

type Props = {
  params: Promise<{ slug: string }>;
  searchParams: Promise<{ line?: string | string[]; variante?: string | string[] }>;
};

/**
 * The server's read, except that a model which exists only in the browser's E2E mocks (never in production, see
 * `lib/e2e-mocked-models.ts`) counts as «not read»: the browser reads it, as before the server read existed.
 */
async function readModel(slug: string): Promise<ServerModelRead> {
  const read = await readCatalogModel(slug);
  return read.status === "missing" && isE2eMockedModel(slug) ? { status: "error" } : read;
}

/** A failed read: not indexed, and not called «no encontrado» — the model may well exist. */
const FAILED_READ: Metadata = { title: "Modelo", robots: { index: false, follow: true } };

/**
 * PR D (review of #373) — the picture a shared link shows. With `?variante=` naming one of the model's new variants:
 * that variant's own picture (`variantShareImage`: its gallery as the server resolved it, the shop's own, not a demo),
 * or NONE when it has none («Imagen pendiente») — never the cover, which is one colour's photo. Without it: the
 * model's cover, as before.
 */
async function previewImages(model: CatalogModel, value: string | string[] | undefined) {
  const chosen = typeof value === "string" ? (model.variants ?? []).find((variant) => variant.slug === value) : undefined;
  if (!chosen) return model.image_url ? [{ url: model.image_url }] : [];
  const image = variantShareImage(chosen, await siteOrigin());
  return image ? [{ url: image }] : [];
}

export async function generateMetadata(
  { params, searchParams }: Pick<Props, "params"> & Partial<Pick<Props, "searchParams">>,
): Promise<Metadata> {
  const { slug } = await params;
  const { variante } = (await searchParams) ?? {};
  const read = await readModel(slug);
  if (read.status === "missing") notFound();
  if (read.status === "error") return FAILED_READ;
  const { model } = read;
  // CATALOG-RICH-DESC-01: search engines and link previews get the text, never the Markdown marks.
  const summary = markdownToPlainText(model.description, 160);
  // The shop's name is appended by the root layout's title template.
  return {
    title: model.name,
    description: summary || `Elige la opción de ${model.name} que prefieras.`,
    // SEO-01: «?line=used» is the same page; so is «?variante=» (D-D-1): the canonical never carries them.
    alternates: await canonical(v3ModelPath(slug)),
    openGraph: {
      title: model.name,
      description: summary || undefined,
      images: await previewImages(model, variante),
    },
  };
}

/**
 * SEO-AUDIT #2 — the model as a `ProductGroup` (same builder and rules as V4: new variants with their own Offer in
 * soles and real availability, no Offer for used devices, nothing invented) and its breadcrumb, with V3 addresses.
 * JSON-LD is an extra: a payload it cannot be built from leaves it out; it never breaks the page.
 */
async function structuredData(model: CatalogModel) {
  try {
    const origin = await siteOrigin();
    return [
      modelGroupJsonLd(model, origin, markdownToPlainText(model.description, 300), V3_MODEL_PATHS),
      breadcrumbJsonLd([
        { name: "Inicio", path: "/" },
        { name: "Catálogo", path: "/product" },
        { name: model.name, path: v3ModelPath(model.slug) },
      ], origin),
    ].filter((item): item is Record<string, unknown> => item !== null);
  } catch {
    return null;
  }
}

/**
 * PR D (D-D-1) — `?variante=` only when it is exactly the slug of one of this model's NEW variants; what goes on is
 * the payload's own slug (the request's value is only compared, never rendered). Anything else is `undefined`: the
 * usual first choice, no error. The same rule as the V4 page.
 */
function requestedVariant(model: CatalogModel | undefined, value: string | string[] | undefined): string | undefined {
  if (!model || typeof value !== "string" || !value) return undefined;
  return (model.variants ?? []).find((variant) => variant.slug === value)?.slug;
}

export default async function ModelPage({ params, searchParams }: Props) {
  const { slug } = await params;
  const { line, variante } = await searchParams;
  const read = await readModel(slug);
  if (read.status === "missing") notFound();
  const initialModel = read.status === "found" ? read.model : undefined;
  const structured = initialModel ? await structuredData(initialModel) : null;
  return (
    <>
      <JsonLd data={structured} />
      <ModelDetail
        slug={slug}
        initialLine={typeof line === "string" ? line : undefined}
        initialModel={initialModel}
        initialVariant={requestedVariant(initialModel, variante)}
      />
    </>
  );
}
