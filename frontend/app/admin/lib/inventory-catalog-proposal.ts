/**
 * INV-FAST-B1 — «Proponer lo que falta»: el catálogo que faltan las filas pegadas, aprobado por la persona.
 *
 * El servidor propone (`…/text-preview/catalog-proposal/`, sólo lectura) y crea SÓLO lo aprobado
 * (`…/catalog-proposal/apply/`, atómico e idempotente). Viajan modelo, capacidad, color, SIM, región y línea; nunca el
 * texto, costos, IMEI ni series.
 *
 * SIM y región (decisión del propietario, 2026-11-16): el servidor las lee contra el atributo de la empresa con ese ROL
 * (etiqueta y alias de la empresa). El lector de texto guarda la SIM como código (`chip` / `esim`: el token original
 * —«chip», «físico», «e-sim»— ya no existe), así que viaja la etiqueta más común de ese código («Chip», «eSIM»); la empresa
 * que la llama de otro modo («Físico», «Nano SIM») la concilia con un alias de opción. La región ya es el código
 * normalizado («LL/A», «J/A») y viaja tal cual, sin identificadores. Una opción de SIM o región que falta NO se aprueba
 * al marcar la variante: la persona la marca aparte (`approve.options`). Toda variante exige IMEI salvo que la persona marque «Sin IMEI» en ella. Nada se guarda en el navegador.
 */
import { API_BASE } from "../../lib/api";
import { fetchWithAuth } from "../../lib/auth";
import type { AttributeRole } from "./internal-api";
import { capacityInRange, colourIsOnlyIdentifier, modelWithoutIdentifiers } from "./inventory-text-match";
import type { TextCandidate } from "./inventory-text-preview";

export type ProposalRow = Pick<TextCandidate, "id" | "kind" | "line" | "model" | "capacityGb" | "colour">
  & Partial<Pick<TextCandidate, "ramGb" | "sim" | "region">>;

/**
 * What the text names that the catalogue does not distinguish (RAM): not an option of a variant, so it is never
 * reconciled — and never dropped in silence: each row that carries it says so. SIM and region travel to the server and
 * are reconciled there (options of the attributes with those roles), so they are not listed here.
 */
export function identityExtras(row: Partial<Pick<TextCandidate, "ramGb" | "sim" | "region">>): string {
  return row.ramGb ? `${row.ramGb} GB de RAM` : "";
}

/** The SIM code the reader keeps, as the text the server's resolver reads (label or company alias). */
export const SIM_TEXT: Record<"chip" | "esim", string> = { chip: "Chip", esim: "eSIM" };

export type PlanModel = { ref: string; name: string; status: "existing" | "missing"; model_id: number | null; row_keys: string[] };
export type PlanOption = {
  ref: string; attribute_id: number; attribute_name: string; label: string; status: "existing" | "missing"; option_id: number | null;
  role?: AttributeRole;
  /** A missing SIM or region option: created only when the person ticks it (`approve.options`). */
  needs_approval?: boolean;
};
export type PlanVariant = {
  ref: string; model_ref: string; line: "new" | "used"; option_refs: string[]; row_keys: string[];
  /** VARIANT-SAFE-01: `archived` — a variant IS it but it is archived: never approvable, never created again. */
  status: "existing" | "missing" | "archived"; product_id: number | null; suggested_name: string; suggested_price: string | null;
  requires_imei: boolean; creates: { model: boolean; options: string[] };
  /** VARIANT-SAFE-01: the server's sentence about the archived variant. */
  archived?: string;
  /** Review HIGH: its rows say different SIMs / regions the catalogue cannot separate — never approvable. */
  mixed?: string;
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
export type Approval = { models: string[]; variants: string[]; options: string[] };
export type ApplyResult = {
  created: { models: { ref: string; name: string }[]; options: { ref: string; label: string }[]; variants: { ref: string; name: string }[] };
  existing: { models: unknown[]; variants: unknown[] };
  replayed: boolean;
};

/** The rows that can ask for a catalogue: devices (an accessory is found by its product name). */
export function proposalRows(rows: ProposalRow[]) {
  return rows
    .filter(row => row.kind === "device" && row.line !== "accessory" && row.model.trim() && !colourIsOnlyIdentifier(row.colour))
    .map(row => {
      const sim = row.sim ? SIM_TEXT[row.sim] ?? "" : "";
      const region = modelWithoutIdentifiers(row.region ?? "").slice(0, 30);
      return {
        key: String(row.id), kind: row.kind, line: row.line, model: modelWithoutIdentifiers(row.model).slice(0, 120),
        capacityGb: capacityInRange(row.capacityGb) ? row.capacityGb : null, colour: modelWithoutIdentifiers(row.colour).slice(0, 60),
        // Only when said: a row without them is the same request it always was.
        ...(sim ? { sim } : {}), ...(region ? { region } : {}),
      };
    });
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
    { rows: proposalRows(rows), approve: { ...approve, options: approve.options ?? [] }, prices: clean, without_imei: withoutImei.filter(ref => approve.variants.includes(ref)) },
    companyId, { "Idempotency-Key": idempotencyKey });
}

/**
 * What «Seleccionar todo» approves: the missing models and variants. Never a SIM or region option: each one is ticked
 * by the person (`options` stays empty here).
 */
export function approvable(plan: Proposal["plan"]): Approval {
  return {
    models: plan.models.filter(model => model.status === "missing").map(model => model.ref),
    variants: plan.variants.filter(variant => variant.status === "missing" && !variant.mixed).map(variant => variant.ref),
    options: [],
  };
}

/** Ticking a variant ticks its missing model — never its missing SIM or region option; unticking a model unticks its variants. */
export function toggleVariant(plan: Proposal["plan"], approval: Approval, ref: string, on: boolean): Approval {
  if (on && plan.variants.find(item => item.ref === ref)?.mixed) return approval;
  const variant = plan.variants.find(item => item.ref === ref);
  if (!variant) return approval;
  const variants = on ? [...new Set([...approval.variants, ref])] : approval.variants.filter(item => item !== ref);
  const model = plan.models.find(item => item.ref === variant.model_ref);
  const models = on && model?.status === "missing" ? [...new Set([...approval.models, model.ref])] : approval.models;
  return { models, variants, options: approval.options };
}

export function toggleModel(plan: Proposal["plan"], approval: Approval, ref: string, on: boolean): Approval {
  if (on) return { ...approval, models: [...new Set([...approval.models, ref])] };
  const dropped = new Set(plan.variants.filter(variant => variant.model_ref === ref).map(variant => variant.ref));
  return {
    models: approval.models.filter(item => item !== ref), variants: approval.variants.filter(item => !dropped.has(item)),
    options: approval.options,
  };
}

/** The SIM or region option the person approves (or withdraws) creating. */
export function toggleOption(approval: Approval, ref: string, on: boolean): Approval {
  const options = on ? [...new Set([...approval.options, ref])] : approval.options.filter(item => item !== ref);
  return { ...approval, options };
}

/** The missing SIM / region options of the plan: each one has its own checkbox, unticked. */
export function optionsToApprove(plan: Proposal["plan"]): PlanOption[] {
  return plan.options.filter(option => option.status === "missing" && option.needs_approval);
}

/** The options an approved variant needs that the person has not ticked: the server would refuse them. */
export function pendingOptions(plan: Proposal["plan"], approval: Approval): PlanOption[] {
  const needed = new Set(plan.variants.filter(variant => approval.variants.includes(variant.ref)).flatMap(variant => variant.creates.options));
  return optionsToApprove(plan).filter(option => needed.has(option.ref) && !approval.options.includes(option.ref));
}

/** What approving this would create, in numbers, for the button and the summary. */
export function approvalCounts(plan: Proposal["plan"], approval: Approval) {
  const variants = plan.variants.filter(variant => approval.variants.includes(variant.ref));
  const options = new Set(variants.flatMap(variant => variant.creates.options));
  return { models: approval.models.length, variants: variants.length, options: options.size };
}
