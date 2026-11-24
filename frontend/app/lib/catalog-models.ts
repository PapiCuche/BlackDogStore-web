/**
 * DEVICE-COMMERCE-01 · DC2 — models, variants and used devices in the shop.
 *
 * A MODEL is what a buyer thinks they are buying («the phone»); its VARIANTS are
 * the products the shop actually stocks (one per combination of options), and a
 * USED UNIT is one concrete second-hand device with its own price, battery and
 * photos. Everything here comes from the API: labels, options, colours and
 * prices are the tenant's, never this file's.
 *
 * Nothing is decided here that the server has not said. Availability is the
 * server's `available` flag, a battery with no reading stays without a number,
 * and an IMEI is only ever the hint the server chose to publish.
 */

import { API_BASE, apiUrl } from "./api";
import { fetchWithAuth } from "./auth";
import { getSessionKey } from "./cart";

// ---------------------------------------------------------------------------
// Types — exactly the public contract
// ---------------------------------------------------------------------------

export type ConditionLine = "new" | "used";

export type LineSummary = { label: string; count: number };
export type Lines = Record<ConditionLine, LineSummary>;

export type CategoryRef = { slug: string; name: string } | null;

export type CatalogModelCard = {
  slug: string;
  name: string;
  image_url: string;
  category: CategoryRef;
  from_price: string | null;
  has_new: boolean;
  has_used: boolean;
  /**
   * SUPPLIER-DEVICE-FLOW-02: published, but nothing of it can be bought now
   * (in the line asked for). Shown with its photo and last price as «Agotado».
   * Absent on servers before it: treated as on sale.
   */
  sold_out?: boolean;
};

export type CatalogModelList = { lines: Lines; results: CatalogModelCard[] };

/** `swatch_hex`: the swatch the server resolved for THIS model (its own, else the company's; '' = none). */
export type AttributeOption = { id: number; label: string; swatch_hex: string; sort_order: number };

/**
 * CATALOG-COMMERCIAL-01 · A1 — WHAT an attribute means, whatever the company
 * calls it. A closed technical vocabulary (`VariantAttribute.ROLE_CHOICES`).
 */
export type AttributeRole = "capacity" | "color" | "sim" | "region" | "other";

/**
 * The order the roles read in on a purchase page: capacity → colour → SIM →
 * region → the rest. The same as the panel's `ROLE_ORDER` and the server's
 * `VariantAttribute.ROLE_ORDER` (a test pins the panel's).
 */
export const ATTRIBUTE_ROLE_ORDER: readonly AttributeRole[] = ["capacity", "color", "sim", "region", "other"];

export type ModelAttribute = {
  id: number;
  name: string;
  kind: "text" | "color";
  /** P2 · V4: the attribute's role. Optional: a server that does not send it leaves `sort_order` in charge. */
  role?: AttributeRole;
  sort_order: number;
  options: AttributeOption[];
};

export type VariantImage = {
  url: string; alt_text: string; is_primary: boolean; width: number; height: number;
  /** CATALOG-REFERENCE-01 · PR C: a demo resource (labelled «Imagen referencial»). Absent: not one. */
  is_demo?: boolean;
  /** FASE 1.2 · Y: a finish picture's view (main, front, back, side, other; '' or absent = not said). */
  view?: string;
};

/**
 * DC3 — what a product costs and how long it takes when it is sold «bajo
 * pedido». `null` = there is no offer that covers it. Who supplies it and at
 * what cost is never part of the public contract.
 */
export type OnOrder = {
  lead_time_days: number;
  price: string;
  /**
   * Owner's decision: present (true) only when the company lets a web buyer ask
   * the supplier although there is own stock. Absent otherwise.
   */
  over_stock?: boolean;
};

export type Variant = {
  product_id: number;
  slug: string;
  name: string;
  /** attribute id (as a string) → option id. */
  options: Record<string, number>;
  price: string;
  image_url: string;
  images: VariantImage[];
  /** True for own stock AND for a supplier offer. */
  available: boolean;
  /** `own_stock` | `supplier` | `none`; `availabilityLabel` is the one place that reads it. */
  availability: string;
  sells_by_unit: boolean;
  /** DC3: the offer when `availability` is `supplier`. */
  on_order?: OnOrder | null;
  /**
   * CATALOG-REFERENCE-01 · PR C — what the variant shows, as the server resolved it: its own pictures, else its
   * finish's (model + colour), else empty with `image_pending`. Absent on older servers (`variantGallery` applies
   * the same rule then).
   */
  gallery?: VariantImage[];
  image_pending?: boolean;
};

/**
 * P2 · F3 — an accessory that fits the model (`accessory_compat.for_model_public`):
 * only what the shop sells online, in the company's order. The same words as a
 * variant for its price and availability; never a quantity or a cost.
 */
export type ModelAccessory = {
  /** The product's id: what the cart takes. */
  id: number;
  slug: string;
  name: string;
  price: string;
  image_url: string;
  images: VariantImage[];
  available: boolean;
  /** `own_stock` | `supplier` | `none`, as a variant's. */
  availability: string;
  on_order?: OnOrder | null;
  sells_by_unit: boolean;
};

export type CosmeticGrade = { code: string; label: string; description: string };

export type UnitPublicPhoto = {
  url: string; alt_text: string; width: number; height: number;
  view?: string; view_label?: string; defect_note?: string; is_primary?: boolean;
};

/** UNIT-PHOTO-02 — what a used device says when it has no confirmed photo: never a generic picture in its place. */
export const NO_UNIT_PHOTOS_TEXT = "Sin fotos de este equipo";

export type UsedUnit = {
  id: string;
  product_id: number;
  /** attribute NAME → option label, e.g. {"Capacidad": "128 GB"}. */
  options: Record<string, string>;
  battery_health: number | null;
  cosmetic_grade: CosmeticGrade | null;
  price: string;
  /**
   * UNIT-PHOTO-02 — the photos of THIS device confirmed as such (never a generic picture), the primary first. Each says
   * which side it shows (`view_label`: «Frontal», «Defecto: …»); absent on older servers.
   */
  photos: UnitPublicPhoto[];
  notes: string;
  imei_hint: string;
  /**
   * USED-READY-01 — what the shop states about THIS device. Absent on older servers. `warranty_days` null: the
   * company's warranty policy applies; 0: sold without warranty.
   */
  warranty_days?: number | null;
  /** Where `warranty_days` comes from: the device itself, the company's default, or only its policy text. */
  warranty_source?: "unit" | "company" | "policy";
  functional_state?: "full" | "with_faults" | null;
  functional_state_label?: string;
  defects?: string;
  accessories?: string;
};

/** «90 días de garantía», «Sin garantía», or null when the shop's policy applies (the page shows it). */
export function unitWarrantyText(days: number | null | undefined): string | null {
  if (days === null || days === undefined) return null;
  if (days === 0) return "Sin garantía";
  return days === 1 ? "1 día de garantía" : `${days} días de garantía`;
}

export type CatalogModel = {
  slug: string;
  name: string;
  description: string;
  image_url: string;
  category: CategoryRef;
  attributes: ModelAttribute[];
  lines: Lines;
  variants: Variant[];
  used_units: UsedUnit[];
  /** P2 · F3: accessories that fit it. Empty or absent (older servers): there are none to show. */
  accessories?: ModelAccessory[];
  /** SUPPLIER-DEVICE-FLOW-02: no variant available and no used device. Absent on older servers. */
  sold_out?: boolean;
  /** CATALOG-REFERENCE-01 · PR C: colour option id → the pictures of that finish. Absent on older servers. */
  finish_images?: Record<string, VariantImage[]>;
  as_of: string;
};

// ---------------------------------------------------------------------------
// Reading
// ---------------------------------------------------------------------------

export async function fetchCatalogModels(params: { category?: string; line?: ConditionLine | "" }): Promise<CatalogModelList> {
  const qs = new URLSearchParams();
  if (params.category) qs.set("category", params.category);
  if (params.line) qs.set("line", params.line);
  const query = qs.toString();
  const res = await fetch(apiUrl(`/catalog/models/${query ? `?${query}` : ""}`));
  if (!res.ok) throw new Error("No se pudieron cargar los modelos.");
  const body = await res.json();
  return {
    lines: body?.lines ?? { new: { label: "", count: 0 }, used: { label: "", count: 0 } },
    results: Array.isArray(body?.results) ? body.results : [],
  };
}

/** `null` is «there is no such published model»: the page says so instead of failing. */
export async function fetchCatalogModel(slug: string): Promise<CatalogModel | null> {
  const res = await fetch(apiUrl(`/catalog/models/${encodeURIComponent(slug)}/`));
  if (res.status === 404) return null;
  if (!res.ok) throw new Error("No se pudo cargar el modelo.");
  return res.json();
}

// ---------------------------------------------------------------------------
// Cart writes
// ---------------------------------------------------------------------------

export type CartErrorCode =
  | "unit_required" | "unit_taken" | "sold_out" | "too_many" | "held_by_others" | "not_found"
  // DC3 — «bajo pedido».
  | "on_order_available" | "not_on_order" | "mixed_supply"
  // The company turned «proveedor aunque haya stock» off meanwhile.
  | "stock_available"
  | "";

/** A refusal from the cart, in the server's words. `code` lets the page react (e.g. reload on unit_taken). */
export class CartError extends Error {
  readonly code: CartErrorCode;
  /**
   * DC3: with `on_order_available`, the offer that WOULD cover the quantity —
   * the shelf does not. The page offers it to the buyer; it never adds it alone.
   */
  readonly onOrder: OnOrder | null;
  constructor(message: string, code: CartErrorCode, onOrder: OnOrder | null = null) {
    super(message);
    this.name = "CartError";
    this.code = code;
    this.onOrder = onOrder;
  }
}

function readOnOrder(value: unknown): OnOrder | null {
  if (!value || typeof value !== "object") return null;
  const offer = value as { lead_time_days?: unknown; price?: unknown };
  if (typeof offer.lead_time_days !== "number" || offer.price === undefined || offer.price === null) return null;
  return { lead_time_days: offer.lead_time_days, price: String(offer.price) };
}

async function cartPost<T>(path: string, body: Record<string, unknown>, fallback: string): Promise<T> {
  // `fetchWithAuth`: with a signed-in session the server demands the CSRF token.
  const res = await fetchWithAuth(`${API_BASE}${path}`, { method: "POST", body: JSON.stringify(body) });
  const payload = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = payload && typeof payload.detail === "string" ? payload.detail : "";
    const code = payload && typeof payload.code === "string" ? (payload.code as CartErrorCode) : "";
    throw new CartError(detail || fallback, code, readOnOrder(payload?.on_order));
  }
  return payload as T;
}

/**
 * Adds ONE device. Without `unit` the server picks a device of the variant;
 * with `unit` it is that very used device. With `onOrder` no device is held:
 * the line is «bajo pedido», at the offer's price.
 */
export function addDeviceToCart(product: number, unit?: string, opts: { onOrder?: boolean } = {}) {
  return cartPost<unknown>(
    "/cart/add/",
    {
      session_key: getSessionKey(), product, quantity: 1,
      ...(unit ? { unit } : {}),
      ...(opts.onOrder ? { on_order: true } : {}),
    },
    "No se pudo agregar al carrito.",
  );
}

/** DC3: adds a quantity of a product — from the shelf, or «bajo pedido» when `onOrder`. */
export function addProductToCart(product: number, quantity: number, opts: { onOrder?: boolean } = {}) {
  return cartPost<unknown>(
    "/cart/add/",
    { session_key: getSessionKey(), product, quantity, ...(opts.onOrder ? { on_order: true } : {}) },
    "No se pudo agregar al carrito.",
  );
}

export function releaseCartUnit(unit: string) {
  return cartPost<{ released: number }>(
    "/cart/release-unit/",
    { session_key: getSessionKey(), unit },
    "No se pudo quitar el equipo.",
  );
}

// ---------------------------------------------------------------------------
// Presentation rules — one place each, so DC3 extends rather than rewrites
// ---------------------------------------------------------------------------

/** «entrega en 1 día» / «entrega en 5 días» («entrega estimada en …» once ordered). */
export function deliveryDaysText(days: number | null | undefined, opts: { estimated?: boolean } = {}): string {
  const head = opts.estimated ? "entrega estimada" : "entrega";
  if (days === null || days === undefined || !Number.isFinite(days)) return `${head} a confirmar`;
  return `${head} en ${days} ${days === 1 ? "día" : "días"}`;
}

/** «Bajo pedido», alone: what the shop says of an offer whose delivery time the data does not give. */
export const ON_ORDER_LABEL = "Bajo pedido";

/** «Bajo pedido — entrega en N días»: the one way the shop says it. */
export function onOrderLabel(days: number | null | undefined): string {
  return `Bajo pedido — ${deliveryDaysText(days)}`;
}

/**
 * SUPPLIER-DEVICE-FLOW-02 (owner, 2026-10-20) — published does not mean
 * available. What is published and has nothing to sell stays in the shop
 * (photos, specs, last price) and is said with ONE word, everywhere.
 */
export const SOLD_OUT_LABEL = "Agotado";

/** What a buyer is told about a variant's availability. */
export function availabilityLabel(variant: Pick<Variant, "available" | "availability"> & { on_order?: OnOrder | null }): string {
  switch (variant.availability) {
    case "own_stock":
      return "En stock";
    case "supplier": {
      // PR D (D-D-2): «Bajo pedido» only with a purchasable offer; a delivery time only when the data has one.
      const days = variant.on_order?.lead_time_days;
      if (!variant.on_order) return SOLD_OUT_LABEL;
      return typeof days === "number" && Number.isFinite(days) ? onOrderLabel(days) : ON_ORDER_LABEL;
    }
    case "none":
      return SOLD_OUT_LABEL;
    default:
      return variant.available ? "En stock" : SOLD_OUT_LABEL;
  }
}

/**
 * CATALOG-REFERENCE-01 · G (owner's visual order 4): the picture a colour chip shows — its finish's primary picture,
 * else its first, never a demo resource (a chip never presents a demo as the product). `null`: no picture, and the
 * chip falls back to its swatch (the model's own, else the company's: resolved by the server), or to its name alone.
 */
export function chipPicture(finishImages: Record<string, VariantImage[]> | undefined, optionId: number): string | null {
  const own = (finishImages?.[String(optionId)] ?? []).filter((image) => !image.is_demo);
  return (own.find((image) => image.is_primary) ?? own[0])?.url ?? null;
}

/** DC3: the variant is sold «bajo pedido» (no own stock, an offer covers it). */
export function isOnOrderVariant(variant: Pick<Variant, "availability"> & { on_order?: OnOrder | null }): boolean {
  return variant.availability === "supplier" && Boolean(variant.on_order);
}

/**
 * The supplier offer a buyer may choose INSTEAD of own stock — only when the
 * company allows it (`over_stock: true`). Never the default: the page shows it
 * as an extra, explicit option next to «Agregar al carrito».
 */
export function overStockOffer(onOrder: OnOrder | null | undefined): OnOrder | null {
  return onOrder && onOrder.over_stock === true ? onOrder : null;
}

/** Never a made-up number: no reading is said as such. */
export function batteryText(health: number | null | undefined): string {
  return health === null || health === undefined ? "Batería: no informado" : `Batería: ${health}%`;
}

/** «IMEI termina en 1234» from the published hint; nothing when there is no hint. */
export function imeiHintText(hint: string | null | undefined): string | null {
  const digits = (hint ?? "").replace(/\D/g, "");
  return digits ? `IMEI termina en ${digits}` : null;
}

/** The options of a device as one line, in the model's attribute order when known. */
export function optionsText(options: Record<string, string>, order: string[] = []): string {
  const keys = [...order.filter((name) => name in options), ...Object.keys(options).filter((name) => !order.includes(name))];
  return keys.map((name) => options[name]).filter(Boolean).join(" · ");
}

// ---------------------------------------------------------------------------
// Variant selection
// ---------------------------------------------------------------------------
//
// THE RULE IS HIERARCHICAL, in the order the attributes come in (as on a
// manufacturer's configurator: capacity first, then colour). An option of the
// attribute at position i is selectable when some variant EXISTS with it
// together with the options already chosen for the attributes before it, and
// AVAILABLE when one of those can be bought. Choosing one re-fits the
// attributes after it to the nearest available combination, or else to the
// nearest existing one. That way no option the buyer can press leads nowhere,
// no variant that exists is unreachable — a SOLD-OUT one too, so its photos
// and last price can be seen (owner, 2026-10-20: published is not available,
// and sold out is not hidden) — which a flat «does this exact combination
// exist» rule cannot promise. Buying is the page's call: «Agotado» is never
// added to a cart.

export type Selection = Record<string, number>;

export function sortedAttributes(attributes: ModelAttribute[]): ModelAttribute[] {
  return [...attributes]
    .sort((a, b) => a.sort_order - b.sort_order)
    .map((attribute) => ({ ...attribute, options: [...attribute.options].sort((a, b) => a.sort_order - b.sort_order) }));
}

function roleRank(attribute: ModelAttribute): number {
  const at = ATTRIBUTE_ROLE_ORDER.indexOf(attribute.role ?? "other");
  return at < 0 ? ATTRIBUTE_ROLE_ORDER.indexOf("other") : at;
}

/**
 * P2 · V4 — the attributes in the order of their ROLES (capacity, colour, SIM,
 * region, then any other), and by `sort_order` within the same role. An
 * unknown role counts as «other». With no roles at all (a server that does not
 * send them yet) it is exactly `sortedAttributes`.
 */
export function roleOrderedAttributes(attributes: ModelAttribute[]): ModelAttribute[] {
  const sorted = sortedAttributes(attributes);
  if (!sorted.some((attribute) => attribute.role)) return sorted;
  // `Array.prototype.sort` is stable: the same role keeps its `sort_order`.
  return sorted.sort((a, b) => roleRank(a) - roleRank(b));
}

/** The roles that only get a picker when the variants actually differ by them. */
const PICKED_ONLY_WHEN_DIFFERENT: readonly AttributeRole[] = ["sim", "region"];

/**
 * P2 · V4 — whether the page shows a picker for `attribute`. SIM and region
 * only when the model's variants differ by them: one value for all is not a
 * choice. Every other attribute keeps its picker. A hidden attribute still
 * takes part in the selection (it has one value), so no variant is lost.
 */
export function hasPicker(attribute: ModelAttribute, variants: Variant[]): boolean {
  if (!attribute.role || !PICKED_ONLY_WHEN_DIFFERENT.includes(attribute.role)) return true;
  const key = String(attribute.id);
  return new Set(variants.map((variant) => variant.options[key])).size > 1;
}

export function matchVariant(variants: Variant[], attributes: ModelAttribute[], selection: Selection): Variant | null {
  return variants.find((variant) => attributes.every((a) => variant.options[String(a.id)] === selection[String(a.id)])) ?? null;
}

function fitsPrefix(variant: Variant, attributes: ModelAttribute[], selection: Selection, upTo: number): boolean {
  for (let j = 0; j < upTo; j++) {
    const key = String(attributes[j].id);
    if (variant.options[key] !== selection[key]) return false;
  }
  return true;
}

function leadsTo(
  variants: Variant[], attributes: ModelAttribute[], selection: Selection, index: number, optionId: number,
  onlyAvailable: boolean,
): boolean {
  const key = String(attributes[index].id);
  return variants.some(
    (variant) => (!onlyAvailable || variant.available)
      && variant.options[key] === optionId && fitsPrefix(variant, attributes, selection, index),
  );
}

/** Whether pressing `optionId` of the attribute at `index` leads to a variant at all — sold out included. */
export function isOptionSelectable(
  variants: Variant[], attributes: ModelAttribute[], selection: Selection, index: number, optionId: number,
): boolean {
  return leadsTo(variants, attributes, selection, index, optionId, false);
}

/** Whether pressing `optionId` of the attribute at `index` leads to a variant that can be bought now. */
export function isOptionAvailable(
  variants: Variant[], attributes: ModelAttribute[], selection: Selection, index: number, optionId: number,
): boolean {
  return leadsTo(variants, attributes, selection, index, optionId, true);
}

/**
 * Set `optionId` at `index`, then re-fit every later attribute: to an
 * available variant when there is one, otherwise to an existing (sold-out) one.
 */
export function selectOption(
  variants: Variant[], attributes: ModelAttribute[], selection: Selection, index: number, optionId: number,
): Selection {
  const next: Selection = { ...selection, [String(attributes[index].id)]: optionId };
  for (let j = index + 1; j < attributes.length; j++) {
    const attribute = attributes[j];
    const key = String(attribute.id);
    if (next[key] !== undefined && isOptionAvailable(variants, attributes, next, j, next[key])) continue;
    const first =
      attribute.options.find((option) => isOptionAvailable(variants, attributes, next, j, option.id))
      ?? (next[key] !== undefined && isOptionSelectable(variants, attributes, next, j, next[key])
        ? undefined
        : attribute.options.find((option) => isOptionSelectable(variants, attributes, next, j, option.id)));
    if (first) next[key] = first.id;
  }
  return next;
}

/**
 * One option the configurator moved by itself (`chooseOption`): `unavailable` — what was chosen does not exist with
 * the new choice; `restored` — back to what the buyer had chosen, now that it exists again.
 */
export type SelectionChange = { attributeId: number; from: number; to: number; reason: "unavailable" | "restored" };

/** Whether some variant (an available one, with `onlyAvailable`) has every option of `decided`. */
function someVariantHas(variants: Variant[], decided: Selection, onlyAvailable: boolean): boolean {
  return variants.some((variant) => (!onlyAvailable || variant.available)
    && Object.entries(decided).every(([key, value]) => variant.options[key] === value));
}

/**
 * CATALOG-REFERENCE-01 · PR D — the buyer pressed `optionId` of the attribute at `index`. Unlike `selectOption`, ANY
 * option some variant has can be pressed (a colour that only exists in another capacity too, as on a manufacturer's
 * configurator): it stays chosen, and every other attribute, in the page's order, is decided against it and against
 * those decided before:
 *
 *  - an attribute BEFORE it (the capacity, when a colour is pressed) keeps its option while some variant still has it
 *    — even sold out: compatible is not available, and the buyer's capacity is not moved for stock;
 *  - an attribute AFTER it keeps its option while that combination can be bought (`selectOption`'s rule);
 *  - otherwise it moves to the NEAREST option in its list that can be bought, else to the nearest that exists; at the
 *    same distance, the earlier one (the smaller capacity).
 *
 * The result is always the combination of an existing variant, so a pair no variant has (PR B's SIM × region
 * exclusions) is never reached. `changed` lists what moved by itself, for the page to say it.
 */
export function chooseOption(
  variants: Variant[], attributes: ModelAttribute[], selection: Selection, index: number, optionId: number,
  opts: { preferred?: Selection } = {},
): { selection: Selection; changed: SelectionChange[] } {
  const decided: Selection = { [String(attributes[index].id)]: optionId };
  const changed: SelectionChange[] = [];
  attributes.forEach((attribute, at) => {
    if (at === index) return;
    const key = String(attribute.id);
    const current = selection[key];
    const fits = (option: number, onlyAvailable: boolean) =>
      someVariantHas(variants, { ...decided, [key]: option }, onlyAvailable);
    // Review of #373: what the buyer chose for this attribute, when it exists again with the new choice, comes back
    // (browsing colours does not drag the capacity along).
    const wanted = opts.preferred?.[key];
    let next: number | undefined;
    if (wanted !== undefined && wanted !== current && fits(wanted, at > index)) next = wanted;
    else if (current !== undefined && fits(current, at > index)) next = current;
    else {
      const from = attribute.options.findIndex((option) => option.id === current);
      const nearest = attribute.options
        .map((option, position) => ({ id: option.id, distance: from < 0 ? position : Math.abs(position - from), position }))
        .sort((a, b) => a.distance - b.distance || a.position - b.position)
        .map((candidate) => candidate.id);
      next = nearest.find((option) => fits(option, true))
        ?? (current !== undefined && fits(current, false) ? current : nearest.find((option) => fits(option, false)));
    }
    if (next === undefined) return;
    decided[key] = next;
    if (current !== undefined && next !== current) {
      changed.push({ attributeId: attribute.id, from: current, to: next, reason: next === wanted ? "restored" : "unavailable" });
    }
  });
  return { selection: decided, changed };
}

/**
 * What pressing `optionId` leads to (`chooseOption`): a variant that can be bought, a sold-out one, or none at all.
 * `opts.preferred` must be what the press itself will use (the buyer's remembered choices), so the chip says exactly
 * where the press lands (re-review of #373).
 */
export function choiceOutcome(
  variants: Variant[], attributes: ModelAttribute[], selection: Selection, index: number, optionId: number,
  opts: { preferred?: Selection } = {},
): "available" | "sold-out" | "unavailable" {
  const reached = matchVariant(variants, attributes, chooseOption(variants, attributes, selection, index, optionId, opts).selection);
  if (!reached || reached.options[String(attributes[index].id)] !== optionId) return "unavailable";
  return reached.available ? "available" : "sold-out";
}

/**
 * V4-VARIANT-URL — the combination of the NEW variant whose `slug` is exactly `slug`, or `null`: no slug, no such
 * variant in this model (a used device never is one), or a variant that cannot be chosen because another with the
 * same options comes first (`matchVariant` would show that one instead). `slug` is only compared, never used.
 */
export function selectionForVariant(
  variants: Variant[], attributes: ModelAttribute[], slug: string | null | undefined,
): Selection | null {
  if (!slug) return null;
  const variant = variants.find((candidate) => candidate.slug === slug);
  if (!variant) return null;
  const selection: Selection = Object.fromEntries(
    attributes.map((a) => [String(a.id), variant.options[String(a.id)]]).filter(([, v]) => v !== undefined),
  );
  return matchVariant(variants, attributes, selection) === variant ? selection : null;
}

/** The first available combination in display order; the first variant when none is available. */
export function initialSelection(variants: Variant[], attributes: ModelAttribute[]): Selection {
  const ordered = [...variants].sort((a, b) => {
    for (const attribute of attributes) {
      const key = String(attribute.id);
      const ia = attribute.options.findIndex((o) => o.id === a.options[key]);
      const ib = attribute.options.findIndex((o) => o.id === b.options[key]);
      if (ia !== ib) return ia - ib;
    }
    return 0;
  });
  const chosen = ordered.find((variant) => variant.available) ?? ordered[0];
  if (!chosen) return {};
  return Object.fromEntries(attributes.map((a) => [String(a.id), chosen.options[String(a.id)]]).filter(([, v]) => v !== undefined));
}
