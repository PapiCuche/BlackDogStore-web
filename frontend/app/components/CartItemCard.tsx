import { ProductImage } from "./ProductImage";
import Link from "next/link";
import { formatMoney } from "../lib/format";
import { batteryText, imeiHintText, optionsText } from "../lib/catalog-models";

/** DC2: one concrete device held in the cart for this buyer. */
export type CartUnit = {
  id: string;
  price: string;
  options: Record<string, string>;
  battery_health: number | null;
  /** The grade's label, or "" when the device has none. */
  cosmetic_grade: string;
  imei_hint: string;
  held_until: string;
};

/** The earliest hold of a line, as «HH:MM» in the buyer's clock. */
export function heldUntilText(units: CartUnit[]): string | null {
  const times = units.map((unit) => new Date(unit.held_until).getTime()).filter((t) => Number.isFinite(t));
  if (times.length === 0) return null;
  return new Date(Math.min(...times)).toLocaleTimeString("es-PE", { hour: "2-digit", minute: "2-digit", hour12: false });
}

/** What a line costs: the server's `line_total` when it sends one, price × quantity otherwise. */
export function cartLineAmount(line: { quantity: number; line_total?: string | number | null; product: { price?: number | string } }): number {
  if (line.line_total !== undefined && line.line_total !== null && line.line_total !== "") {
    const total = Number(line.line_total);
    if (Number.isFinite(total)) return total;
  }
  return Number(line.product.price ?? 0) * line.quantity;
}

type CartItemCardProps = {
  id: number;
  quantity: number;
  /**
   * INVENTORY-UNIFIED-01. `false`: el producto dejó de venderse en la web
   * después de entrar al carrito. El servidor ya no manda su precio ni su
   * ficha: sólo el nombre y la imagen, para poder decirlo y quitarlo.
   */
  available?: boolean;
  product: {
    id: number;
    name: string;
    price?: number | string;
    slug?: string;
    image_url?: string;
  };
  /** DC2: sold device by device — the quantity is the number of devices, each with its own price. */
  sells_by_unit?: boolean;
  line_total?: string;
  units?: CartUnit[];
  onQuantityChange?: (quantity: number) => void;
  onRemove?: () => void;
  onReleaseUnit?: (unitId: string) => void;
};

const REMOVE =
  "min-h-11 rounded-xl border border-danger-border bg-danger-surface px-3 py-2 text-xs font-semibold text-danger transition hover:border-danger-border";

function Thumb({ product }: { product: CartItemCardProps["product"] }) {
  return (
    <div className="relative flex h-16 w-16 shrink-0 items-center justify-center overflow-hidden rounded-xl border border-bd-border bg-background">
      {product.image_url ? (
        <ProductImage src={product.image_url} alt="" sizes="64px" className="object-contain p-1" />
      ) : (
        <span className="font-display text-xl font-black text-foreground/[0.12]" aria-hidden="true">
          {product.name.trim().charAt(0).toUpperCase() || "·"}
        </span>
      )}
    </div>
  );
}

function UnitLine({ product, units, lineTotal, onRemove, onReleaseUnit }: {
  product: CartItemCardProps["product"];
  units: CartUnit[];
  lineTotal: number;
  onRemove?: () => void;
  onReleaseUnit?: (unitId: string) => void;
}) {
  const held = heldUntilText(units);
  const count = units.length;
  return (
    <div className="rounded-2xl border border-bd-border bg-surface p-4 sm:p-5">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-center gap-4">
          <Thumb product={product} />
          <div className="min-w-0">
            <Link
              href={`/product/${product.slug}`}
              className="block truncate font-display font-extrabold uppercase tracking-[-0.02em] text-foreground underline-offset-4 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
            >
              {product.name}
            </Link>
            <p className="mt-1 text-sm text-muted">{count === 1 ? "1 equipo" : `${count} equipos`}</p>
            {held ? <p className="mt-0.5 text-xs text-muted">Reservado para ti hasta {held}</p> : null}
          </div>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-4 sm:justify-end">
          <p className="min-w-24 text-right font-display font-extrabold tabular-nums text-foreground" data-testid="cart-line-total">
            S/ {formatMoney(lineTotal)}
          </p>
          {onRemove ? (
            <button type="button" onClick={onRemove} aria-label={`Eliminar ${product.name}`} className={REMOVE}>
              Eliminar
            </button>
          ) : null}
        </div>
      </div>

      <ul className="mt-4 divide-y divide-bd-border border-t border-bd-border" aria-label={`Equipos de ${product.name}`}>
        {units.map((unit, index) => {
          const imei = imeiHintText(unit.imei_hint);
          const options = optionsText(unit.options ?? {});
          const describe = imei ?? `equipo ${index + 1}`;
          return (
            <li key={unit.id} className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0 text-sm">
                {options ? <p className="font-semibold text-foreground">{options}</p> : null}
                <p className="text-muted">
                  {[batteryText(unit.battery_health), unit.cosmetic_grade ? `Estado: ${unit.cosmetic_grade}` : "", imei ?? ""]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
              </div>
              <div className="flex items-center justify-between gap-4 sm:justify-end">
                <p className="font-semibold tabular-nums text-foreground">S/ {formatMoney(unit.price)}</p>
                {onReleaseUnit ? (
                  <button
                    type="button"
                    onClick={() => onReleaseUnit(unit.id)}
                    aria-label={`Quitar ${describe}`}
                    className="min-h-11 rounded-xl border border-bd-border px-3 py-2 text-xs font-semibold text-foreground transition hover:border-foreground/25 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
                  >
                    Quitar
                  </button>
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function CartItemCard({
  quantity, available, product, sells_by_unit, line_total, units, onQuantityChange, onRemove, onReleaseUnit,
}: CartItemCardProps) {
  function updateQuantity(raw: string) {
    const next = Number(raw);
    if (!Number.isFinite(next) || next < 1) return;
    onQuantityChange?.(Math.floor(next));
  }

  if (available === false) {
    // Sin precio, sin cantidad y sin enlace: la ficha ya no existe para el público.
    return (
      <div className="rounded-2xl border border-dashed border-bd-border bg-surface p-4 sm:p-5">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex min-w-0 items-center gap-4 opacity-70">
            <Thumb product={product} />
            <div className="min-w-0">
              <p className="truncate font-display font-extrabold uppercase tracking-[-0.02em] text-foreground">
                {product.name}
              </p>
              <p className="mt-1 text-sm font-semibold text-danger">Ya no está disponible</p>
            </div>
          </div>
          {onRemove ? (
            <button type="button" onClick={onRemove} aria-label={`Eliminar ${product.name}`} className={REMOVE}>
              Eliminar
            </button>
          ) : null}
        </div>
      </div>
    );
  }

  if (sells_by_unit) {
    return (
      <UnitLine
        product={product}
        units={units ?? []}
        lineTotal={cartLineAmount({ quantity, line_total, product })}
        onRemove={onRemove}
        onReleaseUnit={onReleaseUnit}
      />
    );
  }

  const price = product.price ?? 0;

  return (
    <div className="rounded-2xl border border-bd-border bg-surface p-4 sm:p-5">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-center gap-4">
          <Thumb product={product} />
          <div className="min-w-0">
            {/* STOREFRONT-V3 — la línea lleva a la ficha: quien duda de lo que
                puso en el carrito vuelve a verlo sin buscarlo. */}
            <Link
              href={`/product/${product.slug}`}
              className="block truncate font-display font-extrabold uppercase tracking-[-0.02em] text-foreground underline-offset-4 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
            >
              {product.name}
            </Link>
            <p className="mt-1 text-sm text-muted">S/ {formatMoney(price)} c/u</p>
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-4 sm:justify-end">
          <label className="flex items-center gap-2 text-xs font-semibold text-muted">
            Cantidad
            <input
              type="number"
              min={1}
              value={quantity}
              // Con dos líneas hay dos campos «Cantidad»: cada uno dice de qué.
              aria-label={`Cantidad de ${product.name}`}
              onChange={(event) => updateQuantity(event.target.value)}
              className="h-11 w-16 rounded-xl border border-bd-border bg-background px-2 text-center text-sm tabular-nums text-foreground focus:border-foreground/25 focus:outline-none"
            />
          </label>
          <p className="min-w-24 text-right font-display font-extrabold tabular-nums text-foreground">
            S/ {formatMoney(cartLineAmount({ quantity, line_total, product }))}
          </p>
          {onRemove ? (
            <button type="button" onClick={onRemove} className={REMOVE}>
              Eliminar
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
