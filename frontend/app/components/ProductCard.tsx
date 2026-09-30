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
      <span className="rounded-full border border-red-500/25 bg-red-500/10 px-2.5 py-1 text-[9px] font-bold uppercase tracking-[0.16em] text-red-300">
        Sin stock
      </span>
    );
  }

  if (inventory <= 3) {
    return (
      <span className="rounded-full border border-amber-400/25 bg-amber-400/10 px-2.5 py-1 text-[9px] font-bold uppercase tracking-[0.16em] text-amber-300">
        Últimas {inventory}
      </span>
    );
  }

  return (
    <span className="rounded-full border border-bd-border bg-background/80 px-2.5 py-1 text-[9px] font-bold uppercase tracking-[0.16em] text-muted-foreground">
      En stock
    </span>
  );
}

function ProductPlaceholder() {
  return (
    <div className="flex h-full items-center justify-center text-muted-foreground" aria-hidden="true">
      <svg className="h-10 w-10" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.4} d="M20 7l-8-4-8 4m16 0-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4" />
      </svg>
    </div>
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
      href={"/product/" + slug}
      className="group flex h-full flex-col overflow-hidden rounded-xl border border-bd-border bg-surface transition duration-300 hover:-translate-y-0.5 hover:border-accent/60 hover:bg-surface-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
    >
      <div className="relative aspect-[4/3] overflow-hidden bg-background">
        {image_url ? (
          <Image
            src={image_url}
            alt={name}
            fill
            className={"object-cover transition duration-500 group-hover:scale-[1.025] " + (outOfStock ? "opacity-55" : "")}
            sizes="(max-width: 640px) 100vw, (max-width: 1280px) 50vw, 33vw"
          />
        ) : (
          <ProductPlaceholder />
        )}

        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-20 bg-gradient-to-t from-surface to-transparent" />
        <div className="absolute left-3 top-3">
          <StockBadge inventory={inventory} />
        </div>
      </div>

      <div className="flex flex-1 flex-col p-5">
        <div className="flex min-h-5 items-center justify-between gap-3">
          <span className="text-[10px] font-bold uppercase tracking-[0.18em] text-muted-foreground">
            {category?.name || "Producto"}
          </span>
          {average_rating !== null && average_rating !== undefined && review_count ? (
            <span className="text-[10px] text-muted-foreground">
              ★ {average_rating.toFixed(1)} ({review_count})
            </span>
          ) : null}
        </div>

        <h2 className="mt-3 line-clamp-2 font-display text-xl font-black uppercase leading-tight text-foreground">
          {name}
        </h2>

        <p className="mt-2 line-clamp-2 flex-1 text-sm leading-6 text-muted-foreground">
          {description || "Consulta disponibilidad, características y condiciones del producto."}
        </p>

        <div className="mt-5 flex items-end justify-between gap-4 border-t border-bd-border pt-4">
          <div>
            <span className="block text-[9px] font-bold uppercase tracking-[0.18em] text-muted-foreground">
              Precio
            </span>
            <span className="mt-1 block font-display text-xl font-black text-foreground">
              S/ {formatMoney(price)}
            </span>
          </div>
          <span className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-[0.14em] text-muted-foreground transition group-hover:text-foreground">
            {outOfStock ? "Ver producto" : "Ver detalles"}
            <span className="transition-transform group-hover:translate-x-1" aria-hidden="true">→</span>
          </span>
        </div>
      </div>
    </Link>
  );
}
