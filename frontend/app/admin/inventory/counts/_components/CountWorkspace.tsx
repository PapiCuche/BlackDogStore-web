"use client";

/**
 * MIGRATION-D — counting accessories in one branch (docs/adr-migration-count.md).
 *
 * The branch is the count's: it was chosen when the count was opened and the
 * server resolved it. Nothing here sends a branch.
 *
 * TWO MODES, NEVER MIXED, NEVER SWITCHED BY THEMSELVES (§5):
 *   · «Cantidad total» (default): a scan finds the line and asks how many there
 *     are; what is typed REPLACES the line's value.
 *   · «Sumar 1 por escaneo»: each scan adds one ON THE SERVER, sending the value
 *     the screen showed (`expected`). A 409 means somebody else (or a retried
 *     request) already changed it: the screen shows the server's value and does
 *     not add — the person scans again if that unit was not counted yet.
 *
 * BLANK ≠ ZERO. An empty field is never sent. «Sin contar» and «0 · verificado:
 * no hay» look different on purpose. The old system's quantity is shown as
 * «Referencia del sistema anterior» and is never copied into the counted value.
 *
 * NO STOCK MOVES HERE. Lines are annotations; approval is a separate screen
 * (MIGRATION-F) and nothing in this component calls it.
 *
 * Scanner use: focus lives in the scan field, Enter submits, every result is
 * confirmed in a polite live region (plus a short tone and a highlighted row),
 * every error in an assertive one.
 */

import Link from "next/link";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { EmptyBox, Panel, TableWrap, Td, Th } from "../../../components/InventoryUi";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "../../../components/internal-ui";
import { scanInputProps } from "../../../lib/scan-rows";
import {
  CountApiError,
  fetchCountDetail,
  recountLines,
  scanCount,
  searchCountProducts,
  setCountLines,
  submitCountForReview,
  supportsCountContract,
  type CountDetail,
  type CountLine,
  type CountLineWrite,
} from "../../../lib/counts-api";
import { fetchAdminProducts } from "../../../../lib/admin";
import {
  LINE_STATE_LABEL,
  MAX_QUANTITY,
  PLAUSIBLE_QUANTITY,
  ambiguousLines,
  lineCode,
  lineDifference,
  lineMatchesCode,
  lineState,
  mergeLine,
  overlappingLines,
  parseQuantity,
  pendingLines,
  signed,
} from "./count-lines";

type Mode = "total" | "increment";
type SearchHit = { id: number; name: string; is_serialized?: boolean };
type Entry = {
  product: number;
  name: string;
  line: CountLine | null;
  /** «Fue un error de tecleo» on a line whose window is not empty (§3.2 rule 3). */
  correction: boolean;
};
type Alert = { message: string; devices?: boolean };

const UNITS_HREF = "/admin/inventory/units";
const UNITS_LINK_TEXT = "Inventario › Equipos";

function tone(ok: boolean) {
  // A short beep for the person holding the scanner. Optional by nature: some
  // browsers have no audio until a gesture, jsdom has none at all.
  try {
    const Ctx =
      (window as unknown as { AudioContext?: typeof AudioContext }).AudioContext ??
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.frequency.value = ok ? 880 : 220;
    gain.gain.value = 0.05;
    osc.connect(gain).connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + (ok ? 0.08 : 0.25));
    osc.onended = () => void ctx.close().catch(() => {});
  } catch {
    /* silence is fine: the live region and the row highlight still confirm */
  }
}

export function CountWorkspace({
  count,
  onCountChange,
  canCount,
  canSeeCosts,
  onRecounted,
}: {
  count: CountDetail;
  onCountChange: (next: CountDetail) => void;
  canCount: boolean;
  canSeeCosts: boolean;
  /** A line went back to «not counted»: what blocked the approval may be gone. */
  onRecounted?: () => void;
}) {
  const contract = supportsCountContract(count);
  // Only a MIGRATION count freezes its counters at `review` (the server agrees:
  // an ordinary count in review still takes entries and recounts).
  const frozenAtReview = contract && count.kind === "migration";
  const openStatuses = frozenAtReview ? ["draft", "counting"] : ["draft", "counting", "review"];
  const editable = canCount && openStatuses.includes(count.status);

  const [mode, setMode] = useState<Mode>("total");
  const [query, setQuery] = useState("");
  const [entry, setEntry] = useState<Entry | null>(null);
  const [qty, setQty] = useState("");
  const [reason, setReason] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [status, setStatus] = useState("");
  const [alert, setAlert] = useState<Alert | null>(null);
  const [flash, setFlash] = useState<number | null>(null);
  const [sound, setSound] = useState(true);
  const [busy, setBusy] = useState(false);
  // Products the server refused with `entry_required`: their total moved since
  // they were counted, and only the person knows whether it is a new count or
  // a typo. A server-ambiguous line is not here: a typo fix cannot clear it.
  const [needsEntry, setNeedsEntry] = useState<ReadonlySet<number>>(() => new Set());

  const scanRef = useRef<HTMLInputElement | null>(null);
  const qtyRef = useRef<HTMLInputElement | null>(null);
  const countRef = useRef(count);
  const codeToProduct = useRef(new Map<string, number>());
  const queue = useRef<Promise<void>>(Promise.resolve());
  // A keyboard-wedge scanner types a whole code in a few milliseconds and ends
  // with Enter. In the quantity field that is a scan, not a count.
  const keyTimes = useRef<number[]>([]);
  const burst = useRef(false);
  const ids = useId();

  useEffect(() => {
    countRef.current = count;
  }, [count]);

  useEffect(() => {
    if (editable) scanRef.current?.focus();
    // Only on arrival: later focus moves are explicit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (entry) qtyRef.current?.focus();
  }, [entry]);

  useEffect(() => {
    if (flash === null) return;
    const t = window.setTimeout(() => setFlash(null), 1200);
    return () => window.clearTimeout(t);
  }, [flash]);

  const publish = useCallback(
    (next: CountDetail) => {
      countRef.current = next;
      onCountChange(next);
    },
    [onCountChange],
  );

  const applyLine = useCallback(
    (line: CountLine) => publish(mergeLine(countRef.current, line)),
    [publish],
  );

  const confirm = useCallback(
    (message: string, product?: number) => {
      setAlert(null);
      setStatus(message);
      if (product !== undefined) setFlash(product);
      if (sound) tone(true);
    },
    [sound],
  );

  const fail = useCallback(
    (next: Alert) => {
      setAlert(next);
      if (sound) tone(false);
    },
    [sound],
  );

  const focusScan = () => scanRef.current?.focus();

  async function refresh() {
    try {
      publish(await fetchCountDetail(count.id));
    } catch {
      /* best effort: what was announced stands, the list is re-read on reload */
    }
  }

  function settle(product: number) {
    setNeedsEntry((prev) => {
      if (!prev.has(product)) return prev;
      const next = new Set(prev);
      next.delete(product);
      return next;
    });
  }

  function knownCodes(): Set<string> {
    const codes = new Set<string>(codeToProduct.current.keys());
    for (const line of countRef.current.items) {
      if (line.product_code) codes.add(line.product_code);
      for (const code of line.product_codes ?? []) codes.add(code);
    }
    return codes;
  }

  function onQtyKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (/^\d$/.test(e.key)) {
      keyTimes.current.push(performance.now());
      return;
    }
    if (e.key === "Enter") {
      const times = keyTimes.current;
      burst.current = times.length >= 6 && (times[times.length - 1] - times[0]) / (times.length - 1) < 35;
    }
    keyTimes.current = [];
  }

  function refuseQuantity(message: string) {
    fail({ message });
    setQty("");
    keyTimes.current = [];
    burst.current = false;
    qtyRef.current?.focus();
  }

  function localLine(target: { code?: string; product?: number }): CountLine | undefined {
    const items = countRef.current.items;
    if (target.product !== undefined) return items.find((l) => l.product === target.product);
    const code = target.code ?? "";
    const known = codeToProduct.current.get(code);
    return items.find((l) => (known !== undefined && l.product === known) || lineMatchesCode(l, code));
  }

  function remember(code: string | undefined, line: CountLine) {
    if (code) codeToProduct.current.set(code, line.product);
  }

  function scanFailure(err: unknown, code?: string) {
    if (err instanceof CountApiError) {
      if (err.code === "expected_mismatch") {
        if (err.line) {
          remember(code, err.line);
          applyLine(err.line);
        }
        const shown = err.line?.physical_quantity;
        fail({
          message:
            `${err.line?.product_name ?? "El producto"} cambió mientras escaneabas: ahora va en ` +
            `${shown === null || shown === undefined ? "sin contar" : shown}. No se sumó; ` +
            "vuelve a escanear si esa unidad no estaba contada.",
        });
        return;
      }
      if (err.code === "serialized_product") {
        const name = err.product?.name ?? "Ese código";
        fail({
          message: `${name} es un equipo con serie: se cuenta equipo por equipo (IMEI o serie), no en este recuento. ` +
            "El recuento de equipos llega en una fase próxima; mientras tanto, revísalo en",
          devices: true,
        });
        return;
      }
      if (err.code === "recount_required") {
        if (err.line) applyLine(err.line);
        fail({ message: `${err.line?.product_name ?? "Esta línea"} tiene que recontarse antes de seguir. ${err.message}` });
        return;
      }
      fail({ message: err.message });
      return;
    }
    fail({ message: err instanceof Error ? err.message : "No se pudo registrar la lectura." });
  }

  function increment(target: { code: string } | { product: number }) {
    const job = async () => {
      const local = localLine(target);
      const expected = local ? local.physical_quantity : null;
      const code = "code" in target ? target.code : undefined;
      try {
        const res = await scanCount(count.id, { ...target, mode: "increment", expected });
        remember(code, res.line);
        applyLine(res.line);
        confirm(`${res.line.product_name}: ${res.line.physical_quantity ?? "sin contar"}`, res.line.product);
        if (countRef.current.status === "draft") {
          // A scan answers with the line only; the first quantity also moved
          // the count to «En conteo», which the page needs (badge, actions).
          await refresh();
        }
      } catch (err) {
        scanFailure(err, code);
      } finally {
        focusScan();
      }
    };
    // One at a time: the next scan's `expected` is read after the previous answer.
    queue.current = queue.current.then(job, job);
    return queue.current;
  }

  function openEntry(product: number, name: string, correction = false) {
    const line = countRef.current.items.find((l) => l.product === product) ?? null;
    setHits(null);
    setQty("");
    setReason("");
    // Keystrokes of an abandoned entry are not the start of this one.
    keyTimes.current = [];
    burst.current = false;
    setEntry({ product, name, line, correction });
  }

  async function search(term: string) {
    try {
      // The count's own search matches codes too; today's server has none, so
      // the catalogue search (by name) stands in for it.
      const results: SearchHit[] = contract
        ? await searchCountProducts(count.id, term)
        : ((await fetchAdminProducts({ search: term, page_size: 20 })).results as SearchHit[]);
      setHits(results);
      if (results.length === 0) fail({ message: `Sin resultados para «${term}».` });
      else setStatus(`${results.length} resultado(s) para «${term}».`);
    } catch (err) {
      fail({ message: err instanceof Error ? err.message : "No se pudo buscar." });
    }
  }

  async function lookup(code: string) {
    try {
      const res = await scanCount(count.id, { code, mode: "lookup" });
      remember(code, res.line);
      applyLine(res.line);
      setStatus(`${res.line.product_name}: escribe la cantidad total.`);
      openEntry(res.line.product, res.line.product_name);
    } catch (err) {
      if (err instanceof CountApiError && err.code === "not_found") {
        // Not a code this company knows: maybe a name typed by hand.
        await search(code);
        return;
      }
      scanFailure(err, code);
      focusScan();
    }
  }

  async function onScan() {
    const term = query.trim();
    if (!term) return;
    setQuery("");
    setAlert(null);
    if (!contract) {
      await search(term);
      return;
    }
    if (mode === "increment") {
      await increment({ code: term });
      return;
    }
    await lookup(term);
  }

  function pickHit(hit: SearchHit) {
    if (contract && mode === "increment") {
      setHits(null);
      void increment({ product: hit.id });
      return;
    }
    openEntry(hit.id, hit.name);
  }

  function closeEntry() {
    setEntry(null);
    focusScan();
  }

  async function saveEntry() {
    if (!entry) return;
    const text = qty.trim();
    const n = parseQuantity(qty);
    const scanned = burst.current;
    burst.current = false;
    keyTimes.current = [];
    if (text === "") {
      fail({ message: "Escribe la cantidad contada. Si verificaste que no hay ninguna, escribe 0." });
      qtyRef.current?.focus();
      return;
    }
    if (knownCodes().has(text)) {
      refuseQuantity(
        `«${text}» es un código de producto, no una cantidad: no se guardó. ` +
          `Escribe cuántas unidades de ${entry.name} hay, o pulsa Cancelar antes de escanear el siguiente.`,
      );
      return;
    }
    if (scanned) {
      refuseQuantity(
        "Eso llegó de golpe, como una lectura del lector, no como una cantidad: no se guardó. " +
          `Escribe cuántas unidades de ${entry.name} hay, o pulsa Cancelar antes de escanear el siguiente.`,
      );
      return;
    }
    if (/^\d+$/.test(text) && (n === null || n > MAX_QUANTITY)) {
      refuseQuantity(
        `La cantidad es demasiado grande (el máximo es ${MAX_QUANTITY.toLocaleString("es-PE")}). ` +
          "¿Se escaneó un código en este campo?",
      );
      return;
    }
    if (n === null) {
      fail({ message: "La cantidad es un número entero desde 0." });
      qtyRef.current?.focus();
      return;
    }
    if (entry.correction && !reason.trim()) {
      fail({ message: "Explica la corrección: queda registrada con tu nombre." });
      return;
    }
    if (
      n > PLAUSIBLE_QUANTITY &&
      !window.confirm(`¿${n.toLocaleString("es-PE")} unidades de ${entry.name}? Es mucho para un recuento: confirma que no es un código.`)
    ) {
      qtyRef.current?.focus();
      return;
    }
    const write: CountLineWrite = { product: entry.product, physical_quantity: n };
    if (entry.correction) {
      write.entry = "correction";
      write.entry_note = reason.trim();
    }
    setBusy(true);
    try {
      const next = await setCountLines(count.id, [write], contract);
      publish(next);
      settle(entry.product);
      confirm(`${entry.name}: ${n}${n === 0 ? " (verificado: no hay)" : ""}`, entry.product);
      setEntry(null);
      focusScan();
    } catch (err) {
      if (err instanceof CountApiError && err.code === "entry_required") {
        const products = err.products?.length ? err.products : [entry.product];
        setNeedsEntry((prev) => new Set([...prev, ...products]));
        setEntry(null);
        fail({ message: `${entry.name} tuvo movimientos de stock desde que se contó: recuéntalo o corrige el error de tecleo.` });
        // A line this screen did not hold yet: read it, so the choice can be offered.
        if (!entry.line) await refresh();
        focusScan();
        return;
      }
      fail({ message: err instanceof Error ? err.message : "No se pudo guardar la cantidad." });
    } finally {
      setBusy(false);
    }
  }

  async function recount(line: CountLine) {
    setBusy(true);
    try {
      const next = await recountLines(count.id, [line.product]);
      publish(next);
      settle(line.product);
      onRecounted?.();
      confirm(`Vuelve a contar ${line.product_name}: quedó sin contar.`, line.product);
    } catch (err) {
      fail({ message: err instanceof Error ? err.message : "No se pudo pedir el reconteo." });
    } finally {
      setBusy(false);
      focusScan();
    }
  }

  async function submit() {
    const names = (lines: CountLine[]) => lines.map((l) => `  · ${l.product_name}`).join("\n");
    const open = pendingLines(countRef.current);
    const doubtful = blockingRecount();
    const blocked = overlappingLines(countRef.current);
    const summary = [
      open.length ? `Sin contar (${open.length}):\n${names(open)}` : "",
      doubtful.length ? `Por recontar (${doubtful.length}):\n${names(doubtful)}` : "",
      blocked.length ? `Bloqueadas por otra corrección (${blocked.length}):\n${names(blocked)}` : "",
    ].filter(Boolean).join("\n\n");
    if (
      !window.confirm(
        `¿Enviar el recuento #${count.id} a revisión?\n\n` +
          (summary ? `${summary}\n\n` : "") +
          (frozenAtReview
            ? "Dejarás de poder anotar cantidades. No se mueve stock: la aprobación es otro paso."
            : "No se mueve stock: la aprobación es otro paso."),
      )
    ) {
      return;
    }
    setBusy(true);
    try {
      const next = await submitCountForReview(count.id);
      publish(next);
      setEntry(null);
      confirm("Enviado a revisión. Pendiente de aprobación.");
    } catch (err) {
      fail({ message: err instanceof Error ? err.message : "No se pudo enviar a revisión." });
      // The server may have listed products that gained stock meanwhile:
      // show them, so they can be counted or excluded.
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  /** Lines to recount: the server's ambiguous ones and those refused with `entry_required`. */
  function blockingRecount(): CountLine[] {
    const current = countRef.current;
    const seen = new Set<number>();
    return current.items.filter((l) => {
      const wanted = lineState(l) === "ambiguous" || needsEntry.has(l.product);
      if (!wanted || seen.has(l.product)) return false;
      seen.add(l.product);
      return true;
    });
  }

  function changeMode(next: Mode) {
    setMode(next);
    setEntry(null);
    setStatus(
      next === "increment"
        ? "Modo: cada escaneo suma 1 a la cantidad contada."
        : "Modo: escribe la cantidad total; reemplaza la anterior.",
    );
    focusScan();
  }

  const pending = pendingLines(count);
  const ambiguous = count.items.filter(
    (l) => ambiguousLines(count).includes(l) || (needsEntry.has(l.product) && lineState(l) === "counted"),
  );
  const overlapping = overlappingLines(count);
  const blockedTitleId = `${ids}-blocked`;
  const hasReference = count.items.some((l) => l.reference_quantity !== null && l.reference_quantity !== undefined);
  const showCosts = canSeeCosts && count.items.some((l) => l.difference_value !== undefined);
  const awaitingApproval = count.status === "review";

  const modeLabelId = `${ids}-mode`;
  const scanId = `${ids}-scan`;
  const qtyId = `${ids}-qty`;
  const reasonId = `${ids}-reason`;
  const modeHintId = `${ids}-mode-hint`;
  const pendingTitleId = `${ids}-pending`;
  const ambiguousTitleId = `${ids}-ambiguous`;
  const hitsTitleId = `${ids}-hits`;

  return (
    <div className="space-y-6">
      {/* Live regions exist before anything is announced, or nothing is read. */}
      <p role="status" aria-live="polite" className="sr-only">{status}</p>
      <div data-testid="count-alerts" aria-live="assertive">
        {alert ? (
          <div role="alert" className="rounded-xl border border-danger-border bg-danger-surface px-4 py-3 text-sm text-danger">
            {alert.message}
            {alert.devices ? (
              <>
                {" "}
                <Link href={UNITS_HREF} className="font-semibold underline">{UNITS_LINK_TEXT}</Link>.
              </>
            ) : null}
          </div>
        ) : null}
      </div>

      {awaitingApproval ? (
        <div className="rounded-xl border border-warning-border bg-warning-surface px-4 py-3 text-sm text-foreground">
          <strong>Pendiente de aprobación</strong>
          <span className="text-muted">
            {editable
              ? " — en revisión: todavía puedes corregir y recontar líneas. Ningún stock se mueve hasta que se apruebe."
              : " — las cantidades ya no se anotan. Ningún stock se mueve hasta que alguien con permiso lo apruebe."}
          </span>
        </div>
      ) : null}

      {canCount ? (
        <Panel
          title="Contar"
          description={
            contract
              ? "Escanea el código o escribe un nombre y pulsa Enter."
              : "Busca el producto por nombre y pulsa Enter. Este servidor todavía no resuelve códigos de barras."
          }
        >
          <div className="space-y-4">
            <div role="radiogroup" aria-labelledby={modeLabelId} aria-describedby={modeHintId} className="space-y-2">
              <p id={modeLabelId} className="text-[11px] font-semibold uppercase tracking-widest text-muted">
                Modo de conteo
              </p>
              <div className="grid gap-2 sm:grid-cols-2">
                {(
                  [
                    { value: "total", title: "Cantidad total", hint: "Escribes cuántas hay. Reemplaza lo anterior." },
                    { value: "increment", title: "Sumar 1 por escaneo", hint: "Cada lectura suma una unidad." },
                  ] as const
                ).map((option) => {
                  const disabled = !editable || (option.value === "increment" && !contract);
                  const checked = mode === option.value;
                  return (
                    <label
                      key={option.value}
                      className={`flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2.5 text-sm focus-within:ring-2 focus-within:ring-foreground/30 ${
                        checked
                          ? option.value === "increment"
                            ? "border-warning-border bg-warning-surface"
                            : "border-foreground/40 bg-surface-2"
                          : "border-bd-border"
                      } ${disabled ? "cursor-not-allowed opacity-50" : ""}`}
                    >
                      <input
                        type="radio"
                        name={`${ids}-mode-choice`}
                        value={option.value}
                        checked={checked}
                        disabled={disabled}
                        onChange={() => changeMode(option.value)}
                        className="mt-1"
                      />
                      <span>
                        <span className="block font-semibold text-foreground">{option.title}</span>
                        <span className="block text-xs text-muted">{option.hint}</span>
                      </span>
                    </label>
                  );
                })}
              </div>
              <p id={modeHintId} className="text-xs text-muted">
                {mode === "increment"
                  ? "Ahora: SUMAR 1 por escaneo. Para corregir un número, usa «Corregir» y escribe el total."
                  : "Ahora: CANTIDAD TOTAL. Tras escanear, escribe cuántas hay y pulsa Enter."}
                {!contract ? " «Sumar 1 por escaneo» no está disponible en este servidor." : null}
              </p>
            </div>

            <form
              className="flex flex-col gap-2 sm:flex-row sm:items-end"
              onSubmit={(e) => {
                e.preventDefault();
                void onScan();
              }}
            >
              <div className="min-w-0 flex-1">
                <label htmlFor={scanId} className="mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-muted">
                  {contract ? "Código de barras o búsqueda" : "Buscar producto"}
                </label>
                <input
                  id={scanId}
                  ref={scanRef}
                  {...scanInputProps}
                  enterKeyHint="go"
                  type="search"
                  value={query}
                  disabled={!editable}
                  onChange={(e) => setQuery(e.target.value)}
                  className={`${internalInputClass} text-base`}
                  placeholder={contract ? "Escanea o escribe y pulsa Enter" : "Nombre del producto"}
                />
              </div>
              <button type="submit" disabled={!editable} className={internalButtonClass}>
                {contract && mode === "increment" ? "Sumar 1" : "Buscar"}
              </button>
              <label className="flex items-center gap-2 text-xs text-muted sm:pb-3">
                <input type="checkbox" checked={sound} onChange={(e) => setSound(e.target.checked)} />
                Sonido
              </label>
            </form>

            {hits && hits.length > 0 ? (
              <section aria-labelledby={hitsTitleId} className="rounded-lg border border-bd-border">
                <h3 id={hitsTitleId} className="border-b border-bd-border px-3 py-2 text-xs font-semibold text-muted">
                  Resultados de búsqueda
                </h3>
                <ul className="divide-y divide-bd-border">
                  {hits.map((hit) => (
                    <li key={hit.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm">
                      <span className="min-w-0 text-foreground">{hit.name}</span>
                      {hit.is_serialized ? (
                        <span className="text-xs text-muted">
                          Es un equipo: se cuenta equipo por equipo.{" "}
                          <Link href={UNITS_HREF} className="underline">Ver equipos</Link>
                        </span>
                      ) : (
                        <button
                          type="button"
                          disabled={!editable}
                          onClick={() => pickHit(hit)}
                          aria-label={`Elegir ${hit.name}`}
                          className={internalButtonClass}
                        >
                          {contract && mode === "increment" ? "Sumar 1" : "Elegir"}
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}

            {entry && editable ? (
              <form
                aria-label={`Cantidad de ${entry.name}`}
                className="space-y-3 rounded-lg border border-foreground/30 bg-surface-2 p-4"
                onSubmit={(e) => {
                  e.preventDefault();
                  void saveEntry();
                }}
                onKeyDown={(e) => {
                  if (e.key === "Escape") {
                    e.preventDefault();
                    closeEntry();
                  }
                }}
              >
                <div>
                  <p className="font-semibold text-foreground">{entry.name}</p>
                  <dl className="mt-1 grid gap-x-4 gap-y-0.5 text-xs text-muted sm:grid-cols-2">
                    {entry.line && lineCode(entry.line) ? <div>Código: <span className="font-mono">{lineCode(entry.line)}</span></div> : null}
                    {entry.line?.current_theoretical !== undefined && entry.line?.current_theoretical !== null ? (
                      <div>Stock teórico actual: {entry.line.current_theoretical}</div>
                    ) : null}
                    {entry.line?.reference_quantity !== undefined && entry.line?.reference_quantity !== null ? (
                      <div>Referencia del sistema anterior: {entry.line.reference_quantity} (sólo referencia)</div>
                    ) : null}
                    <div>
                      {entry.line && entry.line.physical_quantity !== null
                        ? `Contado hasta ahora: ${entry.line.physical_quantity}. Lo que escribas lo reemplaza.`
                        : "Sin contar todavía."}
                    </div>
                  </dl>
                  {entry.line?.in_transit_quantity ? (
                    <p className="mt-2 text-xs text-warning">
                      Llegan {entry.line.in_transit_quantity} en camino: si ya están en el estante y no se recibieron, recíbelas antes de contar.
                    </p>
                  ) : null}
                  {entry.line?.paid_pending_dispatch ? (
                    <p className="mt-2 text-xs text-warning">
                      {entry.line.paid_pending_dispatch} apartadas para pedidos pagados: no las cuentes.
                    </p>
                  ) : null}
                </div>
                <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
                  <div className="sm:w-40">
                    <label htmlFor={qtyId} className="mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-muted">
                      Cantidad contada
                    </label>
                    <input
                      id={qtyId}
                      ref={qtyRef}
                      type="number"
                      inputMode="numeric"
                      min={0}
                      step={1}
                      enterKeyHint="done"
                      value={qty}
                      onChange={(e) => setQty(e.target.value)}
                      onKeyDown={onQtyKeyDown}
                      className={`${internalInputClass} text-base tabular-nums`}
                      aria-describedby={`${qtyId}-hint`}
                    />
                  </div>
                  {entry.correction ? (
                    <div className="min-w-0 flex-1">
                      <label htmlFor={reasonId} className="mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-muted">
                        Motivo de la corrección
                      </label>
                      <input
                        id={reasonId}
                        value={reason}
                        maxLength={250}
                        onChange={(e) => setReason(e.target.value)}
                        className={internalInputClass}
                      />
                    </div>
                  ) : null}
                  <button type="submit" disabled={busy} className={internalPrimaryButtonClass}>
                    Guardar total
                  </button>
                  <button type="button" onClick={closeEntry} className={internalButtonClass}>
                    Cancelar
                  </button>
                </div>
                <p id={`${qtyId}-hint`} className="text-xs text-muted">
                  Vacío no se guarda. 0 = verificaste que no hay ninguna.
                  {entry.correction ? " Corrige la misma observación mal escrita; si dudas, mejor recuenta." : null}
                </p>
              </form>
            ) : null}

            {contract && count.status === "counting" && editable ? (
              <div className="flex flex-wrap items-center gap-3 border-t border-bd-border pt-4">
                <button type="button" disabled={busy} onClick={() => void submit()} className={internalButtonClass}>
                  Enviar a revisión
                </button>
                <span className="text-xs text-muted">
                  Cierra la anotación. No mueve stock: la aprobación es otro paso.
                </span>
              </div>
            ) : null}
          </div>
        </Panel>
      ) : null}

      {ambiguous.length > 0 ? (
        <section
          aria-labelledby={ambiguousTitleId}
          className="rounded-xl border border-warning-border bg-warning-surface p-5"
        >
          <h2 id={ambiguousTitleId} className="text-sm font-semibold text-foreground">
            {ambiguous.length} línea(s) necesitan reconteo
          </h2>
          <p className="mt-1 text-xs text-muted">
            Hubo movimientos de stock mientras se contaban: no se sabe si lo contado los incluye. Vuelve a
            contarlas. Bloquean la aprobación.
          </p>
          <ul className="mt-3 space-y-2">
            {ambiguous.map((line) => {
              const typoFixes = needsEntry.has(line.product) && lineState(line) !== "ambiguous";
              return (
                <li key={line.product} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                  <span className="min-w-0">
                    <span className="font-medium text-foreground">{line.product_name}</span>
                    <span className="block text-xs text-muted">
                      {typoFixes
                        ? "Cambió el stock desde que se contó: elige si es un reconteo o la corrección de un error de tecleo."
                        : line.review_detail || "Recuéntalo desde cero."}
                    </span>
                  </span>
                  {editable && contract ? (
                    <span className="flex flex-wrap gap-2">
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => void recount(line)}
                        aria-label={`Recontar ${line.product_name}`}
                        className={internalPrimaryButtonClass}
                      >
                        Recontar
                      </button>
                      {typoFixes ? (
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => openEntry(line.product, line.product_name, true)}
                          aria-label={`Fue un error de tecleo en ${line.product_name}`}
                          className={internalButtonClass}
                        >
                          Fue un error de tecleo
                        </button>
                      ) : null}
                    </span>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}

      {overlapping.length > 0 ? (
        <section
          aria-labelledby={blockedTitleId}
          className="rounded-xl border border-warning-border bg-warning-surface p-5"
        >
          <h2 id={blockedTitleId} className="text-sm font-semibold text-foreground">
            {overlapping.length} línea(s) bloqueada(s) por otra corrección
          </h2>
          <p className="mt-1 text-xs text-muted">
            Otro ajuste, carga o recuento corrigió estos productos después de contarlos: aplicar los dos corregiría
            dos veces la misma diferencia. Recuéntalos para aprobar.
          </p>
          <ul className="mt-3 space-y-2">
            {overlapping.map((line) => (
              <li key={line.product} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <span className="min-w-0">
                  <span className="font-medium text-foreground">{line.product_name}</span>
                  {line.review_detail ? <span className="block text-xs text-muted">{line.review_detail}</span> : null}
                </span>
                {editable && contract ? (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void recount(line)}
                    aria-label={`Recontar ${line.product_name}`}
                    className={internalPrimaryButtonClass}
                  >
                    Recontar
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section aria-labelledby={pendingTitleId} className="min-w-0 rounded-xl border border-bd-border bg-surface">
        <header className="border-b border-bd-border px-5 py-4">
          <h2 id={pendingTitleId} className="text-sm font-semibold text-foreground">Pendientes ({pending.length})</h2>
          <p className="mt-0.5 text-xs text-muted">Sin cantidad todavía. Un pendiente no es un cero.</p>
        </header>
        <div className="p-5">
          {pending.length === 0 ? (
            <p className="text-sm text-muted">Nada pendiente en las líneas de este recuento.</p>
          ) : (
            <ul className="divide-y divide-bd-border">
              {pending.map((line) => (
                <li key={line.product} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
                  <span className="min-w-0">
                    <span className="block text-foreground">{line.product_name}</span>
                    <span className="block text-xs text-muted">
                      {lineCode(line) ? <span className="font-mono">{lineCode(line)}</span> : "Sin código"}
                      {line.reference_quantity !== null && line.reference_quantity !== undefined
                        ? ` · Ref. anterior ${line.reference_quantity}`
                        : null}
                    </span>
                  </span>
                  {editable ? (
                    <button
                      type="button"
                      onClick={() => openEntry(line.product, line.product_name)}
                      aria-label={`Contar ${line.product_name}`}
                      className={internalButtonClass}
                    >
                      Contar
                    </button>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      <Panel title="Líneas del recuento" description={`${count.items.length} producto(s) · ${count.counted_items} contado(s)`}>
        {count.items.length === 0 ? (
          <EmptyBox message="Todavía no se registró ninguna cantidad." />
        ) : (
          <TableWrap>
            <thead>
              <tr className="border-b border-bd-border">
                <Th>Producto</Th>
                <Th>Código</Th>
                {hasReference ? <Th right>Referencia del sistema anterior</Th> : null}
                <Th right>{contract ? "Stock teórico actual" : "Teórico al anotar"}</Th>
                <Th right>Contado</Th>
                <Th right>Diferencia</Th>
                <Th>Estado</Th>
                {showCosts ? <Th right>Valor de la diferencia</Th> : null}
                {editable ? <Th>Acciones</Th> : null}
              </tr>
            </thead>
            <tbody>
              {count.items.map((line) => {
                const state = lineState(line);
                const diff = lineDifference(line);
                const theoretical = contract ? line.current_theoretical ?? line.theoretical_at_start : line.theoretical_at_start;
                return (
                  <tr
                    key={line.product}
                    data-flash={flash === line.product ? "true" : undefined}
                    className={`border-b border-bd-border/60 transition-colors ${
                      flash === line.product ? "bg-foreground/[0.08]" : ""
                    }`}
                  >
                    <Td>{line.product_name}</Td>
                    <Td muted>{lineCode(line) ? <span className="font-mono">{lineCode(line)}</span> : "—"}</Td>
                    {hasReference ? (
                      <td data-testid="reference-cell" className="px-3 py-2.5 text-right text-sm text-muted tabular-nums">
                        {line.reference_quantity ?? "—"}
                      </td>
                    ) : null}
                    <Td right muted>{theoretical ?? "—"}</Td>
                    <td data-testid="counted-cell" className="px-3 py-2.5 text-right text-sm tabular-nums">
                      {line.physical_quantity === null ? (
                        <span className="inline-block rounded border border-dashed border-bd-border px-1.5 py-0.5 text-xs text-muted">
                          Sin contar
                        </span>
                      ) : line.physical_quantity === 0 ? (
                        <span className="font-semibold text-foreground">
                          0 <span className="block text-[11px] font-normal text-muted">verificado: no hay</span>
                        </span>
                      ) : (
                        <span className="font-semibold text-foreground">{line.physical_quantity}</span>
                      )}
                    </td>
                    <Td right muted={diff === null || diff === 0}>{diff === null ? "—" : signed(diff)}</Td>
                    <Td>
                      <span
                        className={`inline-flex rounded-md border px-2 py-0.5 text-[11px] font-medium ${
                          state === "ambiguous" || state === "overlapping"
                            ? "border-warning-border bg-warning-surface text-warning"
                            : "border-bd-border text-muted"
                        }`}
                        title={line.exclusion_reason || line.review_detail || undefined}
                      >
                        {LINE_STATE_LABEL[state]}
                      </span>
                    </Td>
                    {showCosts ? <Td right muted>{line.difference_value ?? "—"}</Td> : null}
                    {editable ? (
                      <Td>
                        {state === "counted" ? (
                          <button
                            type="button"
                            onClick={() => openEntry(line.product, line.product_name)}
                            aria-label={`Corregir ${line.product_name}`}
                            className="text-xs font-semibold underline"
                          >
                            Corregir
                          </button>
                        ) : null}
                      </Td>
                    ) : null}
                  </tr>
                );
              })}
            </tbody>
          </TableWrap>
        )}
      </Panel>
    </div>
  );
}
