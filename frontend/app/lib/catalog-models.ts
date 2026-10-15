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
};

export type CatalogModelList = { lines: Lines; results: CatalogModelCard[] };

export type AttributeOption = { id: number; label: string; swatch_hex: string; sort_order: number };

export type ModelAttribute = {
  id: number;
  name: string;
  kind: "text" | "color";
  sort_order: number;
  options: AttributeOption[];
};

export type VariantImage = { url: string; alt_text: string; is_primary: boolean; width: number; height: number };

export type Variant = {
  product_id: number;
  slug: string;
  name: string;
  /** attribute id (as a string) → option id. */
  options: Record<string, number>;
  price: string;
  image_url: string;
  images: VariantImage[];
  available: boolean;
  /** DC3 adds supplier availability; `availabilityLabel` is the one place that reads it. */
  availability: string;
  sells_by_unit: boolean;
};

export type CosmeticGrade = { code: string; label: string; description: string };

export type UsedUnit = {
  id: string;
  product_id: number;
  /** attribute NAME → option label, e.g. {"Capacidad": "128 GB"}. */
  options: Record<string, string>;
  battery_health: number | null;
  cosmetic_grade: CosmeticGrade | null;
  price: string;
  photos: { url: string; alt_text: string; width: number; height: number }[];
  notes: string;
  imei_hint: string;
};

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
  | "unit_required" | "unit_taken" | "sold_out" | "too_many" | "held_by_others" | "not_found" | "";

/** A refusal from the cart, in the server's words. `code` lets the page react (e.g. reload on unit_taken). */
export class CartError extends Error {
  readonly code: CartErrorCode;
  constructor(message: string, code: CartErrorCode) {
    super(message);
    this.name = "CartError";
    this.code = code;
  }
}

async function cartPost<T>(path: string, body: Record<string, unknown>, fallback: string): Promise<T> {
  // `fetchWithAuth`: with a signed-in session the server demands the CSRF token.
  const res = await fetchWithAuth(`${API_BASE}${path}`, { method: "POST", body: JSON.stringify(body) });
  const payload = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = payload && typeof payload.detail === "string" ? payload.detail : "";
    const code = payload && typeof payload.code === "string" ? (payload.code as CartErrorCode) : "";
    throw new CartError(detail || fallback, code);
  }
  return payload as T;
}

/**
 * Adds ONE device. Without `unit` the server picks a device of the variant;
 * with `unit` it is that very used device.
 */
export function addDeviceToCart(product: number, unit?: string) {
  return cartPost<unknown>(
    "/cart/add/",
    { session_key: getSessionKey(), product, quantity: 1, ...(unit ? { unit } : {}) },
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

/** What a buyer is told about a variant's availability. DC3 adds supplier stock here. */
export function availabilityLabel(variant: Pick<Variant, "available" | "availability">): string {
  switch (variant.availability) {
    case "own_stock":
      return "En stock";
    case "none":
      return "Sin stock";
    default:
      return variant.available ? "En stock" : "Sin stock";
  }
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
// attribute at position i is selectable when some AVAILABLE variant has it
// together with the options already chosen for the attributes before it.
// Choosing one re-fits the attributes after it to the nearest available
// combination. That way no option the buyer can press leads nowhere, and no
// variant that exists and is available is unreachable — which a flat
// «does this exact combination exist» rule cannot promise.

export type Selection = Record<string, number>;

export function sortedAttributes(attributes: ModelAttribute[]): ModelAttribute[] {
  return [...attributes]
    .sort((a, b) => a.sort_order - b.sort_order)
    .map((attribute) => ({ ...attribute, options: [...attribute.options].sort((a, b) => a.sort_order - b.sort_order) }));
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

/** Whether pressing `optionId` of the attribute at `index` leads to an available variant. */
export function isOptionSelectable(
  variants: Variant[], attributes: ModelAttribute[], selection: Selection, index: number, optionId: number,
): boolean {
  const key = String(attributes[index].id);
  return variants.some(
    (variant) => variant.available && variant.options[key] === optionId && fitsPrefix(variant, attributes, selection, index),
  );
}

/** Set `optionId` at `index`, then re-fit every later attribute to an available variant. */
export function selectOption(
  variants: Variant[], attributes: ModelAttribute[], selection: Selection, index: number, optionId: number,
): Selection {
  const next: Selection = { ...selection, [String(attributes[index].id)]: optionId };
  for (let j = index + 1; j < attributes.length; j++) {
    const attribute = attributes[j];
    const key = String(attribute.id);
    if (next[key] !== undefined && isOptionSelectable(variants, attributes, next, j, next[key])) continue;
    const first = attribute.options.find((option) => isOptionSelectable(variants, attributes, next, j, option.id));
    if (first) next[key] = first.id;
  }
  return next;
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
