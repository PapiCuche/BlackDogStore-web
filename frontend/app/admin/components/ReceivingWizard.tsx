"use client";

/**
 * INV-OPS-01 — «Recibir mercadería»: cuatro pasos, de la caja a las etiquetas.
 *
 *   1 Procedencia · 2 Artículos · 3 Revisar y confirmar · 4 Terminado
 *
 * Una persona nueva debe poder recibir un equipo o unos accesorios sin conocer
 * la estructura del sistema: aquí se busca el artículo (se reutiliza si ya
 * existe), se anotan los equipos o la cantidad, se REVISA lo que va a pasar y
 * recién entonces se confirma. El servidor decide todo (permisos, duplicados,
 * etiquetas); esta pantalla sólo le pregunta antes (`preview`) y le hace caso.
 *
 * Todo lo escrito vive en memoria de esta página. Nada —serie, IMEI, costo—
 * se guarda en el navegador. La sucursal recordada es la de `last-branch.ts`.
 *
 * Confirmar es idempotente: una `Idempotency-Key` por INTENTO. Si la respuesta
 * se pierde, el reintento manda la MISMA clave y el servidor no repite nada;
 * la clave sólo cambia si cambia el contenido o tras un éxito.
 */

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { fetchAdminProductDetail } from "@/app/lib/admin";

import { fetchSuppliers, type SupplierRow } from "../lib/internal-api";
import {
  buildReceiptBody, confirmReceipt, DOCUMENT_REF_MAX, fingerprint, hasProblems, limitProblem, newLine, newReceiptKey,
  NOTES_MAX, previewReceipt, ReceiptApiError, stashDraft, takeStashedDraft, validateLines,
  type DraftLine, type DraftScope, type LineProblems, type ReceiptOrigin, type ReceiptPreview, type ReceiptResult, type UnitProblems,
} from "../lib/inventory-receipts";
import { preferredBranch, rememberBranch } from "../lib/last-branch";
import { ErrorBox, Spinner } from "./InventoryUi";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";
import { extractLabelBatch, NextStepBar } from "./NextStepBar";
import { ReceivingLeaveDialog, useReceivingNavigation } from "./useReceivingNavigation";
import { ReceivingLinesEditor, toReceiptProduct } from "./ReceivingLinesEditor";
import { toPickerProduct } from "./ProductPicker";

export type ReceivingAccess = {
  /** `costs.view`: costs are neither asked nor sent without it. */
  costs: boolean;
  /** `products.manage`: only they are offered «crear artículo». */
  manageProducts: boolean;
  /** `products.view`: the supplier list and the publication centre. */
  viewProducts: boolean;
  /** `inventory.labels.print`: the links to the labels screen. */
  labels: boolean;
};

const STEPS = [
  { id: 1, label: "Procedencia" },
  { id: 2, label: "Artículos" },
  { id: 3, label: "Revisar y confirmar" },
  { id: 4, label: "Terminado" },
] as const;

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

function Progress({ step }: { step: number }) {
  return (
    <nav aria-label="Pasos de la recepción">
      <ol className="grid grid-cols-4 gap-1.5 sm:gap-3">
        {STEPS.map((s) => {
          const state = s.id === step ? "current" : s.id < step ? "done" : "todo";
          return (
            <li
              key={s.id}
              aria-current={state === "current" ? "step" : undefined}
              className={`min-w-0 rounded-lg border px-2 py-2 text-center text-[11px] leading-tight sm:text-xs ${
                state === "current" ? "border-foreground bg-foreground text-background font-bold"
                : state === "done" ? "border-success-border text-success font-semibold"
                : "border-bd-border text-muted"
              }`}
            >
              <span className="block text-[10px] uppercase tracking-widest opacity-70">Paso {s.id}</span>
              <span className="block break-words">{s.label}</span>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

/**
 * What the server found, put next to the exact device row (`unit_issues`) or, for
 * the rest of a line's errors, on the line itself.
 */
function serverProblems(
  data: ReceiptPreview, lineKeys: string[], unitKeys: string[][], show: (index: number, message: string) => string,
): Record<string, LineProblems> {
  const out: Record<string, LineProblems> = {};
  for (const l of data.lines) {
    const key = lineKeys[l.index];
    if (!key || (l.status !== "error" && l.duplicates.length === 0)) continue;
    const entry: LineProblems = { units: {} };
    const pinned = new Set<string>();
    for (const issue of l.unit_issues ?? []) {
      const unitKey = unitKeys[l.index]?.[issue.unit];
      if (!unitKey) continue;
      pinned.add(issue.message);
      const field = (["serial_number", "imei", "imei2", "cost"] as const).find((f) => f === issue.field) ?? "row";
      const current: UnitProblems = entry.units[unitKey] ?? {};
      const text = show(l.index, issue.message);
      entry.units[unitKey] = { ...current, [field]: current[field] ? `${current[field]} ${text}` : text };
    }
    const rest = [...l.errors, ...l.duplicates].filter((m) => !pinned.has(m)).map((m) => show(l.index, m));
    if (rest.length) entry.line = rest.join(" ");
    out[key] = entry;
  }
  return out;
}

/** The kind of a duplicate, told from the server's own wording; neutral when it cannot be told. */
function duplicateGroups(duplicates: string[]): { title: string; items: string[] }[] {
  const repeated = duplicates.filter((d) => /repetid[oa]/i.test(d));
  const registered = duplicates.filter((d) => !/repetid[oa]/i.test(d) && /ya est[aá] registrad/i.test(d));
  const other = duplicates.filter((d) => !repeated.includes(d) && !registered.includes(d));
  const groups = [
    { title: "Repetidos en esta recepción:", items: repeated },
    { title: "Ya registrados en el sistema:", items: registered },
    { title: repeated.length + registered.length > 0 ? "Otros duplicados:" : "Duplicados:", items: other },
  ];
  return groups.filter((g) => g.items.length > 0);
}

const mergeProblems = (
  a: Record<string, LineProblems> | null, b: Record<string, LineProblems>,
): Record<string, LineProblems> => {
  const out: Record<string, LineProblems> = { ...(a ?? {}) };
  for (const [key, extra] of Object.entries(b)) {
    const base = out[key];
    out[key] = base ? { ...base, line: base.line ?? extra.line, units: { ...extra.units, ...base.units } } : extra;
  }
  return out;
};

type PreviewState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error"; message: string; line: number | null }
  | { status: "ready"; data: ReceiptPreview; print: string };

/** The most supplier pages read (25 each): past it the list says it is partial. */
const SUPPLIER_PAGES = 40;

export function ReceivingWizard({
  access, scope, branches, defaultBranch = null, initialProductId = null,
}: {
  access: ReceivingAccess;
  /** Who is typing, and for which company: the draft kept in memory belongs to that pair only. */
  scope: DraftScope;
  /** Branches this person reaches (the server re-checks the one sent). */
  branches: { id: number; name: string }[];
  defaultBranch?: number | null;
  /** `?product=<id>`: a product already chosen (e.g. coming back from «crear artículo»). */
  initialProductId?: number | null;
}) {
  // Coming back from «crear artículo»: the draft kept in memory is given back ONCE, here.
  const [held] = useState(() => takeStashedDraft(scope));
  const [step, setStep] = useState<1 | 2 | 3 | 4>(() => (initialProductId !== null || (held?.lines.length ?? 0) > 0 ? 2 : 1));
  const [origin, setOrigin] = useState<ReceiptOrigin>(() => {
    const only = branches.length === 1 ? branches[0].id : null;
    const fallback = preferredBranch(branches, only ?? defaultBranch);
    if (!held) return { branch: fallback, supplier: null, document_ref: "", notes: "" };
    const kept = held.origin.branch !== null && branches.some((b) => b.id === held.origin.branch);
    return { ...held.origin, branch: kept ? held.origin.branch : fallback };
  });
  const [lines, setLines] = useState<DraftLine[]>(() => held?.lines ?? []);
  // What the restored draft could not keep, said once and visibly.
  const [notices, setNotices] = useState<string[]>(() => {
    if (!held) return [];
    const out = ["Recuperamos tu lista de la recepción."];
    if (held.origin.branch !== null && !branches.some((b) => b.id === held.origin.branch)) {
      out.push("La sucursal de tu lista ya no está disponible: elige otra en el primer paso.");
    }
    return out;
  });
  const [ambiguous, setAmbiguous] = useState(false);
  const [suppliers, setSuppliers] = useState<SupplierRow[] | null>(null);
  const [supplierNote, setSupplierNote] = useState("");
  const [problems, setProblems] = useState<Record<string, LineProblems> | null>(null);
  const [focus, setFocus] = useState<{ lineKey: string; token: number; unitKey?: string } | null>(null);
  const [rawPreview, setPreview] = useState<PreviewState>({ status: "idle" });
  const [acceptedFor, setAcceptedFor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [result, setResult] = useState<ReceiptResult | null>(null);
  const [linkNote, setLinkNote] = useState("");
  const [limitNote, setLimitNote] = useState("");
  const [failureLine, setFailureLine] = useState<number | null>(null);

  const attempt = useRef<{ key: string; print: string } | null>(null);
  const inFlight = useRef(false);
  const previewTicket = useRef(0);
  const lineKeys = useRef<string[]>([]);
  const unitKeys = useRef<string[][]>([]);
  const started = useRef(false);
  const tokens = useRef(0);
  const heading = useRef<HTMLHeadingElement>(null);
  const moveFocus = useRef(true);
  const firstStep = useRef(true);
  const nextToken = () => ++tokens.current;

  // `?product=`: the product the person came back with (or followed a link to).
  useEffect(() => {
    if (initialProductId === null || started.current) return;
    started.current = true;
    void fetchAdminProductDetail(initialProductId).then(
      (loaded) => {
        const product = toReceiptProduct(toPickerProduct(loaded));
        setLines((current) => {
          const known = current.find((l) => l.product.id === product.id);
          if (known) { setFocus({ lineKey: known.key, token: nextToken() }); return current; }
          const line = newLine(product);
          setFocus({ lineKey: line.key, token: nextToken() });
          return [...current, line];
        });
      },
      (err) => setLinkNote(err instanceof Error && err.message ? err.message : "No se pudo cargar el artículo del enlace."),
    );
  }, [initialProductId]);

  // Supplier list: optional help, never a reason to stop.
  useEffect(() => {
    if (!access.viewProducts) return;
    let cancelled = false;
    void (async () => {
      try {
        const all: SupplierRow[] = [];
        let total = 0;
        for (let page = 1; page <= SUPPLIER_PAGES; page += 1) {
          const answer = await fetchSuppliers({ active: "true", page });
          if (cancelled) return;
          all.push(...answer.results);
          total = answer.count;
          if (answer.results.length === 0 || all.length >= total) break;
        }
        const complete = all.length >= total;
        setSuppliers(all);
        if (!complete) {
          setSupplierNote(`Se muestran los primeros ${all.length} de ${total} proveedores. Si no ves el tuyo, recibe sin proveedor y anota su nombre en las observaciones.`);
        } else {
          // A supplier carried over from a restored draft that this company no longer lists: out, visibly.
          setOrigin((current) => {
            if (current.supplier === null || all.some((s) => s.id === current.supplier)) return current;
            setNotices((n) => [...n, "El proveedor de tu lista ya no está disponible: elige otro o sigue sin proveedor."]);
            return { ...current, supplier: null };
          });
        }
      } catch {
        if (!cancelled) setSupplierNote("No se pudo cargar la lista de proveedores. Puedes seguir sin elegir uno.");
      }
    })();
    return () => { cancelled = true; };
  }, [access.viewProducts]);

  // Browser navigation: one history entry per step, and a question before leaving with something to lose.
  const nav = useReceivingNavigation({
    step,
    onStep: (next) => { moveFocus.current = true; setStep(next); window.scrollTo?.({ top: 0 }); },
    guard: step < 4 && (lines.length > 0 || busy || ambiguous),
    strong: step < 4 && (busy || ambiguous),
    finished: step === 4,
  });

  const built = useMemo(
    () => (origin.branch === null ? null : buildReceiptBody(origin, lines, { costs: access.costs })),
    [origin, lines, access.costs],
  );
  const print = built ? fingerprint(built.body) : "";

  // Anything typed after the review makes the review (and the tick) stale.
  const preview: PreviewState = rawPreview.status === "ready" && rawPreview.print !== print ? { status: "idle" } : rawPreview;
  const accepted = acceptedFor === print;

  const runPreview = useCallback(async () => {
    if (!built) return;
    const ticket = ++previewTicket.current;
    setPreview({ status: "loading" });
    setAcceptedFor(null);
    setFailure(null);
    lineKeys.current = built.lineKeys;
    unitKeys.current = built.unitKeys;
    try {
      const data = await previewReceipt(built.body);
      if (ticket === previewTicket.current) setPreview({ status: "ready", data, print: fingerprint(built.body) });
    } catch (err) {
      if (ticket === previewTicket.current) {
        setPreview({
          status: "error", message: err instanceof Error ? err.message : "No se pudo revisar la recepción.",
          line: err instanceof ReceiptApiError ? err.line : null,
        });
      }
    }
  }, [built]);

  // Each step change puts the cursor on that step's heading (a screen reader announces it), unless the
  // caller is about to focus something more specific («Corregir» lands on the exact field).
  useEffect(() => {
    if (firstStep.current) { firstStep.current = false; return; }
    if (moveFocus.current) heading.current?.focus();
    moveFocus.current = true;
  }, [step]);

  function goTo(next: 1 | 2 | 3 | 4, focusHeading = true, mode: "auto" | "replace" | "push" = "auto") {
    moveFocus.current = focusHeading;
    if (mode === "replace") nav.replaceStep(next);
    else if (mode === "push" || next > step) nav.pushStep(next);
    else if (next < step) nav.popStep();
    setStep(next);
    if (typeof window !== "undefined") window.scrollTo?.({ top: 0 });
  }

  function next() {
    if (step === 1) {
      if (origin.branch === null) return;
      goTo(2);
    } else if (step === 2) {
      if (lines.length === 0) { setProblems({}); return; }
      const tooMany = limitProblem(lines);
      if (tooMany) { setLimitNote(tooMany); return; }
      setLimitNote("");
      const found = validateLines(lines, { costs: access.costs });
      setProblems(found);
      if (hasProblems(found)) {
        const firstKey = lines.find((l) => found[l.key])?.key;
        if (firstKey) setFocus({ lineKey: firstKey, token: nextToken() });
        return;
      }
      goTo(3);
      void runPreview();
    }
  }

  function fix(index: number) {
    if (ambiguous) return; // an unanswered confirmation: nothing is edited until it is settled
    const key = lineKeys.current[index];
    goTo(2, !lineKeys.current[index]);
    if (!key) return;
    // The first device the server pointed at, so the cursor lands on the exact row.
    const first = preview.status === "ready" ? preview.data.lines.find((l) => l.index === index)?.unit_issues?.[0] : undefined;
    setFocus({ lineKey: key, token: nextToken(), unitKey: first ? unitKeys.current[index]?.[first.unit] : undefined });
  }

  async function confirm() {
    if (inFlight.current || !built || preview.status !== "ready") return;
    const now = fingerprint(built.body);
    if (preview.print !== now) return;
    // ONE key per attempt: reused on retry, replaced only when the content changed.
    if (!attempt.current || attempt.current.print !== now) {
      const key = newReceiptKey();
      if (!key) {
        setFailure("Este navegador no puede generar la clave de seguridad de la recepción. Abre la página en una conexión segura (https) o usa otro navegador.");
        return;
      }
      attempt.current = { key, print: now };
    }
    inFlight.current = true;
    setBusy(true);
    setFailure(null);
    setFailureLine(null);
    try {
      const done = await confirmReceipt(built.body, attempt.current.key);
      attempt.current = null; // a success spends the key
      setAmbiguous(false);
      rememberBranch(origin.branch);
      setResult(done);
      goTo(4, true, "replace");
    } catch (err) {
      const api = err instanceof ReceiptApiError ? err : null;
      if (api?.preview) {
        // The server refused the whole reception and wrote nothing: show its review.
        setPreview({ status: "ready", data: api.preview, print: now });
        setFailure("No se registró nada: hay artículos con problemas. Corrígelos y vuelve a confirmar.");
      } else if (api?.code === "idempotency_conflict") {
        // The same key met different content: this attempt is over, the next one gets a new key.
        attempt.current = null;
        setAmbiguous(false);
        setFailure("Esta recepción cambió respecto al intento anterior y no se registró. Revisa los datos y pulsa «Confirmar» otra vez.");
      } else if (api?.code === "busy") {
        // Another reception holds the same shelf or devices: nothing was saved, the key and the content stay.
        setFailure("Otra recepción está en curso; vuelve a intentarlo en unos segundos.");
      } else if (api?.ambiguous) {
        // No answer: it may be saved. The key and the content are frozen until it is settled.
        setAmbiguous(true);
        setFailure(null);
      } else {
        setFailure(err instanceof Error ? err.message : "No se pudo registrar la recepción.");
        setFailureLine(api?.line ?? null);
      }
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  function another() {
    // Branch and supplier stay; what belongs to THIS delivery is emptied.
    setLines([]);
    setOrigin((current) => ({ ...current, document_ref: "", notes: "" }));
    setPreview({ status: "idle" });
    setProblems(null);
    setResult(null);
    setFailure(null);
    setAmbiguous(false);
    setNotices([]);
    attempt.current = null;
    // The old result entry becomes the start of the new flow, so «atrás» from step 2 lands on step 1.
    nav.replaceStep(1);
    goTo(2, true, "push");
  }

  /** «Revisé el historial y no se registró nada»: the person's word that the attempt did not happen. */
  function discardAttempt() {
    attempt.current = null;
    setAmbiguous(false);
    setFailure(null);
  }

  /** The server counts the devices SENT («Equipo 2»); the screen numbers every row. Say the screen's number. */
  const show = (index: number, message: string): string => {
    const lineKey = built?.lineKeys[index];
    const line = lines.find((l) => l.key === lineKey);
    return message.replace(/\bEquipo (\d+)\b/g, (whole, n) => {
      const unitKey = built?.unitKeys[index]?.[Number(n) - 1];
      const at = line && unitKey ? line.units.findIndex((u) => u.key === unitKey) : -1;
      return at >= 0 ? `Equipo ${at + 1}` : whole;
    });
  };

  const fromServer = rawPreview.status === "ready" && rawPreview.print === print && built
    ? serverProblems(rawPreview.data, built.lineKeys, built.unitKeys, show) : {};
  const shownProblems = mergeProblems(problems, fromServer);
  const canContinueFromOrigin = origin.branch !== null;

  const previewErrors = preview.status === "ready" ? preview.data.lines.some((l) => l.status === "error") : true;
  const badLines = preview.status === "ready"
    ? preview.data.lines.filter((l) => l.status === "error" || l.duplicates.length > 0).length : 0;
  const canConfirm = preview.status === "ready" && preview.data.ok && !previewErrors && accepted && !busy;

  return (
    <div className="space-y-6">
      <Progress step={step} />
      <p className="sr-only" aria-live="polite">{`Paso ${step} de 4: ${STEPS[step - 1].label}`}</p>

      {linkNote ? <ErrorBox message={linkNote} /> : null}
      {step < 4 && notices.length > 0 ? (
        <div className="space-y-1">
          {notices.map((n) => <p key={n} role="status" className="rounded-lg border border-bd-border bg-surface px-3 py-2 text-xs text-foreground">{n}</p>)}
        </div>
      ) : null}
      {nav.prompt ? <ReceivingLeaveDialog prompt={nav.prompt} onStay={nav.stay} onLeave={nav.leave} /> : null}

      {step === 1 ? (
        <section aria-labelledby="step-origin" className="space-y-5 rounded-xl border border-bd-border bg-surface p-4 sm:p-6">
          <div>
            <h2 id="step-origin" ref={heading} tabIndex={-1} className="text-base font-semibold text-foreground outline-none">¿De dónde viene y adónde llega?</h2>
            <p className="mt-1 text-sm text-muted">
              Elige la sucursal que recibe la mercadería. El proveedor y el documento son opcionales, pero ayudan a
              encontrar esta compra después.
            </p>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <label htmlFor="receive-branch" className="block text-xs font-semibold text-foreground">Sucursal que recibe *</label>
              <select
                id="receive-branch" value={origin.branch ?? ""}
                onChange={(e) => setOrigin({ ...origin, branch: e.target.value ? Number(e.target.value) : null })}
                className={`mt-1.5 min-h-11 ${internalInputClass}`}
              >
                <option value="">Elige una sucursal</option>
                {branches.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
              </select>
              {branches.length === 0 ? (
                <p className="mt-1 text-xs text-warning">No tienes ninguna sucursal asignada: pide que te asignen una para poder recibir.</p>
              ) : null}
            </div>
            {access.viewProducts ? (
              <div>
                <label htmlFor="receive-supplier" className="block text-xs font-semibold text-foreground">Proveedor (opcional)</label>
                <select
                  id="receive-supplier" value={origin.supplier ?? ""}
                  onChange={(e) => setOrigin({ ...origin, supplier: e.target.value ? Number(e.target.value) : null })}
                  className={`mt-1.5 min-h-11 ${internalInputClass}`}
                >
                  <option value="">Sin proveedor</option>
                  {(suppliers ?? []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
                {supplierNote ? <p role="status" className="mt-1 text-xs text-warning">{supplierNote}</p> : null}
              </div>
            ) : null}
            <div>
              <label htmlFor="receive-doc" className="block text-xs font-semibold text-foreground">Referencia del documento (opcional)</label>
              <input
                id="receive-doc" value={origin.document_ref} maxLength={DOCUMENT_REF_MAX} autoComplete="off"
                onChange={(e) => setOrigin({ ...origin, document_ref: e.target.value })}
                placeholder="Ej.: factura F001-234 o guía 0001-55"
                className={`mt-1.5 min-h-11 ${internalInputClass}`}
              />
              <p className="mt-1 text-xs text-muted">{origin.document_ref.length}/{DOCUMENT_REF_MAX}</p>
            </div>
            <div className="md:col-span-2">
              <label htmlFor="receive-notes" className="block text-xs font-semibold text-foreground">Observaciones (opcional)</label>
              <textarea
                id="receive-notes" value={origin.notes} maxLength={NOTES_MAX} rows={3}
                onChange={(e) => setOrigin({ ...origin, notes: e.target.value })}
                placeholder="Ej.: caja golpeada, falta un cargador…"
                className={`mt-1.5 ${internalInputClass}`}
              />
              <p className="mt-1 text-xs text-muted">{origin.notes.length}/{NOTES_MAX}</p>
            </div>
          </div>
        </section>
      ) : null}

      {step === 2 ? (
        <section aria-labelledby="step-items" className="space-y-4">
          <div>
            <h2 id="step-items" ref={heading} tabIndex={-1} className="text-base font-semibold text-foreground outline-none">¿Qué llegó?</h2>
            <p className="mt-1 text-sm text-muted">
              Agrega cada artículo. Los equipos con serie se anotan uno por uno (serie e IMEI); los accesorios, por
              cantidad. Puedes mezclar varios en la misma recepción.
            </p>
          </div>
          <ReceivingLinesEditor
            lines={lines}
            onLines={(next) => { setLines(next); if (problems) setProblems(validateLines(next, { costs: access.costs })); }}
            canCosts={access.costs}
            canManageProducts={access.manageProducts}
            problems={shownProblems && hasProblems(shownProblems) ? shownProblems : null}
            focus={focus}
            onCreateArticle={() => stashDraft(origin, lines, scope)}
          />
          {limitNote ? <p role="alert" className="text-sm text-danger">{limitNote}</p> : null}
          {problems && lines.length === 0 ? (
            <p role="alert" className="text-sm text-danger">Agrega al menos un artículo para continuar.</p>
          ) : null}
          {problems && hasProblems(problems) ? (
            <p role="alert" className="text-sm text-danger">
              Revisa los campos marcados en rojo: hay datos que faltan o están repetidos.
            </p>
          ) : null}
        </section>
      ) : null}

      {step === 3 ? (
        <section aria-labelledby="step-review" className="space-y-4">
          <div>
            <h2 id="step-review" ref={heading} tabIndex={-1} className="text-base font-semibold text-foreground outline-none">Revisa antes de registrar</h2>
            <p className="mt-1 text-sm text-muted">
              Todavía no se guardó nada. Esto es lo que pasará cuando confirmes.
            </p>
          </div>

          {preview.status === "loading" ? <Spinner label="Revisando la recepción…" /> : null}
          {preview.status === "error" ? (
            <div className="space-y-3">
              <ErrorBox message={preview.message} />
              <div className="flex flex-wrap gap-2">
                <button type="button" className={`${internalButtonClass} min-h-11`} onClick={() => void runPreview()}>Reintentar</button>
                <button type="button" className={`${internalButtonClass} min-h-11`} onClick={() => (preview.line !== null ? fix(preview.line) : goTo(2))}>
                  {preview.line !== null ? "Ir a ese artículo" : "Volver a los artículos"}
                </button>
              </div>
            </div>
          ) : null}
          {preview.status === "idle" ? (
            <div className="space-y-3">
              <p className="text-sm text-muted">Cambiaste algo después de revisar. Vuelve a revisar para poder confirmar.</p>
              <button type="button" className={`${internalPrimaryButtonClass} min-h-11`} onClick={() => void runPreview()}>Revisar de nuevo</button>
            </div>
          ) : null}

          {preview.status === "ready" ? (
            <>
              <dl className="grid gap-3 rounded-xl border border-bd-border bg-surface p-4 text-sm sm:grid-cols-3">
                <div><dt className="text-xs text-muted">Sucursal</dt><dd className="font-semibold text-foreground">{preview.data.branch.name}</dd></div>
                <div><dt className="text-xs text-muted">Proveedor</dt><dd className="font-semibold text-foreground">{preview.data.supplier?.name ?? "Sin proveedor"}</dd></div>
                <div><dt className="text-xs text-muted">Documento</dt><dd className="break-words font-semibold text-foreground">{preview.data.document_ref || "Sin referencia"}</dd></div>
              </dl>

              {/* The server totals count only the lines that are ok: with a problem they would read «0», so they are not shown. */}
              {previewErrors || !preview.data.ok ? (
                <p className="text-sm font-semibold text-danger" role="status">
                  No se puede confirmar todavía: corrige {plural(badLines, "línea", "líneas")}.
                </p>
              ) : (
                <p className="text-sm text-foreground" role="status">
                  Entrarán {plural(preview.data.totals.quantity_units, "unidad", "unidades")} por cantidad y{" "}
                  {plural(preview.data.totals.devices, "equipo identificado", "equipos identificados")}. Se prepararán{" "}
                  {plural(preview.data.totals.labels, "etiqueta", "etiquetas")}.
                </p>
              )}

              <ul className="space-y-3" aria-label="Detalle por artículo">
                {preview.data.lines.map((l) => (
                  <li key={l.index} className={`rounded-xl border p-4 ${l.status === "error" || l.duplicates.length ? "border-danger" : "border-bd-border"} bg-surface`}>
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className="break-words text-sm font-semibold text-foreground">{l.product.name}</p>
                        <p className="text-xs text-muted">
                          Se usa el artículo que ya existe{l.product.sku ? ` (${l.product.sku})` : ""}.{" "}
                          {l.status === "error" || l.duplicates.length > 0
                            ? "Esta línea tiene errores; corrígela para poder confirmar."
                            : <>
                              {l.kind === "units"
                                ? `Entran ${plural(l.will_receive, "equipo identificado", "equipos identificados")}.`
                                : `Entran ${plural(l.will_receive, "unidad", "unidades")}.`}{" "}
                              {l.labels > 0 ? `${plural(l.labels, "etiqueta", "etiquetas")}.` : ""}
                            </>}
                        </p>
                      </div>
                      {l.status === "error" || l.duplicates.length > 0 ? (
                        <button type="button" className={`${internalButtonClass} min-h-11`} aria-label={`Corregir ${l.product.name}`} onClick={() => fix(l.index)}>Corregir</button>
                      ) : null}
                    </div>
                    {l.errors.length ? (
                      <ul role="alert" className="mt-2 list-disc space-y-1 pl-5 text-xs text-danger">{l.errors.map((e) => <li key={e}>{show(l.index, e)}</li>)}</ul>
                    ) : null}
                    {duplicateGroups(l.duplicates).map((group) => (
                      <div key={group.title} role="alert" className="mt-2 text-xs text-danger">
                        <p className="font-semibold">{group.title}</p>
                        <ul className="list-disc pl-5">{group.items.map((d) => <li key={d}>{show(l.index, d)}</li>)}</ul>
                      </div>
                    ))}
                    {l.warnings.length ? (
                      <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-warning">{l.warnings.map((w) => <li key={w}>{show(l.index, w)}</li>)}</ul>
                    ) : null}
                  </li>
                ))}
              </ul>

              {previewErrors || !preview.data.ok ? (
                <p role="alert" className="text-sm text-danger">
                  Hay artículos con problemas. Usa «Corregir» para arreglarlos; mientras tanto no se puede confirmar.
                </p>
              ) : (
                <label className="flex items-start gap-3 rounded-lg border border-bd-border p-3 text-sm text-foreground">
                  <input
                    type="checkbox" checked={accepted} onChange={(e) => setAcceptedFor(e.target.checked ? print : null)}
                    className="mt-0.5 h-5 w-5 accent-[var(--primary)]"
                  />
                  <span>Revisé los datos y quiero registrar esta recepción en {preview.data.branch.name}.</span>
                </label>
              )}
            </>
          ) : null}

          {ambiguous ? (
            <div role="alert" className="space-y-3 rounded-xl border border-warning-border bg-warning-surface p-4 text-sm text-foreground">
              <p className="font-semibold">Puede que la recepción ya se haya registrado.</p>
              <p className="text-muted">
                No llegó la respuesta del servidor. Reintenta con el mismo botón (no se duplicará) o revisa el
                historial de movimientos antes de cambiar nada.
              </p>
              <div className="flex flex-wrap gap-2">
                <button type="button" className={`${internalPrimaryButtonClass} min-h-11`} disabled={busy} onClick={() => void confirm()}>
                  Reintentar confirmación
                </button>
                <a
                  href="/admin/inventory/movements" target="_blank" rel="noopener noreferrer"
                  data-receiving-leave="ok" className={`${internalButtonClass} inline-flex min-h-11 items-center`}
                >
                  Revisar el historial de movimientos (se abre en otra pestaña)
                </a>
                <button type="button" className={`${internalButtonClass} min-h-11`} disabled={busy} onClick={discardAttempt}>
                  Revisé el historial y no se registró nada, quiero editar
                </button>
              </div>
            </div>
          ) : null}
          {failure ? (
            <div className="space-y-2">
              <ErrorBox message={failure} />
              {failureLine !== null ? (
                <button type="button" className={`${internalButtonClass} min-h-11`} onClick={() => fix(failureLine)}>Ir a ese artículo</button>
              ) : null}
            </div>
          ) : null}
        </section>
      ) : null}

      {step === 4 && result ? (
        <ReceivingDone result={result} access={access} onAnother={another} headingRef={heading} />
      ) : null}

      {step < 4 ? (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-bd-border pt-4">
          <button
            type="button" className={`${internalButtonClass} min-h-11`}
            disabled={step === 1 || busy || ambiguous}
            onClick={() => goTo((step - 1) as 1 | 2)}
          >
            Atrás
          </button>
          {step === 3 ? (
            <button
              type="button" className={`${internalPrimaryButtonClass} min-h-11`}
              disabled={!canConfirm} onClick={() => void confirm()}
            >
              {busy ? "Registrando…" : "Confirmar recepción"}
            </button>
          ) : (
            <button
              type="button" className={`${internalPrimaryButtonClass} min-h-11`}
              disabled={step === 1 ? !canContinueFromOrigin : false} onClick={next}
            >
              {step === 1 ? "Siguiente: artículos" : "Siguiente: revisar"}
            </button>
          )}
        </div>
      ) : null}
    </div>
  );
}

function ReceivingDone({ result, access, onAnother, headingRef }: { result: ReceiptResult; access: ReceivingAccess; onAnother: () => void; headingRef: React.Ref<HTMLHeadingElement> }) {
  const labelFailed = result.label_error || result.lines.some((l) => l.label_error);
  // A failed batch comes as `{ id: null, status: "error" }`: it is a warning, never «ready to print».
  const readyBatches = result.label_batches.filter((b) => b.id != null && b.status !== "error");
  const batch = extractLabelBatch({ label_batches: readyBatches });
  const first = result.lines[0];
  const branch = typeof result.branch === "object" ? result.branch.name : "";
  const supplier = result.supplier && typeof result.supplier === "object" ? result.supplier.name : "";
  return (
    <section aria-labelledby="step-done" className="space-y-4">
      <div className="rounded-xl border border-success-border bg-success-surface/40 p-4 sm:p-6">
        <h2 id="step-done" ref={headingRef} tabIndex={-1} className="text-base font-semibold text-foreground outline-none">Recepción registrada</h2>
        <p className="mt-1 text-sm text-foreground" role="status">
          {result.replayed ? "Esta recepción ya estaba registrada: no se repitió. " : ""}
          Entraron {plural(result.totals.quantity_units, "unidad", "unidades")} por cantidad y{" "}
          {plural(result.totals.devices, "equipo identificado", "equipos identificados")}
          {branch ? ` en ${branch}` : ""}{supplier ? `, de ${supplier}` : ""}
          {result.document_ref ? ` (${result.document_ref})` : ""}.
        </p>
        <ul className="mt-3 space-y-1 text-sm text-foreground" aria-label="Lo que se registró">
          {result.lines.map((l) => (
            <li key={l.index} className="break-words">
              <span className="font-semibold">{l.product.name}</span>:{" "}
              {l.kind === "units" ? plural(l.units, "equipo identificado", "equipos identificados") : plural(l.received, "unidad", "unidades")}
            </li>
          ))}
        </ul>
      </div>

      {labelFailed ? (
        <div role="alert" className="rounded-xl border border-warning-border bg-warning-surface p-4 text-sm text-foreground">
          <p className="font-semibold">El stock sí se registró, pero no se pudieron preparar las etiquetas.</p>
          <p className="mt-1 text-muted">
            No repitas la recepción: el stock ya está. Abre las etiquetas y prepáralas desde allí, o avisa a quien
            administra el sistema si el problema sigue.
          </p>
          {access.labels ? (
            <Link href="/admin/inventory/labels" className={`${internalButtonClass} mt-3 inline-flex min-h-11 items-center`}>Ir a las etiquetas</Link>
          ) : null}
        </div>
      ) : null}

      {!access.labels ? (
        <p role="status" className="rounded-lg border border-bd-border bg-surface px-3 py-2 text-sm text-muted">
          Tu usuario no puede imprimir etiquetas. Pide a alguien con permiso de etiquetas que las imprima.
        </p>
      ) : null}

      {first ? (
        <NextStepBar
          product={{ id: first.product.id, name: first.product.name }}
          displayName={result.lines.map((l) => l.product.name).join(" · ")}
          showLabels={access.labels}
          showPublication={access.viewProducts && result.lines.length === 1}
          labelBatch={access.labels ? batch : null}
        >
          <button type="button" className={`${internalPrimaryButtonClass} min-h-11`} onClick={onAnother}>Registrar otra recepción</button>
        </NextStepBar>
      ) : null}

      <div className="flex flex-wrap gap-2">
        {batch && access.labels ? (
          <Link href={batch.href} className={`${internalPrimaryButtonClass} inline-flex min-h-11 items-center`}>Imprimir o ver etiquetas</Link>
        ) : null}
        {access.viewProducts
          ? result.lines.map((l) => (
            <Link
              key={l.index}
              href={`/admin/products/publication?search=${encodeURIComponent(l.product.name)}`}
              className={`${internalButtonClass} inline-flex min-h-11 items-center`}
              aria-label={`Completar ficha comercial de ${l.product.name}`}
            >
              Completar ficha comercial{result.lines.length > 1 ? `: ${l.product.name}` : ""}
            </Link>
          ))
          : null}
      </div>
    </section>
  );
}
