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
 *  - `useUsedLineConfigurator`: the filters over used devices, and buying one.
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
  fetchCatalogModel,
  initialSelection,
  isOnOrderVariant,
  matchVariant,
  overStockOffer,
  selectOption,
  sortedAttributes,
  type CatalogModel,
  type ConditionLine,
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

export function cartErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "No se pudo agregar al carrito.";
}

// ---------------------------------------------------------------------------
// The model
// ---------------------------------------------------------------------------

/**
 * `model`: `undefined` while loading, `null` when there is no such published
 * model. `error`: why the read failed. `reload` reads it again.
 */
export function useCatalogModel(slug: string) {
  const [model, setModel] = useState<CatalogModel | null | undefined>(undefined);
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

export function useNewLineConfigurator(model: CatalogModel, onReload: () => void): NewLineConfigurator {
  const attributes = useMemo(() => sortedAttributes(model.attributes ?? []), [model.attributes]);
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
      if (error instanceof CartError && error.code === "unit_taken") onReload();
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
  buy: (unit: UsedUnit) => Promise<void>;
};

export function useUsedLineConfigurator(model: CatalogModel, onReload: () => void): UsedLineConfigurator {
  const attributes = useMemo(() => sortedAttributes(model.attributes ?? []), [model.attributes]);
  const order = attributes.map((a) => a.name);
  const units = model.used_units ?? [];
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [busyUnit, setBusyUnit] = useState<string | null>(null);
  const [status, setStatus] = useState<ConfiguratorStatus>(null);

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
    setStatus(null);
    try {
      await addDeviceToCart(unit.product_id, unit.id);
      setStatus({ kind: "success", message: "Equipo agregado al carrito. Lo reservamos para ti por un tiempo." });
      emitCartChange();
      track({
        name: "ADD_TO_CART", item: toItem({ id: unit.product_id, name: model.name, price: unit.price }, 1),
        currency: DEFAULT_CURRENCY, value: Number(unit.price),
      });
    } catch (error) {
      setStatus({ kind: "error", message: cartErrorMessage(error) });
      if (error instanceof CartError && error.code === "unit_taken") onReload();
    } finally {
      setBusyUnit(null);
    }
  }

  return { attributes, order, units, filterable, filters, setFilter, visible, busyUnit, status, buy };
}
