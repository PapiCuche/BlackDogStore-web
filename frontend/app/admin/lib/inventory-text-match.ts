// INV-TEXT-02 — which real variant each reviewed candidate is (read-only).
// Only the model, capacity, colour, kind and line travel: never the text,
// costs, quantities, IMEIs or serials. The server answers; nothing is written.

import { API_BASE } from "../../lib/api";
import { fetchWithAuth } from "../../lib/auth";
import type { TextCandidate } from "./inventory-text-preview";

export type CandidateDraft = Pick<TextCandidate, "id" | "kind" | "line" | "model" | "capacityGb" | "colour">;

export type MatchOption = { product_id: number; name: string; condition_line: string; options: string[] };
export type MatchResult = { status: "exact" | "ambiguous" | "none"; reason: string; options: MatchOption[] };

/** A capacity the catalogue can have (the server refuses anything else). */
export const capacityInRange = (gb: number | null) => gb === null || (Number.isInteger(gb) && gb > 0 && gb <= 8192);

/** The request body: exactly the allowed fields, nothing else from the candidate. */
export function matchPayload(drafts: CandidateDraft[]) {
  return {
    candidates: drafts.map(draft => ({
      key: String(draft.id), kind: draft.kind, line: draft.line, model: draft.model.slice(0, 120),
      // Out of range: not sent (the row says why) — one bad row never fails the whole search.
      capacityGb: capacityInRange(draft.capacityGb) ? draft.capacityGb : null, colour: draft.colour.slice(0, 60),
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

/** A digit run that is an identifier and not a product line («15 128 2300»): long, or phone-shaped. */
function looksLikeIdentifier(run: string): boolean {
  const groups = run.split(/[ .,/-]/);
  const digits = groups.join("");
  return groups.some(group => group.length >= 7) || digits.length >= 12
    || (digits.length === 9 && digits.startsWith("9") && groups.every(group => group.length >= 2));
}

/**
 * The original text as the person pasted it, for display: long digit runs (IMEIs, phones, documents, also
 * split by spaces, dots, dashes, slashes or commas) and codes that mix letters and digits (serials) masked.
 */
export function maskedSource(source: string): string {
  return source
    .replace(/\d+(?:[ .,/-]\d+)*/g, run => (looksLikeIdentifier(run) ? "••••" + run.replace(/\D/g, "").slice(-4) : run))
    .replace(/\b(?=[A-Za-z0-9-]*\d)(?=[A-Za-z0-9-]*[A-Za-z])[A-Za-z0-9-]{6,}\b/g,
      code => (/^\d{1,3}(?:gb|tb|w|m)$/i.test(code) ? code : "••••" + code.slice(-4)));
}
