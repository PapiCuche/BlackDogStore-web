import { ImagePending } from "./ImagePending";
import { ProductImage } from "./ProductImage";
import { REFERENCE_IMAGE_LABEL } from "../lib/model-configurator";
import Link from "next/link";
import { formatMoney } from "../lib/format";
import {
  NO_UNIT_PHOTOS_TEXT, SOLD_OUT_LABEL, batteryText, imeiHintText, onOrderLabel, optionsText, type UnitPublicPhoto,
} from "../lib/catalog-models";

/**
 * DC3 — where a cart line comes from. A line is all shelf or all «bajo
 * pedido»; a supplier line costs the offer's price, and when the offer no
 * longer covers it the server sends `available: false` and no price.
 */
export type CartSupply =
  | { origin: "own" }
  | { origin: "supplier"; available: boolean; lead_time_days: number | null; price: string | null };

type SellableLine = {
  available?: boolean;
  supply?: CartSupply | null;
  sells_by_unit?: boolean;
  product?: { inventory?: number };
};

/**
 * SUPPLIER-DEVICE-FLOW-02 — a line from the shop's own shelf whose shelf is
 * now empty: still published, but «Agotado». The server refuses to charge it
 * (checkout re-checks the stock); this says so before the form. A device line
 * is not judged here: it holds its own device, and the server says when that
 * hold is gone. Without a stock figure nothing is assumed.
 */
export function isCartLineSoldOut(line: SellableLine): boolean {
  if (line.available === false || line.sells_by_unit) return false;
  if (line.supply && line.supply.origin !== "own") return false;
  const inventory = line.product?.inventory;
  return typeof inventory === "number" && inventory <= 0;
}

/** A line the buyer can pay now: still sold online, not sold out and, if on order, still covered by an offer. */
export function isCartLineSellable(line: SellableLine): boolean {
  if (line.available === false) return false;
  if (line.supply?.origin === "supplier" && !line.supply.available) return false;
  if (isCartLineSoldOut(line)) return false;
  return true;
}

/** A «bajo pedido» line the offer no longer covers. */
export function isSupplyUnavailable(line: { supply?: CartSupply | null }): boolean {
  return line.supply?.origin === "supplier" && !line.supply.available;
}

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
  /** UNIT-PHOTO-02 — a used device: ITS primary photo confirmed as this device, or null. Absent on a new device. */
  photo?: UnitPublicPhoto | null;
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
    /** What the shop's shelf has now; 0 on an own-stock line is «Agotado». */
    inventory?: number;
    /** `new` or `used`. A new device has no battery reading to tell. */
    condition_line?: string;
    /**
     * CATALOG-REFERENCE-01 · G: a new variant of a model with no picture of its own nor of its colour. The server
     * resolves the picture by the model page's rule (`image_url` is then its colour's finish); never another colour's.
     */
    image_pending?: boolean;
    /** That picture is a demo resource: shown and labelled «Imagen referencial», as on the model page. */
    image_is_demo?: boolean;
    /**
     * UNIT-PHOTO-02 — `unit`: a used device's line. `image_url` is THAT device's primary photo, or empty — said
     * «Sin fotos de este equipo», never replaced by a generic picture.
     */
    image_scope?: "unit";
  };
  /** DC2: sold device by device — the quantity is the number of devices, each with its own price. */
  sells_by_unit?: boolean;
  line_total?: string | null;
  units?: CartUnit[];
  /** DC3: shelf or «bajo pedido». Absent on servers before DC3: the shelf. */
  supply?: CartSupply | null;
  onQuantityChange?: (quantity: number) => void;
  onRemove?: () => void;
  onReleaseUnit?: (unitId: string) => void;
};

const REMOVE =
  "min-h-11 rounded-xl border border-danger-border bg-danger-surface px-3 py-2 text-xs font-semibold text-danger transition hover:border-danger-border";

/**
 * A line's picture: what the server resolved (for a new variant of a model, its colour's finish by the model page's
 * rule), the pending marker, or the name's initial for a product with no picture at all. `small`: the checkout's
 * summary (40 px) instead of the cart (64 px).
 */
export function CartThumb({ product, small = false }: {
  product: Pick<CartItemCardProps["product"], "name" | "image_url" | "image_pending" | "image_is_demo" | "image_scope">;
  small?: boolean;
}) {
  // Review of #377: a demo resource is shown as on the model page — and said, never passed off as the product.
  const demo = Boolean(product.image_url && product.image_is_demo);
  return (
    <div className="shrink-0">
      <div
        data-testid="cart-thumb"
        className={`relative flex ${small ? "h-10 w-10 rounded-lg" : "h-16 w-16 rounded-xl"} items-center justify-center overflow-hidden border border-bd-border bg-background`}
      >
        {product.image_url ? (
          <ProductImage src={product.image_url} alt={demo ? REFERENCE_IMAGE_LABEL : ""} sizes={small ? "40px" : "64px"} className="object-contain p-1" />
        ) : product.image_scope === "unit" ? (
          <span data-testid="cart-thumb-no-unit-photo" className="px-1 text-center text-[9px] font-semibold leading-tight text-muted">
            {NO_UNIT_PHOTOS_TEXT}
          </span>
        ) : product.image_pending ? (
          <ImagePending compact className="text-muted" />
        ) : (
          <span className="font-display text-xl font-black text-foreground/[0.12]" aria-hidden="true">
            {product.name.trim().charAt(0).toUpperCase() || "·"}
          </span>
        )}
      </div>
      {demo ? (
        <p aria-hidden="true" data-testid="cart-thumb-demo" className="mt-1 max-w-[4.5rem] text-[10px] font-semibold leading-tight text-muted">
          {REFERENCE_IMAGE_LABEL}
        </p>
      ) : null}
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
  // A battery reading belongs to a used device; a new one says only its options and its IMEI.
  const tellsBattery = product.condition_line !== "new";
  return (
    <div className="rounded-2xl border border-bd-border bg-surface p-4 sm:p-5">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-center gap-4">
          <CartThumb product={product} />
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
              <div className="flex min-w-0 items-center gap-3 text-sm">
                {unit.photo !== undefined ? (
                  // UNIT-PHOTO-02: each used device with ITS photo — or saying it has none.
                  <span data-testid="cart-unit-photo" className="relative flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-lg border border-bd-border bg-background">
                    {unit.photo ? (
                      <ProductImage src={unit.photo.url} alt={unit.photo.alt_text || `Foto de este equipo${unit.photo.view_label ? `: ${unit.photo.view_label}` : ""}`} sizes="40px" className="object-contain p-0.5" />
                    ) : (
                      <span className="px-0.5 text-center text-[8px] leading-tight text-muted">{NO_UNIT_PHOTOS_TEXT}</span>
                    )}
                  </span>
                ) : null}
                <div className="min-w-0">
                {options ? <p className="font-semibold text-foreground">{options}</p> : null}
                <p className="text-muted">
                  {[tellsBattery ? batteryText(unit.battery_health) : "", unit.cosmetic_grade ? `Estado: ${unit.cosmetic_grade}` : "", imei ?? ""]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
                </div>
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
  quantity, available, product, sells_by_unit, line_total, units, supply, onQuantityChange, onRemove, onReleaseUnit,
}: CartItemCardProps) {
  const fromSupplier = supply?.origin === "supplier" ? supply : null;
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
            <CartThumb product={product} />
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

  // A device on order holds no device yet: it is a quantity, like any other line.
  if (sells_by_unit && !fromSupplier) {
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

  const price = fromSupplier ? fromSupplier.price : product.price ?? 0;
  const supplyGone = fromSupplier !== null && !fromSupplier.available;
  const soldOut = isCartLineSoldOut({ available, supply, sells_by_unit, product });
  const unpayable = supplyGone || soldOut;

  return (
    <div className="rounded-2xl border border-bd-border bg-surface p-4 sm:p-5">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-center gap-4">
          <CartThumb product={product} />
          <div className="min-w-0">
            {/* STOREFRONT-V3 — la línea lleva a la ficha: quien duda de lo que
                puso en el carrito vuelve a verlo sin buscarlo. */}
            <Link
              href={`/product/${product.slug}`}
              className="block truncate font-display font-extrabold uppercase tracking-[-0.02em] text-foreground underline-offset-4 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
            >
              {product.name}
            </Link>
            {unpayable ? null : <p className="mt-1 text-sm text-muted">S/ {formatMoney(price ?? 0)} c/u</p>}
            {soldOut ? (
              <p data-testid="cart-line-sold-out" className="mt-1 text-sm font-semibold text-danger">{SOLD_OUT_LABEL}</p>
            ) : null}
            {fromSupplier ? (
              <p
                data-testid="cart-line-supply"
                className={`mt-0.5 text-xs font-semibold ${supplyGone ? "text-danger" : "text-warning"}`}
              >
                {supplyGone ? "Ya no disponible bajo pedido" : onOrderLabel(fromSupplier.lead_time_days)}
              </p>
            ) : null}
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-4 sm:justify-end">
          {soldOut ? null : (
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
          )}
          <p className="min-w-24 text-right font-display font-extrabold tabular-nums text-foreground" data-testid="cart-line-total">
            {unpayable ? "—" : `S/ ${formatMoney(cartLineAmount({ quantity, line_total, product }))}`}
          </p>
          {onRemove ? (
            <button type="button" onClick={onRemove} aria-label={`Eliminar ${product.name}`} className={REMOVE}>
              Eliminar
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
