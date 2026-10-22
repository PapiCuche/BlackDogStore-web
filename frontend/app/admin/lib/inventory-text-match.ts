// INV-TEXT-02 — which real variant each reviewed candidate is (read-only).
// Only the model, capacity, colour, kind and line travel: never the text,
// costs, quantities, IMEIs or serials. The server answers; nothing is written.

import { API_BASE } from "../../lib/api";
import { fetchWithAuth } from "../../lib/auth";
import type { TextCandidate } from "./inventory-text-preview";

export type CandidateDraft = Pick<TextCandidate, "id" | "kind" | "line" | "model" | "capacityGb" | "colour">;

export type MatchOption = { product_id: number; name: string; condition_line: string; options: string[] };
export type MatchResult = { status: "exact" | "ambiguous" | "none"; reason: string; options: MatchOption[] };

/** The request body: exactly the allowed fields, nothing else from the candidate. */
export function matchPayload(drafts: CandidateDraft[]) {
  return {
    candidates: drafts.map(draft => ({
      key: String(draft.id), kind: draft.kind, line: draft.line, model: draft.model.slice(0, 120),
      capacityGb: draft.capacityGb, colour: draft.colour.slice(0, 60),
    })),
  };
}

export async function matchCandidates(drafts: CandidateDraft[]): Promise<Record<string, MatchResult>> {
  const res = await fetchWithAuth(`${API_BASE}/admin/inventory/text-preview/match/`, {
    method: "POST", body: JSON.stringify(matchPayload(drafts)),
  });
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    throw new Error(body && typeof body.detail === "string" ? body.detail
      : res.status === 403 ? "No tienes permiso para consultar el catálogo." : "No se pudo consultar el catálogo.");
  }
  return (body?.matches ?? {}) as Record<string, MatchResult>;
}

/** The original text as the person pasted it, with long digit runs (IMEIs) masked for display. */
export function maskedSource(source: string): string {
  return source.replace(/\d(?:[ .-]?\d){11,}/g, run => "••••" + run.replace(/\D/g, "").slice(-4));
}
