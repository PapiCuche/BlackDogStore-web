import type { Metadata } from "next";
import { JsonLd } from "../components/JsonLd";
import { HomeV4 } from "../components/v4/HomeV4";
import { canonical } from "../lib/canonical";
import { readCatalogModelList, readStorefrontConfig } from "../lib/catalog-models-server";
import { siteOrigin } from "../lib/site-origin";
import { designOf } from "../lib/storefront-design";
import { modelListJsonLd } from "../lib/structured-data";

/**
 * P2 · F2 — the home page of the V4 preview.
 *
 * The models are read here, on the server (the same public API, the same
 * tenant rule and no cache shared between hosts), so the first HTML already
 * has the grid; the shop's texts come from the config the root layout read.
 *
 * P2 · F5a — while the shop is in V3 (the default) this is a PREVIEW: never
 * indexed (the layout says it too), not in the sitemap, and it names no
 * preferred address. When the owner chooses V4 in the panel, `/` redirects
 * here (`proxy.ts`), so this becomes the shop's home: indexed, with its own
 * absolute canonical and the shop's default title.
 *
 * V4-SEO — only then the listed models are also an `ItemList` (JSON-LD) of
 * their V4 pages. The preview (V3 mode) emits none.
 */

export async function generateMetadata(): Promise<Metadata> {
  const config = await readStorefrontConfig();
  if (designOf(config.page) !== "v4") {
    return { title: "Vista previa", robots: { index: false, follow: false } };
  }
  return { alternates: await canonical("/v4"), robots: { index: true, follow: true } };
}

export default async function HomePageV4() {
  const [models, config] = await Promise.all([readCatalogModelList(), readStorefrontConfig()]);
  const list = models && designOf(config.page) === "v4" ? modelListJsonLd(models.results, await siteOrigin()) : null;
  return (
    <>
      <JsonLd data={list} />
      <HomeV4 models={models} />
    </>
  );
}
