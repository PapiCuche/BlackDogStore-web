"use client";

/**
 * DC2 — the public page of a model (`/modelo/<slug>`): its new variants and its used devices.
 *
 * CATALOG-REFERENCE-01 · PR D (ADR §4.3) — the premium configurator (`ModelConfiguratorV4`, the same component as
 * the `/v4` preview) in the identity of the Manual V4, ONLY inside this block: `data-design="v4"` scopes every rule
 * of `v4.css` (imported by the page), `data-v4-scope="block"` makes it a block between the V3 header and footer that
 * follows the visitor's light/dark theme, and Montserrat is applied by class here, never globally. The V3 chrome and
 * every other page keep their design. «Catálogo» is the shop's catalogue (`/product`).
 *
 * Until PR D this file was the V3 presentation (`VariantPicker`, `UsedUnitCard`); the behaviour was already shared
 * (`lib/model-configurator.ts`), so only the look changed.
 */

import { useEffect, useRef } from "react";
import { ModelConfiguratorV4 } from "./v4/ModelConfiguratorV4";
import { montserratV4 } from "../fonts/montserrat-v4";
import type { CatalogModel } from "../lib/catalog-models";

export default function ModelDetail({
  slug, initialLine, initialModel, initialVariant,
}: {
  slug: string;
  initialLine?: string;
  /**
   * SEO-AUDIT #2 — the model as the server read it, so the first HTML is the page itself (no skeleton, no flash).
   * `undefined` when the server read failed: the browser reads it, as before. It is read again in the browser
   * either way (prices and stock move).
   */
  initialModel?: CatalogModel | null;
  /** PR D (D-D-1): the new variant `?variante=` named, already matched by the page against the model's own slugs. */
  initialVariant?: string;
}) {
  const block = useRef<HTMLDivElement>(null);
  // Review of #373: the V3 header is sticky; the photo column (sticky from `lg`) must stop below it, whatever its
  // height (logo size, wrapping). Its height is measured and handed to the block as `--v4-sticky-offset`; nothing of
  // the V3 header is changed. Before it is measured (server HTML) the offset is 0, as in the `/v4` preview.
  useEffect(() => {
    const header = document.querySelector<HTMLElement>("header.v3-store-header");
    const target = block.current;
    if (!header || !target || typeof ResizeObserver === "undefined") return;
    const write = () => target.style.setProperty("--v4-sticky-offset", `${Math.ceil(header.getBoundingClientRect().height)}px`);
    write();
    const observer = new ResizeObserver(write);
    observer.observe(header);
    return () => observer.disconnect();
  }, []);
  return (
    <div ref={block} data-design="v4" data-v4-scope="block" className={montserratV4.variable}>
      <ModelConfiguratorV4
        slug={slug}
        initialLine={initialLine}
        initialModel={initialModel}
        initialVariant={initialVariant}
        catalogHref="/product"
      />
    </div>
  );
}
