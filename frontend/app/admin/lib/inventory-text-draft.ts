/**
 * INV-TEXT-03 — de una lista de WhatsApp revisada a un BORRADOR de la importación oficial
 * (docs/adr-inv-text-03.md). Nada de esto escribe inventario.
 *
 * Ofertas de proveedor: las filas de la plantilla oficial se ensayan en el servidor con el importador
 * oficial (`…/text-preview/offer-draft/`), que lo revierte todo. Viajan sólo la variante elegida, el precio
 * comercial que escribió la persona, la cantidad y —con `costs.view`— el costo; nunca el texto, IMEI ni series.
 *
 * Stock propio (INV-RECEIVE-FLOW-04): ya no se arma aquí. La lista va a «Recibir mercadería», el único camino que
 * registra equipos de una lista, con el precio de venta de cada seminuevo.
 */
import { API_BASE } from "../../lib/api";
import { fetchWithAuth } from "../../lib/auth";
import { imeiIsValid, type InventoryOrigin, type TextCandidate } from "./inventory-text-preview";
import type { SnapshotPreview, WeeklyImportSummary } from "./supplier-import-api";

/** A reviewed candidate with what the draft adds: the chosen variant, the commercial price and the quantity. */
export type DraftCandidate = TextCandidate & {
  productId: number | null;
  productName: string;
  /** What the customer pays when buying on order. Typed by the person; never worked out. */
  commercialPrice: string;
  /** null = not confirmed (it does not sell), 0 = sold out, N = confirmed. */
  confirmedQuantity: number | null;
};

export type ReviewItem = { id: number; reason: string };

export type DraftPlan = {
  origin: InventoryOrigin;
  included: DraftCandidate[];
  review: ReviewItem[];
  duplicates: number[];
  unknownQuantities: number;
};

/** Which rows make the draft and, for every other one, why not. Duplicates are marked, never added up. */
export function planDraft(rows: DraftCandidate[], origin: InventoryOrigin): DraftPlan {
  const included: DraftCandidate[] = [];
  const review: ReviewItem[] = [];
  const duplicates: number[] = [];
  for (const row of rows) {
    const reason = whyNot(row, origin);
    if (row.duplicateOf !== null) duplicates.push(row.id);
    if (reason) review.push({ id: row.id, reason });
    else included.push(row);
  }
  const unknownQuantities = origin === "supplier" ? included.filter(row => row.confirmedQuantity === null).length : 0;
  return { origin, included, review, duplicates, unknownQuantities };
}

function whyNot(row: DraftCandidate, origin: InventoryOrigin): string {
  if (row.duplicateOf !== null) return `Posible duplicado del registro ${row.duplicateOf}: no se suma ni entra en el borrador.`;
  if (origin === "supplier") {
    if (row.line === "used") {
      return "Seminuevo de proveedor: no es una oferta ni stock propio. Se recibe por equipo cuando llega a la tienda.";
    }
    if (row.productId === null) return "Elige la variante del catálogo («Buscar en el catálogo»).";
    if (!row.commercialPrice.trim()) return "Escribe el precio comercial: nunca se calcula solo.";
    return "";
  }
  if (row.kind === "accessory" || row.line === "accessory") {
    return "Accesorio propio: se carga por cantidad en «Carga de stock», no como equipo.";
  }
  if (row.productId === null) return "Elige la variante del catálogo («Buscar en el catálogo»).";
  if (!row.serial) return "Falta el número de serie: la Carga de equipos lo exige.";
  if (row.imei && !imeiIsValid(row.imei)) return "IMEI no válido (15 dígitos que superen Luhn).";
  if (row.imei2 && (!row.imei || row.imei2 === row.imei || !imeiIsValid(row.imei2))) {
    return "IMEI 2 no válido o sin un IMEI 1 distinto.";
  }
  return "";
}

// ---------------------------------------------------------------------------
// Supplier offers: the dry run of the official preview
// ---------------------------------------------------------------------------

export type OfferDraftSettings = {
  supplierId: number | null;
  validFrom: string;
  validUntil: string;
  leadTimeDays: string;
  /** INV-SNAPSHOT-01: this is the supplier's COMPLETE list. Off by default: partial. */
  complete: boolean;
};

export type OfferDraftRow = {
  key: string;
  product_id: number;
  commercial_price: string;
  confirmed_quantity: number | null;
  agreed_cost?: string;
};

export type OfferDraftBody = {
  supplier_id: number | null;
  valid_from: string;
  valid_until: string;
  lead_time_days: string;
  complete: boolean;
  rows: OfferDraftRow[];
};

/** The request body: exactly the template's fields, nothing else from the candidate. */
export function offerDraftPayload(plan: DraftPlan, settings: OfferDraftSettings, canSeeCosts: boolean): OfferDraftBody {
  return {
    supplier_id: settings.supplierId, valid_from: settings.validFrom, valid_until: settings.validUntil,
    lead_time_days: settings.leadTimeDays, complete: settings.complete,
    rows: plan.included.map(row => ({
      key: String(row.id), product_id: row.productId as number, commercial_price: row.commercialPrice.trim(),
      confirmed_quantity: row.confirmedQuantity,
      ...(canSeeCosts && row.supplierCost ? { agreed_cost: row.supplierCost } : {}),
    })),
  };
}

export type OfferDraftAnswerRow = {
  key: string | null;
  row_number: number;
  action: "create" | "update" | "no_change" | "skip" | "error";
  errors: string[];
  warnings: string[];
  data: { price_change?: { before: string; after: string } } & Record<string, unknown>;
};

export type OfferDraftAnswer = {
  dry_run: true;
  summary: WeeklyImportSummary;
  rows: OfferDraftAnswerRow[];
  snapshot?: SnapshotPreview;
  cost_column_ignored: boolean;
};

function withCompany(path: string, companyId?: number | null): string {
  return companyId ? `${path}?company=${encodeURIComponent(String(companyId))}` : path;
}

async function post(path: string, body: OfferDraftBody, companyId?: number | null): Promise<Response> {
  const res = await fetchWithAuth(`${API_BASE}${withCompany(path, companyId)}`, {
    method: "POST", body: JSON.stringify(body),
  });
  if (res.ok) return res;
  const answer = await res.json().catch(() => null);
  throw new Error(answer && typeof answer.detail === "string" ? answer.detail
    : res.status === 403 ? "No tienes permiso para preparar esta importación."
      : res.status === 404 ? "El proveedor o una variante ya no existen en esta empresa. Vuelve a buscar."
        : "No se pudo generar el borrador.");
}

/** What the official preview would stage. The server rolls it all back: nothing is written. */
export async function requestOfferDraft(body: OfferDraftBody, companyId?: number | null): Promise<OfferDraftAnswer> {
  const res = await post("/admin/inventory/text-preview/offer-draft/", body, companyId);
  return (await res.json()) as OfferDraftAnswer;
}

/** The same draft as the official template: the workbook the official offer importer reads. */
export async function offerDraftFile(body: OfferDraftBody, companyId?: number | null): Promise<File> {
  const res = await post("/admin/inventory/text-preview/offer-draft/file/", body, companyId);
  const served = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") ?? "")?.[1];
  const blob = await res.blob();
  return new File([blob], served ?? "borrador-ofertas.xlsx", {
    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  });
}

/** The draft workbook, to keep or to upload by hand in «Ofertas de proveedores». */
export async function downloadOfferDraft(body: OfferDraftBody, companyId?: number | null): Promise<void> {
  const file = await offerDraftFile(body, companyId);
  const url = URL.createObjectURL(file);
  const link = document.createElement("a");
  link.href = url;
  link.download = file.name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------

export type DraftSummary = {
  origin: InventoryOrigin;
  newOffers: number;
  updates: number;
  soldOut: number;
  unchanged: number;
  priceChanges: number;
  /** Only for a complete list: running offers absent from it (switched off only by the official apply). */
  deactivations: number | null;
  looksTruncated: boolean;
  review: ReviewItem[];
  duplicates: number;
  unknownQuantities: number;
};

export function offerSummary(plan: DraftPlan, answer: OfferDraftAnswer | null): DraftSummary {
  const rejected: ReviewItem[] = (answer?.rows ?? [])
    .filter(row => row.action === "error" && row.key !== null)
    .map(row => ({ id: Number(row.key), reason: row.errors.join(" ") }));
  const accepted = new Set((answer?.rows ?? []).filter(row => row.action !== "error").map(row => row.key));
  const counts = answer?.summary;
  return {
    origin: "supplier", newOffers: counts?.new ?? 0, updates: counts?.updated ?? 0, soldOut: counts?.sold_out ?? 0,
    unchanged: counts?.unchanged ?? 0, priceChanges: counts?.price_changes ?? 0,
    deactivations: answer?.snapshot ? answer.snapshot.deactivate_count : null,
    looksTruncated: Boolean(answer?.snapshot?.looks_truncated),
    review: [...plan.review, ...rejected].sort((a, b) => a.id - b.id),
    duplicates: plan.duplicates.length,
    unknownQuantities: plan.included.filter(row => row.confirmedQuantity === null && accepted.has(String(row.id))).length,
  };
}
