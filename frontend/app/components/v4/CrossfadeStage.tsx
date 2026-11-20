"use client";

/**
 * CATALOG-REFERENCE-01 · PR D — the configurator's main picture, cross-faded.
 *
 * Choosing another thumbnail used to remount the image: the box went blank until the next file arrived. Here, WITHIN
 * ONE FINISH (`finishKey`), the picture on show stays until the next one has loaded, and the next one fades in over
 * it; only then does the previous one leave. The box never changes size (the caller gives it its aspect ratio).
 *
 * ANOTHER FINISH (review of #373, D-C-1): the previous colour's photo leaves AT ONCE — never on screen, not even for a
 * frame, while the name, the price and the chip already say the new colour. Until the new picture loads the box shows
 * a neutral loading surface (no photo, and not «Imagen pendiente»: that one means the finish has no picture at all).
 *
 *  - Only the incoming picture has a name; the outgoing one is hidden from assistive technology (`aria-hidden`,
 *    empty `alt`), so a screen reader never hears two photos.
 *  - The first picture (the one in the server's HTML, the page's LCP) is shown at once and carries the priority;
 *    later ones load on demand.
 *  - A next picture that does not load within `MAX_WAIT_MS` is shown anyway: a previous colour's photo never stays
 *    on screen for a choice it does not belong to (PR C, D-C-1). The caller draws «Imagen pendiente» itself when
 *    there is no picture at all (`picture: null` empties the stage at once, never keeping another colour's).
 *  - With reduced motion the stylesheet removes the transition: the swap is instant, still without a blank box.
 */

import { useEffect, useState } from "react";
import { ProductImage } from "../ProductImage";

export type StagePicture = { url: string; alt_text: string; is_demo?: boolean };

/** How long the fade lasts (`.v4-layer` in `v4.css`); the outgoing picture leaves after it. */
export const CROSSFADE_MS = 240;
/** The longest the outgoing picture waits for the incoming one to load. */
export const MAX_WAIT_MS = 1200;

type Layer = { id: number; url: string; ready: boolean; first: boolean };

/** `layers` with layer `id` marked as loaded (the same array when nothing changes). */
function ready(layers: Layer[], id: number): Layer[] {
  return layers.some((layer) => layer.id === id && !layer.ready)
    ? layers.map((layer) => (layer.id === id ? { ...layer, ready: true } : layer))
    : layers;
}

export function CrossfadeStage({
  picture, alt, sizes, imageClassName = "", finishKey = null,
}: {
  /** What is on show now; `null` empties the stage at once. */
  picture: StagePicture | null;
  /** The name of the picture on show when it has no alt text of its own. */
  alt: string;
  sizes: string;
  imageClassName?: string;
  /** Which finish `picture` belongs to; a change drops every picture of the previous one at once. */
  finishKey?: string | null;
}) {
  const url = picture?.url ?? null;
  const [layers, setLayers] = useState<Layer[]>(() => (url ? [{ id: 0, url, ready: true, first: true }] : []));
  const [seen, setSeen] = useState(url);
  const [seenFinish, setSeenFinish] = useState(finishKey);
  const [nextId, setNextId] = useState(1);

  // A new picture to show: adjusted while rendering (React's «state from a changed prop»), not in an effect, so the
  // previous picture is never removed for one frame before the next one is added.
  if (url !== seen || finishKey !== seenFinish) {
    const sameFinish = finishKey === seenFinish;
    setSeen(url);
    setSeenFinish(finishKey);
    if (!url) setLayers([]);
    else if (seen === null || !sameFinish) {
      // From nothing on show («Imagen pendiente»), or from ANOTHER finish: nothing is kept under the new picture.
      const id = nextId;
      setNextId(id + 1);
      setLayers([{ id, url, ready: false, first: false }]);
    } else {
      const id = nextId;
      setNextId(id + 1);
      setLayers((current) => {
        const settled = current.filter((layer) => layer.ready);
        // Back to a picture still on screen (a quick back-and-forth): it simply stays.
        const same = settled.find((layer) => layer.url === url);
        return same ? [same] : [...settled.slice(-1), { id, url, ready: false, first: false }];
      });
    }
  }

  // Nothing to show is NOTHING, whatever the layers hold: a pending finish never keeps a colour's photo, even for a
  // frame in which the reset above has not been applied yet.
  // A finish change not applied yet (the frame before the reset) shows nothing rather than the old colour.
  const shownLayers = url && finishKey === seenFinish ? layers : [];
  const top = shownLayers[shownLayers.length - 1];

  // The incoming picture is shown after `MAX_WAIT_MS` even if it never said it loaded.
  useEffect(() => {
    if (!top || top.ready) return;
    const timer = window.setTimeout(() => setLayers((current) => ready(current, top.id)), MAX_WAIT_MS);
    return () => window.clearTimeout(timer);
  }, [top]);

  // Once it is in, the ones under it leave after the fade.
  useEffect(() => {
    if (!top?.ready || shownLayers.length < 2) return;
    const timer = window.setTimeout(() => setLayers((current) => current.filter((layer) => layer.id === top.id)), CROSSFADE_MS);
    return () => window.clearTimeout(timer);
  }, [top, shownLayers.length]);

  return (
    <>
      {top && !top.ready && shownLayers.length === 1 ? (
        <div data-testid="v4-stage-loading" aria-hidden="true" className="v4-stage-loading absolute inset-0" />
      ) : null}
      {shownLayers.map((layer) => {
        const current = layer === top;
        return (
          <div
            key={layer.id}
            data-layer=""
            data-ready={String(layer.ready)}
            aria-hidden={current ? undefined : true}
            className="v4-layer absolute inset-0"
            style={{ opacity: layer.ready ? 1 : 0 }}
          >
            <ProductImage
              src={layer.url}
              alt={current ? (picture?.alt_text || alt) : ""}
              className={imageClassName}
              sizes={sizes}
              priority={layer.first}
              fetchPriority={layer.first ? "high" : undefined}
              eager
              onLoad={() => setLayers((current) => ready(current, layer.id))}
              onError={() => setLayers((current) => ready(current, layer.id))}
            />
          </div>
        );
      })}
    </>
  );
}
