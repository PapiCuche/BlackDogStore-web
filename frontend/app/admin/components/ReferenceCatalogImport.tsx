"use client";

/**
 * Productos › Modelos › Catálogo de referencia — CATALOG-REFERENCE-01 · PR E.
 *
 * The manufacturers' reference models (data verified on official pages, `backend/catalog_reference/`), seen from ONE
 * company: which it already imported, and what importing others would do.
 *
 *   ELEGIR → PREVISUALIZAR (no escribe) → IMPORTAR (una vez, con Idempotency-Key)
 *
 * The preview says, per model, `new` / `reused` / `conflict` / `pending_review` and the server's warnings (a reused
 * model whose variants would read «incompleta», say). A conflict is never imported: it is taken out of the selection.
 * A model that is not `complete` is imported only with its own approval box ticked. NOTHING IS PUBLISHED: what is
 * created is hidden, without variants, prices, pictures or stock.
 *
 * Seeing and previewing need `products.view`; importing needs `products.manage`. The server checks both; here only
 * the controls are drawn or not.
 */

import Link from "next/link";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

import {
  applyReferenceImport, fetchReferenceModels, newIdempotencyKey, previewReferenceImport,
  type Completeness, type PreviewItem, type PreviewState, type ReferenceApplyResult, type ReferenceModel,
  type ReferencePreview,
} from "../lib/catalog-presentation-api";
import { ROLE_LABELS } from "../lib/palette";
import { EmptyBox, ErrorBox, Panel, Spinner } from "./InventoryUi";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";

const message = (error: unknown, fallback: string) => (error instanceof Error ? error.message : fallback);

/** What the server takes in one preview or import (`catalog_reference_import.MAX_KEYS`). */
export const MAX_SELECTION = 100;

export const COMPLETENESS_LABELS: Record<Completeness, string> = {
  complete: "Completo",
  partial: "Parcial",
  pending_review: "Por revisar",
};

export const STATE_LABELS: Record<PreviewState, string> = {
  new: "Nuevo",
  reused: "Se reutiliza",
  conflict: "Conflicto",
  pending_review: "Pendiente de revisión",
};

const STATE_TONE: Record<PreviewState, string> = {
  new: "border-foreground/30 text-foreground",
  reused: "border-success-border text-success",
  conflict: "border-danger-border bg-danger-surface text-danger",
  pending_review: "border-warning-border bg-warning-surface text-warning",
};

const COMPLETENESS_TONE: Record<Completeness, string> = {
  complete: "border-bd-border text-muted",
  partial: "border-warning-border bg-warning-surface text-warning",
  pending_review: "border-warning-border bg-warning-surface text-warning",
};

/** A line of the dataset (`pro_max`) as words; the vocabulary is the dataset's, not this screen's. */
export function lineLabel(line: string): string {
  const text = line.replace(/_/g, " ").trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : "—";
}

const fold = (text: string) => text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

const touch = "min-h-[44px]";

function Badge({ tone, children }: { tone: string; children: React.ReactNode }) {
  return (
    <span className={`inline-flex shrink-0 items-center whitespace-nowrap rounded-full border px-2.5 py-1 text-[11px] font-semibold ${tone}`}>
      {children}
    </span>
  );
}

type Filters = { search: string; line: string; generation: string; completeness: string; onlyNew: boolean };

const NO_FILTERS: Filters = { search: "", line: "", generation: "", completeness: "", onlyNew: false };

export function ReferenceCatalogImport({ canManage, companyId }: { canManage: boolean; companyId: number | null }) {
  const [models, setModels] = useState<ReferenceModel[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [selected, setSelected] = useState<string[]>([]);
  const [preview, setPreview] = useState<ReferencePreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [approved, setApproved] = useState<Set<string>>(new Set());
  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [result, setResult] = useState<ReferenceApplyResult | null>(null);
  // One key per previewed selection: a retry of the same «Importar» is the same request, never a second import.
  const idempotency = useRef<string>("");
  const previewHeading = useRef<HTMLHeadingElement>(null);
  const resultRef = useRef<HTMLDivElement>(null);
  const ids = useId();

  const load = useCallback(() => {
    fetchReferenceModels(companyId).then((data) => { setModels(data); setLoadError(null); }, (error) => setLoadError(message(error, "No se pudo leer el catálogo de referencia.")));
  }, [companyId]);

  useEffect(() => { load(); }, [load]);

  const lines = useMemo(() => [...new Set((models ?? []).map((m) => m.line))].sort(), [models]);
  const generations = useMemo(
    () => [...new Set((models ?? []).map((m) => String(m.generation ?? "")).filter(Boolean))]
      .sort((a, b) => a.localeCompare(b, "es", { numeric: true })),
    [models],
  );

  const shown = useMemo(() => {
    const term = fold(filters.search.trim());
    return (models ?? []).filter((model) => (
      (!term || fold(model.name).includes(term) || model.key.includes(term))
      && (!filters.line || model.line === filters.line)
      && (!filters.generation || String(model.generation ?? "") === filters.generation)
      && (!filters.completeness || model.completeness === filters.completeness)
      && (!filters.onlyNew || model.linked_model_id === null)
    ));
  }, [models, filters]);

  const byKey = useMemo(() => new Map((models ?? []).map((m) => [m.key, m] as const)), [models]);

  function resetPreview() {
    setPreview(null);
    setPreviewError(null);
    setApplyError(null);
    setApproved(new Set());
    idempotency.current = "";
  }

  function toggle(key: string, on: boolean) {
    setResult(null);
    resetPreview();
    setSelected((current) => (on ? (current.includes(key) || current.length >= MAX_SELECTION ? current : [...current, key])
      : current.filter((k) => k !== key)));
  }

  function selectShown() {
    setResult(null);
    resetPreview();
    setSelected((current) => {
      const next = [...current];
      for (const model of shown) {
        if (next.length >= MAX_SELECTION) break;
        if (!next.includes(model.key)) next.push(model.key);
      }
      return next;
    });
  }

  async function runPreview(keys = selected) {
    if (!keys.length || previewing) return;
    setPreviewing(true);
    setPreviewError(null);
    setApplyError(null);
    setResult(null);
    try {
      const answer = await previewReferenceImport(keys, companyId);
      setPreview(answer);
      setApproved(new Set());
      idempotency.current = newIdempotencyKey();
      requestAnimationFrame(() => previewHeading.current?.focus());
    } catch (error) {
      setPreview(null);
      setPreviewError(message(error, "No se pudo previsualizar la importación."));
    } finally {
      setPreviewing(false);
    }
  }

  function dropFromSelection(key: string) {
    const next = selected.filter((k) => k !== key);
    setSelected(next);
    resetPreview();
    if (next.length) void runPreview(next);
  }

  const items = preview?.items ?? [];
  const conflicts = items.filter((item) => item.state === "conflict");
  const unapproved = items.filter((item) => item.needs_approval && item.state !== "conflict" && !approved.has(item.key));
  const nothingToDo = items.length > 0 && items.every((item) => !item.changes);
  const blocked = conflicts.length > 0 || unapproved.length > 0;

  async function apply() {
    if (!preview || blocked || applying || !canManage) return;
    setApplying(true);
    setApplyError(null);
    try {
      const keys = items.map((item) => item.key);
      const approve = items.filter((item) => item.needs_approval && approved.has(item.key)).map((item) => item.key);
      const answer = await applyReferenceImport(keys, approve, idempotency.current || newIdempotencyKey(), companyId);
      setResult(answer);
      setSelected([]);
      setPreview(null);
      setApproved(new Set());
      idempotency.current = "";
      load();
      requestAnimationFrame(() => resultRef.current?.focus());
    } catch (error) {
      setApplyError(message(error, "No se pudo importar."));
    } finally {
      setApplying(false);
    }
  }

  return (
    <div className="space-y-6">
      <p className="text-sm">
        <Link href="/admin/products/models" className="text-muted underline-offset-4 hover:text-foreground hover:underline">← Todos los modelos</Link>
      </p>

      <p role="note" className="rounded-lg border border-bd-border bg-surface px-4 py-3 text-sm text-muted">
        Importar crea el modelo con sus capacidades, colores, SIM y regiones. <strong className="text-foreground">Nada se publica:</strong>{" "}
        el modelo queda oculto, sin variantes, sin precios, sin imágenes y sin stock. Lo que ya tienes no se renombra ni se borra.
      </p>

      {result ? (
        <div ref={resultRef} tabIndex={-1} role="status" className="space-y-2 rounded-lg border border-success-border px-4 py-3 text-sm text-success focus:outline-none">
          <p className="font-semibold">
            {result.replayed ? "Esta importación ya se había hecho: no se repitió." : "Importación terminada."}
          </p>
          <p className="text-foreground">
            {result.created.models.length} modelo(s) nuevo(s) · {result.linked.models} unido(s) a uno que ya tenías ·{" "}
            {result.palette_added} opción(es) añadida(s) a paletas · {result.exclusions_added} combinación(es) marcada(s) como inexistente(s).
          </p>
          {result.items.length ? (
            <ul className="flex flex-wrap gap-2 text-foreground">
              {result.items.map((item) => (
                <li key={item.key}>
                  <Link href={`/admin/products/models/${item.model_id}`} className="underline underline-offset-4">
                    {byKey.get(item.key)?.name ?? item.key}
                  </Link>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      <Panel
        id="referencia"
        title="Modelos de referencia"
        description="Elige los modelos que vendes. Los ya importados enlazan a su modelo."
      >
        {loadError ? (
          <div className="space-y-2">
            <ErrorBox message={loadError} />
            <button type="button" className={`${touch} ${internalButtonClass}`} onClick={load}>Reintentar</button>
          </div>
        ) : models === null ? <Spinner label="Cargando el catálogo de referencia…" /> : models.length === 0 ? (
          <EmptyBox message="El catálogo de referencia está vacío: todavía no se cargó en este servidor." />
        ) : (
          <div className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" role="search" aria-label="Filtrar modelos de referencia">
              <label className="block text-xs text-muted sm:col-span-2 lg:col-span-1">
                Buscar
                <input
                  type="search"
                  value={filters.search}
                  onChange={(e) => setFilters({ ...filters, search: e.target.value })}
                  placeholder="Nombre del modelo"
                  className={`mt-1.5 ${internalInputClass}`}
                />
              </label>
              <label className="block text-xs text-muted">
                Línea
                <select value={filters.line} onChange={(e) => setFilters({ ...filters, line: e.target.value })} className={`mt-1.5 ${internalInputClass}`}>
                  <option value="">Todas</option>
                  {lines.map((line) => <option key={line} value={line}>{lineLabel(line)}</option>)}
                </select>
              </label>
              <label className="block text-xs text-muted">
                Generación
                <select value={filters.generation} onChange={(e) => setFilters({ ...filters, generation: e.target.value })} className={`mt-1.5 ${internalInputClass}`}>
                  <option value="">Todas</option>
                  {generations.map((generation) => <option key={generation} value={generation}>{generation}</option>)}
                </select>
              </label>
              <label className="block text-xs text-muted">
                Datos
                <select value={filters.completeness} onChange={(e) => setFilters({ ...filters, completeness: e.target.value })} className={`mt-1.5 ${internalInputClass}`}>
                  <option value="">Todos</option>
                  {(Object.keys(COMPLETENESS_LABELS) as Completeness[]).map((value) => (
                    <option key={value} value={value}>{COMPLETENESS_LABELS[value]}</option>
                  ))}
                </select>
              </label>
              <label className={`flex items-center gap-2 text-sm text-foreground sm:col-span-2 lg:col-span-4 ${touch}`}>
                <input type="checkbox" className="h-4 w-4" checked={filters.onlyNew} onChange={(e) => setFilters({ ...filters, onlyNew: e.target.checked })} />
                Sólo los que aún no importé
              </label>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3 border-y border-bd-border py-3">
              <p className="text-sm text-muted" aria-live="polite">
                {shown.length} de {models.length} modelos · <strong className="text-foreground">{selected.length} elegido(s)</strong>
                {selected.length >= MAX_SELECTION ? ` (máximo ${MAX_SELECTION} por importación)` : ""}
              </p>
              <div className="flex flex-wrap gap-2">
                <button type="button" className={`${touch} ${internalButtonClass}`} onClick={selectShown} disabled={!shown.length || selected.length >= MAX_SELECTION}>
                  Elegir los mostrados
                </button>
                {selected.length ? (
                  <button type="button" className={`${touch} ${internalButtonClass}`} onClick={() => { setSelected([]); resetPreview(); }}>
                    Quitar la selección
                  </button>
                ) : null}
                <button
                  type="button"
                  className={`${touch} ${internalPrimaryButtonClass}`}
                  onClick={() => void runPreview()}
                  disabled={!selected.length || previewing}
                >
                  {previewing ? "Previsualizando…" : `Previsualizar (${selected.length})`}
                </button>
              </div>
            </div>

            {shown.length === 0 ? <EmptyBox message="Ningún modelo coincide con los filtros." /> : (
              <ul aria-label="Modelos de referencia" className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                {shown.map((model) => {
                  const id = `${ids}-${model.key}`;
                  const checked = selected.includes(model.key);
                  return (
                    <li key={model.key} className={`flex min-w-0 items-start gap-3 rounded-lg border px-3 py-2.5 ${checked ? "border-foreground/40 bg-foreground/[0.03]" : "border-bd-border"}`}>
                      <input
                        id={id}
                        type="checkbox"
                        className="mt-1 h-5 w-5 shrink-0"
                        checked={checked}
                        disabled={!checked && selected.length >= MAX_SELECTION}
                        onChange={(e) => toggle(model.key, e.target.checked)}
                      />
                      <div className="min-w-0 flex-1">
                        <label htmlFor={id} className="block cursor-pointer break-words text-sm font-semibold text-foreground">{model.name}</label>
                        <p className="mt-0.5 text-xs text-muted">
                          {lineLabel(model.line)}
                          {model.generation ? ` · generación ${model.generation}` : ""}
                          {model.announced ? ` · ${model.announced.slice(0, 4)}` : ""}
                        </p>
                        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                          <Badge tone={COMPLETENESS_TONE[model.completeness]}>{COMPLETENESS_LABELS[model.completeness]}</Badge>
                          {model.linked_model_id !== null ? (
                            <Link href={`/admin/products/models/${model.linked_model_id}`} className="text-xs font-semibold text-foreground underline underline-offset-4">
                              Importado · ver modelo
                            </Link>
                          ) : null}
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        )}
      </Panel>

      {previewError ? <ErrorBox message={previewError} /> : null}

      {preview ? (
        <section aria-labelledby={`${ids}-preview`} className="min-w-0 rounded-xl border border-bd-border bg-surface">
          <header className="flex flex-wrap items-start justify-between gap-3 border-b border-bd-border px-5 py-4">
            <div className="min-w-0">
              <h2 id={`${ids}-preview`} ref={previewHeading} tabIndex={-1} className="text-sm font-semibold text-foreground focus:outline-none">
                Vista previa de la importación
              </h2>
              <p className="mt-0.5 text-xs text-muted">Todavía no se guardó nada.</p>
            </div>
            <ul className="flex flex-wrap gap-1.5" aria-label="Resumen de la vista previa">
              {(Object.keys(STATE_LABELS) as PreviewState[]).map((state) => (
                <li key={state}><Badge tone={STATE_TONE[state]}>{STATE_LABELS[state]}: {preview.counts?.[state] ?? 0}</Badge></li>
              ))}
            </ul>
          </header>
          <div className="space-y-4 p-5">
            {preview.attributes?.some((a) => a.status !== "reused") ? (
              <ul className="space-y-1 text-xs text-muted" aria-label="Atributos">
                {preview.attributes.filter((a) => a.status !== "reused").map((a) => (
                  <li key={a.role} className={a.status === "conflict" ? "text-danger" : ""}>
                    {a.status === "new" ? `Se creará el atributo «${a.name}» (${ROLE_LABELS[a.role] ?? a.role}).` : a.reason}
                  </li>
                ))}
              </ul>
            ) : null}

            <ol className="space-y-3" aria-label="Modelos a importar">
              {items.map((item) => (
                <PreviewRow
                  key={item.key}
                  item={item}
                  approved={approved.has(item.key)}
                  onApprove={(on) => setApproved((current) => {
                    const next = new Set(current);
                    if (on) next.add(item.key); else next.delete(item.key);
                    return next;
                  })}
                  onDrop={() => dropFromSelection(item.key)}
                  canManage={canManage}
                />
              ))}
            </ol>

            {applyError ? <p role="alert" className="rounded-lg border border-danger-border bg-danger-surface px-4 py-3 text-sm text-danger">{applyError}</p> : null}

            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-bd-border pt-4">
              <p className="min-w-0 flex-1 text-xs text-muted" id={`${ids}-apply-hint`}>
                {conflicts.length ? `${conflicts.length} con conflicto: quítalos de la selección o resuélvelos antes de importar. ` : ""}
                {unapproved.length ? `${unapproved.length} sin aprobar: marca «Apruebo importarlo así» en cada uno. ` : ""}
                {nothingToDo ? "Ya está todo importado: importar no cambiaría nada. " : ""}
                {!canManage ? "Para importar hace falta permiso para gestionar productos." : ""}
              </p>
              {canManage ? (
                <button
                  type="button"
                  className={`${touch} ${internalPrimaryButtonClass}`}
                  onClick={() => void apply()}
                  disabled={blocked || applying || items.length === 0}
                  aria-describedby={`${ids}-apply-hint`}
                >
                  {applying ? "Importando…" : `Importar ${items.length} modelo(s) ocultos`}
                </button>
              ) : null}
            </div>
          </div>
        </section>
      ) : null}
    </div>
  );
}

function PreviewRow({
  item, approved, onApprove, onDrop, canManage,
}: { item: PreviewItem; approved: boolean; onApprove: (on: boolean) => void; onDrop: () => void; canManage: boolean }) {
  const id = useId();
  const created = item.options.filter((o) => o.status === "new").length;
  const reused = item.options.filter((o) => o.status === "reused").length;
  return (
    <li className="rounded-lg border border-bd-border bg-background p-4" aria-labelledby={`${id}-name`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 id={`${id}-name`} className="break-words text-sm font-semibold text-foreground">{item.name}</h3>
          <p className="mt-0.5 text-xs text-muted">
            {item.model_status === "new" ? "Se creará oculto." : null}
            {item.model_status === "reused" && item.model_id ? (
              <>
                {item.link_model ? "Se unirá a tu modelo " : "Ya es tu modelo "}
                <Link href={`/admin/products/models/${item.model_id}`} className="text-foreground underline underline-offset-4">{item.model_name}</Link>
                {": su nombre, precio, fotos y publicación no cambian."}
              </>
            ) : null}
          </p>
        </div>
        <Badge tone={STATE_TONE[item.state]}>{STATE_LABELS[item.state]}</Badge>
      </div>

      <p className="mt-2 text-xs text-muted">
        {created} opción(es) nueva(s) · {reused} reutilizada(s)
        {item.palette_add.length ? ` · ${item.palette_add.length} a la paleta` : ""}
        {item.exclusions_add ? ` · ${item.exclusions_add} combinación(es) que no existen` : ""}
        {!item.changes ? " · sin cambios" : ""}
      </p>

      {item.reason ? <p className="mt-2 text-sm text-danger">{item.reason}</p> : null}

      {item.warnings.length ? (
        <ul className="mt-2 space-y-1" aria-label={`Avisos de ${item.name}`}>
          {item.warnings.map((warning) => (
            <li key={warning} role="note" className="rounded-lg border border-warning-border bg-warning-surface px-3 py-2 text-xs text-warning">{warning}</li>
          ))}
        </ul>
      ) : null}

      {item.needs_approval && item.state !== "conflict" ? (
        <div className="mt-3 rounded-lg border border-warning-border bg-warning-surface px-3 py-2 text-xs text-warning">
          <p>
            Los datos de este modelo están {COMPLETENESS_LABELS[item.completeness].toLowerCase()} en el catálogo de referencia
            {item.missing.length ? `: falta ${item.missing.join(", ")}` : ""}. Revisa lo que importas.
          </p>
          {canManage ? (
            <label className={`mt-1 flex items-center gap-2 font-semibold text-foreground ${touch}`}>
              <input type="checkbox" className="h-4 w-4" checked={approved} onChange={(e) => onApprove(e.target.checked)} />
              Apruebo importarlo así
            </label>
          ) : null}
        </div>
      ) : null}

      {item.state === "conflict" ? (
        <button type="button" className={`mt-3 ${touch} ${internalButtonClass}`} onClick={onDrop}>
          Quitar {item.name} de la selección
        </button>
      ) : null}
    </li>
  );
}
