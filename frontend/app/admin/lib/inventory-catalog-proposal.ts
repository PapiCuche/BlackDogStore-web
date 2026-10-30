/**
 * INV-FAST-B1 — «Proponer lo que falta»: el catálogo que faltan las filas pegadas, aprobado por la persona.
 *
 * El servidor propone (`…/text-preview/catalog-proposal/`, sólo lectura) y crea SÓLO lo aprobado
 * (`…/catalog-proposal/apply/`, atómico e idempotente). Viajan modelo, capacidad, color y línea; nunca el texto,
 * costos, IMEI ni series. Toda variante exige IMEI salvo que la persona marque «Sin IMEI» en ella. Nada se guarda en el navegador.
 */
import { API_BASE } from "../../lib/api";
import { fetchWithAuth } from "../../lib/auth";
import { capacityInRange, colourIsOnlyIdentifier, modelWithoutIdentifiers } from "./inventory-text-match";
import type { TextCandidate } from "./inventory-text-preview";

export type ProposalRow = Pick<TextCandidate, "id" | "kind" | "line" | "model" | "capacityGb" | "colour">
  & Partial<Pick<TextCandidate, "ramGb" | "sim" | "region">>;

/**
 * What the text names that the catalogue does not distinguish yet (RAM, chip / eSIM, region): they are not options of
 * a variant, so they are never reconciled — and never dropped in silence: each row that carries one says so.
 */
export function identityExtras(row: Partial<Pick<TextCandidate, "ramGb" | "sim" | "region">>): string {
  return [
    row.ramGb ? `${row.ramGb} GB de RAM` : "",
    row.sim === "esim" ? "eSIM" : row.sim === "chip" ? "chip físico" : "",
    row.region ? `región ${row.region}` : "",
  ].filter(Boolean).join(", ");
}

export type PlanModel = { ref: string; name: string; status: "existing" | "missing"; model_id: number | null; row_keys: string[] };
export type PlanOption = {
  ref: string; attribute_id: number; attribute_name: string; label: string; status: "existing" | "missing"; option_id: number | null;
};
export type PlanVariant = {
  ref: string; model_ref: string; line: "new" | "used"; option_refs: string[]; row_keys: string[];
  status: "existing" | "missing"; product_id: number | null; suggested_name: string; suggested_price: string | null;
  requires_imei: boolean; creates: { model: boolean; options: string[] };
};
export type PlanRow = { key: string; variant_ref: string | null; model_ref: string | null; reason: string; warnings: string[] };
export type ProposalCounts = {
  models_missing: number; models_existing: number; options_missing: number; options_existing: number;
  variants_missing: number; variants_existing: number; rows_resolved: number; rows_unresolved: number;
};
export type Proposal = {
  plan: { models: PlanModel[]; options: PlanOption[]; variants: PlanVariant[] };
  rows: PlanRow[];
  counts: ProposalCounts;
};
export type Approval = { models: string[]; variants: string[] };
export type ApplyResult = {
  created: { models: { ref: string; name: string }[]; options: { ref: string; label: string }[]; variants: { ref: string; name: string }[] };
  existing: { models: unknown[]; variants: unknown[] };
  replayed: boolean;
};

/** The rows that can ask for a catalogue: devices (an accessory is found by its product name). */
export function proposalRows(rows: ProposalRow[]) {
  return rows
    .filter(row => row.kind === "device" && row.line !== "accessory" && row.model.trim() && !colourIsOnlyIdentifier(row.colour))
    .map(row => ({
      key: String(row.id), kind: row.kind, line: row.line, model: modelWithoutIdentifiers(row.model).slice(0, 120),
      capacityGb: capacityInRange(row.capacityGb) ? row.capacityGb : null, colour: modelWithoutIdentifiers(row.colour).slice(0, 60),
    }));
}

function withCompany(path: string, companyId?: number | null): string {
  return companyId ? `${path}?company=${encodeURIComponent(String(companyId))}` : path;
}

async function post(path: string, body: unknown, companyId?: number | null, headers: Record<string, string> = {}) {
  const res = await fetchWithAuth(`${API_BASE}${withCompany(path, companyId)}`, {
    method: "POST", body: JSON.stringify(body), headers,
  });
  const answer = await res.json().catch(() => null);
  if (res.ok) return answer;
  throw new Error(answer && typeof answer.detail === "string" ? answer.detail
    : res.status === 403 ? "No tienes permiso para crear el catálogo: hace falta gestionar productos y ajustar inventario."
      : "No se pudo completar la operación.");
}

export function requestProposal(rows: ProposalRow[], companyId?: number | null): Promise<Proposal> {
  return post("/admin/inventory/text-preview/catalog-proposal/", { rows: proposalRows(rows) }, companyId);
}

export function applyProposal(
  rows: ProposalRow[], approve: Approval, prices: Record<string, string>, idempotencyKey: string, companyId?: number | null,
  withoutImei: string[] = [],
): Promise<ApplyResult> {
  const clean = Object.fromEntries(Object.entries(prices).filter(([, price]) => price.trim()).map(([ref, price]) => [ref, price.trim()]));
  return post("/admin/inventory/text-preview/catalog-proposal/apply/",
    // A device carries an IMEI unless the person says THIS variant does not: that statement is the only one that travels.
    { rows: proposalRows(rows), approve, prices: clean, without_imei: withoutImei.filter(ref => approve.variants.includes(ref)) },
    companyId, { "Idempotency-Key": idempotencyKey });
}

/** What can be approved: the missing models and the missing variants. */
export function approvable(plan: Proposal["plan"]): Approval {
  return {
    models: plan.models.filter(model => model.status === "missing").map(model => model.ref),
    variants: plan.variants.filter(variant => variant.status === "missing").map(variant => variant.ref),
  };
}

/** Ticking a variant ticks its missing model; unticking a model unticks its variants. */
export function toggleVariant(plan: Proposal["plan"], approval: Approval, ref: string, on: boolean): Approval {
  const variant = plan.variants.find(item => item.ref === ref);
  if (!variant) return approval;
  const variants = on ? [...new Set([...approval.variants, ref])] : approval.variants.filter(item => item !== ref);
  const model = plan.models.find(item => item.ref === variant.model_ref);
  const models = on && model?.status === "missing" ? [...new Set([...approval.models, model.ref])] : approval.models;
  return { models, variants };
}

export function toggleModel(plan: Proposal["plan"], approval: Approval, ref: string, on: boolean): Approval {
  if (on) return { ...approval, models: [...new Set([...approval.models, ref])] };
  const dropped = new Set(plan.variants.filter(variant => variant.model_ref === ref).map(variant => variant.ref));
  return { models: approval.models.filter(item => item !== ref), variants: approval.variants.filter(item => !dropped.has(item)) };
}

/** What approving this would create, in numbers, for the button and the summary. */
export function approvalCounts(plan: Proposal["plan"], approval: Approval) {
  const variants = plan.variants.filter(variant => approval.variants.includes(variant.ref));
  const options = new Set(variants.flatMap(variant => variant.creates.options));
  return { models: approval.models.length, variants: variants.length, options: options.size };
}
