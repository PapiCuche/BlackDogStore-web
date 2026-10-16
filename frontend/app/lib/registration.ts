/**
 * COMMERCE-ADMIN-UX-01 — «Registrar artículo».
 *
 * Lo que el asistente le manda al servidor y lo que decide ANTES de mandarlo:
 * qué pasos puede usar quien mira (con el motivo cuando no), el margen estimado
 * (sólo para quien ve costos) y el cuerpo de la petición. El servidor
 * (`POST /api/admin/products/register/`) vuelve a decidir todo: llama, en una
 * sola transacción, a los mismos servicios que las demás pantallas.
 *
 * UN COSTO NUNCA VIAJA sin `costs.view`: `buildRegistrationBody` lo quita,
 * aunque el borrador lo tenga, y el servidor respondería 403 si llegara.
 */

import { API_BASE } from "./api";
import { fetchWithAuth } from "./auth";

export type ArticleKind = "new_device" | "used_device" | "accessory" | "supplier";

export const ARTICLE_KINDS: { kind: ArticleKind; label: string; description: string }[] = [
  { kind: "new_device", label: "Equipo nuevo", description: "Un modelo con su capacidad y color; cada equipo con su serie e IMEI." },
  { kind: "used_device", label: "Equipo seminuevo", description: "Un equipo suelto: batería, estado estético, fotos reales, su precio y su costo." },
  { kind: "accessory", label: "Accesorio o repuesto", description: "Se cuenta por cantidad en cada sucursal, con su código y su costo de compra." },
  { kind: "supplier", label: "Disponible por proveedor", description: "No entra en tu stock: se vende «bajo pedido» con la oferta de un proveedor." },
];

export const DEVICE_KINDS: readonly ArticleKind[] = ["new_device", "used_device"];

/** Lo que quien mira puede hacer, según sus capacidades (UX: el servidor decide). */
export type RegistrationAccess = {
  /** `products.manage`: registrar algo. */
  manage: boolean;
  /** `inventory.adjust`: equipos con serie y stock en una sucursal. */
  adjust: boolean;
  /** `costs.view`: ver e indicar costos y el margen. */
  costs: boolean;
};

export type StepState = { enabled: boolean; reason: string | null };

export const MANAGE_REASON = "Registrar artículos requiere el permiso «Gestionar productos».";
export const ADJUST_REASON =
  "Registrar equipos con serie e IMEI, o stock en una sucursal, requiere el permiso «Ajustar inventario».";
export const COSTS_REASON = "Los costos y el margen sólo los ve quien tiene el permiso «Ver costos y márgenes».";

const OK: StepState = { enabled: true, reason: null };

/** Si se puede elegir este tipo, y por qué no. */
export function kindState(kind: ArticleKind, access: RegistrationAccess): StepState {
  if (!access.manage) return { enabled: false, reason: MANAGE_REASON };
  if (DEVICE_KINDS.includes(kind) && !access.adjust) return { enabled: false, reason: ADJUST_REASON };
  return OK;
}

/** El paso de stock de un tipo: qué hace y si quien mira puede usarlo. */
export function stockState(kind: ArticleKind, access: RegistrationAccess): StepState {
  if (kind === "supplier") {
    return { enabled: false, reason: "Lo que vende un proveedor no entra en tu stock: se registra su oferta." };
  }
  if (!access.adjust) {
    return { enabled: false, reason: `${ADJUST_REASON} El producto se registra sin stock.` };
  }
  return OK;
}

/** El costo, sólo para quien ve costos. */
export function costState(access: RegistrationAccess): StepState {
  return access.costs ? OK : { enabled: false, reason: COSTS_REASON };
}

function amount(text: string): number | null {
  const value = Number(String(text ?? "").trim().replace(",", "."));
  return Number.isFinite(value) && String(text ?? "").trim() !== "" ? value : null;
}

/** Margen estimado: precio − costo, y sobre el precio. `null` sin los dos o sin `costs.view`. */
export function estimatedMargin(
  price: string,
  cost: string,
  access: Pick<RegistrationAccess, "costs">,
): { amount: string; percent: string } | null {
  if (!access.costs) return null;
  const p = amount(price);
  const c = amount(cost);
  if (p === null || c === null || p <= 0) return null;
  const margin = p - c;
  return { amount: margin.toFixed(2), percent: ((margin / p) * 100).toFixed(1) };
}

// ---------------------------------------------------------------------------
// The draft
// ---------------------------------------------------------------------------

export type UnitDraft = { serial_number: string; imei: string; imei2: string; cost: string };

export type UsedDraft = {
  serial_number: string;
  imei: string;
  battery_health: string;
  cosmetic_grade: string;
  sale_price: string;
  cost: string;
  public_notes: string;
};

export type StockRowDraft = { branch: string; quantity: string; unit_cost: string };

export type OfferDraft = {
  commercial_price: string;
  agreed_cost: string;
  confirmed_quantity: string;
  valid_from: string;
  valid_until: string;
  lead_time_days: string;
};

export type RegistrationDraft = {
  kind: ArticleKind | null;
  name: string;
  category: string;
  price: string;
  reference_cost: string;
  sku: string;
  description: string;
  is_active: boolean;
  show_online: boolean;
  model_mode: "existing" | "new";
  model_id: string;
  model_name: string;
  /** attribute id -> option id ("" = none). */
  options: Record<string, string>;
  requires_imei: boolean;
  branch: string;
  units: UnitDraft[];
  used: UsedDraft;
  stock: StockRowDraft[];
  supplier_mode: "none" | "existing" | "new";
  supplier_id: string;
  supplier_name: string;
  offer: OfferDraft;
};

export const emptyUnit = (): UnitDraft => ({ serial_number: "", imei: "", imei2: "", cost: "" });

export function emptyDraft(today = new Date()): RegistrationDraft {
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  const later = new Date(today);
  later.setDate(later.getDate() + 14);
  return {
    kind: null,
    name: "", category: "", price: "", reference_cost: "", sku: "", description: "",
    is_active: true, show_online: true,
    model_mode: "existing", model_id: "", model_name: "", options: {}, requires_imei: true,
    branch: "", units: [emptyUnit()],
    used: { serial_number: "", imei: "", battery_health: "", cosmetic_grade: "", sale_price: "", cost: "", public_notes: "" },
    stock: [],
    supplier_mode: "none", supplier_id: "", supplier_name: "",
    offer: { commercial_price: "", agreed_cost: "", confirmed_quantity: "", valid_from: iso(today), valid_until: iso(later), lead_time_days: "" },
  };
}

const text = (value: string) => value.trim();
const optional = (value: string) => (value.trim() === "" ? undefined : value.trim());
const intOrUndefined = (value: string) => (value.trim() === "" ? undefined : Number(value.trim()));

/** El cuerpo de la petición. Sin `costs.view` no lleva ningún costo, tenga lo que tenga el borrador. */
export function buildRegistrationBody(
  draft: RegistrationDraft,
  access: RegistrationAccess,
  { dryRun = false }: { dryRun?: boolean } = {},
): Record<string, unknown> {
  if (!draft.kind) throw new Error("Elige qué vas a registrar.");
  const kind = draft.kind;
  const cost = (value: string) => (access.costs ? optional(value) : undefined);
  const body: Record<string, unknown> = {
    kind,
    product: {
      name: text(draft.name),
      category: draft.category ? Number(draft.category) : null,
      price: text(draft.price),
      description: text(draft.description),
      sku: optional(draft.sku),
      is_active: draft.is_active,
      show_online: draft.show_online,
      reference_cost: cost(draft.reference_cost),
    },
  };
  if (dryRun) body.dry_run = true;

  // A supplier's article MAY be a variant of a model; a device MUST be one.
  const supplierModel = kind === "supplier"
    && (draft.model_mode === "new" ? Boolean(text(draft.model_name)) : Boolean(draft.model_id));
  const usesModel = kind === "new_device" || kind === "used_device" || supplierModel;
  if (usesModel) {
    body.model = draft.model_mode === "new" ? { name: text(draft.model_name) } : draft.model_id ? { id: Number(draft.model_id) } : null;
    body.options = Object.values(draft.options).filter(Boolean).map(Number);
  }

  const withStock = stockState(kind, access).enabled;
  if (kind === "new_device" || kind === "used_device") {
    body.requires_imei = draft.requires_imei;
    if (draft.branch) body.branch = Number(draft.branch);
  }
  if (kind === "new_device" && withStock) {
    body.units = draft.units
      .filter((u) => u.serial_number.trim() || u.imei.trim())
      .map((u) => ({
        serial_number: text(u.serial_number),
        imei: optional(u.imei),
        imei2: optional(u.imei2),
        cost: cost(u.cost),
      }));
  }
  if (kind === "used_device") {
    const u = draft.used;
    body.unit = {
      serial_number: text(u.serial_number),
      imei: optional(u.imei),
      battery_health: intOrUndefined(u.battery_health),
      cosmetic_grade: optional(u.cosmetic_grade),
      sale_price: optional(u.sale_price),
      public_notes: optional(u.public_notes),
      cost: cost(u.cost),
    };
  }
  if (kind === "accessory" && withStock) {
    body.stock = draft.stock
      .filter((row) => row.branch && row.quantity.trim())
      .map((row) => ({ branch: Number(row.branch), quantity: Number(row.quantity), unit_cost: cost(row.unit_cost) }));
  }
  const wantsSupplier = kind === "supplier" || ((kind === "new_device" || kind === "accessory") && draft.supplier_mode !== "none");
  if (wantsSupplier) {
    body.supplier = draft.supplier_mode === "existing" && draft.supplier_id
      ? { id: Number(draft.supplier_id) }
      : { name: text(draft.supplier_name) };
    const o = draft.offer;
    body.offer = {
      commercial_price: text(o.commercial_price),
      agreed_cost: cost(o.agreed_cost),
      confirmed_quantity: intOrUndefined(o.confirmed_quantity),
      valid_from: text(o.valid_from),
      valid_until: text(o.valid_until),
      lead_time_days: intOrUndefined(o.lead_time_days),
    };
  }
  return JSON.parse(JSON.stringify(body)) as Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// The request
// ---------------------------------------------------------------------------

export type RegistrationStep = "kind" | "product" | "model" | "stock" | "supplier" | "costs" | "review";

export type RegistrationResult = {
  dry_run: boolean;
  replayed: boolean;
  kind: ArticleKind;
  product: {
    id: number | null;
    existing_id: number | null;
    created: boolean;
    name: string;
    slug: string;
    is_active: boolean;
    is_published_online: boolean;
    is_serialized: boolean;
    sale_mode: "stock" | "stock_or_order";
    condition_line: "new" | "used";
  };
  model: { id: number | null; created: boolean; name: string; slug: string } | null;
  units: { id: number | null }[];
  movements: number[];
  supplier: { id: number | null; created: boolean; name: string } | null;
  offer: { id: number | null } | null;
  /** `model_hidden`: a model created here stays hidden with its first variant (absent in older answers). */
  publication: { requested: boolean; published: boolean; problems: string[]; model_hidden?: boolean };
  warnings: string[];
  links: { product: string | null; units: string | null; storefront: string | null };
};

export class RegistrationApiError extends Error {
  readonly status: number;
  readonly step: RegistrationStep | null;
  readonly field: string | null;
  readonly line: number | null;
  readonly code: string | null;

  constructor(message: string, status: number, body: Record<string, unknown> | null) {
    super(message);
    this.name = "RegistrationApiError";
    this.status = status;
    this.step = typeof body?.step === "string" ? (body.step as RegistrationStep) : null;
    this.field = typeof body?.field === "string" ? body.field : null;
    this.line = typeof body?.line === "number" ? body.line : null;
    this.code = typeof body?.code === "string" ? body.code : null;
  }
}

/** Una clave por intento: el mismo cuerpo reenviado con ella no registra dos veces. */
export function newIdempotencyKey(): string {
  const cryptoApi = typeof globalThis !== "undefined" ? (globalThis.crypto as Crypto | undefined) : undefined;
  if (cryptoApi && typeof cryptoApi.randomUUID === "function") return `reg-${cryptoApi.randomUUID()}`;
  return `reg-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

function firstMessage(body: Record<string, unknown> | null): string {
  if (!body) return "";
  if (typeof body.detail === "string") return body.detail;
  for (const value of Object.values(body)) {
    if (Array.isArray(value) && value.length) return String(value[0]);
    if (typeof value === "string") return value;
  }
  return "";
}

/** `dryRun`: lo que pasaría, sin escribir nada (sin clave). Si no, registra con `key`. */
export async function submitRegistration(
  body: Record<string, unknown>,
  { key, companyId }: { key?: string; companyId?: number | null } = {},
): Promise<RegistrationResult> {
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  const headers: Record<string, string> = {};
  if (key) headers["Idempotency-Key"] = key;
  const res = await fetchWithAuth(`${API_BASE}/admin/products/register/${qs}`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  const data = (await res.json().catch(() => null)) as Record<string, unknown> | null;
  if (res.ok) return data as unknown as RegistrationResult;
  const fallback = res.status === 403
    ? "No tienes permiso para esta parte del registro."
    : res.status === 401
      ? "Sesión expirada. Vuelve a iniciar sesión."
      : "No se pudo registrar el artículo.";
  throw new RegistrationApiError(firstMessage(data) || fallback, res.status, data);
}

// ---------------------------------------------------------------------------
// Publicación e-commerce — models
// ---------------------------------------------------------------------------

export type ModelVisibilityResult = { action: "publish" | "hide"; changed: number; unchanged: number; total: number };

export async function bulkSetModelVisibility(
  action: "publish" | "hide",
  ids: number[],
  companyId?: number | null,
): Promise<ModelVisibilityResult> {
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  const res = await fetchWithAuth(`${API_BASE}/admin/catalog/models/bulk-visibility/${qs}`, {
    method: "POST",
    body: JSON.stringify({ action, ids }),
  });
  const data = (await res.json().catch(() => null)) as Record<string, unknown> | null;
  if (res.ok) return data as unknown as ModelVisibilityResult;
  if (res.status === 403) throw new Error("No tienes permisos para publicar u ocultar modelos.");
  if (res.status === 404) throw new Error("Algún modelo ya no existe. Actualiza la lista y vuelve a seleccionar.");
  throw new Error(firstMessage(data) || "No se pudo cambiar la visibilidad de los modelos.");
}
