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
 *   * ANY received colour of the same company model (by model id, not by spelling) — wherever the line was moved,
 *     copied or rewritten — when the line has no received counterpart, or its counterpart DID have the words the
 *     cut leaves out (a line cannot lose «glaciar» it never had: a typo fix on a sibling «512gb blanco» is free);
 * unless a received line of the SAME configuration (model, capacity, SIM and region) offers that colour as written:
 * a sibling «512gb blanco» never excuses «256gb glaciar blanco» → «256gb blanco». Single letters are not words here
 * («azul l» → «azul» is a typo). The blocking rule does not depend on how lines are paired; pairing (exact text, then
 * the same text anywhere, then the closest words within a changed block) only says what to show as received.
 * No second grammar: the received text is read with `readSupplierList` and the same lexicon.
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
/** Model, capacity, SIM and region as read: the configuration apart from its colour. */
const configurationIdentity = (line: SupplierLine, lexicon: SupplierLexicon) => [
  modelIdentity(line, lexicon), phraseKey(line.capacityText).replace(/ /g, ""), phraseKey(line.simText), phraseKey(line.regionText),
].join("|");
/**
 * Words two lines share, over the words of both (Jaccard): to pair lines of a changed block. Numbers of three digits
 * or more (prices, phones) are left out — a corrected price is the commonest fix; a model number («15») stays.
 */
const pairingWords = (text: string) => new Set(phraseKey(text).split(" ").filter((word) => word && !/^\d{3,}$/.test(word)));
/**
 * A pasted line is an EDITED copy of a received one when it takes words out of it: it adds at most one word (a typo
 * fixed on the way) and drops more than it adds (prices and phones left out). A line that swaps one word for another
 * — the next message's «naranja» after «azul» — is new supplier text.
 */
export function isEditedCopy(received: string, pasted: string): boolean {
  const before = pairingWords(received);
  const after = pairingWords(pasted);
  const added = [...after].filter((word) => !before.has(word)).length;
  const dropped = [...before].filter((word) => !after.has(word)).length;
  return after.size > 0 && added <= 1 && dropped > added;
}
export function likeness(a: string, b: string): number {
  const x = pairingWords(a);
  const y = pairingWords(b);
  const both = [...x].filter((word) => y.has(word)).length;
  const any = new Set([...x, ...y]).size;
  return any ? both / any : 0;
}

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

  // Every colour the received text offers, by company model and by configuration: a correction may never keep only
  // part of one, unless the same configuration was offered in that colour as written.
  const coloursByModel = new Map<string, string[]>();
  const coloursByConfiguration = new Map<string, string[]>();
  for (const line of receivedReading.lines) {
    const colour = colourOf(line);
    const model = modelIdentity(line, lexicon);
    if (!colour || !model) continue;
    coloursByModel.set(model, [...(coloursByModel.get(model) ?? []), colour]);
    const configuration = configurationIdentity(line, lexicon);
    coloursByConfiguration.set(configuration, [...(coloursByConfiguration.get(configuration) ?? []), colour]);
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
    // Changed offer lines and received offer lines of the block are paired most-alike first (at least half of their
    // words shared; on a tie, the same position first). Never by position alone: two edited lines that swapped
    // places must not borrow each other's history.
    const blockPairs = new Map<number, number>();
    if (common !== null) {
      const candidates: { from: number; to: number; value: number; positional: boolean }[] = [];
      receivedBlock.forEach((from, i) => {
        if (!receivedByNumber.has(from)) return;
        currentBlock.forEach((to, j) => {
          const value = likeness(received[from - 1], current[to - 1]);
          if (value >= 0.5) candidates.push({ from, to, value, positional: i === j });
        });
      });
      candidates.sort((a, b) => b.value - a.value || Number(b.positional) - Number(a.positional));
      const taken = new Set<number>();
      for (const { from, to } of candidates) {
        if (taken.has(from) || blockPairs.has(to)) continue;
        taken.add(from);
        blockPairs.set(to, from);
      }
    }
    const pairOf = (number: number): number | null => blockPairs.get(number) ?? null;
    currentBlock.forEach((number) => {
      const line = currentByNumber.get(number);
      if (!line) return;
      const text = current[number - 1];
      if ((leftOver.get(text) ?? 0) > 0) {
        leftOver.set(text, leftOver.get(text)! - 1);
        return;
      }
      const counterpart = pairOf(number);
      const before = counterpart ? receivedByNumber.get(counterpart) : undefined;
      const shown = before ?? (counterpart ? skippedByNumber.get(counterpart) : undefined);
      let blocked: string | null = null;
      // A colour still in doubt blocks the line by itself; a line without a colour is incomplete for the server.
      if (!line.colourDoubt && line.colourText) {
        const was = colourOf(before);
        if (was && cutFrom(line.colourText, was)) blocked = blockText(was, line.colourText);
        if (!blocked) {
          const had = before ? new Set(wordsOf(before.display)) : null;
          const kept = distinct(line.colourText);
          const cut = (coloursByModel.get(modelIdentity(line, lexicon)) ?? []).find((colour) => cutFrom(line.colourText, colour)
            && (had === null || [...distinct(colour)].some((word) => !kept.has(word) && had.has(word))));
          const asWritten = (coloursByConfiguration.get(configurationIdentity(line, lexicon)) ?? [])
            .some((colour) => sameWords(line.colourText, colour));
          if (cut && !asWritten) blocked = blockText(cut, line.colourText);
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
