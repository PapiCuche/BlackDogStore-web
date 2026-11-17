import type { Metadata } from "next";
import { ModelConfiguratorV4 } from "../../../components/v4/ModelConfiguratorV4";
import { readCatalogModel } from "../../../lib/catalog-models-server";

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
 */

type Props = {
  params: Promise<{ slug: string }>;
  searchParams: Promise<{ line?: string | string[] }>;
};

const NOINDEX = { index: false, follow: false } as const;

export async function generateMetadata({ params }: Pick<Props, "params">): Promise<Metadata> {
  const { slug } = await params;
  const read = await readCatalogModel(slug);
  if (read.status === "missing") return { title: "Modelo no encontrado", robots: NOINDEX };
  if (read.status === "error") return { title: "Modelo", robots: NOINDEX };
  return { title: read.model.name || "Modelo", robots: NOINDEX };
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
