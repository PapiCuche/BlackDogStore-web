import Link from "next/link";
import { SOLD_OUT_LABEL } from "../lib/catalog-models";
import { formatMoney } from "../lib/format";
import { ProductImage } from "./ProductImage";

type ProductCardProps = {
  id: number;
  slug: string;
  name: string;
  price: number | string;
  description?: string;
  image_url?: string;
  inventory?: number;
  category?: { id: number; name: string; slug: string };
  average_rating?: number | null;
  review_count?: number;
  /** DC3: an empty shelf an offer still covers is «bajo pedido», not «agotado». */
  on_order?: { lead_time_days: number; price: string } | null;
};

function StockBadge({ inventory, onOrder }: { inventory?: number; onOrder?: boolean }) {
  if (inventory === undefined) return null;
  if (inventory === 0 && onOrder) {
    return (
      <span className="rounded-full border border-warning-border bg-warning-surface px-2 py-0.5 text-[9px] font-bold uppercase tracking-widest text-warning">
        Bajo pedido
      </span>
    );
  }
  if (inventory === 0) {
    // SUPPLIER-DEVICE-FLOW-02: published, nothing to sell — still listed, with its last price.
    return (
      <span className="rounded-full border border-danger-border bg-danger-surface px-2 py-0.5 text-[9px] font-bold uppercase tracking-widest text-danger">
        {SOLD_OUT_LABEL}
      </span>
    );
  }
  if (inventory <= 3) {
    return (
      <span className="rounded-full border border-warning-border bg-warning-surface px-2 py-0.5 text-[9px] font-bold uppercase tracking-widest text-warning">
        Últimas {inventory}
      </span>
    );
  }
  return (
    <span className="rounded-full border border-bd-border bg-surface px-2 py-0.5 text-[9px] font-bold uppercase tracking-widest text-muted">
      En stock
    </span>
  );
}

export function ProductCard({
  slug,
  name,
  price,
  description,
  image_url,
  inventory,
  category,
  average_rating,
  review_count,
  on_order,
}: ProductCardProps) {
  const onOrder = Boolean(on_order);
  const outOfStock = inventory !== undefined && inventory === 0 && !onOrder;

  return (
    <Link
      href={`/product/${slug}`}
      className="v3-product-card group flex flex-col overflow-hidden rounded-lg bg-background transition-shadow duration-300 hover:shadow-lg focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-primary"
    >
      {/* Image */}
      <div className="relative aspect-[4/3] overflow-hidden bg-background">
        {image_url ? (
          <ProductImage
            src={image_url}
            alt={name}
            sizes="(max-width: 640px) 100vw, (max-width: 1280px) 50vw, 33vw"
            className={`object-contain p-6 transition-transform duration-300 ease-out group-hover:scale-105 ${outOfStock ? "opacity-50" : ""}`}
          />
        ) : (
          <div className="flex h-full items-center justify-center">
            <div className="flex h-20 w-20 items-center justify-center rounded-full border border-bd-border text-2xl font-semibold text-foreground/[0.12]" aria-hidden="true">
              {name.trim().charAt(0).toUpperCase() || "·"}
            </div>
          </div>
        )}


        {/* Stock badge overlay */}
        <div className="absolute left-3 top-3">
          <StockBadge inventory={inventory} onOrder={onOrder} />
        </div>
      </div>

      {/* Content */}
      <div className="flex flex-1 flex-col px-5 pb-5 pt-4">
        {/* Category + rating row */}
        <div className="mb-2 flex items-center justify-between gap-2">
          {category ? (
            <span className="text-[9px] font-bold uppercase tracking-[0.2em] text-muted">
              {category.name}
            </span>
          ) : (
            <span />
          )}
          {average_rating !== null && average_rating !== undefined && review_count ? (
            <span className="text-[9px] text-muted">
              ★ {average_rating.toFixed(1)} ({review_count})
            </span>
          ) : null}
        </div>

        <div className="flex items-start justify-between gap-3">
          <h2 className="font-display text-xl font-semibold leading-tight text-foreground transition group-hover:text-foreground line-clamp-2">
            {name}
          </h2>
          <span className="shrink-0 text-sm font-semibold text-foreground">
            S/ {formatMoney(price)}
          </span>
        </div>

        <p className="mt-2 flex-1 text-sm leading-6 text-muted line-clamp-2">
          {description || "Consulta las características y condiciones de este producto."}
        </p>

        <div className="mt-5 flex items-center gap-1.5 text-xs font-bold uppercase tracking-widest text-muted transition group-hover:text-foreground">
          {outOfStock ? "Ver producto" : "Ver detalles"}
          <span className="transition-transform group-hover:translate-x-1">→</span>
        </div>
      </div>
    </Link>
  );
}
