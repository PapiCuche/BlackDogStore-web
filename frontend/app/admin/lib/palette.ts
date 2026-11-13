/**
 * CATALOG-COMMERCIAL-01 · A1 — the palette of a model, as the panel reads it.
 *
 * Pure helpers over the payload of `GET /admin/catalog/models/<id>/palette/`.
 * The words here are the platform's names for the ROLES and the STATES (closed
 * technical vocabularies); the values (labels, swatches) are each company's.
 */

import {
  CatalogApiError, type AdminModelPalette, type AttributeRole, type CatalogLine, type PaletteStatus, type PaletteVariant,
} from "./internal-api";

/** What a role is called on screen. Its attribute keeps the company's own name. */
export const ROLE_LABELS: Record<AttributeRole, string> = {
  capacity: "Capacidad",
  color: "Color",
  sim: "SIM",
  region: "Región",
  other: "Otro",
};

/** The reading order of the roles (ADR §9): capacity → colour → SIM → region → other. */
export const ROLE_ORDER: AttributeRole[] = ["capacity", "color", "sim", "region", "other"];

/** The state of a configuration (ADR §8.3), in the operator's words. */
export const PALETTE_STATUS_LABELS: Record<PaletteStatus, string> = {
  variante_existente: "Con variante",
  combinacion_valida_sin_variante: "Sin variante",
  fuera_de_paleta: "Fuera de la paleta",
  incompleta: "Incompleta",
};

/** The option ids the palette holds now. */
export function paletteSelection(palette: Pick<AdminModelPalette, "attributes">): Set<number> {
  const chosen = new Set<number>();
  for (const attribute of palette.attributes) {
    for (const option of attribute.options) if (option.in_palette) chosen.add(option.id);
  }
  return chosen;
}

export function sameSelection(a: Set<number>, b: Set<number>): boolean {
  if (a.size !== b.size) return false;
  for (const id of a) if (!b.has(id)) return false;
  return true;
}

/** Only the attributes with something in the palette, each with only its palette options: what a variant may pick. */
export function paletteAttributes(palette: Pick<AdminModelPalette, "attributes">) {
  return palette.attributes
    .map((attribute) => ({ ...attribute, options: attribute.options.filter((option) => option.in_palette) }))
    .filter((attribute) => attribute.options.length > 0);
}

// ---------------------------------------------------------------------------
// A1b-2 — the model's «cockpit»: drafts created from the palette and their one commercial transition.
// ---------------------------------------------------------------------------

/** The word the server appends to the generated name of a used-line variant (`device_catalog.USED_SUFFIX`). */
export const USED_NAME_SUFFIX = "Seminuevo";

/** The name column's length (`Product.name`, 255 characters). */
const NAME_MAX = 255;

/**
 * The name the server generates for a variant (`device_catalog.variant_name`): the model's name, then the option
 * labels in ROLE order (the palette's attributes already come in that order), then «Seminuevo» for the used line;
 * every part with its blanks collapsed, cut to 255 characters. Shown as the proposal the operator may edit.
 */
export function variantNamePreview(modelName: string, labelsInRoleOrder: string[], line: CatalogLine): string {
  const parts = [modelName, ...labelsInRoleOrder, ...(line === "used" ? [USED_NAME_SUFFIX] : [])];
  const name = parts.map((part) => String(part).split(/\s+/).filter(Boolean).join(" ")).join(" ");
  return Array.from(name).slice(0, NAME_MAX).join("");
}

/** Where a variant stands commercially, in the operator's words. */
export type CommercialState = "draft" | "enabled" | "published" | "hidden";

export const COMMERCIAL_STATE_LABELS: Record<CommercialState, string> = {
  draft: "Borrador",
  enabled: "Habilitada",
  published: "Publicada",
  hidden: "Oculta",
};

/**
 * A pending draft is a draft whatever else it says (nothing sells it); then published; then enabled but not
 * published; else hidden. A historical row (no provenance) is only ever «Publicada» or «Oculta», as before.
 */
export function commercialState(row: Pick<PaletteVariant, "draft_pending" | "sale_enabled_at" | "is_published_online">): CommercialState {
  if (row.draft_pending) return "draft";
  if (row.is_published_online) return "published";
  if (row.sale_enabled_at) return "enabled";
  return "hidden";
}

/** Why a variant sits under «Revisar». */
export type ReviewReason = "draft" | "no_price" | "no_image" | "fuera_de_paleta" | "incompleta";

export const REVIEW_LABELS: Record<ReviewReason, string> = {
  draft: "Borrador",
  no_price: "Sin precio",
  no_image: "Sin imagen",
  fuera_de_paleta: "Fuera de la paleta",
  incompleta: "Incompleta",
};

/**
 * What a variant lacks, from the palette payload alone. A used-line variant is priced device by device, so its
 * own price never counts (the server's `_needs_review` says the same). A field an older server does not send
 * is never read as a fault.
 */
export function reviewReasons(row: PaletteVariant): ReviewReason[] {
  const reasons: ReviewReason[] = [];
  if (row.draft_pending) reasons.push("draft");
  if (row.condition_line === "new" && row.price_set === false) reasons.push("no_price");
  if (row.has_image === false) reasons.push("no_image");
  if (row.status === "fuera_de_paleta" || row.status === "incompleta") reasons.push(row.status);
  return reasons;
}

/**
 * The panel's own words for the codes «Habilitar para la venta» may refuse with, used only when the server
 * does not send its text (`product_publication.PROBLEM_LABELS` + `device_catalog.DRAFT_PROBLEM_LABELS`).
 */
export const PROBLEM_FALLBACK_LABELS: Record<string, string> = {
  draft_pending: "Es un borrador creado desde la paleta: habilita la variante para la venta primero.",
  inactive: "El producto está inactivo.",
  no_price: "No tiene precio de venta.",
  no_image: "No tiene imagen.",
  no_category: "No tiene categoría.",
  no_model: "La variante ya no pertenece a un modelo.",
  no_unit: "Es seminuevo: recibe al menos un equipo con su precio de venta antes de habilitarla.",
  fuera_de_paleta: "Lleva una opción que la paleta del modelo ya no admite.",
  incompleta: "Le falta una opción de la paleta del modelo.",
};

/** Each refused code as a sentence: the server's text, else the panel's, else the code itself (never dropped). */
export function problemMessages(codes: string[], labels: Record<string, string>): string[] {
  return codes.map((code) => labels[code] || PROBLEM_FALLBACK_LABELS[code] || code);
}

/**
 * What a refused or failed cockpit call says: the server's own text (it never carries costs or internal data),
 * a fixed sentence for a 404 (the row is gone or belongs to another company: both read the same), a network
 * sentence when the request never arrived, else `fallback`.
 */
export function cockpitErrorMessage(error: unknown, fallback: string, notFound: string): string {
  if (error instanceof CatalogApiError) return error.status === 404 ? notFound : error.message || fallback;
  if (error instanceof TypeError) return "No se pudo conectar con el servidor. Revisa tu conexión y vuelve a intentarlo.";
  return fallback;
}
