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
      <span className="rounded-full border border-red-500/25 bg-red-500/10 px-2.5 py-1 text-[10px] font-bold uppercase tracking-[0.08em] text-red-300">
        Sin stock
      </span>
    );
  }

  if (inventory <= 3) {
    return (
      <span className="rounded-full border border-amber-400/25 bg-amber-400/10 px-2.5 py-1 text-[10px] font-bold uppercase tracking-[0.08em] text-amber-200">
        Últimas {inventory}
      </span>
    );
  }

  return (
    <span className="rounded-full border border-bd-border bg-background/70 px-2.5 py-1 text-[10px] font-bold uppercase tracking-[0.08em] text-muted">
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
      className="group flex min-h-full flex-col overflow-hidden rounded-2xl border border-bd-border bg-surface transition hover:border-foreground/20 hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/70"
    >
      <div className="relative aspect-[4/3] overflow-hidden border-b border-bd-border bg-background">
        {image_url ? (
          <Image
            src={image_url}
            alt={name}
            fill
            className={`object-cover transition-transform duration-500 group-hover:scale-[1.025] ${outOfStock ? "opacity-55" : ""}`}
            sizes="(max-width: 640px) 100vw, (max-width: 1280px) 50vw, 33vw"
          />
        ) : (
          <div className="flex h-full items-center justify-center">
            <div className="flex h-20 w-20 items-center justify-center rounded-full border border-bd-border text-2xl font-black text-foreground/[0.12]" aria-hidden="true">
              {name.trim().charAt(0).toUpperCase() || "·"}
            </div>
          </div>
        )}

        <div className="absolute left-4 top-4">
          <StockBadge inventory={inventory} />
        </div>
      </div>

      <div className="flex flex-1 flex-col p-5 sm:p-6">
        <div className="flex min-h-5 items-center justify-between gap-3">
          <span className="text-[10px] font-bold uppercase tracking-[0.16em] text-muted">
            {category?.name || "Producto"}
          </span>
          {average_rating !== null && average_rating !== undefined && review_count ? (
            <span className="text-[11px] tabular-nums text-muted">
              ★ {average_rating.toFixed(1)} · {review_count}
            </span>
          ) : null}
        </div>

        <h2 className="mt-3 line-clamp-2 font-display text-xl font-extrabold uppercase leading-[1.05] tracking-[-0.025em] text-foreground">
          {name}
        </h2>

        <p className="mt-3 line-clamp-2 flex-1 text-sm leading-6 text-muted">
          {description || "Revisa el detalle del producto, su disponibilidad y condiciones de compra."}
        </p>

        <div className="mt-6 flex items-end justify-between gap-4 border-t border-bd-border pt-5">
          <div>
            <span className="block text-[10px] font-bold uppercase tracking-[0.14em] text-muted">
              Precio
            </span>
            <span className="mt-1 block text-lg font-extrabold tabular-nums text-foreground">
              S/ {formatMoney(price)}
            </span>
          </div>
          <span className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-[0.08em] text-foreground">
            {outOfStock ? "Ver producto" : "Ver detalle"}
            <span className="transition-transform group-hover:translate-x-1" aria-hidden="true">→</span>
          </span>
        </div>
      </div>
    </Link>
  );
}
