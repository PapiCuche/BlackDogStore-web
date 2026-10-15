"use client";

/**
 * DC3 — «bajo pedido» en la caja.
 *
 * Cuando el estante no tiene (o no alcanza) y una oferta vigente lo cubre, el
 * cajero puede añadir la línea bajo pedido A PROPÓSITO: es otro precio y otro
 * plazo, y el cliente tiene que oírlo. Nunca se añade sola, y una línea es toda
 * del estante o toda bajo pedido.
 */

import type { PosProduct } from "../../lib/internal-api";

function money(value: string | number) {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n)
    ? n.toLocaleString("es-PE", { style: "currency", currency: "PEN" })
    : String(value);
}

export function leadTimeText(days: number | null | undefined): string {
  if (days === null || days === undefined) return "plazo por confirmar";
  return `${days} ${days === 1 ? "día" : "días"}`;
}

/** «Bajo pedido (N días)» — lo que la línea dice de sí misma. */
export function onOrderLineLabel(days: number | null | undefined): string {
  return `Bajo pedido (${leadTimeText(days)})`;
}

/** Un resultado de búsqueda: añadir del estante y, si hay oferta, añadir bajo pedido. */
export function PosProductHit({
  product, onAdd, onAddOnOrder,
}: {
  product: PosProduct;
  onAdd: (product: PosProduct) => void;
  onAddOnOrder: (product: PosProduct) => void;
}) {
  return (
    <div className="flex flex-col border-b border-bd-border/70 last:border-0 sm:flex-row sm:items-stretch">
      <button
        type="button"
        onClick={() => onAdd(product)}
        className="flex w-full min-w-0 flex-1 items-center justify-between gap-3 px-4 py-3 text-left text-sm transition hover:bg-foreground/[0.03]"
      >
        <span className="min-w-0 flex-1 break-words text-foreground">{product.name}</span>
        <span className="flex shrink-0 items-center gap-3 text-xs">
          <span className={product.available > 0 ? "text-muted" : "text-danger"}>{product.available} disp.</span>
          <span className="font-mono text-foreground">{money(product.price)}</span>
        </span>
      </button>
      {product.on_order ? (
        <button
          type="button"
          onClick={() => onAddOnOrder(product)}
          aria-label={`Agregar ${product.name} bajo pedido (${leadTimeText(product.on_order.lead_time_days)})`}
          className="shrink-0 border-t border-bd-border/70 px-4 py-3 text-left text-xs font-semibold text-warning transition hover:bg-warning-surface sm:border-l sm:border-t-0"
        >
          + {onOrderLineLabel(product.on_order.lead_time_days)} · <span className="font-mono">{money(product.on_order.price)}</span>
        </button>
      ) : null}
    </div>
  );
}

/** Tras escanear un producto sin estante que una oferta cubre: la opción, explícita. */
export function PosOnOrderOffer({
  product, onAdd, onDismiss,
}: {
  product: PosProduct;
  onAdd: (product: PosProduct) => void;
  onDismiss: () => void;
}) {
  if (!product.on_order) return null;
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-warning-border bg-warning-surface px-4 py-2.5 text-sm text-warning">
      <span className="min-w-0 flex-1">
        {product.name} se puede vender bajo pedido: {money(product.on_order.price)}, entrega en {leadTimeText(product.on_order.lead_time_days)}.
      </span>
      <button
        type="button"
        onClick={() => onAdd(product)}
        className="rounded border border-warning-border px-3 py-1.5 text-xs font-semibold transition hover:bg-background"
      >
        Agregar bajo pedido
      </button>
      <button type="button" onClick={onDismiss} className="text-xs text-muted transition hover:text-foreground" aria-label="Descartar bajo pedido">
        ✕
      </button>
    </div>
  );
}

/** En la línea: que es bajo pedido y en cuántos días. */
export function PosOnOrderBadge({ days }: { days: number | null | undefined }) {
  return (
    <span className="mt-1 inline-flex rounded border border-warning-border bg-warning-surface px-2 py-0.5 text-[11px] font-semibold text-warning" data-testid="pos-line-on-order">
      {onOrderLineLabel(days)}
    </span>
  );
}
