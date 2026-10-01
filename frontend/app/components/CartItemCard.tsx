import Image from "next/image";
import { formatMoney } from "../lib/format";

type CartItemCardProps = {
  id: number;
  quantity: number;
  product: {
    id: number;
    name: string;
    price: number | string;
    slug: string;
    image_url?: string;
  };
  onQuantityChange?: (quantity: number) => void;
  onRemove?: () => void;
};

export function CartItemCard({ quantity, product, onQuantityChange, onRemove }: CartItemCardProps) {
  function updateQuantity(raw: string) {
    const next = Number(raw);
    if (!Number.isFinite(next) || next < 1) return;
    onQuantityChange?.(Math.floor(next));
  }

  return (
    <div className="rounded-2xl border border-bd-border bg-surface p-4 sm:p-5">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-center gap-4">
          <div className="relative flex h-16 w-16 shrink-0 items-center justify-center overflow-hidden rounded-xl border border-bd-border bg-background">
            {product.image_url ? (
              <Image
                src={product.image_url}
                alt=""
                fill
                sizes="64px"
                className="object-cover"
              />
            ) : (
              <span className="font-display text-xl font-black text-foreground/[0.12]" aria-hidden="true">
                {product.name.trim().charAt(0).toUpperCase() || "·"}
              </span>
            )}
          </div>
          <div className="min-w-0">
            <p className="truncate font-display font-extrabold uppercase tracking-[-0.02em] text-foreground">
              {product.name}
            </p>
            <p className="mt-1 text-sm text-muted">S/ {formatMoney(product.price)} c/u</p>
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-4 sm:justify-end">
          <label className="flex items-center gap-2 text-xs font-semibold text-muted">
            Cantidad
            <input
              type="number"
              min={1}
              value={quantity}
              onChange={(event) => updateQuantity(event.target.value)}
              className="w-16 rounded-xl border border-bd-border bg-background px-2 py-2 text-center text-sm tabular-nums text-foreground focus:border-foreground/25 focus:outline-none"
            />
          </label>
          <p className="min-w-24 text-right font-display font-extrabold tabular-nums text-foreground">
            S/ {formatMoney(Number(product.price) * quantity)}
          </p>
          {onRemove ? (
            <button
              type="button"
              onClick={onRemove}
              className="rounded-xl border border-red-500/25 bg-red-500/10 px-3 py-2 text-xs font-semibold text-red-300 transition hover:border-red-400/50"
            >
              Eliminar
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
