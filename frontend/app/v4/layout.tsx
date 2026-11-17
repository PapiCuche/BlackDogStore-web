import type { Metadata } from "next";
import localFont from "next/font/local";
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
// Served from the project (app/fonts/README.md). Variable font: 400/500/700 are all inside it.
const montserrat = localFont({
  src: "../fonts/montserrat/montserrat-latin-wght-normal.woff2",
  variable: "--font-v4",
  weight: "100 900",
  display: "swap",
});

export const metadata: Metadata = {
  robots: { index: false, follow: false },
};

export default function V4Layout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <div data-design="v4" className={montserrat.variable}>
      <TopBarV4 />
      {children}
    </div>
  );
}
