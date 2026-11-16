import type { Metadata } from "next";
import { HomeV4 } from "../components/v4/HomeV4";
import { readCatalogModelList } from "../lib/catalog-models-server";

/**
 * P2 · F2 — the home page of the V4 preview.
 *
 * The models are read here, on the server (the same public API, the same
 * tenant rule and no cache shared between hosts), so the first HTML already
 * has the grid; the shop's texts come from the config the root layout read.
 *
 * Never indexed (the layout says it too), not in the sitemap, and it names
 * no preferred address (`alternates`): the preview is not the shop's public
 * home.
 */

export const metadata: Metadata = {
  title: "Vista previa",
  robots: { index: false, follow: false },
};

export default async function HomePageV4() {
  const models = await readCatalogModelList();
  return <HomeV4 models={models} />;
}
