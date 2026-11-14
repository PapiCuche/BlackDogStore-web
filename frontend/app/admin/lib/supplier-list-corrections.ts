/**
 * B1-1 · Hand corrections of a supplier list (owner's rule of 2026-11-14 on compound colours).
 *
 * A person may correct WRITING mistakes in the pasted text and read it again. A correction may never turn a colour the
 * received text left in doubt into a clean one by DELETING words: «glaciar blanco» corrected to «blanco» could change
 * the colour the supplier offers. That ambiguity is resolved only through the authorised ways of A2 — the model's
 * palette or a company alias — after which the received text itself reads without doubt.
 *
 * The received text is the one of the first reading of a list (memory only, like the list itself). A corrected line
 * (any line not exactly as received) stays blocked when:
 *   * it is paired with a received line whose colour was in doubt and its colour has fewer words; or
 *   * ANY received line of the same model had a colour in doubt and the corrected colour is only part of it
 *     («blanco» out of «glaciar blanco»), wherever the line was moved, copied or rewritten.
 * Lines are paired by their exact text (longest common subsequence); a changed block pairs line by line only when it
 * keeps the same number of lines — only to show what was received. No second grammar: the received text is read with
 * `readSupplierList` and the same lexicon.
 */

import { readSupplierList, type LineCorrection, type SupplierLexicon, type SupplierLine } from "./supplier-list-grammar";

const MAX_ALIGNED_CELLS = 2_000_000;
const splitLines = (text: string) => text.replace(/\r\n?/g, "\n").split("\n");
const same = (line: string) => line.trim().replace(/\s+/g, " ");
/** Words of a colour text, joiners included: a correction keeps them or replaces them, never just removes them. */
const wordsOf = (text: string): string[] => (text.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? []);
const wordCount = (text: string) => wordsOf(text).length;
/** Every word of `part` is in `whole` (as many times) and `part` has fewer: a colour cut out of a longer one. */
function cutFrom(part: string, whole: string): boolean {
  const pool = wordsOf(whole);
  const words = wordsOf(part);
  if (!words.length || words.length >= pool.length) return false;
  return words.every((word) => {
    const at = pool.indexOf(word);
    if (at < 0) return false;
    pool.splice(at, 1);
    return true;
  });
}
const modelKey = (line: SupplierLine | undefined) => (line?.model ? wordsOf(line.model.text).join(" ") : "");

/** Pairs of (received index, current index) with the same text, in order (longest common subsequence). */
function commonLines(received: string[], current: string[]): [number, number][] | null {
  const n = received.length;
  const m = current.length;
  // A huge text is not aligned (the table would not fit): nothing is paired, every changed line is shown as corrected
  // without its received line. The blocking rule by model below does not depend on pairing.
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

const unknownColour = (line: SupplierLine | undefined) =>
  line?.colourDoubt?.reason === "unknown_words" ? line.colourDoubt : null;

/**
 * Per current line key, how it corrects the received text. A line exactly as received has no entry.
 * `blocked` says why the line stays pending and how to resolve it.
 */
export function correctionsAgainst(receivedText: string, currentText: string, lexicon: SupplierLexicon): Record<string, LineCorrection> {
  if (receivedText === currentText) return {};
  const received = splitLines(receivedText);
  const current = splitLines(currentText);
  const receivedReading = readSupplierList(receivedText, lexicon);
  const currentReading = readSupplierList(currentText, lexicon);
  const receivedByNumber = new Map(receivedReading.lines.map((line) => [line.number, line]));
  const skippedByNumber = new Map(receivedReading.skipped.map((line) => [line.number, line]));
  const currentByNumber = new Map(currentReading.lines.map((line) => [line.number, line]));

  // The colours in doubt of the received text, by model: a correction may never keep only part of one.
  const doubtsByModel = new Map<string, string[]>();
  for (const line of receivedReading.lines) {
    const doubt = unknownColour(line);
    const model = modelKey(line);
    if (doubt && model) doubtsByModel.set(model, [...(doubtsByModel.get(model) ?? []), doubt.text]);
  }

  const out: Record<string, LineCorrection> = {};
  const common = commonLines(received.map(same), current.map(same));
  const pairs = [...(common ?? []), [received.length, current.length] as [number, number]];
  let fromReceived = 0;
  let fromCurrent = 0;
  for (const [toReceived, toCurrent] of pairs) {
    const receivedBlock = Array.from({ length: toReceived - fromReceived }, (_, k) => fromReceived + k + 1);
    const currentBlock = Array.from({ length: toCurrent - fromCurrent }, (_, k) => fromCurrent + k + 1);
    const paired = common !== null && receivedBlock.length === currentBlock.length;
    currentBlock.forEach((number, k) => {
      const line = currentByNumber.get(number);
      if (!line) return;
      const counterpart = paired ? receivedBlock[k] : null;
      const before = counterpart ? receivedByNumber.get(counterpart) ?? skippedByNumber.get(counterpart) : undefined;
      let blocked: string | null = null;
      // A colour still in doubt blocks the line by itself; a line without a colour is incomplete for the server.
      if (!line.colourDoubt && line.colourText) {
        const pairedDoubt = unknownColour(counterpart ? receivedByNumber.get(counterpart) : undefined);
        const lost = pairedDoubt && wordCount(line.colourText) < wordCount(pairedDoubt.text)
          ? pairedDoubt.text
          : (doubtsByModel.get(modelKey(line)) ?? []).find((text) => cutFrom(line.colourText, text));
        if (lost) {
          blocked = `La corrección quitó palabras del color recibido «${lost}» y dejó «${line.colourText}»: podría cambiar el color que ofrece el proveedor. Si es un solo color, agrégalo a la paleta del modelo; si es otra forma de escribir un color existente, regístralo como alias. Corregir el texto sólo vale para errores de escritura.`;
        }
      }
      out[line.key] = {
        received: before ? { display: before.display, displayWithoutAmounts: before.displayWithoutAmounts } : null,
        blocked,
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
