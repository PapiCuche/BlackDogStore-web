import Link from "next/link";
import Image from "next/image";
import { formatMoney } from "../lib/format";

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
};

function StockBadge({ inventory }: { inventory?: number }) {
  if (inventory === undefined) return null;
  if (inventory === 0) {
    return (
      <span className="rounded-full border border-red-500/20 bg-red-500/10 px-2 py-0.5 text-[9px] font-bold uppercase tracking-widest text-red-400">
        Sin stock
      </span>
    );
  }
  if (inventory <= 3) {
    return (
      <span className="rounded-full border border-amber-500/20 bg-amber-500/10 px-2 py-0.5 text-[9px] font-bold uppercase tracking-widest text-amber-400">
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
}: ProductCardProps) {
  const outOfStock = inventory !== undefined && inventory === 0;

  return (
    <Link
      href={`/product/${slug}`}
      className="group flex flex-col overflow-hidden rounded-2xl border border-bd-border bg-surface transition-all duration-300 hover:border-bd-border hover:bg-surface"
    >
      {/* Image */}
      <div className="relative h-52 overflow-hidden bg-surface">
        {image_url ? (
          <Image
            src={image_url}
            alt={name}
            fill
            className={`object-cover transition duration-500 group-hover:scale-105 ${outOfStock ? "opacity-50" : ""}`}
            sizes="(max-width: 640px) 100vw, (max-width: 1280px) 50vw, 33vw"
          />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-3">
            <img
              src="/assets/branding/logo-icon.png"
              alt=""
              className="h-12 w-12 object-contain opacity-[0.07] invert"
            />
          </div>
        )}
        <div className="absolute inset-0 bg-gradient-to-t from-surface/80 to-transparent" />

        {/* Stock badge overlay */}
        <div className="absolute left-3 top-3">
          <StockBadge inventory={inventory} />
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
          <h2 className="font-display text-lg font-black uppercase leading-tight text-foreground transition group-hover:text-foreground line-clamp-2">
            {name}
          </h2>
          <span className="shrink-0 rounded-full border border-bd-border bg-surface-2 px-3 py-1 text-sm font-black text-foreground">
            S/ {formatMoney(price)}
          </span>
        </div>

        <p className="mt-2 flex-1 text-sm leading-6 text-muted line-clamp-2">
          {description || "Producto Apple de calidad premium con garantía."}
        </p>

        <div className="mt-5 flex items-center gap-1.5 text-xs font-bold uppercase tracking-widest text-muted transition group-hover:text-foreground">
          {outOfStock ? "Ver producto" : "Ver detalles"}
          <span className="transition group-hover:translate-x-1">→</span>
        </div>
      </div>
    </Link>
  );
}
