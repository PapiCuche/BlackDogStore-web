import Link from "next/link";
import { formatMoney } from "../lib/format";

type CartItemCardProps = {
  id: number;
  quantity: number;
  product: {
    id: number;
    name: string;
    price: number | string;
    slug: string;
  };
  onQuantityChange?: (quantity: number) => void;
  onRemove?: () => void;
};

export function CartItemCard({ quantity, product, onQuantityChange, onRemove }: CartItemCardProps) {
  return (
    <article className="rounded-xl border border-bd-border bg-surface p-5 transition hover:border-accent/40">
      <div className="flex flex-col gap-5 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-center gap-4">
          <div
            className="flex h-14 w-14 shrink-0 items-center justify-center rounded-xl border border-bd-border bg-background text-muted-foreground"
            aria-hidden="true"
          >
            <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.4} d="M20 7l-8-4-8 4m16 0-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4" />
            </svg>
          </div>
          <div className="min-w-0">
            <Link
              href={"/product/" + product.slug}
              className="line-clamp-2 font-display font-black uppercase text-foreground transition hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              {product.name}
            </Link>
            <p className="mt-1 text-sm text-muted-foreground">
              S/ {formatMoney(product.price)} por unidad
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-4 sm:justify-end">
          <label className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.12em] text-muted-foreground">
            Cant.
            <input
              type="number"
              min={1}
              value={quantity}
              onChange={(e) => onQuantityChange?.(Number(e.target.value))}
              className="w-16 rounded-lg border border-bd-border bg-background px-2 py-2 text-center text-sm text-foreground focus:border-accent/60 focus:outline-none focus:ring-2 focus:ring-accent/20"
            />
          </label>

          <div className="min-w-24 text-right">
            <span className="block text-[9px] font-bold uppercase tracking-[0.16em] text-muted-foreground">
              Importe
            </span>
            <p className="mt-1 font-display font-black text-foreground">
              S/ {formatMoney(Number(product.price) * quantity)}
            </p>
          </div>

          {onRemove ? (
            <button
              type="button"
              onClick={onRemove}
              className="rounded-lg border border-red-500/25 bg-red-500/[0.07] px-3 py-2 text-xs font-medium text-red-300 transition hover:bg-red-500/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400"
            >
              Eliminar
            </button>
          ) : null}
        </div>
      </div>
    </article>
  );
}
