/**
 * B1-1 · Hand corrections of a supplier list (owner's rule of 2026-11-14 on compound colours).
 *
 * A person may correct WRITING mistakes in the pasted text and read it again. A correction may never turn a colour
 * into a shorter one by DELETING words: «glaciar blanco» corrected to «blanco» changes the colour the supplier offers,
 * even after «Glaciar Blanco» is added to the palette. The doubt is resolved only through the authorised ways of A2 —
 * the model's palette or a company alias — with the text left as it arrived.
 *
 * The received text is the list as it was PASTED (memory only, like the list itself; see the screen). A corrected
 * line (any line not exactly as received) stays blocked when its colour keeps only some of the distinct words of:
 *   * the colour of the received line it is paired with; or
 *   * any received colour of the same company model (by model id, not by spelling), when the line is not paired or
 *     its colour changed — wherever the line was moved, copied or rewritten.
 * A colour that the received text also offers as written for that model is not a cut. Single letters are not words
 * here («azul l» → «azul» is a typo). Lines are paired by their exact text (longest common subsequence, then the same
 * text anywhere); a changed block pairs line by line only when it keeps the same number of lines. No second grammar:
 * the received text is read with `readSupplierList` and the same lexicon.
 */

import {
  modelIdOfText, phraseKey, readSupplierList, type LineCorrection, type SupplierLexicon, type SupplierLine,
} from "./supplier-list-grammar";

const MAX_ALIGNED_CELLS = 2_000_000;
export const splitLines = (text: string) => text.replace(/\r\n?/g, "\n").split("\n");
export const sameLine = (line: string) => line.trim().replace(/\s+/g, " ");

/** The words that make a colour: folded, single letters left out (a stray key is a typo, not a word). */
const wordsOf = (text: string): string[] => (text.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase()
  .match(/[\p{L}\p{N}]+/gu) ?? []).filter((word) => word.length > 1 || /\d/.test(word));
const distinct = (text: string) => new Set(wordsOf(text));
const sameWords = (a: string, b: string) => {
  const x = distinct(a);
  const y = distinct(b);
  return x.size === y.size && [...x].every((word) => y.has(word));
};
/** `part` keeps only some of the distinct words of `whole`: a colour cut out of a longer one. */
function cutFrom(part: string, whole: string): boolean {
  const x = distinct(part);
  const y = distinct(whole);
  return x.size > 0 && x.size < y.size && [...x].every((word) => y.has(word));
}

/** Pairs of (received index, current index) with the same text, in order (longest common subsequence). */
function commonLines(received: string[], current: string[]): [number, number][] | null {
  const n = received.length;
  const m = current.length;
  // A huge text is not aligned (the table would not fit): changed lines are not paired. The blocking rule by model
  // does not depend on pairing.
  if (n * m > MAX_ALIGNED_CELLS) return null;
  const table: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      table[i][j] = received[i] === current[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  const pairs: [number, number][] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (received[i] === current[j]) { pairs.push([i, j]); i++; j++; }
    else if (table[i + 1][j] >= table[i][j + 1]) i++;
    else j++;
  }
  return pairs;
}

/** The colour a line offers as written: its clean colour, or the colour zone left in doubt by unknown words. */
const colourOf = (line: SupplierLine | undefined) =>
  line?.colourText || (line?.colourDoubt?.reason === "unknown_words" ? line.colourDoubt.text : "");

const modelIdentity = (line: SupplierLine, lexicon: SupplierLexicon) => {
  if (!line.model) return "";
  const id = modelIdOfText(line.model.text, lexicon);
  return id !== null ? `id:${id}` : `text:${phraseKey(line.model.text)}`;
};

const blockText = (received: string, kept: string) =>
  `La corrección quitó palabras del color recibido «${received}» y dejó «${kept}»: cambiaría el color que ofrece el proveedor. Deja el color como llegó y resuélvelo en la paleta del modelo o con un alias. Corregir el texto sólo vale para errores de escritura.`;

/** Share of the non-blank lines of `text` that are, as written, lines of `received` (0 when it has none). */
export function sharedShare(received: string, text: string): number {
  const pool = new Set(splitLines(received).map(sameLine).filter(Boolean));
  const lines = splitLines(text).map(sameLine).filter(Boolean);
  return lines.length ? lines.filter((line) => pool.has(line)).length / lines.length : 0;
}

/**
 * Per current line key, how it corrects the received text. A line exactly as received (wherever it moved) has no
 * entry. `blocked` says why the line stays pending and how to resolve it.
 */
export function correctionsAgainst(receivedText: string, currentText: string, lexicon: SupplierLexicon): Record<string, LineCorrection> {
  if (receivedText === currentText) return {};
  const received = splitLines(receivedText).map(sameLine);
  const current = splitLines(currentText).map(sameLine);
  const receivedReading = readSupplierList(receivedText, lexicon);
  const currentReading = readSupplierList(currentText, lexicon);
  const receivedByNumber = new Map(receivedReading.lines.map((line) => [line.number, line]));
  const skippedByNumber = new Map(receivedReading.skipped.map((line) => [line.number, line]));
  const currentByNumber = new Map(currentReading.lines.map((line) => [line.number, line]));

  // Every colour the received text offers, by company model: a correction may never keep only part of one.
  const coloursByModel = new Map<string, string[]>();
  for (const line of receivedReading.lines) {
    const colour = colourOf(line);
    const model = modelIdentity(line, lexicon);
    if (colour && model) coloursByModel.set(model, [...(coloursByModel.get(model) ?? []), colour]);
  }

  const common = commonLines(received, current);
  const pairs = [...(common ?? []), [received.length, current.length] as [number, number]];
  const matchedReceived = new Set((common ?? []).map(([i]) => i));
  // A line moved as it was is not a correction: received lines left over, by text.
  const leftOver = new Map<string, number>();
  received.forEach((text, index) => {
    if (!matchedReceived.has(index) && text) leftOver.set(text, (leftOver.get(text) ?? 0) + 1);
  });

  const out: Record<string, LineCorrection> = {};
  let fromReceived = 0;
  let fromCurrent = 0;
  for (const [toReceived, toCurrent] of pairs) {
    const receivedBlock = Array.from({ length: toReceived - fromReceived }, (_, k) => fromReceived + k + 1);
    const currentBlock = Array.from({ length: toCurrent - fromCurrent }, (_, k) => fromCurrent + k + 1);
    const paired = common !== null && receivedBlock.length === currentBlock.length;
    currentBlock.forEach((number, k) => {
      const line = currentByNumber.get(number);
      if (!line) return;
      const text = current[number - 1];
      if ((leftOver.get(text) ?? 0) > 0) {
        leftOver.set(text, leftOver.get(text)! - 1);
        return;
      }
      const counterpart = paired ? receivedBlock[k] : null;
      const before = counterpart ? receivedByNumber.get(counterpart) : undefined;
      const shown = before ?? (counterpart ? skippedByNumber.get(counterpart) : undefined);
      let blocked: string | null = null;
      // A colour still in doubt blocks the line by itself; a line without a colour is incomplete for the server.
      if (!line.colourDoubt && line.colourText) {
        const was = colourOf(before);
        const unchanged = Boolean(was) && sameWords(line.colourText, was);
        if (was && !unchanged && cutFrom(line.colourText, was)) blocked = blockText(was, line.colourText);
        if (!blocked && !unchanged) {
          const offered = coloursByModel.get(modelIdentity(line, lexicon)) ?? [];
          const cut = offered.find((colour) => cutFrom(line.colourText, colour));
          if (cut && !offered.some((colour) => sameWords(line.colourText, colour))) blocked = blockText(cut, line.colourText);
        }
      }
      const kept = new Set(phraseKey(line.display).split(" "));
      const removedWords = before ? before.unknownWords.filter((word) => !phraseKey(word).split(" ").every((part) => kept.has(part))) : [];
      out[line.key] = {
        received: shown ? { display: shown.display, displayWithoutAmounts: shown.displayWithoutAmounts } : null,
        blocked,
        removedWords,
      };
    });
    fromReceived = toReceived + 1;
    fromCurrent = toCurrent + 1;
  }
  return out;
}

/** The lines with their corrections attached (`lineDoubts` reads `correction.blocked`). */
export function withCorrections(lines: SupplierLine[], corrections: Record<string, LineCorrection>): SupplierLine[] {
  return lines.map((line) => (corrections[line.key] ? { ...line, correction: corrections[line.key] } : line));
}
