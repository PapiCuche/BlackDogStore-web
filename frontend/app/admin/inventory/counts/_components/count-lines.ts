/**
 * MIGRATION-D — how a count line reads on screen. Pure: no fetch, no state.
 *
 * BLANK IS NOT ZERO. `physical_quantity === null` is «nobody counted this»;
 * `0` is «somebody looked and there are none». Every helper here keeps the two
 * apart, and none of them ever reads `reference_quantity` as a count.
 */

import type { CountDetail, CountLine } from "../../../lib/counts-api";

export type LineState = "pending" | "counted" | "ambiguous" | "overlapping" | "excluded";

export function lineState(line: CountLine): LineState {
  if (line.excluded || line.review_state === "excluded") return "excluded";
  if (line.review_state === "ambiguous") return "ambiguous";
  if (line.review_state === "overlapping") return "overlapping";
  if (line.physical_quantity === null) return "pending";
  return "counted";
}

export const LINE_STATE_LABEL: Record<LineState, string> = {
  pending: "Pendiente",
  counted: "Contada",
  ambiguous: "Recontar",
  overlapping: "Bloqueada",
  excluded: "Excluida",
};

/** Not counted and not excluded. An ambiguous line is counted, but must be recounted. */
export function pendingLines(count: CountDetail): CountLine[] {
  return count.items.filter((l) => lineState(l) === "pending");
}

export function ambiguousLines(count: CountDetail): CountLine[] {
  return count.items.filter((l) => lineState(l) === "ambiguous");
}

export function overlappingLines(count: CountDetail): CountLine[] {
  return count.items.filter((l) => lineState(l) === "overlapping");
}

/**
 * The difference the screen shows: the one written at approval if there is one;
 * else the server's forecast; else `physical − theoretical_at_start` (ERP-1).
 * `null` while the line is not counted — never «0».
 */
export function lineDifference(line: CountLine): number | null {
  if (line.difference !== null && line.difference !== undefined) return line.difference;
  if (line.expected_difference !== null && line.expected_difference !== undefined) return line.expected_difference;
  if (line.physical_quantity === null || line.theoretical_at_start === null) return null;
  return line.physical_quantity - line.theoretical_at_start;
}

export function lineCode(line: CountLine): string | null {
  return line.product_code ?? line.product_codes?.[0] ?? null;
}

export function lineMatchesCode(line: CountLine, code: string): boolean {
  return line.product_code === code || Boolean(line.product_codes?.includes(code));
}

/** Replaces the line of the same product, or appends it. */
export function mergeLine(count: CountDetail, line: CountLine): CountDetail {
  const index = count.items.findIndex((l) => l.product === line.product);
  const items = index === -1 ? [...count.items, line] : count.items.map((l, i) => (i === index ? line : l));
  return { ...count, items, counted_items: items.filter((l) => l.physical_quantity !== null).length };
}

export function signed(value: number): string {
  return value > 0 ? `+${value}` : String(value);
}

/** The largest count the server stores (a PostgreSQL integer). */
export const MAX_QUANTITY = 2_147_483_647;
/** Above this a count is asked for again: it is more often a code than a shelf. */
export const PLAUSIBLE_QUANTITY = 9_999;

/** A whole number from 0. Blank and anything else → null (the caller says why). */
export function parseQuantity(raw: string): number | null {
  const text = raw.trim();
  if (!/^\d+$/.test(text)) return null;
  const n = Number(text);
  return Number.isSafeInteger(n) ? n : null;
}
