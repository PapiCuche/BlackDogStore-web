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
import { useCallback, useEffect, useId, useState } from "react";

import { fetchAdminCategories, type AdminCategory } from "@/app/lib/admin";
import {
  DEFAULT_LINE_LABELS,
  createAdminCatalogModel,
  createAttributeOption,
  createCatalogAttribute,
  createCosmeticGrade,
  fetchAdminCatalogModels,
  fetchCatalogAttributes,
  fetchCatalogSettings,
  fetchCosmeticGrades,
  isValidSwatchHex,
  seedCosmeticGrades,
  updateAttributeOption,
  updateCatalogSettings,
  type AdminCatalogAttribute,
  type AdminCatalogModel,
  type AdminAttributeOption,
  type CatalogSettings,
  type CosmeticGradeRow,
} from "../lib/internal-api";
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

function ModelsSection({ canManage, companyId }: { canManage: boolean; companyId: number | null }) {
  const [models, setModels] = useState<AdminCatalogModel[] | null>(null);
  const [categories, setCategories] = useState<AdminCategory[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [fields, setFields] = useState<Record<string, string>>({});
  const [draft, setDraft] = useState({ name: "", category_id: "", slug: "", description: "", image_url: "", is_published_online: true });

  const load = useCallback(() => {
    fetchAdminCatalogModels(companyId).then(
      (rows) => { setModels(rows); setError(null); },
      (err) => setError(message(err, "No se pudieron cargar los modelos.")),
    );
  }, [companyId]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    fetchAdminCategories(companyId).then(setCategories, () => { /* the form still works without the list */ });
  }, [companyId]);

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
          <label className="block text-xs text-muted md:col-span-2">
            Descripción
            <textarea value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} rows={2} className={`mt-1.5 ${internalInputClass}`} />
          </label>
          <label className="flex items-center gap-2 text-sm text-foreground">
            <input type="checkbox" checked={draft.is_published_online} onChange={(e) => setDraft({ ...draft, is_published_online: e.target.checked })} />
            Publicado en la tienda en línea
          </label>
          <div className="flex justify-end gap-2 md:col-span-2">
            <button type="button" className={internalButtonClass} onClick={() => { setCreating(false); setFields({}); }} disabled={saving}>Cancelar</button>
            <button type="submit" className={internalPrimaryButtonClass} disabled={saving}>{saving ? "Creando…" : "Crear modelo"}</button>
          </div>
        </form>
      ) : null}

      {error ? <div className="mb-4"><ErrorBox message={error} /></div> : null}
      {models === null && !error ? <Spinner label="Cargando modelos…" /> : null}
      {models && models.length === 0 ? (
        <EmptyBox message={canManage ? "Todavía no hay modelos. Crea uno y agrégale sus productos como variantes." : "Todavía no hay modelos."} />
      ) : null}
      {models && models.length > 0 ? (
        <TableWrap>
          <thead>
            <tr className="border-b border-bd-border">
              <Th>Modelo</Th><Th>Categoría</Th><Th right>Variantes</Th><Th right>Seminuevos</Th><Th>Tienda</Th>
            </tr>
          </thead>
          <tbody>
            {models.map((model) => (
              <tr key={model.id} className="border-b border-bd-border/60">
                <Td>
                  <Link href={`/admin/products/models/${model.id}`} className="font-semibold underline-offset-4 hover:underline">{model.name}</Link>
                  <span className="block font-mono text-xs text-muted">{model.slug}</span>
                </Td>
                <Td muted>{model.category_name ?? "—"}</Td>
                <Td right>{model.variant_count}</Td>
                <Td right>{model.used_unit_count}</Td>
                <Td muted>{model.is_published_online ? "Publicado" : "Oculto"}</Td>
              </tr>
            ))}
          </tbody>
        </TableWrap>
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
    </span>
  );
}

/** Add or edit one option. Colour options must carry a valid #RRGGBB before anything is sent. */
function OptionForm({
  kind, initial, submitLabel, onSubmit, onCancel,
}: {
  kind: "text" | "color";
  initial?: { label: string; swatch_hex: string; sort_order?: number };
  submitLabel: string;
  onSubmit: (body: { label: string; swatch_hex?: string; sort_order?: number }) => Promise<void>;
  onCancel?: () => void;
}) {
  const [label, setLabel] = useState(initial?.label ?? "");
  const [hex, setHex] = useState(initial?.swatch_hex ?? "");
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

function AttributesSection({ canManage, companyId }: { canManage: boolean; companyId: number | null }) {
  const [attributes, setAttributes] = useState<AdminCatalogAttribute[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ name: string; kind: "text" | "color" }>({ name: "", kind: "text" });
  const [editing, setEditing] = useState<number | null>(null);

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
      await createCatalogAttribute({ name: draft.name.trim(), kind: draft.kind }, companyId);
      setDraft({ name: "", kind: "text" });
      load();
    } catch (err) {
      setError(message(err, "No se pudo crear el atributo."));
    }
  }

  return (
    <Panel
      title="Atributos y opciones"
      description="Lo que distingue a las variantes de un modelo: por ejemplo la capacidad (texto) o el color (con su muestra)."
    >
      {error ? <div className="mb-4"><ErrorBox message={error} /></div> : null}
      {attributes === null && !error ? <Spinner label="Cargando atributos…" /> : null}
      {attributes && attributes.length === 0 ? <EmptyBox message="Todavía no hay atributos." /> : null}
      {attributes && attributes.length > 0 ? (
        <ul className="space-y-4">
          {[...attributes].sort((a, b) => a.sort_order - b.sort_order).map((attribute) => (
            <li key={attribute.id} className="rounded-lg border border-bd-border bg-background p-4" aria-label={attribute.name}>
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h3 className="text-sm font-semibold text-foreground">{attribute.name}</h3>
                <span className="text-xs text-muted">{attribute.kind === "color" ? "Color" : "Texto"}</span>
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
            <tr className="border-b border-bd-border"><Th>Código</Th><Th>Nombre</Th><Th>Descripción</Th><Th>Activo</Th></tr>
          </thead>
          <tbody>
            {[...grades].sort((a, b) => a.sort_order - b.sort_order).map((grade) => (
              <tr key={grade.id} className="border-b border-bd-border/60">
                <Td><span className="font-mono">{grade.code}</span></Td>
                <Td>{grade.label}</Td>
                <Td muted>{grade.description || "—"}</Td>
                <Td muted>{grade.is_active ? "Sí" : "No"}</Td>
              </tr>
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
          <label className="block text-xs text-muted">
            Nombre
            <input value={draft.label} onChange={(e) => setDraft({ ...draft, label: e.target.value })} className={`mt-1.5 ${internalInputClass}`} />
          </label>
          <label className="block text-xs text-muted">
            Descripción
            <input value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} className={`mt-1.5 ${internalInputClass}`} />
          </label>
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

export function CatalogModelsManager({ canManage, companyId }: { canManage: boolean; companyId: number | null }) {
  return (
    <div className="space-y-6">
      <ModelsSection canManage={canManage} companyId={companyId} />
      <AttributesSection canManage={canManage} companyId={companyId} />
      <GradesSection canManage={canManage} companyId={companyId} />
      <LineLabelsSection canManage={canManage} companyId={companyId} />
    </div>
  );
}
