"use client";

/**
 * P2 · F1 — the model page's behaviour, without its look.
 *
 * Until F1 this lived inside `ModelDetail`, mixed with the V3 markup. The V4
 * preview (`/v4/modelo/[slug]`) needs exactly the same behaviour with another
 * presentation, so it is here once and both designs call it:
 *
 *  - `useCatalogModel`: reads the model, and reads it again on demand (after a
 *    refusal such as «unit_taken»);
 *  - `useLineChoice`: which line (new / used) is shown;
 *  - `useNewLineConfigurator`: the chosen combination, its gallery, and adding
 *    one device of it to the cart (from the shelf or «bajo pedido»);
 *  - `useUsedLineConfigurator`: the filters over used devices, and buying one;
 *  - `useAccessoryCart` (P2 · F3): adding one accessory that fits the model.
 *
 * Nothing here decides a word or a colour: every label still comes from the
 * API or from `catalog-models.ts`.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { emitCartChange } from "./cart";
import { DEFAULT_CURRENCY, toItem } from "./analytics/items";
import { track } from "./analytics/service";
import { isManagedStorefrontImage } from "./storefront-media";
import {
  CartError,
  addDeviceToCart,
  addProductToCart,
  fetchCatalogModel,
  initialSelection,
  isOnOrderVariant,
  matchVariant,
  overStockOffer,
  roleOrderedAttributes,
  selectOption,
  sortedAttributes,
  type CatalogModel,
  type ConditionLine,
  type ModelAccessory,
  type ModelAttribute,
  type OnOrder,
  type Selection,
  type UsedUnit,
  type Variant,
  type VariantImage,
} from "./catalog-models";

/** What the page tells the buyer after a cart write. */
export type ConfiguratorStatus = { kind: "success" | "error"; message: string } | null;

export const CONDITION_LINES: ConditionLine[] = ["new", "used"];

/**
 * The order the attributes (and so the hierarchical selection) follow:
 * `sort_order` as the company set it (V3, unchanged), or P2 · V4 `role`:
 * capacity, colour, SIM, region, then the rest (`roleOrderedAttributes`).
 */
export type AttributeOrder = "sort_order" | "role";

function orderedAttributes(attributes: ModelAttribute[], order: AttributeOrder): ModelAttribute[] {
  return order === "role" ? roleOrderedAttributes(attributes) : sortedAttributes(attributes);
}

/**
 * The refusals that mean the page's availability is stale: the model is read
 * again so it stops offering what is gone. One rule for devices and accessories.
 */
export function refusalChangedStock(error: unknown): boolean {
  return error instanceof CartError && error.code === "unit_taken";
}

export function cartErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "No se pudo agregar al carrito.";
}

// ---------------------------------------------------------------------------
// The model
// ---------------------------------------------------------------------------

/**
 * `model`: `undefined` while loading, `null` when there is no such published
 * model. `error`: why the read failed. `reload` reads it again.
 *
 * P2 · F2 — `initial`: what the server already read for this request (the V4
 * page), shown from the first paint. It is still read again here: prices and
 * availability move. Without it (V3) nothing changes: the page starts loading.
 */
export function useCatalogModel(slug: string, initial?: CatalogModel | null) {
  const [model, setModel] = useState<CatalogModel | null | undefined>(initial);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const reload = useCallback(() => setReloadKey((key) => key + 1), []);

  useEffect(() => {
    let cancelled = false;
    fetchCatalogModel(slug).then(
      (data) => { if (!cancelled) { setModel(data); setError(null); } },
      (err) => { if (!cancelled) setError(err instanceof Error ? err.message : "No se pudo cargar el modelo."); },
    );
    return () => { cancelled = true; };
  }, [slug, reloadKey]);

  return { model, error, reload };
}

/** The lines with something to show, in the page's order. */
export function offeredLines(model: CatalogModel | null | undefined): ConditionLine[] {
  return model ? CONDITION_LINES.filter((key) => (model.lines?.[key]?.count ?? 0) > 0) : [];
}

/**
 * The buyer's choice while it is still offered; otherwise `?line=`, otherwise
 * the first line with something. `null` when no line has anything.
 */
export function useLineChoice(model: CatalogModel | null | undefined, initialLine?: string) {
  const [chosenLine, setLine] = useState<ConditionLine | null>(null);
  const offered = offeredLines(model);
  const line: ConditionLine | null =
    chosenLine && offered.includes(chosenLine)
      ? chosenLine
      : initialLine && offered.includes(initialLine as ConditionLine)
        ? (initialLine as ConditionLine)
        : offered[0] ?? null;
  return { offered, line, setLine };
}

/** The server's word when it sends one; before it, the same rule from what it sent. */
export function isModelSoldOut(model: CatalogModel): boolean {
  return model.sold_out
    ?? (!(model.variants ?? []).some((variant) => variant.available) && (model.used_units ?? []).length === 0);
}

// ---------------------------------------------------------------------------
// Where a picture comes from
// ---------------------------------------------------------------------------

/** A picture the shop did not upload (a supplier's or a manufacturer's CDN): it shows the product, not this one. */
export const REFERENCE_IMAGE_LABEL = "Imagen referencial";
/** The real photos of one used device, uploaded by the shop. */
export const DEVICE_PHOTOS_LABEL = "Fotos de este equipo";

/**
 * The caption a picture carries, or `null` for none.
 *
 * Only what the shop uploaded from its panel is the shop's own picture
 * (`isManagedStorefrontImage`). Anything else is «Imagen referencial» — also
 * on a used device, whose photos are only presented as «this device» when the
 * shop uploaded them.
 */
export function photoLabel(url: string, source: "catalog" | "device"): string | null {
  if (!isManagedStorefrontImage(url)) return REFERENCE_IMAGE_LABEL;
  return source === "device" ? DEVICE_PHOTOS_LABEL : null;
}

// ---------------------------------------------------------------------------
// New line
// ---------------------------------------------------------------------------

/** The chosen variant's pictures; the model's own one when the variant has none. */
export function variantGallery(model: CatalogModel, variant: Variant | null): VariantImage[] {
  if (variant?.images?.length) return variant.images;
  const url = variant?.image_url || model.image_url;
  return url ? [{ url, alt_text: "", is_primary: true, width: 0, height: 0 }] : [];
}

export type NewLineConfigurator = {
  attributes: ModelAttribute[];
  variants: Variant[];
  selection: Selection;
  /** The chosen combination; `null` when it matches no variant. */
  variant: Variant | null;
  gallery: VariantImage[];
  imageIndex: number;
  /** The picture on show, or `null` when there is none. */
  shown: VariantImage | null;
  showImage: (index: number) => void;
  choose: (attributeIndex: number, optionId: number) => void;
  /** Adds one device of the chosen variant; `onOrder` asks the supplier for it. */
  add: (onOrder: boolean) => Promise<void>;
  busy: boolean;
  status: ConfiguratorStatus;
  /** DC3: the shelf ran out while the buyer looked, and this offer covers it. */
  offer: OnOrder | null;
  available: boolean;
  /** DC3: no own device, but an offer covers it. */
  onOrder: OnOrder | null;
  /** An own device is there, and the company lets the buyer ask the supplier anyway. */
  overStock: OnOrder | null;
};

export function useNewLineConfigurator(
  model: CatalogModel, onReload: () => void, opts: { order?: AttributeOrder } = {},
): NewLineConfigurator {
  const order = opts.order ?? "sort_order";
  const attributes = useMemo(() => orderedAttributes(model.attributes ?? [], order), [model.attributes, order]);
  const variants = useMemo(() => model.variants ?? [], [model.variants]);
  const [state, setState] = useState<{ source: Variant[]; selection: Selection }>(() => ({
    source: variants, selection: initialSelection(variants, attributes),
  }));
  let selection = state.selection;
  if (state.source !== variants) {
    // Fresh data (after a reload) may have sold the current choice out: it stays chosen and says so.
    // Only a choice that no longer exists starts again from the first available one.
    const still = matchVariant(variants, attributes, state.selection);
    selection = still ? state.selection : initialSelection(variants, attributes);
    setState({ source: variants, selection });
  }
  // null = the variant's primary picture (CATALOG-VARIANT-IMAGES): its gallery may list another one first.
  const [chosenImage, setChosenImage] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<ConfiguratorStatus>(null);
  const [offer, setOffer] = useState<OnOrder | null>(null);

  const variant = matchVariant(variants, attributes, selection);
  const gallery = variantGallery(model, variant);
  const imageIndex = chosenImage ?? Math.max(0, gallery.findIndex((image) => image.is_primary));
  const shown = gallery[imageIndex] ?? gallery[0] ?? null;

  function choose(index: number, optionId: number) {
    setState((current) => ({ ...current, selection: selectOption(variants, attributes, current.selection, index, optionId) }));
    setChosenImage(null);
    setStatus(null);
    setOffer(null);
  }

  async function add(onOrder: boolean) {
    if (!variant) return;
    setBusy(true);
    setStatus(null);
    setOffer(null);
    try {
      // DC3: «bajo pedido» holds no device; the line waits for the supplier.
      await addDeviceToCart(variant.product_id, undefined, { onOrder });
      const price = onOrder ? (variant.on_order?.price ?? offer?.price ?? variant.price) : variant.price;
      setStatus({ kind: "success", message: onOrder ? "Agregado al carrito bajo pedido." : "Agregado al carrito." });
      emitCartChange();
      track({
        name: "ADD_TO_CART", item: toItem({ id: variant.product_id, name: variant.name, price }, 1),
        currency: DEFAULT_CURRENCY, value: Number(price),
      });
    } catch (error) {
      if (error instanceof CartError && error.code === "on_order_available" && error.onOrder) {
        // The last device went while the buyer looked; an offer still covers it. Their call.
        setOffer(error.onOrder);
        return;
      }
      setStatus({ kind: "error", message: cartErrorMessage(error) });
      if (refusalChangedStock(error)) onReload();
    } finally {
      setBusy(false);
    }
  }

  const available = Boolean(variant?.available);
  const onOrder = variant && isOnOrderVariant(variant) ? variant.on_order ?? null : null;
  const overStock = variant && !onOrder && variant.availability === "own_stock" ? overStockOffer(variant.on_order) : null;

  return {
    attributes, variants, selection, variant, gallery, imageIndex, shown,
    showImage: setChosenImage, choose, add, busy, status, offer, available, onOrder, overStock,
  };
}

// ---------------------------------------------------------------------------
// Used line
// ---------------------------------------------------------------------------

export type UsedLineFilter = { name: string; values: string[] };

export type UsedLineConfigurator = {
  attributes: ModelAttribute[];
  /** Attribute names in the model's order, so every device's options read the same. */
  order: string[];
  units: UsedUnit[];
  /** One filter per attribute, offering only the values some device actually has. */
  filterable: UsedLineFilter[];
  filters: Record<string, string>;
  setFilter: (name: string, value: string) => void;
  visible: UsedUnit[];
  busyUnit: string | null;
  status: ConfiguratorStatus;
  /** The device `status` is about: a page that shows one device at a time says it only next to that one. */
  statusUnit: UsedUnit | null;
  buy: (unit: UsedUnit) => Promise<void>;
};

export function useUsedLineConfigurator(
  model: CatalogModel, onReload: () => void, opts: { order?: AttributeOrder } = {},
): UsedLineConfigurator {
  const attributeOrder = opts.order ?? "sort_order";
  const attributes = useMemo(
    () => orderedAttributes(model.attributes ?? [], attributeOrder), [model.attributes, attributeOrder],
  );
  const order = attributes.map((a) => a.name);
  const units = model.used_units ?? [];
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [busyUnit, setBusyUnit] = useState<string | null>(null);
  const [said, setSaid] = useState<{ status: ConfiguratorStatus; unit: UsedUnit | null }>({ status: null, unit: null });

  const filterable = attributes
    .map((attribute) => {
      const present = new Set(units.map((unit) => unit.options?.[attribute.name]).filter(Boolean) as string[]);
      const values = [
        ...attribute.options.map((o) => o.label).filter((label) => present.has(label)),
        ...[...present].filter((label) => !attribute.options.some((o) => o.label === label)),
      ];
      return { name: attribute.name, values };
    })
    .filter((f) => f.values.length > 1);

  const visible = units.filter((unit) =>
    Object.entries(filters).every(([name, value]) => !value || unit.options?.[name] === value),
  );

  function setFilter(name: string, value: string) {
    setFilters((current) => ({ ...current, [name]: value }));
  }

  async function buy(unit: UsedUnit) {
    setBusyUnit(unit.id);
    setSaid({ status: null, unit: null });
    try {
      await addDeviceToCart(unit.product_id, unit.id);
      setSaid({ status: { kind: "success", message: "Equipo agregado al carrito. Lo reservamos para ti por un tiempo." }, unit });
      emitCartChange();
      track({
        name: "ADD_TO_CART", item: toItem({ id: unit.product_id, name: model.name, price: unit.price }, 1),
        currency: DEFAULT_CURRENCY, value: Number(unit.price),
      });
    } catch (error) {
      setSaid({ status: { kind: "error", message: cartErrorMessage(error) }, unit });
      if (refusalChangedStock(error)) onReload();
    } finally {
      setBusyUnit(null);
    }
  }

  return {
    attributes, order, units, filterable, filters, setFilter, visible, busyUnit,
    status: said.status, statusUnit: said.unit, buy,
  };
}

// ---------------------------------------------------------------------------
// Accessories (P2 · F3)
// ---------------------------------------------------------------------------

/** The accessory's picture: its primary one, else its first, else its `image_url`; `null` for none. */
export function accessoryPicture(accessory: ModelAccessory): { url: string; alt_text: string } | null {
  const images = accessory.images ?? [];
  const image = images.find((candidate) => candidate.is_primary) ?? images[0];
  if (image?.url) return { url: image.url, alt_text: image.alt_text };
  return accessory.image_url ? { url: accessory.image_url, alt_text: "" } : null;
}

export type AccessoryCart = {
  /** DC3: no own stock, but an offer covers it — sold «bajo pedido», at the offer's price. */
  onOrder: OnOrder | null;
  /** Own stock is there, and the company lets the buyer ask the supplier anyway (`over_stock`). */
  overStock: OnOrder | null;
  /** Something can be added: own stock, or an offer. `none` (sold out) adds nothing. */
  purchasable: boolean;
  /** The price the buyer pays: the offer's when it is on order. */
  price: string;
  /** Adds ONE of it; `onOrder` asks the supplier. */
  add: (onOrder: boolean) => Promise<void>;
  busy: boolean;
  status: ConfiguratorStatus;
  /** DC3: the shelf ran out while the buyer looked, and this offer covers it. */
  offer: OnOrder | null;
};

/**
 * One accessory into the cart, by the same path as the product page
 * (`ProductDetail`: `addProductToCart`, quantity 1). A refusal is said in the
 * server's words; an offer that would cover a shelf that ran out is shown,
 * never taken alone. A refusal that says the stock changed (`unit_taken`, as
 * for a device) calls `onReload`, so the card stops saying «En stock».
 */
export function useAccessoryCart(accessory: ModelAccessory, onReload?: () => void): AccessoryCart {
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<ConfiguratorStatus>(null);
  const [offer, setOffer] = useState<OnOrder | null>(null);
  const onOrder = isOnOrderVariant(accessory) ? accessory.on_order ?? null : null;
  const purchasable = accessory.availability === "own_stock" || onOrder !== null;
  const overStock = accessory.availability === "own_stock" ? overStockOffer(accessory.on_order) : null;

  async function add(asOnOrder: boolean) {
    if (!purchasable && !asOnOrder) return;
    setBusy(true);
    setStatus(null);
    setOffer(null);
    try {
      await addProductToCart(accessory.id, 1, { onOrder: asOnOrder });
      const price = asOnOrder ? (onOrder?.price ?? offer?.price ?? overStock?.price ?? accessory.price) : accessory.price;
      setStatus({ kind: "success", message: asOnOrder ? "Agregado al carrito bajo pedido." : "Agregado al carrito." });
      emitCartChange();
      track({
        name: "ADD_TO_CART", item: toItem({ id: accessory.id, name: accessory.name, price }, 1),
        currency: DEFAULT_CURRENCY, value: Number(price),
      });
    } catch (error) {
      if (error instanceof CartError && error.code === "on_order_available" && error.onOrder) {
        setOffer(error.onOrder);
        return;
      }
      setStatus({ kind: "error", message: cartErrorMessage(error) });
      if (refusalChangedStock(error)) onReload?.();
    } finally {
      setBusy(false);
    }
  }

  return {
    onOrder, overStock, purchasable, price: onOrder ? onOrder.price : accessory.price, add, busy, status, offer,
  };
}
