import type { CSSProperties } from "react";

/**
 * Presentation rule for images uploaded from the tenant storefront editor.
 *
 * Transparent PNG/WebP cut-outs otherwise look pasted onto a flat surface. A
 * restrained drop shadow follows the alpha silhouette, adding depth without
 * turning the image into a card. The same rule is harmless on an opaque upload
 * and, importantly, also applies to images uploaded before this rule existed.
 *
 * External URLs are left untouched: only files served by our own storefront
 * image pipeline receive this treatment.
 */
const MANAGED_STOREFRONT_IMAGE = /^\/api\/storefront\/images\/[0-9a-f]{32}\/?(?:\?.*)?$/i;

export function isManagedStorefrontImage(src: string): boolean {
  return MANAGED_STOREFRONT_IMAGE.test(src || "");
}

export function storefrontMediaStyle(src: string): CSSProperties | undefined {
  if (!isManagedStorefrontImage(src)) return undefined;
  return {
    filter: "drop-shadow(0 8px 10px rgba(0, 0, 0, 0.18))",
  };
}
