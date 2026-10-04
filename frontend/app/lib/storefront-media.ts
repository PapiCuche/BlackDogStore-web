import type { CSSProperties } from "react";

/**
 * Presentation rule for images uploaded from the tenant storefront editor.
 *
 * Transparent PNG/WebP cut-outs otherwise look pasted onto a flat surface. A
 * restrained drop shadow follows the alpha silhouette, adding depth without
 * turning the image into a card. The same rule is harmless on an opaque upload
 * and, importantly, also applies to images uploaded before this rule existed.
 *
 * THE VALUE LIVES IN CSS (`--cutout-shadow`, `--cutout-shadow-on-slab` in
 * globals.css), not here. A dark shadow cannot be seen on a dark surface, and
 * there are two of those: the hero slab, and the whole page in the dark theme.
 * The component only says which surface the image sits on; the theme decides
 * what depth looks like there.
 *
 * External URLs are left untouched: only files served by our own storefront
 * image pipeline receive this treatment.
 */
const MANAGED_STOREFRONT_IMAGE = /^\/api\/storefront\/images\/[0-9a-f]{32}\/?(?:\?.*)?$/i;

export function isManagedStorefrontImage(src: string): boolean {
  return MANAGED_STOREFRONT_IMAGE.test(src || "");
}

/** `page`: the section background. `slab`: the dark slab, in either theme. */
export type StorefrontMediaSurface = "page" | "slab";

export function storefrontMediaStyle(
  src: string,
  surface: StorefrontMediaSurface = "page",
): CSSProperties | undefined {
  if (!isManagedStorefrontImage(src)) return undefined;
  return {
    filter: surface === "slab" ? "var(--cutout-shadow-on-slab)" : "var(--cutout-shadow)",
  };
}
