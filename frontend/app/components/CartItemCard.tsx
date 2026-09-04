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
    <div className="rounded-2xl border border-bd-border bg-surface p-5 transition hover:border-bd-border">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-4">
          <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-xl border border-bd-border bg-surface">
            <img src="/assets/branding/logo-icon.png" alt="" className="h-6 w-6 opacity-10 invert" />
          </div>
          <div>
            <p className="font-display font-black uppercase text-foreground">{product.name}</p>
            <p className="text-sm text-muted">S/ {formatMoney(product.price)} c/u</p>
          </div>
        </div>

        <div className="flex items-center justify-between gap-4 sm:justify-end">
          <div className="flex items-center gap-2">
            <label htmlFor="components-cartitemcard-cant" className="text-xs uppercase tracking-widest text-muted">Cant.</label>
            <input id="components-cartitemcard-cant"
              type="number"
              min={1}
              value={quantity}
              onChange={(e) => onQuantityChange?.(Number(e.target.value))}
              className="w-16 rounded-xl border border-bd-border bg-surface px-2 py-1.5 text-center text-sm text-foreground focus:border-bd-border focus:outline-none"
            />
          </div>
          <p className="font-display font-black text-foreground">S/ {formatMoney(Number(product.price) * quantity)}</p>
          {onRemove && (
            <button
              type="button"
              onClick={onRemove}
              className="rounded-full border border-red-500/20 bg-red-500/[0.08] px-3 py-1.5 text-xs font-medium text-red-400 transition hover:bg-red-500/15"
            >
              Eliminar
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
