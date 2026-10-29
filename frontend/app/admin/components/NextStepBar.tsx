"use client";

/**
 * INV-UX-A1 — «Siguiente paso»: what usually follows loading merchandise.
 *
 * Shown after a registration, a stock movement or a device entry. Links only;
 * the optional children are the screen's own «registrar otro» buttons.
 *
 * LABEL-A may add `label_batch` / `label_batches` to those responses. This is
 * written to work with or without them: `extractLabelBatch` reads what is there
 * and returns null when nothing is.
 */

import Link from "next/link";
import type { ReactNode } from "react";
import { internalButtonClass } from "./internal-ui";

export type LabelBatchInfo = { count: number; href: string };

const LABELS_HREF = "/admin/inventory/labels";

/** `label_batch` (one) or `label_batches` (several) out of any API response; null when absent. */
export function extractLabelBatch(response: unknown): LabelBatchInfo | null {
  if (!response || typeof response !== "object") return null;
  const body = response as Record<string, unknown>;
  const many = Array.isArray(body.label_batches) ? body.label_batches : null;
  const one = body.label_batch && typeof body.label_batch === "object" ? body.label_batch : null;
  const first = (many && many.length > 0 ? many[0] : one) as Record<string, unknown> | null;
  if (!first || typeof first !== "object") return null;
  const url = typeof first.url === "string" && first.url.startsWith("/admin/") ? first.url : LABELS_HREF;
  const declared = typeof first.count === "number" && first.count > 0 ? first.count : null;
  return { count: many && many.length > 1 ? many.length : declared ?? 1, href: url };
}

export function NextStepBar({
  product, displayName, published, showStorefront = true, showLabels = true, showPublication = true, labelBatch, children,
}: {
  product: { id: number; name: string; slug?: string };
  /** Several products at once (a reception): what the heading says. Links keep using `product`. */
  displayName?: string;
  /** The person may open the labels screen (`inventory.labels.print`). */
  showLabels?: boolean;
  /** The person may open the publication centre, and `product` is the only one it would search. */
  showPublication?: boolean;
  /** Whether the shop shows it now. Only a clear `true` offers «Ver en la tienda». */
  published?: boolean | null;
  /** The registration screen already has its own «Ver en la tienda». */
  showStorefront?: boolean;
  labelBatch?: LabelBatchInfo | null;
  children?: ReactNode;
}) {
  const publication = `/admin/products/publication?search=${encodeURIComponent(product.name)}`;
  return (
    <section aria-label="Siguiente paso" className="rounded-lg border border-success-border bg-success-surface/40 p-4">
      <h3 className="text-sm font-semibold text-foreground">Siguiente paso</h3>
      <p className="mt-0.5 break-words text-xs text-muted">{displayName ?? product.name}</p>
      {labelBatch ? (
        <p role="status" className="mt-3 text-sm text-foreground">
          Etiquetas listas para imprimir
          {labelBatch.count > 1 ? ` (${labelBatch.count} lotes)` : ""}.{" "}
          <Link href={labelBatch.href} className="font-semibold underline">Abrir las etiquetas</Link>
        </p>
      ) : null}
      <div className="mt-3 flex flex-wrap gap-2">
        {children}
        {showLabels ? <Link href={LABELS_HREF} className={internalButtonClass}>Etiquetas</Link> : null}
        {showPublication ? <Link href={publication} className={internalButtonClass}>Publicación</Link> : null}
        {showStorefront && published === true && product.slug ? (
          <a href={`/product/${product.slug}`} target="_blank" rel="noreferrer" className={internalButtonClass}>
            Ver en la tienda
          </a>
        ) : null}
      </div>
    </section>
  );
}
