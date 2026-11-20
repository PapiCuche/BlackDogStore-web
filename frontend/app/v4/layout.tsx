import type { Metadata } from "next";
import { montserratV4 } from "../fonts/montserrat-v4";
import { TopBarV4 } from "../components/v4/TopBarV4";
import "./v4.css";

/**
 * P2 · F1 — the V4 preview (`/v4/...`).
 *
 * The V4 design (Manual V4.0, pending ratification) is seen only by whoever
 * opens this route: it is not indexed, it is not in the sitemap, and V3 —
 * every shop's design — does not change. The company is resolved from the
 * request host as everywhere else (root layout and `StorefrontProvider`).
 * Removing the preview is deleting this folder.
 *
 * Montserrat is loaded HERE and nowhere else: the rest of the shop keeps its
 * fonts. Three weights, the manual's scale (`v4.css`).
 *
 * P2 · F5a — the noindex below stays the DEFAULT of everything under `/v4`.
 * Only the home and model pages lift it, and only when the shop's design is
 * `v4` (chosen in the panel; see `lib/storefront-design.ts`). A new V4 page
 * that says nothing stays out of the index.
 */
// Montserrat, served from the project and shared with the public model page's configurator block (PR D).
const montserrat = montserratV4;

export const metadata: Metadata = {
  robots: { index: false, follow: false },
};

export default function V4Layout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <div data-design="v4" className={montserrat.variable}>
      <TopBarV4 />
      {/* V4-QA: «Saltar al contenido» lands here, past the top bar (`SkipLink`); focusable only by script. */}
      <main id="contenido-v4" tabIndex={-1} className="outline-none">{children}</main>
    </div>
  );
}
