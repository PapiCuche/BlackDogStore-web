/**
 * CATALOG-REFERENCE-01 · PR E — what each company decides about its catalogue, from the panel.
 *
 *   GET  /admin/catalog/reference/models/                 the reference models, with the company's linked model   products.view
 *   POST /admin/catalog/reference/preview/                {keys}: what importing would do; writes nothing         products.view
 *   POST /admin/catalog/reference/apply/                  {keys, approve} + Idempotency-Key                       products.manage
 *   GET/POST /admin/catalog/models/<id>/finish-images/    the finishes and their pictures / upload (multipart)    view / manage
 *   PATCH/DELETE …/finish-images/<image>/                 alt text, primary, provenance, licence check / remove   products.manage
 *   POST …/finish-images/order/                           {color_option_id, order}                                 products.manage
 *   GET/PUT /admin/catalog/models/<id>/option-order/     the model's own order of its options                    view / manage
 *   GET  /admin/catalog/models/<id>/commercial/           «Configuración comercial» (read only)                   products.view
 *
 * The company is a SELECTION (`?company=` for a MASTER who switched); the server resolves and re-checks it. Nothing
 * here is kept in the browser.
 */
import { API_BASE } from "../../lib/api";
import { fetchWithAuth } from "../../lib/auth";
import { CatalogApiError, type AttributeRole } from "./internal-api";

function withCompany(path: string, companyId?: number | null): string {
  return companyId ? `${path}?company=${encodeURIComponent(String(companyId))}` : path;
}

async function call<T>(path: string, init: RequestInit, fallback: string, companyId?: number | null): Promise<T> {
  let res: Response;
  try {
    res = await fetchWithAuth(`${API_BASE}${withCompany(path, companyId)}`, init);
  } catch {
    throw new CatalogApiError("No hay conexión con el servidor. Inténtalo de nuevo.", 0, "network");
  }
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => null);
  if (res.ok) return body as T;
  const detail = body && typeof body.detail === "string" ? body.detail : "";
  const code = body && typeof body.code === "string" ? body.code : null;
  const field = body && typeof body.field === "string" ? body.field : null;
  const message = detail
    || (res.status === 403 ? "No tienes permisos para esta operación." : "")
    || (res.status === 404 ? "No se encontró: puede que ya no exista o que sea de otra empresa." : "")
    || fallback;
  throw new CatalogApiError(message, res.status, code, field);
}

const json = (method: string, body: unknown, headers: Record<string, string> = {}): RequestInit => ({
  method, body: JSON.stringify(body), headers,
});

// ---------------------------------------------------------------------------
// Reference catalogue (PR B endpoints)
// ---------------------------------------------------------------------------

export type Completeness = "complete" | "partial" | "pending_review";

export type ReferenceModel = {
  key: string;
  name: string;
  manufacturer: string;
  family: string;
  line: string;
  generation: string;
  announced: string | null;
  completeness: Completeness;
  missing: string[];
  verified_at: string;
  dataset_version: string;
  /** The company's model joined to this reference; null while it was never imported. */
  linked_model_id: number | null;
};

export type PreviewState = "new" | "reused" | "conflict" | "pending_review";

export type PreviewOption = {
  reference_key: string; role: AttributeRole; label: string; status: "new" | "reused" | "conflict";
  option_id: number | null; reason: string;
};

export type PreviewItem = {
  key: string; name: string; manufacturer: string; family: string;
  completeness: Completeness; missing: string[]; needs_approval: boolean;
  state: PreviewState; model_status: "new" | "reused" | "conflict";
  model_id: number | null; model_name: string; link_model: boolean; reason: string;
  warnings: string[]; attributes_new: AttributeRole[]; options: PreviewOption[];
  palette_add: string[]; exclusions_add: number; changes: boolean;
  /** FASE 1.2: the reference model's numbers and the regions the source gives each ('' when none). Never imported. */
  model_numbers?: ReferenceModelNumber[];
};

export type ReferenceModelNumber = { number: string; regions: string };

export type ReferencePreview = {
  items: PreviewItem[];
  attributes: { role: AttributeRole; status: "new" | "reused" | "conflict"; name: string; reason: string; attribute_id: number | null }[];
  counts: Record<PreviewState, number>;
};

export type ReferenceApplyResult = {
  created: { models: { key: string; model_id: number; name: string }[]; attributes: unknown[]; options: unknown[] };
  linked: { models: number; options: number };
  palette_added: number;
  exclusions_added: number;
  items: { key: string; model_id: number; state: PreviewState }[];
  replayed: boolean;
};

export function fetchReferenceModels(companyId?: number | null): Promise<ReferenceModel[]> {
  return call<{ results: ReferenceModel[] }>("/admin/catalog/reference/models/", {},
    "No se pudo leer el catálogo de referencia.", companyId).then((data) => (Array.isArray(data?.results) ? data.results : []));
}

export function previewReferenceImport(keys: string[], companyId?: number | null): Promise<ReferencePreview> {
  return call("/admin/catalog/reference/preview/", json("POST", { keys }), "No se pudo previsualizar la importación.", companyId);
}

export function applyReferenceImport(
  keys: string[], approve: string[], idempotencyKey: string, companyId?: number | null,
): Promise<ReferenceApplyResult> {
  return call("/admin/catalog/reference/apply/", json("POST", { keys, approve }, { "Idempotency-Key": idempotencyKey }),
    "No se pudo importar.", companyId);
}

/** A key for one «Importar» press: a retry of the same press sends the same one and is not applied twice. */
export function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `ref-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}

// ---------------------------------------------------------------------------
// Finish galleries (PR C endpoints)
// ---------------------------------------------------------------------------

export type SourceKind = "own_photo" | "supplier_authorized" | "licensed_stock" | "demo" | "unknown";

export type FinishImage = {
  id: number;
  color_option_id: number;
  url: string;
  alt_text: string;
  is_primary: boolean;
  sort_order: number;
  width: number;
  height: number;
  source: {
    kind: SourceKind; kind_label: string; url: string; license: string; attribution: string; is_demo: boolean;
    license_verified: boolean; license_verified_at: string | null;
    /** PR E: who verified it (absent from an older server). */
    license_verified_by?: string | null;
  };
};

export type Finish = {
  /** `swatch_set: false`: «Muestra sin configurar» (a reference colour still holding the importer's default). */
  option: { id: number; label: string; swatch_hex: string; swatch_set?: boolean };
  images: FinishImage[];
  image_pending: boolean;
  /** Its attribute stopped being a colour after the pictures were placed: they can only be removed or edited. */
  not_colour: boolean;
};

export type FinishListing = {
  finishes: Finish[];
  max_per_colour: number;
  source_kinds: { value: SourceKind; label: string }[];
};

export type Provenance = { source_kind: SourceKind | ""; license: string; source_url: string; attribution: string };

const finishes = (modelId: number) => `/admin/catalog/models/${modelId}/finish-images/`;

export function fetchFinishImages(modelId: number, companyId?: number | null): Promise<FinishListing> {
  return call<FinishListing>(finishes(modelId), {}, "No se pudieron leer las imágenes por acabado.", companyId)
    .then((data) => {
      if (!data || !Array.isArray(data.finishes)) throw new CatalogApiError("El servidor no respondió las imágenes por acabado.", 502);
      return data;
    });
}

export function uploadFinishImage(
  modelId: number, colourId: number, file: File, provenance: Provenance, altText: string, companyId?: number | null,
): Promise<FinishImage> {
  const form = new FormData();
  form.append("file", file);
  form.append("color_option_id", String(colourId));
  form.append("alt_text", altText);
  for (const [field, value] of Object.entries(provenance)) form.append(field, value);
  // Sin `Content-Type`: con un FormData lo escribe el navegador, con el boundary.
  return call(finishes(modelId), { method: "POST", body: form }, "No se pudo subir la imagen.", companyId);
}

export type FinishImagePatch = Partial<{
  alt_text: string; is_primary: true; license_verified: boolean;
  source_kind: SourceKind; license: string; source_url: string; attribution: string;
}>;

export function patchFinishImage(
  modelId: number, imageId: number, data: FinishImagePatch, companyId?: number | null,
): Promise<FinishImage> {
  return call(`${finishes(modelId)}${imageId}/`, json("PATCH", data), "No se pudo guardar el cambio.", companyId);
}

export function deleteFinishImage(modelId: number, imageId: number, companyId?: number | null): Promise<void> {
  return call(`${finishes(modelId)}${imageId}/`, { method: "DELETE" }, "No se pudo quitar la imagen.", companyId);
}

export function reorderFinishImages(
  modelId: number, colourId: number, order: number[], companyId?: number | null,
): Promise<FinishListing> {
  return call(`${finishes(modelId)}order/`, json("POST", { color_option_id: colourId, order }),
    "No se pudo guardar el orden.", companyId);
}

/**
 * What the server will refuse anyway, said before the trip (the server stays the authority): the origin, and the
 * licence or authorisation text unless it is the shop's own photo; a source address, when given, is a web page.
 */
export function provenanceErrors(value: Provenance): Partial<Record<keyof Provenance, string>> {
  const errors: Partial<Record<keyof Provenance, string>> = {};
  if (!value.source_kind || value.source_kind === "unknown") {
    errors.source_kind = "Indica de dónde sale la imagen.";
  }
  if (value.source_kind && value.source_kind !== "own_photo" && !value.license.trim()) {
    errors.license = "Escribe la licencia o la autorización con la que se usa esta imagen.";
  }
  const url = value.source_url.trim();
  if (url && !/^https?:\/\/[^\s/]+/i.test(url)) {
    errors.source_url = "La dirección de origen debe ser una página web (http o https).";
  }
  return errors;
}

// ---------------------------------------------------------------------------
// PR E — the model's own option order, and its commercial summary
// ---------------------------------------------------------------------------

export type OrderedAttribute = {
  id: number; name: string; kind: "text" | "color"; role: AttributeRole;
  options: { id: number; label: string; swatch_hex: string; placed: boolean }[];
};

export type ModelOptionOrder = { model_id: number; has_palette: boolean; attributes: OrderedAttribute[] };

export function fetchModelOptionOrder(modelId: number, companyId?: number | null): Promise<ModelOptionOrder> {
  return call<ModelOptionOrder>(`/admin/catalog/models/${modelId}/option-order/`, {},
    "No se pudo leer el orden de las opciones.", companyId).then((data) => {
    if (!data || !Array.isArray(data.attributes)) throw new CatalogApiError("El servidor no respondió el orden de las opciones.", 502);
    return data;
  });
}

export function saveModelOptionOrder(
  modelId: number, attributeId: number, optionIds: number[], companyId?: number | null,
): Promise<ModelOptionOrder> {
  return call(`/admin/catalog/models/${modelId}/option-order/`, json("PUT", { attribute_id: attributeId, option_ids: optionIds }),
    "No se pudo guardar el orden.", companyId);
}

export type CommercialSummary = {
  model_id: number;
  visibility: { is_published_online: boolean; is_featured: boolean; sort_order: number };
  content: { has_description: boolean; has_cover: boolean; reference_key: string };
  palette: { has_palette: boolean; options: Partial<Record<AttributeRole, number>>; exclusions: number };
  variants: { total: number; new: number; used: number; published: number; priced: number; pending_drafts: number; on_order: number };
  finishes: { colours: number; pending: number };
  /** `lowest_agreed_cost` only comes to a caller with `costs.view`. */
  suppliers: { id: number; name: string; offers: number; variants: number; lowest_agreed_cost?: string | null }[];
  /** null: the caller may not see promotions (not «there are none»). */
  promotions: { id: number; name: string; is_live: boolean }[] | null;
};

export function fetchCommercialSummary(modelId: number, companyId?: number | null): Promise<CommercialSummary> {
  return call<CommercialSummary>(`/admin/catalog/models/${modelId}/commercial/`, {},
    "No se pudo leer la configuración comercial.", companyId).then((data) => {
    if (!data || !data.visibility || !data.variants) throw new CatalogApiError("El servidor no respondió la configuración comercial.", 502);
    return data;
  });
}

/** «Muestra sin configurar»: an imported colour still holding the importer's neutral default (G, owner's order 4). */
export const SWATCH_UNSET_LABEL = "Muestra sin configurar";
