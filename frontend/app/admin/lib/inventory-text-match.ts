// INV-TEXT-02 — which real variant each reviewed candidate is (read-only).
// Only the model, capacity, colour, kind and line travel: never the text,
// costs, quantities, IMEIs or serials. The server answers; nothing is written.

import { API_BASE } from "../../lib/api";
import { fetchWithAuth } from "../../lib/auth";
import { imeiIsValid, replaceLuhnSpans, type TextCandidate } from "./inventory-text-preview";
import { identifierTokens } from "./inventory-text-stages";

export type CandidateDraft = Pick<TextCandidate, "id" | "kind" | "line" | "model" | "capacityGb" | "colour">;

export type MatchOption = {
  product_id: number; name: string; condition_line: string; options: string[];
  /** INV-RECEIVE-UX-02: how the article is counted, so a receipt line can be built without another request. */
  is_serialized?: boolean; requires_imei?: boolean; is_active?: boolean;
};
export type MatchResult = { status: "exact" | "ambiguous" | "none"; reason: string; options: MatchOption[] };

/** A capacity the catalogue can have (the server refuses anything else). */
export const capacityInRange = (gb: number | null) => gb === null || (Number.isInteger(gb) && gb > 0 && gb <= 8192);

/** The model as it may travel: without IMEIs, serials or other identifiers, also when typed by hand. */
export function modelWithoutIdentifiers(model: string): string {
  const tokens = model.split(/\s+/).filter(Boolean);
  const ids = identifierTokens(tokens);
  return tokens.filter((_token, index) => !ids.has(index)).join(" ")
    .replace(/\d+(?:[ .,/-]\d+)*/g, run => {
      if (!looksLikeIdentifier(run)) return run;
      // «13 3567 8901 2345 678»: the model number in front stays, the identifier after it goes.
      const lead = run.match(/^(\d{1,2})[ ](.+)$/);
      return lead && looksLikeIdentifier(lead[2]) ? lead[1] : "";
    })
    .split(/\s+/).filter(Boolean).join(" ");
}

/** A colour that is nothing but an identifier would turn into «no colour» once scrubbed: that row is not asked about. */
export function colourIsOnlyIdentifier(colour: string): boolean {
  return colour.trim() !== "" && modelWithoutIdentifiers(colour) === "";
}

const COLOUR_IDENTIFIER_ANSWER: MatchResult = {
  status: "none", reason: "El color lleva un IMEI, una serie u otro identificador: quítalo para buscar.", options: [],
};

/** The request body: exactly the allowed fields, nothing else from the candidate. */
export function matchPayload(drafts: CandidateDraft[]) {
  return {
    candidates: drafts.map(draft => ({
      key: String(draft.id), kind: draft.kind, line: draft.line, model: modelWithoutIdentifiers(draft.model).slice(0, 120),
      // Out of range: not sent (the row says why) — one bad row never fails the whole search.
      capacityGb: capacityInRange(draft.capacityGb) ? draft.capacityGb : null, colour: modelWithoutIdentifiers(draft.colour).slice(0, 60),
    })),
  };
}

/** INV-TEXT-03: `companyId` selects the same company the draft will be prepared in (the server re-validates it). */
export async function matchCandidates(drafts: CandidateDraft[], companyId?: number | null): Promise<Record<string, MatchResult>> {
  const asked = drafts.filter(draft => !colourIsOnlyIdentifier(draft.colour));
  const answers: Record<string, MatchResult> = {};
  for (const draft of drafts) if (colourIsOnlyIdentifier(draft.colour)) answers[String(draft.id)] = COLOUR_IDENTIFIER_ANSWER;
  if (!asked.length) return answers;
  // A search never hangs the preview: 15 s and it gives up (the person can search again).
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  let res: Response;
  try {
    const company = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
    res = await fetchWithAuth(`${API_BASE}/admin/inventory/text-preview/match/${company}`, {
      method: "POST", body: JSON.stringify(matchPayload(asked)), signal: controller.signal,
    });
  } catch {
    throw new Error("El catálogo no respondió a tiempo. Vuelve a buscar.");
  } finally {
    clearTimeout(timer);
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    throw new Error(body && typeof body.detail === "string" ? body.detail
      : res.status === 403 ? "No tienes permiso para consultar el catálogo." : "No se pudo consultar el catálogo.");
  }
  return { ...((body?.matches ?? {}) as Record<string, MatchResult>), ...answers };
}

/** A digit run that is an identifier and not a product line («15 128 2300»): long, or phone-shaped. */
function looksLikeIdentifier(run: string): boolean {
  const groups = run.split(/[ .,/-]/);
  const digits = groups.join("");
  return groups.some(group => group.length >= 7) || digits.length >= 12
    || (digits.length === 9 && digits.startsWith("9") && groups.every(group => group.length >= 2));
}

const masked = (digits: string) => "••••" + digits.slice(-4);

/** Groups that are one identifier as people write them: an IMEI (2-6-6-1, 3-3-3-3-3, 4-4-4-3, 8-7) or a mobile (9xx xxx xxx, 51 9xx xxx xxx). */
function isGroupedIdentifier(groups: string[]): boolean {
  const sizes = groups.map((g) => g.length).join("-");
  if (sizes === "3-3-3") return groups[0].startsWith("9");
  if (sizes === "2-3-3-3") return groups[0] === "51" && groups[1].startsWith("9");
  if (sizes === "3-3-3-3-3") return !["016", "032", "064", "128", "256", "512"].includes(groups[0]);
  return ["2-6-6-1", "4-4-4-3", "8-7"].includes(sizes);
}

/** One run of numbers, each identifier in it masked and every other number (capacity, price) left as written. */
function maskRun(run: string): string {
  const parts = run.split(/([ .,/-])/);
  const groups = parts.filter((_part, index) => index % 2 === 0);
  let out = "";
  for (let i = 0; i < groups.length;) {
    let taken = 0;
    for (const size of [5, 4, 3, 2]) {
      if (i + size <= groups.length && isGroupedIdentifier(groups.slice(i, i + size))) { taken = size; break; }
    }
    if (!taken && looksLikeIdentifier(groups[i])) taken = 1;
    const end = i + Math.max(taken, 1);
    out += taken ? masked(groups.slice(i, end).join("")) : groups[i];
    out += parts[2 * end - 1] ?? "";
    i = end;
  }
  return out;
}

/**
 * The original text as the person pasted it, for display: long digit runs (IMEIs, phones, documents, also
 * split by spaces, dots, dashes, slashes or commas) and codes that mix letters and digits (serials) masked.
 */
export function maskedSource(source: string): string {
  return source
    // After a serial label, its value whatever it looks like (also letters only).
    .replace(/\b(NS|N\/S|S\/N|SN|serie|serial)(\s*[:#=]?\s*)([A-Za-z0-9-]{4,40})/gi,
      (_m, label, gap, value) => `${label}${gap}••••${value.length >= 10 ? value.slice(-2) : ""}`)
    // After an «IMEI» label, the whole value however it is grouped.
    .replace(/\b(imei\s*[12]?)(\s*[:#=]?\s*)(\d(?:[ .,/-]?\d){13,15})(?!\d)/gi, (_m, label, gap, value: string) => {
      // Only the IMEI: a number written right after it («… 2 unid») stays visible.
      const digits = value.replace(/\D/g, "");
      if (digits.length === 16 && imeiIsValid(digits.slice(0, 15))) {
        const tail = /[ .,/-]\d$/.exec(value)?.[0] ?? "";
        if (tail) return `${label}${gap}••••${digits.slice(11, 15)}${tail}`;
      }
      return `${label}${gap}••••${digits.slice(-4)}`;
    })
    // Defence in depth (fourth review): any numbers that together make a Luhn-valid 15-digit IMEI.
    .replace(/[^\n]+/g, (line) => replaceLuhnSpans(line, (digits) => masked(digits)))
    // INV-TEXT-03 third review: elsewhere only the identifiers inside a run of numbers, never the capacity or the price
    // next to them («iPhone 11 64 1004 ••••4321»).
    .replace(/\d+(?:[ .,/-]\d+)*/g, maskRun)
    .replace(/\b(?=[A-Za-z0-9-]*\d)(?=[A-Za-z0-9-]*[A-Za-z])[A-Za-z0-9-]{6,}\b/g,
      code => (/^\d{1,3}(?:gb|tb|w|m)$/i.test(code) ? code : "••••" + code.slice(-4)));
}
