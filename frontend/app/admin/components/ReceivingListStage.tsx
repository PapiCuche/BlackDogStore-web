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
import { UNIT_CONDITIONS, UNIT_NOTES_MAX, type DraftLine, type ReceiptProduct } from "../lib/inventory-receipts";
import {
  applyChoice, applyMatches, chunk, classify, commitRows, editRow, evaluateRows, groupRows, MAX_STAGED, nextPending,
  nextRowId, nextScanTarget, leadIssue, optionToProduct, proposalRowsFor, rowSignature, stageRows, summarize, type Group, type MatchKind,
  type RowEvaluation, type StagedRow,
} from "../lib/receive-list";
import type { CosmeticGradeRow } from "../lib/internal-api";
import { CatalogProposalPanel } from "./CatalogProposalPanel";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";
import { Field } from "./ReceivingLinesEditor";
import { ProductPicker, type PickerProduct } from "./ProductPicker";

type Props = {
  rows: StagedRow[];
  onRows: (update: (rows: StagedRow[]) => StagedRow[]) => void;
  lines: DraftLine[];
  onLines: (lines: DraftLine[]) => void;
  costs: boolean;
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

export function ReceivingListStage({ rows, onRows, lines, onLines, costs, canCreateCatalog, companyId, grades, toReceiptProduct, parked, onPark, issueRowIds }: Props) {
  const [raw, setRaw] = useState("");
  const [declared, setDeclared] = useState<InventoryLine>("used");
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

  const evaluation = useMemo(() => evaluateRows({ rows, lines, costs }), [rows, lines, costs]);
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
  useEffect(() => {
    if (Object.keys(matches).length) onRows((current) => applyMatches(current, matches));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [matches, rows]);

  // «Resolver pendientes» and the scanner move the cursor to a field once it exists in the page. It runs before the
  // browser paints (and the scanner's move is flushed in the same event): a reader types its next character a few
  // milliseconds after Enter, and none may land while the cursor is still on the previous field.
  useLayoutEffect(() => {
    if (!target) return;
    const card = target.group
      ? Array.from(root.current?.querySelectorAll<HTMLElement>("[data-group]") ?? []).find((el) => el.getAttribute("data-group") === target.group)
      : null;
    const el = target.group
      ? card?.querySelector<HTMLElement>("[data-control]")
      : root.current?.querySelector<HTMLElement>(`[data-unit="r${target.row}"][data-field="${target.field}"]`);
    if (!el) {
      // A row that is open and still has no such field never will: do not leave a target to steal focus later.
      if (target.row !== undefined && open.has(target.row)) setTarget(null);
      return;
    }
    el.scrollIntoView?.({ block: "center" });
    el.focus();
    setTarget(null);
  }, [target, open, rows]);

  function analyze() {
    try {
      const preview = previewSupplierText(raw, { line: declared, origin: "own" });
      if (rows.length + preview.candidates.length > MAX_STAGED) {
        setError(`La lista de trabajo admite hasta ${MAX_STAGED} filas y ya hay ${rows.length}. Agrega o descarta lo que tienes y pega el resto en otra tanda.`);
        return;
      }
      const first = issueRowIds(preview.candidates.length);
      const fresh = stageRows(preview, { costs }, first);
      failed.current = false;
      onRows((current) => [...current, ...fresh]);
      setUnrecognized(preview.unrecognized);
      setRaw("");
      setError("");
      setNotice(fresh.length
        ? `Reconocimos ${plural(fresh.length, "equipo o accesorio", "equipos y accesorios")}. ${preview.truncated ? "La lista tenía más de 300: pega el resto en otra tanda. " : ""}Revisa lo que falte abajo.`
        : "No se reconoció ningún equipo en ese texto.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "No se pudo interpretar el texto.");
    }
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

  function resolveNext() {
    const pending = nextPending(rows, activeEvaluation);
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

  function commit() {
    const result = commitRows(rows, lines, { costs, hold: parked });
    if (!result.committed.length) return;
    onLines(result.lines);
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
  const fit = useMemo(() => commitRows(rows, lines, { costs, hold: parked }), [rows, lines, costs, parked]);
  const pending = rows.filter((r) => !heldIds.has(r.id));
  // What can wait for another reception: what cannot be received yet and what does not fit in this one (200
  // devices, 50 articles). A ready device that fits is added now or discarded on purpose, never forgotten.
  const waiting = activeEvaluation.filter((e) => e.bucket !== "ready" || !fit.committed.includes(e.row.id));
  const heldCount = rows.filter((r) => heldIds.has(r.id)).length;

  const missingRows = proposalRowsFor(groups.filter((g) => classify(matches[g.key]) === "missing" && !g.rows[0].product).flatMap((g) => g.rows));

  return (
    <div ref={root} className="space-y-4">
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
        <label className="block text-xs font-semibold text-foreground">
          Texto de la lista
          <textarea
            aria-label="Texto para reconocer" rows={6} maxLength={MAX_TEXT_CHARS} value={raw}
            onChange={(e) => setRaw(e.target.value)} spellCheck={false} autoComplete="off"
            placeholder={"iPhone 13\n128 GB\nBatería 93%\nNS: EJEMPLO123\nIMEI: 350000000000000\nColor: azul"}
            className={`mt-1.5 font-mono text-xs ${internalInputClass}`}
          />
          <span className="mt-1 block font-normal text-muted">{raw.length} / {MAX_TEXT_CHARS} caracteres. Se vacía al analizar.</span>
        </label>
        <button type="button" className={PRIMARY} onClick={analyze} disabled={!raw.trim()}>Analizar y preparar</button>
        {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}
      </div>

      {notice ? <p role="status" className="rounded-lg border border-bd-border bg-surface px-3 py-2 text-xs text-foreground">{notice}</p> : null}

      {rows.length > 0 ? (
        <section aria-labelledby="list-summary-title" className="space-y-4">
          <div className="space-y-3 rounded-xl border border-bd-border bg-surface p-4">
            <h3 id="list-summary-title" className="text-sm font-semibold text-foreground">Lista por resolver</h3>
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
              <button type="button" className={BIG} onClick={resolveNext} disabled={counts.ready === counts.total}>Resolver pendientes</button>
              <button type="button" className={BIG} onClick={() => setConfirmDiscard(true)} disabled={discardable.length === 0}>Descartar lo que falta</button>
              {matching ? <span role="status" className="text-xs text-muted">Buscando en el catálogo…</span> : null}
            </div>
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
                  <button type="button" className={BIG} onClick={() => onPark([...parked, ...waiting.map((e) => e.row.id)])}>
                    {`Dejar estas ${waiting.length} filas para otra recepción`}
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
                open={open} onToggle={(id, on) => setOpen((all) => { const next = new Set(all); if (on) next.add(id); else next.delete(id); return next; })}
                onChange={(id, change, refocus) => {
                  onRows((current) => editRow(current, id, change));
                  if (refocus) {
                    // The row moves to another card: the cursor follows it to its first field.
                    setOpen((all) => new Set(all).add(id));
                    tokens.current += 1;
                    setTarget({ token: tokens.current, row: id, field: "serial" });
                  }
                }}
                onRemove={(id) => onRows((current) => current.filter((r) => r.id !== id))}
                onEnter={focusAfter} costs={costs} grades={grades} order={order} held={heldIds}
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
  onRemove, onEnter, costs, grades, order, held,
}: {
  order: Map<number, number>; held: Set<number>;
  group: Group; result: MatchResult | undefined; evaluation: RowEvaluation[]; canCreateCatalog: boolean; finding: boolean;
  onFinding: (on: boolean) => void; onChoose: (product: ReceiptProduct | null) => void;
  onLine: (line: InventoryLine) => void; toReceiptProduct: Props["toReceiptProduct"];
  open: Set<number>; onToggle: (id: number, on: boolean) => void; onChange: (id: number, change: Partial<StagedRow>, refocus?: boolean) => void;
  onRemove: (id: number) => void; onEnter: (id: number, field: string) => void; costs: boolean; grades: CosmeticGradeRow[];
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
            onEnter={(field) => onEnter(item.row.id, field)} costs={costs} grades={grades} number={order.get(item.row.id) ?? 0}
            held={held.has(item.row.id)}
          />
        ))}
      </ul>
    </li>
  );
}

function RowItem({
  item, open, onToggle, onChange, onRemove, onEnter, costs, grades, number, held,
}: {
  held: boolean; number: number; item: RowEvaluation; open: boolean; onToggle: (on: boolean) => void; onChange: (change: Partial<StagedRow>, refocus?: boolean) => void;
  onRemove: () => void; onEnter: (field: string) => void; costs: boolean; grades: CosmeticGradeRow[];
}) {
  const { row, issues, bucket } = item;
  const problem = (field: string) => leadIssue(issues.filter((i) => i.field === field))?.text;
  const productNotice = leadIssue(issues.filter((i) => i.field === "product"));
  const unit = `r${row.id}`;
  const notice = issues.some((i) => i.kind === "warn");
  const label = held ? "En espera"
    : bucket === "ready" ? (notice ? "Listo · revisar el artículo" : "Listo")
    : bucket === "catalog" ? "Falta el artículo" : leadIssue(issues)?.text ?? "Revisar";
  const device = row.kind === "device";
  const used = row.condition !== "new";
  return (
    <li className={`rounded-lg border ${bucket === "invalid" ? "border-danger" : "border-bd-border"}`}>
      <details open={open} onToggle={(e) => { const now = (e.currentTarget as HTMLDetailsElement).open; if (now !== open) onToggle(now); }}>
        <summary className="flex min-h-11 cursor-pointer flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-xs">
          <span className="font-semibold">{device ? "Equipo" : "Accesorio"} {number}</span>
          {device ? <span className="font-mono">{tail(row.serial)} / {tail(row.imei)}</span> : <span>Cantidad {row.quantity || "—"}</span>}
          {device && used && row.battery.trim() ? <span>Batería {row.battery.trim()}%</span> : null}
          <span className={held ? "text-muted" : bucket === "ready" && !notice ? "text-success" : bucket === "invalid" ? "text-danger" : "text-warning"}>{label}</span>
        </summary>
        {/* The body of a row exists only while it is open: a list of hundreds must not render thousands of fields. */}
        {open ? (
        <div className="space-y-3 border-t border-bd-border p-3">
          {device ? (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <Field label="Número de serie *" mono value={row.serial} unit={unit} field="serial" error={problem("serial")}
                onChange={(serial) => onChange({ serial })} onEnter={() => onEnter("serial")} />
              <Field label={row.product?.requires_imei ? "IMEI *" : "IMEI (opcional)"} mono inputMode="numeric" placeholder="15 dígitos"
                value={row.imei} unit={unit} field="imei" error={problem("imei")}
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
                  error={problem("battery")} onChange={(battery) => onChange({ battery: battery.replace(/[^\d%\s]/g, "").slice(0, 5) })} />
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
              {costs ? (
                <Field label="Costo del equipo (opcional)" inputMode="decimal" placeholder="Ej.: 2500.00" value={row.cost} unit={unit} field="cost"
                  error={problem("cost")} onChange={(cost) => onChange({ cost })} onEnter={() => onEnter("cost")} />
              ) : null}
              <div className="sm:col-span-2 lg:col-span-3">
                <Field label="Observaciones (opcional)" value={row.notes} unit={unit} field="notes" maxLength={UNIT_NOTES_MAX + 50}
                  error={problem("notes")} onChange={(notes) => onChange({ notes })} onEnter={() => onEnter("notes")} />
              </div>
            </div>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Cantidad que entra *" inputMode="numeric" value={row.quantity} unit={unit} field="quantity" error={problem("quantity")}
                onChange={(quantity) => onChange({ quantity: quantity.replace(/\D/g, "") })} />
              {costs ? (
                <Field label="Costo por unidad (opcional)" inputMode="decimal" placeholder="Ej.: 12.50" value={row.cost} unit={unit} field="cost"
                  error={problem("cost")} onChange={(cost) => onChange({ cost })} />
              ) : null}
            </div>
          )}
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
