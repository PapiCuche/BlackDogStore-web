"use client";

/**
 * INV-RECEIVE-UX-02 — «Pegar una lista» dentro del paso «Artículos» del asistente de recepción.
 *
 * El sistema hace el trabajo repetitivo y la persona resuelve las excepciones:
 *
 *   1. pega el texto y pulsa «Analizar y preparar» (reconocedor local: el texto no sale del navegador);
 *   2. el catálogo se busca solo y la coincidencia exacta se asocia sola;
 *   3. lo que falta se resuelve UNA vez por combinación de modelo, capacidad y color (no una por equipo);
 *   4. cada equipo se revisa en su fila (serie, IMEI, batería, estado) y entra a la recepción.
 *
 * Aquí no se escribe nada en el servidor salvo lo que la persona aprueba en «Lo que falta en el catálogo»
 * (modelos y variantes, ocultos de la web). El stock sólo entra con «Confirmar» del paso final.
 * Serie, IMEI, batería, costos y el texto original viven en la memoria de la página.
 */

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";

import { maskedSource, matchCandidates, type MatchResult } from "../lib/inventory-text-match";
import { MAX_TEXT_CHARS, previewSupplierText, type InventoryLine } from "../lib/inventory-text-preview";
import { isUsedLine, priceIsValid, PUBLIC_NOTES_MAX, UNIT_CONDITIONS, UNIT_NOTES_MAX, type DraftLine, type ReceiptProduct } from "../lib/inventory-receipts";
import {
  applyChoice, applyGrades, applyMatches, chunk, classify, commitRows, confirmDoubt, editRow, evaluateRows, FIELD_LABEL, groupRows,
  MAX_STAGED, nextPending, nextRowId, nextScanTarget, leadIssue, optionToProduct, pendingItems, proposalRowsFor, resolveAmount,
  rowSignature, stageRows, summarize, withoutRepeats, forgetSources, mergeChoice, bulkTargets, applyPrice, priceSummary, takesUnitPrice, type Group, type IssueField, type MatchKind, type PasteState, type Pending,
  type RowEvaluation, type StagedRow,
} from "../lib/receive-list";
import type { CosmeticGradeRow } from "../lib/internal-api";
import { CatalogProposalPanel } from "./CatalogProposalPanel";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";
import { DeviceFieldGroups, Field, UsedCommercialNotes } from "./ReceivingLinesEditor";
import { ProductPicker, type PickerProduct } from "./ProductPicker";

type Props = {
  /** INV-TEXT-03: the pasted text, held by the wizard (memory of the page) so leaving the tab or the step keeps it. */
  paste: PasteState;
  onPaste: (update: (paste: PasteState) => PasteState) => void;
  rows: StagedRow[];
  onRows: (update: (rows: StagedRow[]) => StagedRow[]) => void;
  lines: DraftLine[];
  onLines: (lines: DraftLine[]) => void;
  costs: boolean;
  /** `products.manage`: may set the price of a used device. */
  prices: boolean;
  /** `products.manage` + the page's `inventory.adjust`: what creating a variant asks for. */
  canCreateCatalog: boolean;
  companyId: number | null;
  grades: CosmeticGradeRow[];
  /** Turns a catalogue product of the picker into a receipt product. */
  toReceiptProduct: (product: PickerProduct) => ReceiptProduct;
  /** Rows set aside for another reception: they stay in memory but do not hold this one back. */
  parked: number[];
  onPark: (ids: number[]) => void;
  /** Reserves `count` row numbers: the wizard issues each number once, ever, so none is reused. */
  issueRowIds: (count: number) => number;
};

/** Combinations asked of the catalogue per request (the server reads at most 300). */
const MATCH_CHUNK = 100;

const BIG = `${internalButtonClass} min-h-11`;
const PRIMARY = `${internalPrimaryButtonClass} min-h-11`;
const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
const LINE_LABEL: Record<InventoryLine, string> = { new: "Nuevo", used: "Seminuevo", accessory: "Accesorio" };
const tail = (value: string) => (value.trim() ? `••••${value.trim().slice(-4)}` : "—");

const describe = (row: Pick<StagedRow, "model" | "capacityGb" | "colour">) =>
  [row.model || "Sin modelo", row.capacityGb ? `${row.capacityGb} GB` : "", row.colour].filter(Boolean).join(" ");

const KIND_TEXT: Record<MatchKind, string> = {
  exact: "Asociado automáticamente", suggested: "Sugerencia por confirmar", ambiguous: "Elige una variante",
  missing: "No existe en el catálogo", unsearched: "Buscando en el catálogo…",
};

/** The way through a pasted message, as the screen shows where the person is. */
const FLOW = ["Pegar mensaje", "Analizar", "Revisar campos", "Asociar catálogo", "Preparar ingreso", "Confirmar"] as const;

export function ReceivingListStage({ paste, onPaste, rows, onRows, lines, onLines, costs, prices, canCreateCatalog, companyId, grades, toReceiptProduct, parked, onPark, issueRowIds }: Props) {
  const raw = paste.raw;
  const setRaw = (value: string) => onPaste((current) => ({ ...current, raw: value }));
  // Rows an analysis held back because an earlier analysis of the same text already produced them.
  const [repeats, setRepeats] = useState<StagedRow[]>([]);
  // The last row taken out, to put it back where it was.
  const [removed, setRemoved] = useState<{ row: StagedRow; at: number } | null>(null);
  // Where «Siguiente error» was last: pressing it again moves on.
  const lastPending = useRef<Pending | null>(null);
  // The analysed text is shown masked unless the person asks to see it (INV-TEXT-03 review).
  const [showRaw, setShowRaw] = useState(false);
  const [declared, setDeclared] = useState<InventoryLine>(() => paste.handed?.line ?? "used");
  // INV-RECEIVE-FLOW-04: the devices chosen for «mismo precio a los elegidos» and that price. Nothing is applied by default.
  const [chosenForPrice, setChosenForPrice] = useState<Set<number>>(new Set());
  const [bulkPrice, setBulkPrice] = useState("");
  const lastUnpriced = useRef<number | null>(null);
  const [error, setError] = useState("");
  const [unrecognized, setUnrecognized] = useState<{ line: string; reason: string }[]>([]);
  const [notice, setNotice] = useState("");
  const [matches, setMatches] = useState<Record<string, MatchResult>>({});
  const [matching, setMatching] = useState(false);
  const [matchError, setMatchError] = useState("");
  const [open, setOpen] = useState<Set<number>>(new Set());
  const [finding, setFinding] = useState<string | null>(null);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [target, setTarget] = useState<{ token: number; row?: number; field?: string; group?: string } | null>(null);
  // Raised to ask the catalogue again for combinations whose answer was dropped.
  const [nonce, setNonce] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const requested = useRef<Set<string>>(new Set());
  const failed = useRef(false);
  const tokens = useRef(0);

  const evaluation = useMemo(() => evaluateRows({ rows, lines, costs, prices }), [rows, lines, costs, prices]);
  // Rows set aside for another reception do not count here, do not enter this receipt and are not asked about.
  const heldIds = useMemo(() => new Set(parked), [parked]);
  const activeEvaluation = useMemo(() => evaluation.filter((e) => !heldIds.has(e.row.id)), [evaluation, heldIds]);
  const counts = summarize(activeEvaluation);
  const groups = useMemo(() => groupRows(rows), [rows]);
  const byId = useMemo(() => new Map(evaluation.map((e) => [e.row.id, e])), [evaluation]);
  // The person counts the rows of THIS list, top to bottom: row 14 of an earlier paste is not «Equipo 14».
  const order = useMemo(() => new Map(rows.map((r, i) => [r.id, i + 1])), [rows]);

  // The catalogue is searched by itself: one question per combination, never per device. An answer is keyed by
  // its combination, so it is applied whenever it arrives — even after the list changed — and never dropped.
  const mounted = useRef(true);
  const inFlight = useRef(0);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const groupKeys = groups.map((g) => g.key).join("\n");
  useEffect(() => {
    if (failed.current) return;
    const wanted = groups.filter((g) => !requested.current.has(g.key) && !matches[g.key]);
    if (!wanted.length) return;
    wanted.forEach((g) => requested.current.add(g.key));
    inFlight.current += 1;
    setMatching(true);
    setMatchError("");
    // In parts: the server reads at most 300 combinations per request, and one failing part never drops the others.
    Promise.allSettled(chunk(wanted, MATCH_CHUNK).map(async (part) => {
      const answers = await matchCandidates(part.map((g) => g.rows[0]), companyId);
      const bySignature: Record<string, MatchResult> = {};
      for (const g of part) if (answers[String(g.rows[0].id)]) bySignature[g.key] = answers[String(g.rows[0].id)];
      if (mounted.current) setMatches((all) => ({ ...all, ...bySignature }));
      return part;
    })).then((outcomes) => {
      outcomes.forEach((outcome, at) => {
        if (outcome.status === "fulfilled") return;
        chunk(wanted, MATCH_CHUNK)[at].forEach((g) => requested.current.delete(g.key));
        failed.current = true;
        if (mounted.current) {
          setMatchError(outcome.reason instanceof Error ? outcome.reason.message : "No se pudo consultar el catálogo.");
        }
      });
    }).finally(() => {
      inFlight.current -= 1;
      if (mounted.current && inFlight.current === 0) setMatching(false);
    });
    // `groups` is derived from `rows`; its keys are what decide whether a new question is needed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [groupKeys, companyId, nonce]);

  // What the catalogue answered links every row of that combination that has no article yet — the ones that were
  // just pasted, and the ones whose data was corrected into a combination already known. Nothing changes when
  // there is nothing to link, so this never loops.
  // INV-TEXT-03: «estado A» in the text links the company's grade that says exactly that, once the grades are known.
  // Both are worked out on the rows of this render first, and the wizard is only asked to update when something
  // changed: with hundreds of rows, a no-op update still re-renders the whole step.
  useEffect(() => {
    const link = (current: StagedRow[]) => {
      const matched = applyMatches(current, matches);
      return grades.length ? applyGrades(matched, grades) : matched;
    };
    if (link(rows) === rows) return;
    onRows(link);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [matches, rows, grades]);

  // «Siguiente error» and the scanner move the cursor to a field once it exists in the page. It runs before the
  // browser paints (and the scanner's move is flushed in the same event): a reader types its next character a few
  // milliseconds after Enter, and none may land while the cursor is still on the previous field.
  useLayoutEffect(() => {
    if (!target) return;
    const card = target.group
      ? Array.from(root.current?.querySelectorAll<HTMLElement>("[data-group]") ?? []).find((el) => el.getAttribute("data-group") === target.group)
      : null;
    const el = target.group
      ? card?.querySelector<HTMLElement>("[data-control]")
      : root.current?.querySelector<HTMLElement>(`[data-unit="r${target.row}"][data-field="${target.field}"]`)
        // A doubt with no field of its own (the line) is confirmed with its button.
        ?? root.current?.querySelector<HTMLElement>(`[data-unit="r${target.row}"][data-doubt="${target.field}"]`);
    if (!el) {
      // A row that is open and still has no such field never will: do not leave a target to steal focus later.
      if (target.row !== undefined && open.has(target.row)) setTarget(null);
      return;
    }
    el.scrollIntoView?.({ block: "center" });
    el.focus();
    setTarget(null);
  }, [target, open, rows]);

  // INV-RECEIVE-FLOW-04: a list handed over from «Carga masiva» is analysed once, as soon as this tab exists.
  const handedRun = useRef(false);
  useEffect(() => {
    if (handedRun.current || !paste.handed) return;
    handedRun.current = true;
    onPaste((current) => ({ ...current, handed: undefined }));
    if (raw.trim() && raw !== paste.analysed) analyze();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function analyze() {
    setConfirmDiscard(false);
    try {
      const preview = previewSupplierText(raw, { line: declared, origin: "own" });
      // INV-TEXT-03: what an earlier analysis of the same text already produced is not added twice.
      const { fresh: kept, repeated, seen } = withoutRepeats(stageRows(preview, { costs, prices }, 0), paste.seen);
      if (rows.length + kept.length > MAX_STAGED) {
        setError(`La lista de trabajo admite hasta ${MAX_STAGED} filas y ya hay ${rows.length}. Agrega o descarta lo que tienes y pega el resto en otra tanda.`);
        return;
      }
      const first = issueRowIds(kept.length);
      const fresh = kept.map((row, index) => ({ ...row, id: first + index }));
      failed.current = false;
      onRows((current) => [...current, ...fresh]);
      // The text stays in the box (marked as analysed) to compare with the rows; «Vaciar el texto» removes it.
      onPaste((current) => ({ ...current, analysed: raw, seen }));
      setShowRaw(false);
      setRepeats(repeated);
      setUnrecognized(preview.unrecognized);
      setError("");
      const devices = fresh.filter((r) => r.kind === "device").length;
      const doubtful = fresh.filter((r) => (r.doubts ?? []).length || (r.amounts ?? []).length).length;
      setNotice(fresh.length
        ? [
          `Reconocimos ${plural(devices, "equipo", "equipos")} y ${plural(fresh.length - devices, "accesorio", "accesorios")}.`,
          preview.truncated ? "La lista tenía más de 300: pega el resto en otra tanda." : "",
          doubtful ? `${plural(doubtful, "fila tiene", "filas tienen")} datos por confirmar, marcados en amarillo.` : "",
          preview.unrecognized.length ? `${plural(preview.unrecognized.length, "línea no se reconoció", "líneas no se reconocieron")}: están al final.` : "",
          repeated.length ? `${plural(repeated.length, "fila ya estaba", "filas ya estaban")} en la lista por un análisis anterior: no se agregó de nuevo.` : "",
          "Revisa lo que falte abajo.",
        ].filter(Boolean).join(" ")
        : repeated.length ? "Todo lo de este texto ya está en la lista o en la recepción: no se agregó de nuevo."
          : "No se reconoció ningún equipo en ese texto.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "No se pudo interpretar el texto.");
    }
  }

  /** The repeated rows, on purpose: the person says they really arrived twice. */
  function addRepeats() {
    if (!repeats.length) return;
    const first = issueRowIds(repeats.length);
    const again = repeats.map((row, index) => ({ ...row, id: first + index }));
    onRows((current) => [...current, ...again]);
    setRepeats([]);
    setNotice(`Se agregaron ${plural(again.length, "fila repetida", "filas repetidas")} a la lista.`);
  }

  function removeRow(id: number) {
    const at = rows.findIndex((r) => r.id === id);
    if (at < 0) return;
    onRows((current) => current.filter((r) => r.id !== id));
    setOpen((all) => new Set([...all].filter((x) => x !== id)));
    onPaste((current) => ({ ...current, seen: forgetSources(current.seen, [rows[at]]) }));
    setRemoved({ row: rows[at], at });
  }

  function undoRemove() {
    if (!removed) return;
    const { row, at } = removed;
    onRows((current) => {
      if (current.some((r) => r.id === row.id)) return current;
      const next = [...current];
      next.splice(Math.min(at, next.length), 0, row);
      return next;
    });
    onPaste((current) => ({ ...current, seen: forgetSources(current.seen, [row], true) }));
    setRemoved(null);
  }

  function searchAgain(keys?: string[]) {
    failed.current = false;
    const drop = keys ?? groups.map((g) => g.key);
    drop.forEach((k) => requested.current.delete(k));
    setMatches((all) => Object.fromEntries(Object.entries(all).filter(([k]) => !drop.includes(k))));
    setMatchError("");
    setNonce((n) => n + 1);
  }

  function choose(group: Group, product: ReceiptProduct | null) {
    onRows((current) => applyChoice(current, group.key, product));
    setFinding(null);
  }

  function changeLine(group: Group, line: InventoryLine) {
    onRows((current) => group.rows.reduce((all, row) => editRow(all, row.id, { line }), current));
  }

  /** «Siguiente error» (Alt+N): the next thing to look at after the last one visited, wrapping round. */
  function resolveNext() {
    const pending = nextPending(rows, activeEvaluation, lastPending.current);
    lastPending.current = pending;
    if (!pending) return;
    tokens.current += 1;
    if (pending.kind === "catalog") {
      setTarget({ token: tokens.current, group: pending.group });
    } else if (pending.field === "product") {
      // The article is the group's decision: the cursor goes to its «Cambiar», there is no field in the row.
      const row = rows.find((r) => r.id === pending.row);
      if (row) setTarget({ token: tokens.current, group: rowSignature(row) });
    } else {
      setOpen((all) => new Set(all).add(pending.row));
      setTarget({ token: tokens.current, row: pending.row, field: pending.field });
    }
  }

  function bulkAmounts(as: "cost" | "price" | "drop") {
    const ids = bulkTargets(active, as).map((r) => r.id);
    onRows((current) => resolveAmount(current, ids, as));
    setNotice(as === "drop" ? `Se descartaron los importes sin etiqueta de ${plural(ids.length, "fila", "filas")}.`
      : `Se usaron como ${as === "cost" ? "costo" : "precio de venta"} en ${plural(ids.length, "fila", "filas")}. Puedes corregir cada una.`);
  }

  /** «Aplicar a los elegidos»: only on what the person chose, only with this explicit action. */
  function applyChosenPrice() {
    if (!bulkPriceOk || !chosenPriceable.length) return;
    const ids = chosenPriceable.map((r) => r.id);
    onRows((current) => applyPrice(current, ids, bulkPriceValue));
    setChosenForPrice(new Set());
    setNotice(`Precio de venta S/ ${bulkPriceValue} puesto en ${plural(ids.length, "equipo", "equipos")}. Puedes corregir cada uno en su fila.`);
  }

  /**
   * «Siguiente sin precio» (and Enter in a row's price): the cursor goes to the price of the next used device
   * without one, in the order of the screen (card by card), wrapping round. `after` is the row the cursor leaves.
   */
  function nextUnpriced(after?: number) {
    const onScreen = groups.flatMap((g) => g.rows).filter((r) => !heldIds.has(r.id));
    const from = after ?? lastUnpriced.current;
    const at = from === null || from === undefined ? -1 : onScreen.findIndex((r) => r.id === from);
    const unpriced = onScreen.filter((r) => takesUnitPrice(r) && !priceIsValid(r.price) && r.id !== after);
    if (!unpriced.length) return;
    const next = unpriced.find((r) => onScreen.indexOf(r) > at) ?? unpriced[0];
    lastUnpriced.current = next.id;
    tokens.current += 1;
    flushSync(() => setTarget({ token: tokens.current, row: next.id, field: "price" }));
  }

  function commit() {
    const result = commitRows(rows, lines, { costs, prices, hold: parked });
    if (!result.committed.length) return;
    onLines(result.lines);
    setConfirmDiscard(false);
    onRows((current) => current.filter((r) => !result.committed.includes(r.id)));
    setOpen(new Set());
    const added = plural(result.committed.length, "equipo agregado", "equipos agregados");
    setNotice(`${added} a la recepción. ${result.left.length ? `Quedan ${plural(result.left.length, "fila", "filas")} en la lista. ` : ""}${result.limit ?? ""}`.trim());
  }

  // «Descartar lo que falta» drops ONLY what cannot be received yet and is not set aside: never a ready device
  // (it is added or left for later) and never a row someone parked. It asks first, with the numbers.
  const discardable = activeEvaluation.filter((e) => e.bucket !== "ready");
  function discard() {
    const gone = new Set(discardable.map((e) => e.row.id));
    onRows((current) => current.filter((r) => !gone.has(r.id)));
    onPaste((current) => ({ ...current, seen: forgetSources(current.seen, discardable.map((e) => e.row)) }));
    setOpen((all) => new Set([...all].filter((id) => !gone.has(id))));
    setConfirmDiscard(false);
    if (gone.size === rows.length) {
      setMatches({});
      requested.current = new Set();
      setUnrecognized([]);
    }
    setNotice(`Se descartaron ${plural(gone.size, "fila", "filas")} que no se podían recibir. Lo ya agregado a la recepción, los equipos listos y los que esperan no cambiaron.`);
  }

  /** The scanner types a value and presses Enter: the cursor goes to the next empty field of the list. */
  function focusAfter(rowId: number, field: string) {
    const following = nextScanTarget(rows, rowId, field);
    if (!following) { root.current?.querySelector<HTMLElement>("[data-commit]")?.focus(); return; }
    tokens.current += 1;
    const token = tokens.current;
    flushSync(() => {
      setOpen((all) => new Set(all).add(following.row));
      setTarget({ token, row: following.row, field: following.field });
    });
  }

  // What would enter the receipt right now, with the caps of ONE reception (200 devices, 50 articles).
  const fit = useMemo(() => commitRows(rows, lines, { costs, prices, hold: parked }), [rows, lines, costs, prices, parked]);
  const pending = rows.filter((r) => !heldIds.has(r.id));
  // What can wait for another reception: what cannot be received yet and what does not fit in this one (200
  // devices, 50 articles). A ready device that fits is added now or discarded on purpose, never forgotten.
  const waiting = activeEvaluation.filter((e) => e.bucket !== "ready" || !fit.committed.includes(e.row.id));
  const heldCount = rows.filter((r) => heldIds.has(r.id)).length;

  // INV-TEXT-03: the summary of what the analysis found, and what is still to be decided about amounts.
  const active = rows.filter((r) => !heldIds.has(r.id));
  const devicesCount = active.filter((r) => r.kind === "device").length;
  const doubtCount = active.filter((r) => (r.doubts ?? []).length).length;
  const correctedCount = active.filter((r) => (r.corrected ?? []).length).length;
  const withAmounts = active.filter((r) => (r.amounts ?? []).length);
  // INV-TEXT-03 review: the bulk decisions never overwrite a labelled cost or price; those rows are counted apart.
  const singleAmount = costs ? bulkTargets(active, "cost") : [];
  const priceable = prices ? bulkTargets(active, "price") : [];
  const keptCosts = costs ? withAmounts.filter((r) => r.amounts!.length === 1 && r.cost.trim()).length : 0;
  const toVisit = useMemo(() => pendingItems(activeEvaluation).length, [activeEvaluation]);
  // INV-RECEIVE-FLOW-04: the selling price of the used devices, at a glance, and the same price for chosen ones.
  const priceState = priceSummary(active, prices);
  // Only a closed row shows its «Elegir»: an open one (priced in its own form) or one set aside is never touched.
  const chosenPriceable = active.filter((r) => chosenForPrice.has(r.id) && takesUnitPrice(r) && !open.has(r.id));
  const bulkPriceValue = bulkPrice.trim();
  const bulkPriceOk = priceIsValid(bulkPriceValue);
  // The analysed text, masked once per text (not on every render of a long list).
  const maskedRaw = useMemo(() => (raw.trim() && raw === paste.analysed ? maskedSource(raw) : ""), [raw, paste.analysed]);
  const fixes = counts.info + counts.invalid;
  // Where the person is in «Pegar mensaje → … → Confirmar»: each step is done by what is true now, the first one
  // that is not is the current one (catalogue and fields can be settled in any order).
  const listed = rows.length > 0;
  const unanalysed = raw.trim() !== "" && raw !== paste.analysed;
  const flowDone = [
    listed || raw.trim() !== "" || lines.length > 0,
    listed ? !unanalysed : lines.length > 0,
    listed && fixes === 0 && doubtCount === 0,
    listed && counts.catalog === 0,
    !listed && lines.length > 0,
    false,
  ];
  const flowAt = flowDone.findIndex((done) => !done);

  const missingRows = proposalRowsFor(groups.filter((g) => classify(matches[g.key]) === "missing" && !g.rows[0].product).flatMap((g) => g.rows));

  return (
    <div
      ref={root} className="space-y-4"
      onKeyDown={(e) => {
        // «Siguiente error» from anywhere in the list: Alt+N (the code, so a dead key on a Mac still works).
        if (e.altKey && !e.ctrlKey && !e.metaKey && (e.code === "KeyN" || e.key.toLowerCase() === "n")) { e.preventDefault(); resolveNext(); }
      }}
    >
      <ol aria-label="Pasos de la lista pegada" className="flex flex-wrap gap-x-2 gap-y-1 text-[11px]">
        {FLOW.map((name, index) => (
          <li key={name} aria-current={index === flowAt ? "step" : undefined}
            className={`rounded-full border px-2 py-0.5 ${index === flowAt ? "border-foreground font-semibold text-foreground" : flowDone[index] ? "border-success-border text-success" : "border-bd-border text-muted"}`}>
            {flowDone[index] ? "✓ " : ""}{index + 1}. {name}
          </li>
        ))}
      </ol>
      <div className="space-y-3 rounded-xl border border-bd-border bg-surface p-4">
        <div>
          <h3 className="text-sm font-semibold text-foreground">Pegar una lista de equipos</h3>
          <p className="mt-1 text-xs text-muted">
            Pega el mensaje de WhatsApp o tu lista. El texto se lee en este navegador: no va a ninguna IA ni sale de la página.
            El catálogo se busca solo; tú sólo resuelves lo que falte. Con el lector de códigos: en cada equipo escanea la serie, pulsa Enter,
            escanea el IMEI y pulsa Enter; el cursor pasa al siguiente dato que falte.
          </p>
        </div>
        <div className="grid gap-3 sm:grid-cols-[minmax(0,14rem)_1fr]">
          <label className="block text-xs font-semibold text-foreground">
            Estos productos son
            <select
              aria-label="Línea declarada" value={declared} onChange={(e) => setDeclared(e.target.value as InventoryLine)}
              className={`mt-1.5 min-h-11 ${internalInputClass}`}
            >
              <option value="used">Seminuevos</option>
              <option value="new">Nuevos</option>
              <option value="accessory">Accesorios</option>
            </select>
          </label>
          <p className="self-end text-xs text-muted">
            Si en la lista hay nuevos y seminuevos mezclados, corrige la línea de cada grupo después de analizar.
          </p>
        </div>
        {raw.trim() && raw === paste.analysed && !showRaw ? (
          // INV-TEXT-03 review: once analysed, the message is shown masked (IMEIs, series, phones); the full text only on
          // request. It is still kept in the page's memory to compare and to analyse again.
          <div className="space-y-2">
            <p className="text-xs font-semibold text-foreground">Texto analizado (enmascarado)</p>
            <pre aria-label="Texto analizado" className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-bd-border p-2 font-mono text-[11px]">{maskedRaw}</pre>
            <div className="flex flex-wrap gap-2">
              <button type="button" className={BIG} onClick={() => setShowRaw(true)}>Mostrar texto</button>
              <button type="button" className={BIG} onClick={() => setRaw("")}>Vaciar el texto</button>
            </div>
          </div>
        ) : (
        <>
        <label className="block text-xs font-semibold text-foreground">
          Texto de la lista
          <textarea
            aria-label="Texto para reconocer" rows={6} maxLength={MAX_TEXT_CHARS} value={raw}
            onChange={(e) => setRaw(e.target.value)} spellCheck={false} autoComplete="off"
            placeholder={"iPhone 13\n128 GB\nBatería 93%\nNS: EJEMPLO123\nIMEI: 350000000000000\nColor: azul"}
            className={`mt-1.5 font-mono text-xs ${internalInputClass}`}
          />
          <span className="mt-1 block font-normal text-muted">
            {raw.length} / {MAX_TEXT_CHARS} caracteres. {raw.trim() && raw === paste.analysed ? "Ya analizado: se conserva para comparar hasta que lo vacíes." : raw.trim() ? "Sin analizar todavía." : ""}
          </span>
        </label>
        <div className="flex flex-wrap gap-2">
          <button type="button" className={PRIMARY} onClick={analyze} disabled={!raw.trim() || raw === paste.analysed}>
            {raw.trim() && raw === paste.analysed ? "Ya analizado" : "Analizar y preparar"}
          </button>
          {raw ? <button type="button" className={BIG} onClick={() => setRaw("")}>Vaciar el texto</button> : null}
          {raw.trim() && raw === paste.analysed ? <button type="button" className={BIG} onClick={() => setShowRaw(false)}>Ocultar texto</button> : null}
        </div>
        </>
        )}
        {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}
      </div>

      {notice ? <p role="status" className="rounded-lg border border-bd-border bg-surface px-3 py-2 text-xs text-foreground">{notice}</p> : null}
      {repeats.length ? (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-warning-border px-3 py-2 text-xs">
          <p>{`${plural(repeats.length, "fila repetida no se agregó", "filas repetidas no se agregaron")}. Si de verdad llegaron dos veces, agrégalas.`}</p>
          <button type="button" className={BIG} onClick={addRepeats}>{repeats.length === 1 ? "Agregarla igual" : `Agregar las ${repeats.length} igual`}</button>
          <button type="button" className={BIG} onClick={() => setRepeats([])}>No agregar</button>
        </div>
      ) : null}
      {removed ? (
        <div role="status" className="flex flex-wrap items-center gap-2 rounded-lg border border-bd-border px-3 py-2 text-xs">
          <p>{`Se quitó «${describe(removed.row)}» de la lista. Las demás filas no cambiaron.`}</p>
          <button type="button" className={BIG} onClick={undoRemove}>Deshacer</button>
        </div>
      ) : null}

      {rows.length > 0 ? (
        <section aria-labelledby="list-summary-title" className="space-y-4">
          <div className="space-y-3 rounded-xl border border-bd-border bg-surface p-4">
            <h3 id="list-summary-title" className="text-sm font-semibold text-foreground">Lista por resolver</h3>
            <p className="text-xs text-muted" aria-label="Resumen del reconocimiento">
              {`Reconocidos: ${plural(devicesCount, "equipo", "equipos")} y ${plural(active.length - devicesCount, "accesorio", "accesorios")}`}
              {` · ${plural(counts.ready, "fila completa", "filas completas")} · ${plural(fixes, "necesita corrección", "necesitan corrección")}`}
              {doubtCount ? ` · ${plural(doubtCount, "con datos por confirmar", "con datos por confirmar")}` : ""}
              {correctedCount ? ` · ${plural(correctedCount, "corregida", "corregidas")}` : ""}
            </p>
            <p className="sr-only" role="status">
              {`${counts.ready} listos, ${counts.catalog} por asociar al catálogo, ${counts.info} por completar, ${counts.invalid} duplicados o inválidos.`}
            </p>
            <ul className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4" aria-label="Avance de la lista">
              <li className="rounded-lg border border-success-border px-3 py-2"><strong className="block text-base text-success">{counts.ready}</strong>Listos para ingresar</li>
              <li className="rounded-lg border border-bd-border px-3 py-2"><strong className="block text-base text-foreground">{counts.catalog}</strong>Requieren asociación de catálogo</li>
              <li className="rounded-lg border border-bd-border px-3 py-2"><strong className="block text-base text-foreground">{counts.info}</strong>Falta completar información</li>
              <li className="rounded-lg border border-bd-border px-3 py-2"><strong className="block text-base text-danger">{counts.invalid}</strong>Duplicados o inválidos</li>
            </ul>
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" className={PRIMARY} data-commit onClick={commit} disabled={fit.committed.length === 0}>
                {fit.committed.length === 0 ? "Nada listo todavía"
                  : fit.committed.length < counts.ready
                    ? `Agregar ${fit.committed.length} de ${plural(counts.ready, "equipo listo", "equipos listos")} a la recepción`
                    : `Agregar ${plural(counts.ready, "equipo listo", "equipos listos")} a la recepción`}
              </button>
              <button type="button" className={BIG} onClick={resolveNext} disabled={toVisit === 0} aria-keyshortcuts="Alt+N" title="Alt+N">
                Siguiente error{toVisit ? ` (${toVisit})` : ""}
              </button>
              <button type="button" className={BIG} onClick={() => setConfirmDiscard(true)} disabled={discardable.length === 0}>Descartar lo que falta</button>
              {matching ? <span role="status" className="text-xs text-muted">Buscando en el catálogo…</span> : null}
            </div>
            {priceState.priceable > 0 ? (
              <div className="space-y-2 rounded-lg border border-bd-border p-3 text-xs">
                <p role="status" aria-label="Resumen de precios" className="text-foreground">
                  <strong>{`${priceState.priced} de ${plural(priceState.priceable, "seminuevo", "seminuevos")} con precio de venta · ${plural(priceState.pending, "pendiente", "pendientes")}`}</strong>
                  {prices
                    ? ". Escribe el precio en cada fila; el costo nunca se convierte en precio. Uno pendiente entra igual y no se vende hasta tener precio."
                    : ". Tu usuario no fija precios de venta (permiso «Administrar productos»): los equipos entran pendientes y no se venden en caja ni se ofrecen en línea hasta que alguien con ese permiso los complete."}
                </p>
                {prices ? (
                  <div className="flex flex-wrap items-end gap-2">
                    {priceState.pending > 0 ? (
                      <button type="button" className={BIG} onClick={() => nextUnpriced()}>Siguiente sin precio</button>
                    ) : null}
                    <div role="group" aria-label="Precio para varios equipos" className="flex flex-wrap items-end gap-2">
                      <label className="block w-36 text-xs font-semibold text-foreground">
                        Precio para los elegidos (S/)
                        <input
                          value={bulkPrice} inputMode="decimal" placeholder="Ej.: 1899.00" autoComplete="off"
                          // As typed: a decimal comma is refused by the price rule, never rewritten into another amount.
                          onChange={(e) => setBulkPrice(e.target.value.slice(0, 12))}
                          className={`mt-1.5 min-h-11 ${internalInputClass}`}
                        />
                      </label>
                      <button type="button" className={BIG} onClick={applyChosenPrice} disabled={!bulkPriceOk || chosenPriceable.length === 0}>
                        {chosenPriceable.length && bulkPriceOk
                          ? `Aplicar S/ ${bulkPriceValue} ${chosenPriceable.length === 1 ? "al elegido" : `a los ${chosenPriceable.length} elegidos`}`
                          : "Aplicar a los elegidos"}
                      </button>
                      {chosenPriceable.length ? (
                        <button type="button" className={BIG} onClick={() => setChosenForPrice(new Set())}>Quitar la selección</button>
                      ) : null}
                      {chosenPriceable.length ? (
                        <p className="w-full text-muted">{`Elegidos: ${chosenPriceable.map((r) => `equipo ${order.get(r.id) ?? ""}`).join(", ")}`}</p>
                      ) : null}
                    </div>
                    {bulkPriceValue && !bulkPriceOk ? <p role="alert" className="w-full text-danger">Escribe el precio con hasta 2 decimales y mayor que cero, por ejemplo 1899.00.</p> : null}
                  </div>
                ) : null}
              </div>
            ) : null}
            {withAmounts.length > 0 && (costs || priceable.length > 0) ? (
              <div role="group" aria-label="Importes sin etiqueta clara" className="space-y-2 rounded-lg border border-warning-border p-3 text-xs">
                <p className="text-foreground">
                  {`${plural(withAmounts.length, "fila trae", "filas traen")} un importe sin etiqueta clara (por ejemplo «Precio: …»): no se sabe si es el costo o el precio de venta. `}
                  Nada se usa hasta que lo indiques. Puedes decidirlo para todas aquí o fila por fila.
                  {keptCosts ? ` ${plural(keptCosts, "fila ya tiene", "filas ya tienen")} un costo indicado: «Son costos» no ${keptCosts === 1 ? "la toca" : "las toca"}.` : ""}
                </p>
                <div className="flex flex-wrap gap-2">
                  {singleAmount.length ? <button type="button" className={BIG} onClick={() => bulkAmounts("cost")}>{`Son costos (${singleAmount.length})`}</button> : null}
                  {priceable.length ? <button type="button" className={BIG} onClick={() => bulkAmounts("price")}>{`Son precios de venta de seminuevos (${priceable.length})`}</button> : null}
                  <button type="button" className={BIG} onClick={() => bulkAmounts("drop")}>No usarlos</button>
                </div>
              </div>
            ) : null}
            {confirmDiscard && discardable.length > 0 ? (
              <div role="alertdialog" aria-label="Confirmar el descarte" className="space-y-2 rounded-lg border border-danger p-3 text-xs">
                <p className="text-foreground">
                  {`Se descartarán ${plural(discardable.length, "fila", "filas")} que no se pueden recibir todavía. `}
                  {counts.ready > 0 ? `Los ${plural(counts.ready, "equipo listo", "equipos listos")} se quedan en la lista. ` : ""}
                  {heldCount > 0 ? `Las ${plural(heldCount, "fila", "filas")} en espera tampoco se tocan. ` : ""}
                  Los números de serie e IMEI de las descartadas habría que escanearlos otra vez.
                </p>
                <div className="flex flex-wrap gap-2">
                  <button type="button" className={PRIMARY} onClick={discard}>{`Sí, descartar ${plural(discardable.length, "fila", "filas")}`}</button>
                  <button type="button" className={BIG} onClick={() => setConfirmDiscard(false)}>No descartar</button>
                </div>
              </div>
            ) : null}
            {fit.committed.length < counts.ready ? (
              <p role="status" className="text-xs text-warning">
                {`Caben ${fit.committed.length} en esta recepción; los otros ${counts.ready - fit.committed.length} esperan otra recepción. ${fit.limit ?? ""}`.trim()}
              </p>
            ) : null}
            {pending.length > 0 ? (
              <div className="space-y-2">
                <p className="text-xs text-muted">
                  No se pasa al siguiente paso con filas sin resolver: agrégalas, corrígelas, descártalas o déjalas para otra recepción.
                  Nada entra a medias.
                </p>
                {waiting.length > 0 ? (
                  <button type="button" className={BIG} onClick={() => {
                  const ids = waiting.map((e) => e.row.id);
                  setChosenForPrice((all) => new Set([...all].filter((id) => !ids.includes(id))));
                  onPark([...parked, ...ids]);
                }}>
                    {waiting.length === 1 ? "Dejar esta fila para otra recepción" : `Dejar estas ${waiting.length} filas para otra recepción`}
                  </button>
                ) : (
                  <p className="text-xs text-warning">Agrega primero los equipos listos a la recepción: sólo se dejan en espera las filas que no se pueden recibir todavía.</p>
                )}
                <p className="text-xs text-muted">Quedan sólo en esta página: si la recargas o sales, se pierden y habrá que pegarlas otra vez.</p>
              </div>
            ) : null}
            {heldCount > 0 ? (
              <div className="flex flex-wrap items-center gap-2">
                <p role="status" className="text-xs text-muted">{`${plural(heldCount, "fila", "filas")} en espera: no entran a esta recepción y siguen aquí para la próxima.`}</p>
                <button type="button" className={BIG} onClick={() => onPark([])}>{`Volver a incluir ${heldCount === 1 ? "la fila" : `las ${heldCount} filas`} en espera`}</button>
              </div>
            ) : null}
            {matchError ? (
              <p role="alert" className="text-xs text-danger">
                {matchError}{" "}
                <button type="button" className="font-semibold underline" onClick={() => searchAgain()}>Buscar de nuevo</button>
              </p>
            ) : null}
          </div>

          {canCreateCatalog || missingRows.length ? (
            <CatalogProposalPanel
              rows={missingRows} companyId={companyId} allowed={canCreateCatalog}
              onApplied={() => searchAgain(groups.filter((g) => classify(matches[g.key]) === "missing").map((g) => g.key))}
            />
          ) : null}

          <ul className="space-y-3" aria-label="Equipos por variante del catálogo">
            {groups.map((group) => (
              <GroupCard
                key={group.key} group={group} result={matches[group.key]} evaluation={group.rows.map((r) => byId.get(r.id)!)}
                canCreateCatalog={canCreateCatalog} finding={finding === group.key}
                onFinding={(on) => setFinding(on ? group.key : null)}
                onChoose={(product) => choose(group, product)}
                onLine={(line) => changeLine(group, line)}
                toReceiptProduct={toReceiptProduct}
                open={open} onToggle={(id, on) => {
                  setOpen((all) => { const next = new Set(all); if (on) next.add(id); else next.delete(id); return next; });
                  // An opened row is priced in its own form: it leaves the «mismo precio» selection.
                  if (on) setChosenForPrice((all) => { if (!all.has(id)) return all; const next = new Set(all); next.delete(id); return next; });
                }}
                onChange={(id, change, refocus) => {
                  onRows((current) => editRow(current, id, change));
                  if (refocus) {
                    // The row moves to another card: the cursor follows it to its first field.
                    setOpen((all) => new Set(all).add(id));
                    tokens.current += 1;
                    setTarget({ token: tokens.current, row: id, field: "serial" });
                  }
                }}
                onRemove={removeRow}
                onAmount={(id, as, amount) => onRows((current) => resolveAmount(current, [id], as, () => amount))}
                onConfirm={(id, field) => onRows((current) => confirmDoubt(current, id, field))}
                onEnter={focusAfter} costs={costs} prices={prices} grades={grades} order={order} held={heldIds}
                chosenForPrice={chosenForPrice} onEnterPrice={nextUnpriced}
                onChooseForPrice={(id, on) => setChosenForPrice((all) => { const next = new Set(all); if (on) next.add(id); else next.delete(id); return next; })}
              />
            ))}
          </ul>

          {unrecognized.length ? (
            <details className="text-xs">
              <summary className="cursor-pointer">{plural(unrecognized.length, "línea no reconocida", "líneas no reconocidas")}: revisa que ninguna sea un producto</summary>
              <ul className="mt-2 space-y-1" aria-label="Líneas no reconocidas">
                {unrecognized.map((item, index) => (
                  <li key={index}><span className="font-mono">{maskedSource(item.line)}</span> — {item.reason}</li>
                ))}
              </ul>
            </details>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}

function GroupCard({
  group, result, evaluation, canCreateCatalog, finding, onFinding, onChoose, onLine, toReceiptProduct, open, onToggle, onChange,
  onRemove, onEnter, costs, prices, grades, order, held, onAmount, onConfirm, chosenForPrice, onChooseForPrice, onEnterPrice,
}: {
  order: Map<number, number>; held: Set<number>;
  chosenForPrice: Set<number>; onChooseForPrice: (id: number, on: boolean) => void; onEnterPrice: (after: number) => void;
  onAmount: (id: number, as: "cost" | "price" | "drop", amount?: string) => void; onConfirm: (id: number, field: IssueField) => void;
  group: Group; result: MatchResult | undefined; evaluation: RowEvaluation[]; canCreateCatalog: boolean; finding: boolean;
  onFinding: (on: boolean) => void; onChoose: (product: ReceiptProduct | null) => void;
  onLine: (line: InventoryLine) => void; toReceiptProduct: Props["toReceiptProduct"];
  open: Set<number>; onToggle: (id: number, on: boolean) => void; onChange: (id: number, change: Partial<StagedRow>, refocus?: boolean) => void;
  onRemove: (id: number) => void; onEnter: (id: number, field: string) => void; costs: boolean; prices: boolean; grades: CosmeticGradeRow[];
}) {
  const first = group.rows[0];
  const kind = classify(result);
  const chosen = first.product;
  const [pick, setPick] = useState("");
  const count = group.rows.length;
  // «Automáticamente» only while the article is the one the catalogue itself answered; a hand-picked one says so.
  const state = chosen
    ? (kind === "exact" && result?.options[0]?.product_id === chosen.id ? KIND_TEXT.exact : "Asociado")
    : KIND_TEXT[kind];
  const ready = evaluation.filter((e) => e.bucket === "ready" && !held.has(e.row.id)).length;
  return (
    <li data-group={group.key} className="space-y-3 rounded-xl border border-bd-border bg-surface p-4">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h4 className="break-words text-sm font-semibold text-foreground">
            {describe(first)} · {LINE_LABEL[first.line]}
          </h4>
          <p className="text-xs text-muted">
            {plural(count, "equipo", "equipos")} · {ready} de {count} listos
          </p>
        </div>
        <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${chosen ? "border-success-border text-success" : "border-bd-border text-muted"}`}>{state}</span>
      </header>

      {chosen ? (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span>Artículo: <strong className="break-words">{chosen.name}</strong></span>
          <button type="button" className={BIG} data-control onClick={() => onFinding(true)}>Cambiar</button>
        </div>
      ) : kind === "suggested" ? (
        <div className="space-y-2 text-sm">
          <p>Coincide en parte con <strong className="break-words">{result!.options[0].name}</strong>. {result!.reason}</p>
          <div className="flex flex-wrap gap-2">
            <button type="button" className={PRIMARY} data-control onClick={() => onChoose(optionToProduct(result!.options[0], first.kind))}>
              Usar para {plural(count, "equipo", "equipos")}
            </button>
            <button type="button" className={BIG} onClick={() => onFinding(true)}>Buscar otro artículo</button>
          </div>
        </div>
      ) : kind === "ambiguous" ? (
        <div className="space-y-2 text-sm">
          <p>{result!.reason}</p>
          <div className="flex flex-wrap items-end gap-2">
            <label className="block min-w-0 flex-1 text-xs font-semibold">
              Variante
              <select aria-label={`Variante de ${describe(first)}`} data-control value={pick} onChange={(e) => setPick(e.target.value)} className={`mt-1.5 min-h-11 ${internalInputClass}`}>
                <option value="">Elegir…</option>
                {result!.options.map((o) => <option key={o.product_id} value={o.product_id}>{o.name}</option>)}
              </select>
            </label>
            <button
              type="button" className={PRIMARY} disabled={!result!.options.some((o) => String(o.product_id) === pick)}
              onClick={() => { const o = result!.options.find((x) => String(x.product_id) === pick); if (o) onChoose(optionToProduct(o, first.kind)); }}
            >
              Usar para {plural(count, "equipo", "equipos")}
            </button>
            <button type="button" className={BIG} onClick={() => onFinding(true)}>Buscar otro artículo</button>
          </div>
        </div>
      ) : kind === "missing" ? (
        <div className="space-y-2 text-sm">
          <p>{result!.reason || "No existe en el catálogo."}</p>
          <p className="text-xs text-muted">
            {canCreateCatalog
              ? "Aprueba «Lo que falta en el catálogo» más arriba para crearla, o busca otro artículo ya existente."
              : "Tu usuario no puede crear artículos. Busca uno que ya exista o pide a quien administra los productos que lo cree: esta lista queda pendiente."}
          </p>
          <button type="button" className={BIG} data-control onClick={() => onFinding(true)}>Buscar otro artículo</button>
        </div>
      ) : (
        <p className="text-sm text-muted" role="status">Buscando en el catálogo…</p>
      )}

      {finding ? (
        <div className="rounded-lg border border-dashed border-bd-border p-3">
          <ProductPicker
            label="Buscar el artículo de esta variante" value={null} inputId={`pick-${group.rows[0].id}`}
            onChange={(product) => { if (product) onChoose(toReceiptProduct(product)); }}
            helper="Escribe el nombre o el SKU. Se usará para todos los equipos de este grupo."
          />
          <button type="button" className={`${BIG} mt-2`} onClick={() => onFinding(false)}>Cerrar</button>
        </div>
      ) : null}

      <div className="flex flex-wrap gap-2 text-xs">
        {first.kind === "device" ? (
          <button type="button" className={BIG} onClick={() => onLine(first.line === "used" ? "new" : "used")}>
            {first.line === "used" ? "Son nuevos" : "Son seminuevos"}
          </button>
        ) : null}
      </div>

      <ul className="space-y-2" aria-label={`Equipos de ${describe(first)}`}>
        {evaluation.map((item) => (
          <RowItem
            key={item.row.id} item={item} open={open.has(item.row.id)} onToggle={(on) => onToggle(item.row.id, on)}
            onChange={(change, refocus) => onChange(item.row.id, change, refocus)} onRemove={() => onRemove(item.row.id)}
            onEnter={(field) => onEnter(item.row.id, field)} costs={costs} prices={prices} grades={grades} number={order.get(item.row.id) ?? 0}
            held={held.has(item.row.id)}
            chosenForPrice={chosenForPrice.has(item.row.id)} onChooseForPrice={(on) => onChooseForPrice(item.row.id, on)}
            onEnterPrice={() => onEnterPrice(item.row.id)}
            onAmount={(as, amount) => onAmount(item.row.id, as, amount)} onConfirm={(field) => onConfirm(item.row.id, field)}
            earlier={item.merge?.earlierRow != null ? { number: order.get(item.merge.earlierRow) ?? 0, held: held.has(item.merge.earlierRow) } : null}
          />
        ))}
      </ul>
    </li>
  );
}

function RowItem({
  item, open, onToggle, onChange, onRemove, onEnter, costs, prices, grades, number, held, onAmount, onConfirm, earlier,
  chosenForPrice = false, onChooseForPrice, onEnterPrice,
}: {
  /** INV-RECEIVE-FLOW-04: chosen for «mismo precio a los elegidos»; Enter in the price goes to the next unpriced. */
  chosenForPrice?: boolean; onChooseForPrice?: (on: boolean) => void; onEnterPrice?: () => void;
  /** The earlier row this one adds to: its number in the list and whether it is set aside. */
  earlier?: { number: number; held: boolean } | null;
  held: boolean; number: number; item: RowEvaluation; open: boolean; onToggle: (on: boolean) => void; onChange: (change: Partial<StagedRow>, refocus?: boolean) => void;
  onRemove: () => void; onEnter: (field: string) => void; costs: boolean; prices: boolean; grades: CosmeticGradeRow[];
  onAmount: (as: "cost" | "price" | "drop", amount?: string) => void; onConfirm: (field: IssueField) => void;
}) {
  const { row, issues, bucket } = item;
  // What blocks or is missing is red; what the list filled by itself is amber, to confirm; what a person fixed is green.
  const problem = (field: string) => leadIssue(issues.filter((i) => i.field === field && i.kind !== "warn"))?.text;
  const doubts = row.doubts ?? [];
  const doubt = (field: string) => (doubts.some((d) => d.field === field) ? "Por confirmar: lo llenó la lista." : null);
  const corrected = (field: string) => (row.corrected ?? []).includes(field);
  const productNotice = leadIssue(issues.filter((i) => i.field === "product"));
  const unit = `r${row.id}`;
  const notice = issues.some((i) => i.kind === "warn" && i.field === "product");
  const label = held ? "En espera"
    : bucket === "ready" ? (notice ? "Listo · revisar el artículo" : doubts.length ? "Listo · datos por confirmar" : "Listo")
    : bucket === "catalog" ? "Falta el artículo" : leadIssue(issues.filter((i) => i.kind !== "warn"))?.text ?? "Revisar";
  const device = row.kind === "device";
  const used = row.condition !== "new";
  const amounts = row.amounts ?? [];
  const canPrice = prices && device && row.line === "used" && (!row.product || isUsedLine(row.product));
  const merge = item.merge;
  const chosenMerge = mergeChoice(row, merge);
  const quantity = Number(row.quantity) || 0;
  // The ARTICLE's line decides the price rule (INV-COM-01A); the device's own condition only what is measured.
  const usedLine = Boolean(row.product && isUsedLine(row.product));
  return (
    <li className={`rounded-lg border ${bucket === "invalid" ? "border-danger" : "border-bd-border"}`}>
      <details open={open} onToggle={(e) => { const now = (e.currentTarget as HTMLDetailsElement).open; if (now !== open) onToggle(now); }}>
        <summary className="flex min-h-11 cursor-pointer flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-xs">
          <span className="font-semibold">{device ? "Equipo" : "Accesorio"} {number}</span>
          {device ? <span className="font-mono">{tail(row.serial)} / {tail(row.imei)}</span> : <span>Cantidad {row.quantity || "—"}</span>}
          {device && used && row.battery.trim() ? <span>Batería {row.battery.trim()}%</span> : null}
          <span className={held ? "text-muted" : bucket === "ready" && !notice && !doubts.length ? "text-success" : bucket === "invalid" ? "text-danger" : "text-warning"}>{label}</span>
          {doubts.length ? <span className="text-warning">Revisar: {[...new Set(doubts.map((d) => FIELD_LABEL[d.field] ?? d.field))].join(", ")}</span> : null}
          {amounts.length ? <span className="text-warning">Importe por decidir</span> : null}
          {(row.corrected ?? []).length ? <span className="text-success">✓ Corregido: {(row.corrected ?? []).map((f) => FIELD_LABEL[f] ?? f).join(", ")}</span> : null}
        </summary>
        {/* The body of a row exists only while it is open: a list of hundreds must not render thousands of fields. */}
        {open ? (
        <div className="space-y-3 border-t border-bd-border p-3">
          {device ? (
            <DeviceFieldGroups
              used={usedLine}
              variant={row.product?.name}
              device={<>
                <Field label="Número de serie *" mono value={row.serial} unit={unit} field="serial" error={problem("serial")} corrected={corrected("serial")}
                  onChange={(serial) => onChange({ serial })} onEnter={() => onEnter("serial")} />
                <Field label={row.product?.requires_imei ? "IMEI *" : "IMEI (opcional)"} mono inputMode="numeric" placeholder="15 dígitos"
                  value={row.imei} unit={unit} field="imei" error={problem("imei")} doubt={doubt("imei")} corrected={corrected("imei")}
                  onChange={(imei) => onChange({ imei })} onEnter={() => onEnter("imei")} />
                <Field label="IMEI 2 (opcional)" mono inputMode="numeric" placeholder="Sólo si tiene dos" value={row.imei2}
                  unit={unit} field="imei2" error={problem("imei2")} onChange={(imei2) => onChange({ imei2 })} onEnter={() => onEnter("imei2")} />
                <div>
                  <label htmlFor={`${unit}-cond`} className="block text-xs font-semibold text-foreground">Estado del equipo</label>
                  <select id={`${unit}-cond`} value={row.condition} onChange={(e) => onChange({ condition: e.target.value })} className={`mt-1.5 min-h-11 ${internalInputClass}`}>
                    {UNIT_CONDITIONS.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                  </select>
                </div>
                {used ? (
                  <Field label="Batería medida (%)" inputMode="numeric" placeholder="Ej.: 93" value={row.battery} unit={unit} field="battery"
                    error={problem("battery")} corrected={corrected("battery")} onChange={(battery) => onChange({ battery: battery.replace(/[^\d%\s]/g, "").slice(0, 5) })} />
                ) : null}
                {used && grades.length > 0 ? (
                  <div>
                    <label htmlFor={`${unit}-grade`} className="block text-xs font-semibold text-foreground">Estado estético</label>
                    <select id={`${unit}-grade`} value={row.grade} onChange={(e) => onChange({ grade: e.target.value })} className={`mt-1.5 min-h-11 ${internalInputClass}`}>
                      <option value="">Sin indicar</option>
                      {grades.map((g) => <option key={g.id} value={String(g.id)}>{g.label}</option>)}
                    </select>
                  </div>
                ) : null}
                {usedLine ? (
                  <Field label="Detalles para el comprador (opcional)" value={row.publicNotes} unit={unit} field="publicNotes"
                    maxLength={PUBLIC_NOTES_MAX + 50} error={problem("publicNotes")} onChange={(publicNotes) => onChange({ publicNotes })} />
                ) : null}
                <div className="sm:col-span-2 lg:col-span-3">
                  <Field label="Observaciones internas (opcional)" value={row.notes} unit={unit} field="notes" maxLength={UNIT_NOTES_MAX + 50}
                    error={problem("notes")} onChange={(notes) => onChange({ notes })} onEnter={() => onEnter("notes")} />
                </div>
              </>}
              commercial={<>
                {costs ? (
                  <Field label="Costo del equipo: lo que pagó la tienda (opcional)" inputMode="decimal" placeholder="Ej.: 2500.00" value={row.cost} unit={unit} field="cost"
                    error={problem("cost")} doubt={doubt("cost")} corrected={corrected("cost")} onChange={(cost) => onChange({ cost })} onEnter={() => onEnter("cost")} />
                ) : null}
                {/* INV-COM-01A: only a used-line article is priced device by device; without the authority there is no
                    field (INV-RECEIVE-PRICE-03): the device is received unpriced and waits. */}
                {prices && usedLine ? (
                  <Field label="Precio de venta del equipo (S/, opcional)" inputMode="decimal" placeholder="Ej.: 1899.00" value={row.price}
                    unit={unit} field="price" error={problem("price")} corrected={corrected("price")} onChange={(price) => onChange({ price })} />
                ) : null}
                {usedLine ? <UsedCommercialNotes canPrice={prices} canCosts={costs} priced={prices && Boolean(row.price.trim())} /> : null}
              </>}
            />
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Cantidad que entra *" inputMode="numeric" value={row.quantity} unit={unit} field="quantity" error={problem("quantity")} corrected={corrected("quantity")}
                onChange={(quantity) => onChange({ quantity: quantity.replace(/\D/g, "") })} />
              {costs ? (
                <Field label="Costo por unidad (opcional)" inputMode="decimal" placeholder="Ej.: 12.50" value={row.cost} unit={unit} field="cost"
                  error={problem("cost")} doubt={doubt("cost")} corrected={corrected("cost")} onChange={(cost) => onChange({ cost })} />
              ) : null}
            </div>
          )}
          {used && row.gradeText && !row.grade ? (
            <p className="text-xs text-warning">{`El texto dice «${row.gradeText}» como estado estético: elígelo arriba si es uno de los de tu empresa.`}</p>
          ) : null}
          {amounts.length ? (
            <div role="group" aria-label="Importe sin etiqueta clara" tabIndex={-1} data-unit={unit} data-field="amount"
              className="space-y-2 rounded-lg border border-warning-border p-3 text-xs outline-none focus-visible:ring-2">
              <p className="text-foreground">
                {/* INV-TEXT-03 review: without costs.view an amount is shown only when it is the only one (and never one a label
                    called «costo»: those do not reach this row); with several, the only choice is not to use them. */}
                {costs || amounts.length === 1
                  ? `El texto trae ${amounts.map((a) => `S/ ${a}`).join(" y ")} sin decir si es el costo o el precio de venta.`
                  : "El texto trae varios importes sin etiqueta clara: tu usuario no ve costos, así que no se usan."}
                {" "}No se usa hasta que lo indiques.
              </p>
              {(costs ? amounts : amounts.length === 1 ? amounts : []).map((amount) => (
                <div key={amount} className="flex flex-wrap items-center gap-2">
                  {costs && amounts.length > 1 ? <span className="font-semibold">S/ {amount}:</span> : null}
                  {costs ? (
                    <button type="button" className={BIG} onClick={() => onAmount("cost", amount)}>
                      {row.cost.trim() ? `Es el costo (en lugar de S/ ${row.cost.trim()})` : "Es el costo"}
                    </button>
                  ) : null}
                  {canPrice ? <button type="button" className={BIG} onClick={() => onAmount("price", amount)}>Es el precio de venta</button> : null}
                </div>
              ))}
              <button type="button" className={BIG} onClick={() => onAmount("drop")}>No usarlo</button>
            </div>
          ) : null}
          {merge && !device ? (
            <div role="group" aria-label="Cantidad que ya está en la recepción" tabIndex={-1} data-unit={unit} data-field="merge"
              className={`space-y-2 rounded-lg border p-3 text-xs outline-none focus-visible:ring-2 ${chosenMerge ? "border-bd-border" : "border-warning-border"}`}>
              <p className="text-foreground">
                {merge.earlierRow !== null
                  ? `Este artículo ya está en otra fila de esta lista${merge.inReceipt ? ` y la recepción ya tiene ${plural(merge.inReceipt, "unidad", "unidades")}` : ""}. ¿Llegaron todas? Esta fila entra sólo junto con la fila ${earlier?.number ?? ""}${earlier?.held ? ", que está en espera: vuelve a incluirla para agregar las dos" : ""}.`
                  : `La recepción ya tiene ${plural(merge.inReceipt, "unidad", "unidades")} de este artículo. ¿Esta fila se suma o la reemplaza?`}
                {" "}Nada se suma sin que lo elijas.
              </p>
              <div className="flex flex-wrap gap-2">
                <button type="button" className={`${BIG} ${chosenMerge === "add" ? "border-foreground font-bold" : ""}`} aria-pressed={chosenMerge === "add"} onClick={() => onChange({ merge: { choice: "add", ...merge } })}>
                  {merge.earlierRow !== null ? "Sumar a la otra fila" : `Sumar${quantity ? ` (quedan ${merge.inReceipt + quantity})` : ""}`}
                </button>
                {merge.earlierRow === null && merge.receiptLines === 1 ? (
                  <button type="button" className={`${BIG} ${chosenMerge === "replace" ? "border-foreground font-bold" : ""}`} aria-pressed={chosenMerge === "replace"} onClick={() => onChange({ merge: { choice: "replace", ...merge } })}>
                    {`Reemplazar${quantity ? ` (quedan ${quantity})` : ""}`}
                  </button>
                ) : null}
              </div>
            </div>
          ) : null}
          {doubts.length ? (
            <ul aria-label="Datos por confirmar" className="space-y-1 text-xs">
              {doubts.map((d) => (
                <li key={d.field} className="flex flex-wrap items-center gap-2 text-warning">
                  <span>{d.text}</span>
                  <button type="button" className={BIG} data-unit={unit} data-doubt={d.field} onClick={() => onConfirm(d.field)}>Es correcto</button>
                </li>
              ))}
            </ul>
          ) : null}
          {productNotice ? (
            <p role={productNotice.kind === "warn" ? "status" : "alert"} className={`text-xs ${productNotice.kind === "warn" ? "text-warning" : "text-danger"}`}>{productNotice.text}</p>
          ) : null}
          {row.hints.length || row.ramGb || row.sim || row.region ? (
            <ul className="list-disc space-y-1 pl-5 text-xs text-warning">
              {row.hints.map((h) => <li key={h}>{h}</li>)}
              {row.ramGb || row.sim || row.region ? (
                <li>
                  {`El texto trae ${[row.ramGb ? `${row.ramGb} GB de RAM` : "", row.sim === "esim" ? "eSIM" : row.sim === "chip" ? "chip físico" : "", row.region ? `región ${row.region}` : ""].filter(Boolean).join(", ")}. `}
                  No es una opción del catálogo: no distingue la variante. Revísalo.
                </li>
              ) : null}
            </ul>
          ) : null}
          <IdentityFields key={`${row.id}|${row.model}|${row.capacityGb}|${row.colour}|${row.line}`} row={row} unit={unit} onChange={onChange} />
          <details className="text-xs">
            <summary className="cursor-pointer text-muted">Ver texto original (enmascarado)</summary>
            <pre className="mt-1 whitespace-pre-wrap font-mono text-[11px]">{maskedSource(row.source)}</pre>
          </details>
          <button type="button" className={BIG} aria-label={`Quitar ${device ? "el equipo" : "el accesorio"} ${number} de la lista`} onClick={onRemove}>Quitar de la lista</button>
        </div>
        ) : null}
      </details>
      {/* INV-RECEIVE-FLOW-04: a closed used device still shows what matters commercially — its authorized cost, its
          selling price (a field, for who may set it) and whether it is priced — so eight prices need no eight forms.
          Open, the same price is the field of «Información comercial»: never two fields for one value. */}
      {!open && device && usedLine ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-bd-border px-3 py-2 text-xs">
          {costs && row.cost.trim() ? <span className="text-muted">Costo S/ {row.cost.trim()}</span> : null}
          {prices ? (
            <>
              <label className="flex items-center gap-2">
                <span aria-hidden="true" className="font-semibold text-foreground">Precio S/</span>
                <span className="block w-32">
                  <input
                    aria-label={`Precio de venta del equipo ${number} (S/)`} value={row.price} inputMode="decimal" placeholder="Ej.: 1899.00"
                    autoComplete="off" data-unit={unit} data-field="price" aria-invalid={problem("price") ? true : undefined}
                    aria-describedby={problem("price") && label !== problem("price") ? `${unit}-price-error` : undefined}
                    onChange={(e) => onChange({ price: e.target.value })}
                    onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); onEnterPrice?.(); } }}
                    className={`min-h-11 ${internalInputClass} ${problem("price") ? "border-danger" : ""}`}
                  />
                </span>
              </label>
              <label className="flex min-h-11 items-center gap-1.5">
                <input
                  type="checkbox" checked={chosenForPrice} aria-label={`Elegir el equipo ${number} para un mismo precio`}
                  onChange={(e) => onChooseForPrice?.(e.target.checked)}
                />
                <span aria-hidden="true">Elegir</span>
              </label>
            </>
          ) : null}
          {problem("price") ? (label === problem("price") ? null : <span id={`${unit}-price-error`} className="text-danger">{problem("price")}</span>)
            : prices && priceIsValid(row.price) ? <span className="font-semibold text-success">Precio S/ {row.price.trim()}</span>
              : <span className="font-semibold text-warning">Pendiente de precio</span>}
        </div>
      ) : null}
    </li>
  );
}


/**
 * Model, capacity, colour and line decide WHICH variant a row is, so applying a change releases its article and
 * moves the row to another combination (another card). That move NEVER happens as a side effect of leaving a field:
 * a card rebuilt between a pointer-down and its pointer-up swallows the click (the person tapped «Quitar» and
 * nothing happened). It is typed in a local draft and applied on Enter or with its own button.
 */
function IdentityFields({ row, unit, onChange }: { row: StagedRow; unit: string; onChange: (change: Partial<StagedRow>, refocus?: boolean) => void }) {
  const [model, setModel] = useState(row.model);
  const [capacity, setCapacity] = useState(row.capacityGb === null ? "" : String(row.capacityGb));
  const [colour, setColour] = useState(row.colour);
  const gb = capacity ? Number(capacity) : null;
  const dirty = model.trim() !== row.model.trim() || gb !== row.capacityGb || colour.trim() !== row.colour.trim();
  const apply = () => {
    if (!dirty) return;
    onChange({ model: model.trim(), capacityGb: gb, colour: colour.trim() }, true);
  };
  return (
    <details className="text-xs">
      <summary className="min-h-11 cursor-pointer py-2 text-muted">Corregir modelo, capacidad, color o línea</summary>
      <div className="mt-2 grid gap-3 sm:grid-cols-4">
        <Field label="Modelo" value={model} maxLength={120} onChange={setModel} onEnter={apply} />
        <Field label="Capacidad (GB)" inputMode="numeric" value={capacity} onEnter={apply}
          onChange={(v) => setCapacity(v.replace(/\D/g, "").slice(0, 4))} />
        <Field label="Color" value={colour} maxLength={60} onChange={setColour} onEnter={apply} />
        {row.kind === "device" ? (
          <div>
            <label htmlFor={`${unit}-line`} className="block text-xs font-semibold text-foreground">Línea</label>
            <select id={`${unit}-line`} value={row.line === "accessory" ? "new" : row.line} onChange={(e) => onChange({ line: e.target.value as InventoryLine })} className={`mt-1.5 min-h-11 ${internalInputClass}`}>
              <option value="new">Nuevo</option><option value="used">Seminuevo</option>
            </select>
          </div>
        ) : null}
      </div>
      <p className="mt-1 text-muted">Cambiar uno de estos datos separa este equipo del grupo y busca de nuevo su artículo.</p>
      {dirty ? (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <p role="status" className="text-warning">Cambios sin aplicar: pulsa Enter o el botón.</p>
          <button type="button" className={BIG} onClick={apply}>Aplicar cambios y buscar de nuevo</button>
        </div>
      ) : null}
    </details>
  );
}
