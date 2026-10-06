import type { Metadata } from "next";

/**
 * The browser-tab icon: the platform's official isotype, and only that.
 *
 * PLATFORM BRANDING, not tenant branding. A per-company icon needs an upload
 * pipeline or a dynamic icon route, and neither exists (docs/saas-multiempresa.md,
 * «PENDIENTE — favicon por empresa»).
 *
 * TWO SETS, CHOSEN BY THE BROWSER. The isotype is a one-colour silhouette: the
 * dark one disappears on a dark tab strip and the light one on a light one. So
 * each `<link>` carries `media`, and there is deliberately NO unconditional
 * icon among them — a browser that found one could prefer it and show the wrong
 * contrast. A browser that ignores `media` falls back to `/favicon.ico`, which it
 * fetches by itself.
 *
 * NO FILE IN `app/` NAMED `favicon.ico` OR `icon.*`: the framework would emit its
 * own `<link>` for it, ahead of these. The files live in `public/` and are
 * produced from the two official isotypes by `npm run favicons:generate`.
 */
const SIZES = [16, 32, 48, 96] as const;

const set = (variant: "on-light" | "on-dark", scheme: "light" | "dark") =>
  SIZES.map((size) => ({
    url: `/assets/branding/favicon-${variant}-${size}.png`,
    sizes: `${size}x${size}`,
    type: "image/png",
    media: `(prefers-color-scheme: ${scheme})`,
  }));

export const PLATFORM_ICONS: NonNullable<Metadata["icons"]> & { icon: unknown[]; apple: unknown[] } = {
  // `on-light` is the DARK isotype: it goes on a light tab. See public/assets/branding/README.md.
  icon: [...set("on-light", "light"), ...set("on-dark", "dark")],
  apple: [{ url: "/apple-touch-icon.png", sizes: "180x180", type: "image/png" }],
};
