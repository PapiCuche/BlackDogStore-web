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
  buildReceiptBody, confirmReceipt, DOCUMENT_REF_MAX, fingerprint, hasProblems, newLine, newReceiptKey,
  NOTES_MAX, previewReceipt, ReceiptApiError, stashDraft, takeStashedDraft, validateLines,
  type DraftLine, type LineProblems, type ReceiptOrigin, type ReceiptPreview, type ReceiptResult,
} from "../lib/inventory-receipts";
import { preferredBranch, rememberBranch } from "../lib/last-branch";
import { ErrorBox, Spinner } from "./InventoryUi";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";
import { extractLabelBatch, NextStepBar } from "./NextStepBar";
import { ReceivingLinesEditor, toReceiptProduct } from "./ReceivingLinesEditor";
import { toPickerProduct } from "./ProductPicker";

export type ReceivingAccess = {
  /** `costs.view`: costs are neither asked nor sent without it. */
  costs: boolean;
  /** `products.manage`: only they are offered «crear artículo». */
  manageProducts: boolean;
  /** `products.view`: the supplier list and the publication centre. */
  viewProducts: boolean;
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

type PreviewState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: ReceiptPreview; print: string };

export function ReceivingWizard({
  access, branches, defaultBranch = null, initialProductId = null,
}: {
  access: ReceivingAccess;
  /** Branches this person reaches (the server re-checks the one sent). */
  branches: { id: number; name: string }[];
  defaultBranch?: number | null;
  /** `?product=<id>`: a product already chosen (e.g. coming back from «crear artículo»). */
  initialProductId?: number | null;
}) {
  const [step, setStep] = useState<1 | 2 | 3 | 4>(1);
  const [origin, setOrigin] = useState<ReceiptOrigin>(() => {
    const only = branches.length === 1 ? branches[0].id : null;
    return { branch: preferredBranch(branches, only ?? defaultBranch), supplier: null, document_ref: "", notes: "" };
  });
  const [lines, setLines] = useState<DraftLine[]>([]);
  const [suppliers, setSuppliers] = useState<SupplierRow[] | null>(null);
  const [supplierNote, setSupplierNote] = useState("");
  const [problems, setProblems] = useState<Record<string, LineProblems> | null>(null);
  const [focus, setFocus] = useState<{ lineKey: string; token: number } | null>(null);
  const [rawPreview, setPreview] = useState<PreviewState>({ status: "idle" });
  const [acceptedFor, setAcceptedFor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [result, setResult] = useState<ReceiptResult | null>(null);
  const [linkNote, setLinkNote] = useState("");

  const attempt = useRef<{ key: string; print: string } | null>(null);
  const inFlight = useRef(false);
  const previewTicket = useRef(0);
  const lineKeys = useRef<string[]>([]);
  const started = useRef(false);
  const tokens = useRef(0);
  const nextToken = () => ++tokens.current;

  // Coming back from «crear artículo» (draft kept in memory) and/or `?product=`.
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const held = takeStashedDraft();
    let base: DraftLine[] = [];
    if (held) {
      base = held.lines;
      setLines(held.lines);
      setOrigin((current) => ({
        ...held.origin,
        branch: held.origin.branch !== null && branches.some((b) => b.id === held.origin.branch) ? held.origin.branch : current.branch,
      }));
      if (held.lines.length > 0) setStep(2);
    }
    if (initialProductId === null) return;
    setStep(2);
    void fetchAdminProductDetail(initialProductId).then(
      (loaded) => {
        const product = toReceiptProduct(toPickerProduct(loaded));
        setLines((current) => {
          const known = [...base, ...current].find((l) => l.product.id === product.id);
          if (known) { setFocus({ lineKey: known.key, token: nextToken() }); return current.length ? current : base; }
          const line = newLine(product);
          setFocus({ lineKey: line.key, token: nextToken() });
          return [...(current.length ? current : base), line];
        });
      },
      (err) => setLinkNote(err instanceof Error && err.message ? err.message : "No se pudo cargar el artículo del enlace."),
    );
  }, [initialProductId, branches]);

  // Supplier list: optional help, never a reason to stop.
  useEffect(() => {
    if (!access.viewProducts) return;
    let cancelled = false;
    fetchSuppliers({ active: "true" }).then(
      (page) => { if (!cancelled) setSuppliers(page.results); },
      () => { if (!cancelled) setSupplierNote("No se pudo cargar la lista de proveedores. Puedes seguir sin elegir uno."); },
    );
    return () => { cancelled = true; };
  }, [access.viewProducts]);

  // A reload or a closed tab would lose what was typed: ask first.
  const dirty = step < 4 && lines.length > 0;
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

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
    try {
      const data = await previewReceipt(built.body);
      if (ticket === previewTicket.current) setPreview({ status: "ready", data, print: fingerprint(built.body) });
    } catch (err) {
      if (ticket === previewTicket.current) {
        setPreview({ status: "error", message: err instanceof Error ? err.message : "No se pudo revisar la recepción." });
      }
    }
  }, [built]);

  function goTo(next: 1 | 2 | 3 | 4) {
    setStep(next);
    if (typeof window !== "undefined") window.scrollTo?.({ top: 0 });
  }

  function next() {
    if (step === 1) {
      if (origin.branch === null) return;
      goTo(2);
    } else if (step === 2) {
      if (lines.length === 0) { setProblems({}); return; }
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
    const key = lineKeys.current[index];
    goTo(2);
    if (key) setFocus({ lineKey: key, token: nextToken() });
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
    try {
      const done = await confirmReceipt(built.body, attempt.current.key);
      attempt.current = null; // a success spends the key
      rememberBranch(origin.branch);
      setResult(done);
      goTo(4);
    } catch (err) {
      setFailure(err instanceof ReceiptApiError || err instanceof Error ? err.message : "No se pudo registrar la recepción.");
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
    attempt.current = null;
    goTo(2);
  }

  const canContinueFromOrigin = origin.branch !== null;

  const previewErrors = preview.status === "ready" ? preview.data.lines.some((l) => l.status === "error") : true;
  const canConfirm = preview.status === "ready" && preview.data.ok && !previewErrors && accepted && !busy;

  return (
    <div className="space-y-6">
      <Progress step={step} />

      {linkNote ? <ErrorBox message={linkNote} /> : null}

      {step === 1 ? (
        <section aria-labelledby="step-origin" className="space-y-5 rounded-xl border border-bd-border bg-surface p-4 sm:p-6">
          <div>
            <h2 id="step-origin" className="text-base font-semibold text-foreground">¿De dónde viene y adónde llega?</h2>
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
            <h2 id="step-items" className="text-base font-semibold text-foreground">¿Qué llegó?</h2>
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
            problems={problems && hasProblems(problems) ? problems : null}
            focus={focus}
            onCreateArticle={() => stashDraft(origin, lines)}
          />
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
            <h2 id="step-review" className="text-base font-semibold text-foreground">Revisa antes de registrar</h2>
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
                <button type="button" className={`${internalButtonClass} min-h-11`} onClick={() => goTo(2)}>Volver a los artículos</button>
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

              <p className="text-sm text-foreground" role="status">
                Entrarán {plural(preview.data.totals.quantity_units, "unidad", "unidades")} por cantidad y{" "}
                {plural(preview.data.totals.devices, "equipo identificado", "equipos identificados")}. Se prepararán{" "}
                {plural(preview.data.totals.labels, "etiqueta", "etiquetas")}.
              </p>

              <ul className="space-y-3" aria-label="Detalle por artículo">
                {preview.data.lines.map((l) => (
                  <li key={l.index} className={`rounded-xl border p-4 ${l.status === "error" || l.duplicates.length ? "border-danger" : "border-bd-border"} bg-surface`}>
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className="break-words text-sm font-semibold text-foreground">{l.product.name}</p>
                        <p className="text-xs text-muted">
                          Se usa el artículo que ya existe{l.product.sku ? ` (${l.product.sku})` : ""}.{" "}
                          {l.kind === "units"
                            ? `Entran ${plural(l.will_receive, "equipo identificado", "equipos identificados")}.`
                            : `Entran ${plural(l.will_receive, "unidad", "unidades")}.`}{" "}
                          {l.labels > 0 ? `${plural(l.labels, "etiqueta", "etiquetas")}.` : ""}
                        </p>
                      </div>
                      {l.status === "error" || l.duplicates.length > 0 ? (
                        <button type="button" className={`${internalButtonClass} min-h-11`} aria-label={`Corregir ${l.product.name}`} onClick={() => fix(l.index)}>Corregir</button>
                      ) : null}
                    </div>
                    {l.errors.length ? (
                      <ul role="alert" className="mt-2 list-disc space-y-1 pl-5 text-xs text-danger">{l.errors.map((e) => <li key={e}>{e}</li>)}</ul>
                    ) : null}
                    {l.duplicates.length ? (
                      <div role="alert" className="mt-2 text-xs text-danger">
                        <p className="font-semibold">Ya existen en el sistema:</p>
                        <ul className="list-disc pl-5 font-mono">{l.duplicates.map((d) => <li key={d}>{d}</li>)}</ul>
                      </div>
                    ) : null}
                    {l.warnings.length ? (
                      <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-warning">{l.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
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

          {failure ? <ErrorBox message={failure} /> : null}
        </section>
      ) : null}

      {step === 4 && result ? (
        <ReceivingDone result={result} access={access} onAnother={another} />
      ) : null}

      {step < 4 ? (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-bd-border pt-4">
          <button
            type="button" className={`${internalButtonClass} min-h-11`}
            disabled={step === 1 || busy}
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

function ReceivingDone({ result, access, onAnother }: { result: ReceiptResult; access: ReceivingAccess; onAnother: () => void }) {
  const labelFailed = result.label_error || result.lines.some((l) => l.label_error);
  const batch = extractLabelBatch({ label_batches: result.label_batches });
  const first = result.lines[0];
  const branch = typeof result.branch === "object" ? result.branch.name : "";
  const supplier = result.supplier && typeof result.supplier === "object" ? result.supplier.name : "";
  return (
    <section aria-labelledby="step-done" className="space-y-4">
      <div className="rounded-xl border border-success-border bg-success-surface/40 p-4 sm:p-6">
        <h2 id="step-done" className="text-base font-semibold text-foreground">Recepción registrada</h2>
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
          <Link href="/admin/inventory/labels" className={`${internalButtonClass} mt-3 inline-flex min-h-11 items-center`}>Ir a las etiquetas</Link>
        </div>
      ) : null}

      {first ? (
        <NextStepBar product={{ id: first.product.id, name: first.product.name }} labelBatch={batch}>
          <button type="button" className={`${internalPrimaryButtonClass} min-h-11`} onClick={onAnother}>Registrar otra recepción</button>
        </NextStepBar>
      ) : null}

      <div className="flex flex-wrap gap-2">
        {batch && !labelFailed ? (
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
