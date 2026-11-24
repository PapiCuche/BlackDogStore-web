"use client";

/**
 * Productos › Modelos — DEVICE-COMMERCE-01 · DC2.
 *
 * A model groups the products that are options of one device (capacity,
 * colour…) so the shop shows ONE page for it. This screen lists the models and
 * keeps the pieces they are built from: the attributes and their options, the
 * cosmetic grade scale for used devices, and the names of the two lines.
 *
 * Seeing needs `products.view`; every write needs `products.manage`, which the
 * server checks on each request. Without it the forms are simply not drawn.
 */

import Link from "next/link";
import { useCallback, useEffect, useId, useRef, useState } from "react";

import { fetchAdminCategories, type AdminCategory } from "@/app/lib/admin";
import {
  DEFAULT_LINE_LABELS,
  createAdminCatalogModel,
  createAttributeOption,
  createCatalogAttribute,
  createCosmeticGrade,
  fetchAdminCatalogModelPage,
  fetchCatalogAttributes,
  fetchCatalogSettings,
  fetchCosmeticGrades,
  isValidSwatchHex,
  seedCosmeticGrades,
  updateCosmeticGrade,
  updateAttributeOption,
  updateCatalogAttribute,
  updateCatalogSettings,
  type AdminCatalogAttribute,
  type AdminCatalogModel,
  type AdminAttributeOption,
  type CatalogModelFlag,
  type CatalogModelPage,
  type AttributeRole,
  type CatalogSettings,
  type CosmeticGradeRow,
} from "../lib/internal-api";
import { ROLE_LABELS, ROLE_ORDER } from "../lib/palette";
import {
  EMPTY_MODEL_LIST_STATE, MODEL_FLAGS, MODEL_LIST_PAGE_SIZE, isFiltered, modelListQuery, readModelListState,
  writeModelListState, type ModelListState,
} from "../lib/model-list-query";
import { SWATCH_APPROXIMATE_CHECKBOX, SWATCH_APPROXIMATE_LABEL } from "../lib/catalog-presentation-api";
import { DESCRIPTION_MAX_LENGTH } from "@/app/lib/markdown-lite";
import { ClaimsHint } from "./ClaimsHint";
import { DescriptionEditor } from "./DescriptionEditor";
import { CatalogAliasesSection, ResolverTryPanel } from "./CatalogVocabulary";
import { EmptyBox, ErrorBox, Panel, Spinner, TableWrap, Td, Th } from "./InventoryUi";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";

const message = (error: unknown, fallback: string) => (error instanceof Error ? error.message : fallback);

// ---------------------------------------------------------------------------
// Swatch input — a #RRGGBB field with its colour beside it
// ---------------------------------------------------------------------------

export const SWATCH_ERROR = "Usa el formato #RRGGBB, por ejemplo #1F2937.";

export function SwatchInput({
  value, onChange, label = "Color (#RRGGBB)", error,
}: {
  value: string;
  onChange: (value: string) => void;
  label?: string;
  /** Shown under the field; the parent decides when (on submit, not while typing). */
  error?: string | null;
}) {
  const id = useId();
  const errorId = `${id}-error`;
  const valid = isValidSwatchHex(value);
  return (
    <div>
      <label htmlFor={id} className="block text-xs text-muted">{label}</label>
      <div className="mt-1.5 flex items-center gap-2">
        <span
          data-testid="swatch-preview"
          aria-hidden="true"
          className="h-9 w-9 shrink-0 rounded-full border border-foreground/25"
          style={{ backgroundColor: valid ? value : "transparent" }}
        />
        <input
          id={id}
          value={value}
          onChange={(event) => onChange(event.target.value.trim())}
          placeholder="#1F2937"
          spellCheck={false}
          maxLength={7}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
          className={`font-mono ${internalInputClass}`}
        />
      </div>
      {error ? <p id={errorId} role="alert" className="mt-1 text-xs text-danger">{error}</p> : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Models
// ---------------------------------------------------------------------------

const rowActionClass =
  "inline-flex min-h-9 items-center rounded-lg border border-bd-border px-2.5 py-1.5 text-xs font-semibold text-foreground transition-colors hover:border-foreground/25 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2";

/** CATALOG-ADMIN-UX-B — the state chips of the list, as the panel says them. */
export const MODEL_FLAG_LABELS: Record<CatalogModelFlag, string> = {
  incomplete: "Variantes incompletas",
  missing: "Combinaciones sin variante",
  no_palette: "Sin paleta",
};

const MODEL_FLAG_HINTS: Record<CatalogModelFlag, string> = {
  incomplete: "Modelos con variantes a las que les falta una opción de la paleta o que llevan una que la paleta ya no admite.",
  missing: "Modelos cuya paleta tiene combinaciones válidas que todavía no son una variante.",
  no_palette: "Modelos que todavía no dicen qué opciones admiten: sus variantes podrían combinar cualquier opción de la empresa.",
};

const chipClass = (on: boolean) =>
  `inline-flex min-h-[44px] items-center gap-1.5 rounded-full border px-3 text-xs font-semibold transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 ${
    on ? "border-foreground/40 bg-foreground/[0.06] text-foreground" : "border-bd-border text-muted hover:border-foreground/25"
  }`;

/** What a model's palette asks for, on its row of the list (nothing when all is in order). */
function ModelPaletteState({ model }: { model: AdminCatalogModel }) {
  const badges: { text: string; tone: "warning" | "muted" }[] = [];
  if (model.has_palette === false) badges.push({ text: "Sin paleta", tone: "warning" });
  const broken = (model.incomplete_variant_count ?? 0) + (model.outside_variant_count ?? 0);
  if (broken > 0) badges.push({ text: broken === 1 ? "1 variante incompleta" : `${broken} variantes incompletas`, tone: "warning" });
  const missing = model.valid_without_variant ?? 0;
  if (missing > 0) badges.push({ text: missing === 1 ? "1 combinación sin variante" : `${missing} combinaciones sin variante`, tone: "muted" });
  if (badges.length === 0) return model.has_palette ? <span className="text-xs text-muted">En orden</span> : <span className="text-muted">—</span>;
  return (
    <span className="flex flex-col items-start gap-1">
      {badges.map((badge) => (
        <span
          key={badge.text}
          className={`inline-flex min-h-6 items-center whitespace-nowrap rounded-full border px-2 text-[11px] font-semibold ${
            badge.tone === "warning" ? "border-warning-border bg-warning-surface text-warning" : "border-bd-border text-muted"
          }`}
        >
          {badge.text}
        </span>
      ))}
    </span>
  );
}

function ModelsSection({ canManage, companyId, onLoaded }: {
  canManage: boolean; companyId: number | null; onLoaded?: () => void;
}) {
  // CATALOG-ADMIN-UX-B: the search and the filters live in the address (`model-list-query`); the server filters.
  // This section mounts in the browser once the session is known, so the address can be read here.
  const [query, setQuery] = useState<ModelListState>(
    () => (typeof window === "undefined" ? EMPTY_MODEL_LIST_STATE : readModelListState(window.location.search)),
  );
  const [typed, setTyped] = useState(query.search);
  const [page, setPage] = useState<CatalogModelPage | null>(null);
  const models: AdminCatalogModel[] | null = page ? page.results : null;
  const [loading, setLoading] = useState(false);
  const [categories, setCategories] = useState<AdminCategory[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [fields, setFields] = useState<Record<string, string>>({});
  const [draft, setDraft] = useState({ name: "", category_id: "", slug: "", description: "", image_url: "", is_published_online: true });
  const ids = useId();
  // Only the newest read may land: typing fast sends several, and an older answer must not overwrite a newer one.
  const request = useRef(0);
  const onLoadedRef = useRef(onLoaded);
  useEffect(() => { onLoadedRef.current = onLoaded; });

  const load = useCallback(() => {
    const mine = ++request.current;
    setLoading(true);
    fetchAdminCatalogModelPage(modelListQuery(query), companyId).then(
      (answer) => {
        if (mine !== request.current) return;
        // Review of #411: a page past the end (fewer results since the address was written) goes back to the first.
        if (answer.results.length === 0 && answer.count > 0 && query.page > 1) {
          setQuery((current) => ({ ...current, page: 1 }));
          return;
        }
        setPage(answer); setError(null); setLoading(false); onLoadedRef.current?.();
      },
      (err) => {
        if (mine !== request.current) return;
        setError(message(err, "No se pudieron cargar los modelos.")); setLoading(false); onLoadedRef.current?.();
      },
    );
  }, [companyId, query]);

  useEffect(() => { load(); }, [load]);
  // The address follows the list (without a new history entry): a reload or a shared link shows the same list.
  useEffect(() => {
    const next = writeModelListState(window.location.search, query);
    if (next !== window.location.search) {
      window.history.replaceState(window.history.state, "", `${window.location.pathname}${next}${window.location.hash}`);
    }
  }, [query]);
  // Typing searches on its own after a short pause (and from the first page).
  useEffect(() => {
    if (typed === query.search) return;
    const timer = window.setTimeout(() => setQuery((current) => ({ ...current, search: typed, page: 1 })), 300);
    return () => window.clearTimeout(timer);
  }, [typed, query.search]);
  useEffect(() => {
    fetchAdminCategories(companyId).then(setCategories, () => { /* the form still works without the list */ });
  }, [companyId]);

  function change(patch: Partial<ModelListState>) {
    setQuery((current) => ({ ...current, ...patch, page: patch.page ?? 1 }));
  }

  function toggleFlag(flag: CatalogModelFlag) {
    setQuery((current) => ({
      ...current, page: 1,
      flags: current.flags.includes(flag) ? current.flags.filter((f) => f !== flag) : [...current.flags, flag],
    }));
  }

  function clearFilters() {
    setTyped("");
    setQuery(EMPTY_MODEL_LIST_STATE);
  }

  const total = page?.count ?? 0;
  const pages = Math.max(1, Math.ceil(total / MODEL_LIST_PAGE_SIZE));
  const filtered = isFiltered(query);

  async function create(event: React.FormEvent) {
    event.preventDefault();
    if (!draft.name.trim()) { setFields({ name: "Escribe el nombre del modelo." }); return; }
    setSaving(true);
    setFields({});
    setError(null);
    try {
      await createAdminCatalogModel({
        name: draft.name.trim(),
        category_id: draft.category_id ? Number(draft.category_id) : null,
        ...(draft.slug.trim() ? { slug: draft.slug.trim() } : {}),
        description: draft.description,
        image_url: draft.image_url.trim(),
        is_published_online: draft.is_published_online,
      }, companyId);
      setDraft({ name: "", category_id: "", slug: "", description: "", image_url: "", is_published_online: true });
      setCreating(false);
      load();
    } catch (err) {
      setError(message(err, "No se pudo crear el modelo."));
      const withFields = err as { fields?: Record<string, string>; field?: string | null };
      if (withFields.fields) setFields(withFields.fields);
      if (withFields.field) setFields((f) => ({ ...f, [withFields.field as string]: message(err, "") }));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Panel
      title="Modelos"
      description="Cada modelo reúne los productos que son opciones de un mismo equipo y los muestra en una sola página de la tienda."
      action={canManage && !creating ? (
        <button type="button" className={internalPrimaryButtonClass} onClick={() => setCreating(true)}>+ Nuevo modelo</button>
      ) : null}
    >
      {canManage && creating ? (
        <form onSubmit={create} className="mb-6 grid gap-3 rounded-lg border border-bd-border bg-background p-4 md:grid-cols-2" aria-label="Nuevo modelo">
          <label className="block text-xs text-muted">
            Nombre
            <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} className={`mt-1.5 ${internalInputClass}`} aria-invalid={fields.name ? true : undefined} />
            {fields.name ? <span className="mt-1 block text-danger">{fields.name}</span> : null}
          </label>
          <label className="block text-xs text-muted">
            Categoría
            <select value={draft.category_id} onChange={(e) => setDraft({ ...draft, category_id: e.target.value })} className={`mt-1.5 ${internalInputClass}`}>
              <option value="">Sin categoría</option>
              {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
          <label className="block text-xs text-muted">
            Dirección (opcional)
            <input value={draft.slug} onChange={(e) => setDraft({ ...draft, slug: e.target.value })} placeholder="Se genera desde el nombre" className={`mt-1.5 font-mono ${internalInputClass}`} />
            {fields.slug ? <span className="mt-1 block text-danger">{fields.slug}</span> : null}
          </label>
          <label className="block text-xs text-muted">
            Imagen (dirección)
            <input value={draft.image_url} onChange={(e) => setDraft({ ...draft, image_url: e.target.value })} className={`mt-1.5 ${internalInputClass}`} />
          </label>
          <div className="md:col-span-2">
            <DescriptionEditor
              id="new-model-description"
              label="Descripción"
              value={draft.description}
              onChange={(description) => setDraft({ ...draft, description })}
              disabled={saving}
              rows={6}
            />
            {fields.description ? <span className="mt-1 block text-xs text-danger">{fields.description}</span> : null}
          </div>
          <label className="flex items-center gap-2 text-sm text-foreground">
            <input type="checkbox" checked={draft.is_published_online} onChange={(e) => setDraft({ ...draft, is_published_online: e.target.checked })} />
            Publicado en la tienda en línea
          </label>
          <div className="flex justify-end gap-2 md:col-span-2">
            <button type="button" className={internalButtonClass} onClick={() => { setCreating(false); setFields({}); }} disabled={saving}>Cancelar</button>
            <button type="submit" className={internalPrimaryButtonClass} disabled={saving || draft.description.length > DESCRIPTION_MAX_LENGTH}>{saving ? "Creando…" : "Crear modelo"}</button>
          </div>
        </form>
      ) : null}

      {/* CATALOG-ADMIN-UX-B: search and filters, answered by the server; the address keeps them. */}
      <div role="search" aria-label="Buscar modelos" className="mb-4 space-y-3">
        <div className="grid gap-3 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)]">
          <label className="block text-xs text-muted">
            Buscar
            <input
              type="search"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); change({ search: typed }); } }}
              placeholder="Nombre, dirección o alias de proveedor"
              maxLength={100}
              aria-describedby={`${ids}-total`}
              className={`mt-1.5 min-h-[44px] ${internalInputClass}`}
            />
          </label>
          <label className="block text-xs text-muted">
            Categoría
            <select
              value={query.category}
              onChange={(e) => change({ category: e.target.value })}
              className={`mt-1.5 min-h-[44px] ${internalInputClass}`}
            >
              <option value="">Todas</option>
              <option value="none">Sin categoría</option>
              {categories.map((c) => <option key={c.id} value={String(c.id)}>{c.name}</option>)}
            </select>
          </label>
          <label className="block text-xs text-muted">
            Estado
            <select
              value={query.active}
              onChange={(e) => change({ active: e.target.value as ModelListState["active"] })}
              className={`mt-1.5 min-h-[44px] ${internalInputClass}`}
            >
              <option value="">Activos e inactivos</option>
              <option value="true">Activos</option>
              <option value="false">Inactivos</option>
            </select>
          </label>
        </div>
        <div role="group" aria-label="Filtrar por estado de la paleta" className="flex flex-wrap items-center gap-2">
          {MODEL_FLAGS.map((flag) => {
            const on = query.flags.includes(flag);
            const count = page?.facets?.[flag];
            return (
              <button
                key={flag}
                type="button"
                aria-pressed={on}
                title={MODEL_FLAG_HINTS[flag]}
                aria-describedby={`${ids}-${flag}`}
                onClick={() => toggleFlag(flag)}
                className={chipClass(on)}
              >
                {MODEL_FLAG_LABELS[flag]}
                {typeof count === "number" ? <span className="font-normal text-muted">({count})</span> : null}
                <span id={`${ids}-${flag}`} className="sr-only">{MODEL_FLAG_HINTS[flag]}</span>
              </button>
            );
          })}
          {filtered ? (
            <button type="button" className={`min-h-[44px] ${internalButtonClass}`} onClick={clearFilters}>Limpiar filtros</button>
          ) : null}
        </div>
        <p id={`${ids}-total`} role="status" aria-live="polite" className="text-xs text-muted">
          {page === null ? "" : loading ? "Buscando…" : total === 1 ? "1 modelo" : `${total} modelos`}
          {page && !loading && (page.facets?.no_palette ?? 0) > 0 && !query.flags.includes("no_palette")
            ? ` · ${page.facets?.no_palette === 1 ? "1 sin paleta" : `${page.facets?.no_palette} sin paleta`}: sus variantes podrían combinar cualquier opción.`
            : ""}
        </p>
      </div>

      {error ? <div className="mb-4"><ErrorBox message={error} /></div> : null}
      {models === null && !error ? <Spinner label="Cargando modelos…" /> : null}
      {models && models.length === 0 ? (
        <EmptyBox message={filtered
          ? "Ningún modelo coincide con la búsqueda o los filtros."
          : canManage ? "Todavía no hay modelos. Crea uno y agrégale sus productos como variantes." : "Todavía no hay modelos."} />
      ) : null}
      {models && models.length > 0 ? (
        <TableWrap>
          <thead>
            <tr className="border-b border-bd-border">
              <Th>Modelo</Th><Th>Categoría</Th><Th>Paleta</Th><Th right>Variantes</Th><Th right>Seminuevos</Th><Th>Tienda</Th>
              <Th right>Acciones</Th>
            </tr>
          </thead>
          <tbody>
            {models.map((model) => (
              <tr key={model.id} className="border-b border-bd-border/60">
                <Td>
                  <Link href={`/admin/products/models/${model.id}`} className="font-semibold underline underline-offset-4 decoration-foreground/30 hover:decoration-foreground">{model.name}</Link>
                  <span className="block font-mono text-xs text-muted">{model.slug}</span>
                </Td>
                <Td muted>{model.category_name || "—"}</Td>
                <Td><ModelPaletteState model={model} /></Td>
                <Td right>{model.variant_count}</Td>
                <Td right>{model.used_unit_count}</Td>
                <Td muted>
                  {model.is_active === false ? "Inactivo" : model.is_published_online ? "Publicado" : "Oculto"}
                </Td>
                <Td right>
                  {/* CATALOG-EDITOR-FIX-01: what can be done with the row, said on the row. */}
                  <span className="inline-flex flex-wrap justify-end gap-1.5">
                    <Link href={`/admin/products/models/${model.id}`} className={rowActionClass}
                      aria-label={`Ver detalles de ${model.name}`}>Ver detalles</Link>
                    {canManage ? (
                      <>
                        <Link href={`/admin/products/models/${model.id}?editar=modelo`} className={rowActionClass}
                          aria-label={`Editar modelo ${model.name}`}>Editar modelo</Link>
                        <Link href={`/admin/products/models/${model.id}#variantes`} className={rowActionClass}
                          aria-label={`Gestionar variantes de ${model.name}`}>Gestionar variantes</Link>
                      </>
                    ) : null}
                  </span>
                </Td>
              </tr>
            ))}
          </tbody>
        </TableWrap>
      ) : null}
      {page && total > MODEL_LIST_PAGE_SIZE ? (
        <nav aria-label="Páginas de modelos" className="mt-4 flex flex-wrap items-center justify-between gap-2 text-sm">
          <button
            type="button"
            className={`min-h-[44px] ${internalButtonClass}`}
            disabled={query.page <= 1 || loading}
            onClick={() => change({ page: query.page - 1 })}
          >
            Anterior
          </button>
          <span className="text-muted">Página {Math.min(query.page, pages)} de {pages}</span>
          <button
            type="button"
            className={`min-h-[44px] ${internalButtonClass}`}
            disabled={query.page >= pages || loading}
            onClick={() => change({ page: query.page + 1 })}
          >
            Siguiente
          </button>
        </nav>
      ) : null}
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Attributes and options
// ---------------------------------------------------------------------------

function OptionChip({ option, kind }: { option: AdminAttributeOption; kind: "text" | "color" }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-bd-border px-2.5 py-1 text-xs text-foreground">
      {kind === "color" ? (
        <span aria-hidden="true" className="h-3.5 w-3.5 rounded-full border border-foreground/25" style={{ backgroundColor: option.swatch_hex || "transparent" }} />
      ) : null}
      {option.label}
      {kind === "color" && option.swatch_hex ? <span className="font-mono text-muted">{option.swatch_hex}</span> : null}
      {/* FASE 1.2 · Y: a swatch chosen as an approximation, pending commercial validation (panel only). */}
      {kind === "color" && option.swatch_is_approximate ? (
        <span title="Muestra aproximada, pendiente de validación comercial" className="rounded-full border border-warning-border bg-warning-surface px-1.5 text-[11px] font-semibold text-warning">
          {SWATCH_APPROXIMATE_LABEL}
        </span>
      ) : null}
    </span>
  );
}

/** Add or edit one option. Colour options must carry a valid #RRGGBB before anything is sent. */
function OptionForm({
  kind, initial, submitLabel, onSubmit, onCancel,
}: {
  kind: "text" | "color";
  initial?: { label: string; swatch_hex: string; sort_order?: number; swatch_is_approximate?: boolean };
  submitLabel: string;
  onSubmit: (body: { label: string; swatch_hex?: string; sort_order?: number; swatch_is_approximate?: boolean }) => Promise<void>;
  onCancel?: () => void;
}) {
  const [label, setLabel] = useState(initial?.label ?? "");
  const [hex, setHex] = useState(initial?.swatch_hex ?? "");
  // FASE 1.2 · Y: offered when EDITING a colour (the swatch is then the company's, shared by every model).
  const [approximate, setApproximate] = useState(initial?.swatch_is_approximate ?? false);
  const approximateId = useId();
  const [order, setOrder] = useState(initial?.sort_order !== undefined ? String(initial.sort_order) : "");
  const [hexError, setHexError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    if (!label.trim()) { setError("Escribe el nombre de la opción."); return; }
    if (kind === "color" && !isValidSwatchHex(hex)) { setHexError(SWATCH_ERROR); return; }
    setHexError(null);
    setSaving(true);
    try {
      await onSubmit({
        label: label.trim(),
        ...(kind === "color" ? { swatch_hex: hex.toUpperCase() } : {}),
        ...(initial && order.trim() !== "" && Number.isFinite(Number(order)) ? { sort_order: Number(order) } : {}),
        ...(initial && kind === "color" ? { swatch_is_approximate: approximate } : {}),
      });
      if (!initial) { setLabel(""); setHex(""); }
    } catch (err) {
      setError(message(err, "No se pudo guardar la opción."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-wrap items-end gap-3" noValidate>
      <label className="block min-w-40 flex-1 text-xs text-muted">
        Opción
        <input value={label} onChange={(e) => setLabel(e.target.value)} className={`mt-1.5 ${internalInputClass}`} />
      </label>
      {kind === "color" ? (
        <div className="min-w-48 flex-1">
          <SwatchInput value={hex} onChange={(v) => { setHex(v); if (hexError && isValidSwatchHex(v)) setHexError(null); }} error={hexError} />
        </div>
      ) : null}
      {initial && kind === "color" ? (
        <label htmlFor={approximateId} className="flex min-h-[44px] items-center gap-2 text-xs text-foreground">
          <input id={approximateId} type="checkbox" className="h-4 w-4" checked={approximate} onChange={(e) => setApproximate(e.target.checked)} />
          {SWATCH_APPROXIMATE_CHECKBOX}
        </label>
      ) : null}
      {initial ? (
        <label className="block w-24 text-xs text-muted">
          Orden
          <input type="number" value={order} onChange={(e) => setOrder(e.target.value)} className={`mt-1.5 ${internalInputClass}`} />
        </label>
      ) : null}
      <div className="flex gap-2">
        {onCancel ? <button type="button" className={internalButtonClass} onClick={onCancel} disabled={saving}>Cancelar</button> : null}
        <button type="submit" className={internalButtonClass} disabled={saving}>{saving ? "Guardando…" : submitLabel}</button>
      </div>
      {error ? <p role="alert" className="w-full text-xs text-danger">{error}</p> : null}
    </form>
  );
}

/** A1: the role of an attribute — what it means, whatever the company calls it. One per role per company, «otro» aside. */
const roleOptions = ROLE_ORDER.map((role) => <option key={role} value={role}>{ROLE_LABELS[role]}</option>);

function AttributesSection({ canManage, companyId }: { canManage: boolean; companyId: number | null }) {
  const [attributes, setAttributes] = useState<AdminCatalogAttribute[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ name: string; kind: "text" | "color"; role: AttributeRole }>({ name: "", kind: "text", role: "other" });
  const [editing, setEditing] = useState<number | null>(null);
  const [roleBusy, setRoleBusy] = useState<number | null>(null);

  const load = useCallback(() => {
    fetchCatalogAttributes(companyId).then(
      (rows) => { setAttributes(rows); setError(null); },
      (err) => setError(message(err, "No se pudieron cargar los atributos.")),
    );
  }, [companyId]);
  useEffect(() => { load(); }, [load]);

  async function create(event: React.FormEvent) {
    event.preventDefault();
    if (!draft.name.trim()) return;
    try {
      await createCatalogAttribute({ name: draft.name.trim(), kind: draft.kind, role: draft.role }, companyId);
      setDraft({ name: "", kind: "text", role: "other" });
      load();
    } catch (err) {
      setError(message(err, "No se pudo crear el atributo."));
    }
  }

  async function changeRole(attribute: AdminCatalogAttribute, role: AttributeRole) {
    setRoleBusy(attribute.id);
    setError(null);
    try {
      const saved = await updateCatalogAttribute(attribute.id, { role }, companyId);
      setAttributes((rows) => (rows ?? []).map((row) => (row.id === attribute.id ? { ...row, role: saved.role ?? role } : row)));
    } catch (err) {
      setError(message(err, "No se pudo cambiar el rol del atributo."));
    } finally {
      setRoleBusy(null);
    }
  }

  return (
    <Panel
      id="atributos"
      title="Atributos y opciones"
      description="Lo que distingue a las variantes de un modelo: por ejemplo la capacidad (texto) o el color (con su muestra). Cada atributo lleva su rol (capacidad, color, SIM, región u otro) y cada modelo elige, en su página, cuáles de estas opciones admite."
    >
      {error ? <div className="mb-4"><ErrorBox message={error} /></div> : null}
      {attributes === null && !error ? <Spinner label="Cargando atributos…" /> : null}
      {attributes && attributes.length === 0 ? <EmptyBox message="Todavía no hay atributos." /> : null}
      {attributes && attributes.length > 0 ? (
        <ul className="space-y-4">
          {[...attributes].sort((a, b) => a.sort_order - b.sort_order).map((attribute) => (
            <li key={attribute.id} className="rounded-lg border border-bd-border bg-background p-4" aria-label={attribute.name}>
              <div className="flex flex-wrap items-end justify-between gap-2">
                <h3 className="text-sm font-semibold text-foreground">{attribute.name}</h3>
                <div className="flex flex-wrap items-end gap-3">
                  <span className="text-xs text-muted">{attribute.kind === "color" ? "Color" : "Texto"}</span>
                  {canManage ? (
                    <label className="block text-xs text-muted">
                      Rol
                      <select
                        value={attribute.role ?? "other"}
                        aria-label={`Rol de ${attribute.name}`}
                        disabled={roleBusy === attribute.id}
                        onChange={(e) => void changeRole(attribute, e.target.value as AttributeRole)}
                        className={`mt-1.5 ${internalInputClass}`}
                      >
                        {roleOptions}
                      </select>
                    </label>
                  ) : (
                    <span className="text-xs text-muted">Rol: {ROLE_LABELS[attribute.role ?? "other"]}</span>
                  )}
                </div>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                {[...attribute.options].sort((a, b) => a.sort_order - b.sort_order).map((option) => (
                  editing === option.id ? null : (
                    <span key={option.id} className="inline-flex items-center gap-1">
                      <OptionChip option={option} kind={attribute.kind} />
                      {canManage ? (
                        <button type="button" className="rounded px-1.5 text-xs font-semibold text-muted underline-offset-2 hover:text-foreground hover:underline" onClick={() => setEditing(option.id)} aria-label={`Editar ${option.label}`}>
                          Editar
                        </button>
                      ) : null}
                    </span>
                  )
                ))}
                {attribute.options.length === 0 ? <span className="text-xs text-muted">Sin opciones.</span> : null}
              </div>
              {canManage && editing !== null && attribute.options.some((o) => o.id === editing) ? (
                <div className="mt-3 rounded-lg border border-bd-border p-3">
                  {(() => {
                    const option = attribute.options.find((o) => o.id === editing)!;
                    return (
                      <OptionForm
                        kind={attribute.kind}
                        initial={option}
                        submitLabel="Guardar"
                        onCancel={() => setEditing(null)}
                        onSubmit={async (body) => {
                          await updateAttributeOption(option.id, body, companyId);
                          setEditing(null);
                          load();
                        }}
                      />
                    );
                  })()}
                </div>
              ) : null}
              {canManage ? (
                <div className="mt-4 border-t border-bd-border pt-3">
                  <OptionForm
                    kind={attribute.kind}
                    submitLabel="Agregar opción"
                    onSubmit={async (body) => {
                      await createAttributeOption(attribute.id, body, companyId);
                      load();
                    }}
                  />
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}

      {canManage ? (
        <form onSubmit={create} className="mt-5 flex flex-wrap items-end gap-3 border-t border-bd-border pt-4" aria-label="Nuevo atributo">
          <label className="block min-w-40 flex-1 text-xs text-muted">
            Nuevo atributo
            <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="Capacidad, Color…" className={`mt-1.5 ${internalInputClass}`} />
          </label>
          <label className="block text-xs text-muted">
            Tipo
            <select value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value as "text" | "color" })} className={`mt-1.5 ${internalInputClass}`}>
              <option value="text">Texto</option>
              <option value="color">Color</option>
            </select>
          </label>
          <label className="block text-xs text-muted">
            Rol
            <select value={draft.role} onChange={(e) => setDraft({ ...draft, role: e.target.value as AttributeRole })} className={`mt-1.5 ${internalInputClass}`}>
              {roleOptions}
            </select>
          </label>
          <button type="submit" className={internalButtonClass} disabled={!draft.name.trim()}>Crear atributo</button>
        </form>
      ) : null}
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Cosmetic grades
// ---------------------------------------------------------------------------

function GradesSection({ canManage, companyId }: { canManage: boolean; companyId: number | null }) {
  const [grades, setGrades] = useState<CosmeticGradeRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState({ code: "", label: "", description: "" });
  const [busy, setBusy] = useState(false);
  /** INV-GRADES-01: the grade being edited, as typed. */
  const [editing, setEditing] = useState<{ id: number; label: string; description: string; sort_order: string; is_active: boolean } | null>(null);

  const load = useCallback(() => {
    fetchCosmeticGrades(companyId).then(
      (rows) => { setGrades(rows); setError(null); },
      (err) => setError(message(err, "No se pudo cargar la escala de estado.")),
    );
  }, [companyId]);
  useEffect(() => { load(); }, [load]);

  async function seed() {
    setBusy(true);
    try {
      await seedCosmeticGrades(companyId);
      load();
    } catch (err) {
      setError(message(err, "No se pudo crear la escala sugerida."));
    } finally {
      setBusy(false);
    }
  }

  async function saveEdit(grade: CosmeticGradeRow) {
    if (!editing || busy) return;
    const order = Number(editing.sort_order);
    if (!editing.sort_order.trim() || !Number.isInteger(order) || order < 0 || order > 9999) {
      setError("El orden es un número entre 0 y 9999.");
      return;
    }
    const body: Parameters<typeof updateCosmeticGrade>[1] = {
      description: editing.description.trim(), sort_order: order, is_active: editing.is_active,
    };
    if (!grade.in_use && editing.label.trim() !== grade.label) body.label = editing.label.trim();
    setBusy(true);
    try {
      await updateCosmeticGrade(grade.id, body, companyId);
      setEditing(null);
      setError(null);
      load();
    } catch (err) {
      setError(message(err, "No se pudo guardar el grado."));
    } finally {
      setBusy(false);
    }
  }

  async function create(event: React.FormEvent) {
    event.preventDefault();
    if (!draft.code.trim() || !draft.label.trim()) return;
    setBusy(true);
    try {
      await createCosmeticGrade({ code: draft.code.trim(), label: draft.label.trim(), description: draft.description.trim() }, companyId);
      setDraft({ code: "", label: "", description: "" });
      load();
    } catch (err) {
      setError(message(err, "No se pudo crear el grado."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel
      id="escala"
      title="Escala de estado"
      description="Cómo describes el estado estético de un equipo seminuevo. La tienda muestra el nombre y la descripción."
      action={canManage ? (
        <button type="button" className={internalButtonClass} onClick={() => void seed()} disabled={busy}>Crear escala sugerida</button>
      ) : null}
    >
      {error ? <div className="mb-4"><ErrorBox message={error} /></div> : null}
      {grades === null && !error ? <Spinner label="Cargando escala…" /> : null}
      {grades && grades.length === 0 ? (
        <EmptyBox message={canManage ? "Sin grados. Usa «Crear escala sugerida» o agrega los tuyos." : "Sin grados."} />
      ) : null}
      {grades && grades.length > 0 ? (
        <TableWrap>
          <thead>
            <tr className="border-b border-bd-border"><Th>Código</Th><Th>Nombre</Th><Th>Descripción</Th><Th>Orden</Th><Th>Activo</Th>{canManage ? <Th>Acciones</Th> : null}</tr>
          </thead>
          <tbody>
            {[...grades].sort((a, b) => a.sort_order - b.sort_order).map((grade) => (
              editing?.id === grade.id ? (
                <tr key={grade.id} className="border-b border-bd-border/60">
                  <Td><span className="font-mono">{grade.code}</span></Td>
                  <Td>
                    <input aria-label={`Nombre de ${grade.code}`} value={editing.label} disabled={grade.in_use}
                      onChange={(e) => setEditing({ ...editing, label: e.target.value })} className={internalInputClass} maxLength={60} />
                    {grade.in_use ? <p className="mt-1 text-[11px] text-muted">Ya lo tienen equipos: el nombre no cambia para no reescribir su historial.</p> : null}
                    {grade.in_use ? null : <ClaimsHint text={editing.label} companyId={companyId} />}
                  </Td>
                  <Td>
                    <input aria-label={`Descripción de ${grade.code}`} value={editing.description}
                      onChange={(e) => setEditing({ ...editing, description: e.target.value })} className={internalInputClass} maxLength={300} />
                    <ClaimsHint text={editing.description} companyId={companyId} />
                  </Td>
                  <Td>
                    <input aria-label={`Orden de ${grade.code}`} inputMode="numeric" value={editing.sort_order}
                      onChange={(e) => setEditing({ ...editing, sort_order: e.target.value.replace(/[^\d]/g, "") })} className={`w-20 ${internalInputClass}`} />
                  </Td>
                  <Td>
                    <label className="inline-flex min-h-11 items-center gap-2 text-xs">
                      <input type="checkbox" checked={editing.is_active} onChange={(e) => setEditing({ ...editing, is_active: e.target.checked })} />
                      Activo
                    </label>
                  </Td>
                  <Td>
                    <div className="flex flex-wrap gap-2">
                      <button type="button" className={internalButtonClass} disabled={busy} onClick={() => void saveEdit(grade)}>Guardar</button>
                      <button type="button" className={internalButtonClass} disabled={busy} onClick={() => setEditing(null)}>Cancelar</button>
                    </div>
                  </Td>
                </tr>
              ) : (
                <tr key={grade.id} className="border-b border-bd-border/60">
                  <Td><span className="font-mono">{grade.code}</span></Td>
                  <Td>{grade.label}</Td>
                  <Td muted>{grade.description || "—"}</Td>
                  <Td muted>{grade.sort_order}</Td>
                  <Td muted>{grade.is_active ? "Sí" : "No (no se ofrece en recepciones nuevas)"}</Td>
                  {canManage ? (
                    <Td>
                      <button type="button" className={internalButtonClass} disabled={busy}
                        aria-label={`Editar ${grade.label}`}
                        onClick={() => setEditing({ id: grade.id, label: grade.label, description: grade.description,
                          sort_order: String(grade.sort_order), is_active: grade.is_active })}>
                        Editar
                      </button>
                    </Td>
                  ) : null}
                </tr>
              )
            ))}
          </tbody>
        </TableWrap>
      ) : null}
      {canManage ? (
        <form onSubmit={create} className="mt-5 grid gap-3 border-t border-bd-border pt-4 md:grid-cols-[8rem_minmax(0,1fr)_minmax(0,2fr)_auto] md:items-end" aria-label="Nuevo grado">
          <label className="block text-xs text-muted">
            Código
            <input value={draft.code} onChange={(e) => setDraft({ ...draft, code: e.target.value })} className={`mt-1.5 font-mono ${internalInputClass}`} />
          </label>
          <div>
            <label className="block text-xs text-muted">
              Nombre
              <input value={draft.label} onChange={(e) => setDraft({ ...draft, label: e.target.value })} className={`mt-1.5 ${internalInputClass}`} />
            </label>
            <ClaimsHint text={draft.label} companyId={companyId} />
          </div>
          <div>
            <label className="block text-xs text-muted">
              Descripción
              <input value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} className={`mt-1.5 ${internalInputClass}`} />
            </label>
            <ClaimsHint text={draft.description} companyId={companyId} />
          </div>
          <button type="submit" className={internalButtonClass} disabled={busy || !draft.code.trim() || !draft.label.trim()}>Agregar grado</button>
        </form>
      ) : null}
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Line labels
// ---------------------------------------------------------------------------

function LineLabelsSection({ canManage, companyId }: { canManage: boolean; companyId: number | null }) {
  const [settings, setSettings] = useState<CatalogSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetchCatalogSettings(companyId).then(
      (loaded) => setSettings({ new_line_label: loaded.new_line_label ?? "", used_line_label: loaded.used_line_label ?? "" }),
      (err) => setError(message(err, "No se pudieron cargar los nombres de las líneas.")),
    );
  }, [companyId]);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!settings) return;
    setSaving(true);
    setNotice(null);
    setError(null);
    try {
      const saved = await updateCatalogSettings(settings, companyId);
      setSettings({ new_line_label: saved.new_line_label ?? "", used_line_label: saved.used_line_label ?? "" });
      setNotice("Nombres guardados.");
    } catch (err) {
      setError(message(err, "No se pudieron guardar los nombres de las líneas."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Panel title="Nombres de las líneas" description="Cómo llama la tienda a los equipos nuevos y a los seminuevos. En blanco se usa el nombre por defecto.">
      {error ? <div className="mb-4"><ErrorBox message={error} /></div> : null}
      {settings === null && !error ? <Spinner label="Cargando…" /> : null}
      {settings ? (
        <form onSubmit={save} className="grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] md:items-end">
          <label className="block text-xs text-muted">
            Línea de nuevos
            <input
              value={settings.new_line_label}
              onChange={(e) => setSettings({ ...settings, new_line_label: e.target.value })}
              placeholder={DEFAULT_LINE_LABELS.new}
              disabled={!canManage}
              className={`mt-1.5 ${internalInputClass}`}
            />
          </label>
          <label className="block text-xs text-muted">
            Línea de seminuevos
            <input
              value={settings.used_line_label}
              onChange={(e) => setSettings({ ...settings, used_line_label: e.target.value })}
              placeholder={DEFAULT_LINE_LABELS.used}
              disabled={!canManage}
              className={`mt-1.5 ${internalInputClass}`}
            />
          </label>
          {canManage ? <button type="submit" className={internalButtonClass} disabled={saving}>{saving ? "Guardando…" : "Guardar nombres"}</button> : null}
        </form>
      ) : null}
      {notice ? <p role="status" className="mt-3 text-sm text-success">{notice}</p> : null}
    </Panel>
  );
}

export function CatalogModelsManager({ canManage, companyId, goTo = null, onArrived }: {
  canManage: boolean;
  companyId: number | null;
  /**
   * NAV-SALES-01 — «Productos › Colores» (`?seccion=colores`): the section to bring into view and focus once the
   * models table has arrived. A new `request` goes there again (the menu entry clicked with the page already open).
   */
  goTo?: { anchor: string; request: number } | null;
  /** Called once the section has the focus: the page then takes `?seccion=` out of the address. */
  onArrived?: () => void;
}) {
  // «#atributos» from a model's page: the panels move down once the models table arrives, so the page goes
  // there after that (review: scrolling on mount ended above the panel).
  const scrolled = useRef(false);
  const arrived = useRef(onArrived);
  useEffect(() => {
    arrived.current = onArrived;
  });
  const [modelsLoaded, setModelsLoaded] = useState(false);
  const anchor = goTo?.anchor ?? null;
  const request = goTo?.request ?? 0;
  useEffect(() => {
    if (!anchor || !modelsLoaded) return;
    const frame = requestAnimationFrame(() => {
      const target = document.getElementById(anchor);
      if (!target) return;
      target.scrollIntoView?.({ block: "start" });
      if (!target.hasAttribute("tabindex")) target.setAttribute("tabindex", "-1");
      target.focus({ preventScroll: true });
      arrived.current?.();
    });
    return () => cancelAnimationFrame(frame);
  }, [anchor, request, modelsLoaded]);
  const goToHash = useCallback(() => {
    setModelsLoaded(true);
    if (scrolled.current) return;
    scrolled.current = true;
    const target = window.location.hash.slice(1);
    if (target) requestAnimationFrame(() => document.getElementById(target)?.scrollIntoView({ block: "start" }));
  }, []);
  return (
    <div className="space-y-6">
      <ModelsSection canManage={canManage} companyId={companyId} onLoaded={goToHash} />
      <AttributesSection canManage={canManage} companyId={companyId} />
      {/* A2: the supplier vocabulary sits next to the options it reads into, and the resolver right after it. */}
      <CatalogAliasesSection canManage={canManage} companyId={companyId} />
      <ResolverTryPanel companyId={companyId} />
      <GradesSection canManage={canManage} companyId={companyId} />
      <LineLabelsSection canManage={canManage} companyId={companyId} />
    </div>
  );
}
