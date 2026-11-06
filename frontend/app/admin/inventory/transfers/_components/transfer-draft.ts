// Decisión D — the draft of a transfer, as data.
//
// The quantities live in a map keyed by PRODUCT ID, never by the position of a
// row on screen. Searching and «Sólo con cantidad» only decide which rows are
// shown; what «Guardar líneas» sends is always computed from the whole map, so a
// line hidden by a filter is still sent exactly as typed.

import type { AdminProduct } from "../../../../lib/admin";
import type { StockTransferItem } from "../../../../lib/inventory";

/** Typed quantities by product id. A missing key and "" both mean «no line». */
export type TransferDraft = Record<number, string>;

/** One editable row: a product that can be put on the transfer. */
export type DraftRow = { id: number; name: string; code: string };

/** Lower case, without accents (á→a, ñ→n), whitespace collapsed. */
export function normalizeForSearch(text: string): string {
  return text
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/\s+/g, " ")
    .trim();
}

/** Every word of the query appears in the name or the code, in any order. */
export function matchesSearch(row: DraftRow, query: string): boolean {
  const words = normalizeForSearch(query).split(" ").filter(Boolean);
  if (words.length === 0) return true;
  const haystack = normalizeForSearch(`${row.name} ${row.code}`);
  return words.every((word) => haystack.includes(word));
}

export function draftQuantity(value: string | undefined): number {
  return Math.max(0, parseInt(value ?? "", 10) || 0);
}

export function hasQuantity(value: string | undefined): boolean {
  return draftQuantity(value) > 0;
}

/** The payload of «Guardar líneas»: every product with a quantity, shown or not. */
export function draftLines(draft: TransferDraft): { product: number; quantity: number }[] {
  return Object.entries(draft)
    .map(([product, quantity]) => ({ product: Number(product), quantity: draftQuantity(quantity) }))
    .filter((line) => line.quantity > 0);
}

/**
 * The rows to offer: the transfer's own lines whose product is not in the
 * catalogue list (so a saved line never becomes invisible and unreachable),
 * then the catalogue in the order the server gave it.
 */
export function buildDraftRows(products: AdminProduct[], items: StockTransferItem[]): DraftRow[] {
  const listed = new Set(products.map((p) => p.id));
  const orphanLines = items
    .filter((item) => !listed.has(item.product))
    .map((item) => ({ id: item.product, name: item.product_name, code: "" }));
  return [
    ...orphanLines,
    ...products.map((p) => ({ id: p.id, name: p.name, code: p.primary_code ?? "" })),
  ];
}
