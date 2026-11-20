import localFont from "next/font/local";

/**
 * Montserrat, the typeface of the Manual V4, served from the project (`app/fonts/README.md`). Variable font: the
 * manual's 400/500/700 are all inside it.
 *
 * It is never applied globally: only an element that carries `montserratV4.variable` (and so `--font-v4`) with
 * `data-design="v4"` uses it — the `/v4` preview's layout and, since CATALOG-REFERENCE-01 · PR D, the configurator
 * block of the public model page. The rest of the shop keeps its V3 fonts.
 */
export const montserratV4 = localFont({
  src: "./montserrat/montserrat-latin-wght-normal.woff2",
  variable: "--font-v4",
  weight: "100 900",
  display: "swap",
});
