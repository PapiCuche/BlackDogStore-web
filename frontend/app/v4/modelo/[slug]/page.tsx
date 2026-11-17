import type { Metadata } from "next";
import { ModelConfiguratorV4 } from "../../../components/v4/ModelConfiguratorV4";
import { canonical } from "../../../lib/canonical";
import { readCatalogModel } from "../../../lib/catalog-models-server";
import { markdownToPlainText } from "../../../lib/markdown-lite";
import { fetchStorefrontConfig } from "../../../lib/storefront";
import { designOf } from "../../../lib/storefront-design";

/**
 * P2 · F1 — the model page in the V4 preview.
 *
 * Same data and same behaviour as `/modelo/<slug>` (V3); only the design
 * differs. Never indexed, whatever the model: the preview is not the shop's
 * public page.
 *
 * P2 · F2 — painted on the SERVER. The model is read here and handed to the
 * configurator as its first data, so the first HTML already has the name, the
 * price and the main photo (LCP). The browser still reads it again: prices and
 * availability move while somebody looks. `generateMetadata` and the page
 * share ONE read per request (`readCatalogModel` is `cache()`d).
 *
 * When the read failed the page is sent without data and the browser tries
 * again, as before F2. When the model does not exist, the first HTML says so.
 *
 * P2 · F5a — while the shop is in V3 (the default) this stays a preview:
 * never indexed, no canonical. When the owner chooses V4, `/modelo/<slug>`
 * redirects here (`proxy.ts`) and this page takes the V3 page's place in
 * search: indexed, with its own absolute canonical, the description as plain
 * text and the photo for link previews — the same metadata as the V3 page.
 */

type Props = {
  params: Promise<{ slug: string }>;
  searchParams: Promise<{ line?: string | string[] }>;
};

const NOINDEX = { index: false, follow: false } as const;
const INDEX = { index: true, follow: true } as const;

export async function generateMetadata({ params }: Pick<Props, "params">): Promise<Metadata> {
  const { slug } = await params;
  const [read, config] = await Promise.all([readCatalogModel(slug), fetchStorefrontConfig()]);
  if (designOf(config.page) !== "v4") {
    if (read.status === "missing") return { title: "Modelo no encontrado", robots: NOINDEX };
    if (read.status === "error") return { title: "Modelo", robots: NOINDEX };
    return { title: read.model.name || "Modelo", robots: NOINDEX };
  }
  // V4 is the shop's design: the same rules as the V3 model page (`app/modelo/[slug]/page.tsx`). `robots` is
  // always written here because the V4 layout says noindex for the preview, and a page inherits it.
  if (read.status === "missing") return { title: "Modelo no encontrado", robots: { index: false, follow: true } };
  // A failed read is not «no such model»: like V3, it does not ask to be dropped from the index.
  if (read.status === "error") return { title: "Modelo", robots: INDEX };
  const { model } = read;
  const name = model.name || "Modelo";
  const summary = markdownToPlainText(model.description, 160);
  return {
    title: name,
    description: summary || `Elige la opción de ${name} que prefieras.`,
    alternates: await canonical(`/v4/modelo/${encodeURIComponent(slug)}`),
    robots: INDEX,
    openGraph: {
      title: name,
      description: summary || undefined,
      ...(model.image_url ? { images: [{ url: model.image_url }] } : {}),
    },
  };
}

export default async function ModelPageV4({ params, searchParams }: Props) {
  const { slug } = await params;
  const { line } = await searchParams;
  const read = await readCatalogModel(slug);
  const initialModel = read.status === "found" ? read.model : read.status === "missing" ? null : undefined;
  return (
    <ModelConfiguratorV4
      slug={slug}
      initialLine={typeof line === "string" ? line : undefined}
      initialModel={initialModel}
    />
  );
}
