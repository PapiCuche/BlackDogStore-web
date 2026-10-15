"use client";

/**
 * Productos › Modelos › un modelo — DEVICE-COMMERCE-01 · DC2.
 *
 * Its own fields, and the products that are its variants. A variant is an
 * existing product placed in the model with a line (new or used) and one
 * option per attribute; the server refuses a combination that is already
 * taken or an option of another attribute, and says which field is wrong.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { fetchAdminCategories, fetchAdminProducts, type AdminCategory, type AdminProduct } from "@/app/lib/admin";
import {
  DEFAULT_LINE_LABELS,
  fetchAdminCatalogModel,
  fetchCatalogAttributes,
  fetchCatalogSettings,
  setProductVariant,
  updateAdminCatalogModel,
  type AdminCatalogAttribute,
  type AdminCatalogModelDetail,
  type CatalogLine,
} from "../lib/internal-api";
import { EmptyBox, ErrorBox, Panel, Spinner, TableWrap, Td, Th, formatSoles } from "./InventoryUi";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";

const message = (error: unknown, fallback: string) => (error instanceof Error ? error.message : fallback);

type Draft = {
  name: string; slug: string; category_id: string; description: string; image_url: string;
  is_published_online: boolean; sort_order: string;
};

const toDraft = (model: AdminCatalogModelDetail): Draft => ({
  name: model.name,
  slug: model.slug,
  category_id: model.category_id ? String(model.category_id) : "",
  description: model.description ?? "",
  image_url: model.image_url ?? "",
  is_published_online: model.is_published_online,
  sort_order: String(model.sort_order ?? 0),
});

function AddVariantForm({
  modelId, companyId, attributes, lineLabels, onSaved, onCancel,
}: {
  modelId: number;
  companyId: number | null;
  attributes: AdminCatalogAttribute[];
  lineLabels: Record<CatalogLine, string>;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const [search, setSearch] = useState("");
  const [results, setResults] = useState<AdminProduct[]>([]);
  const [searching, setSearching] = useState(false);
  const [product, setProduct] = useState<AdminProduct | null>(null);
  const [line, setLine] = useState<CatalogLine>("new");
  const [chosen, setChosen] = useState<Record<number, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [field, setField] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function find(event: React.FormEvent) {
    event.preventDefault();
    setSearching(true);
    setError(null);
    try {
      const page = await fetchAdminProducts({ search: search.trim(), page_size: 10, company: companyId });
      setResults(page.results);
    } catch (err) {
      setError(message(err, "No se pudieron buscar productos."));
    } finally {
      setSearching(false);
    }
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!product) { setError("Elige el producto que será la variante."); return; }
    setSaving(true);
    setError(null);
    setField(null);
    try {
      await setProductVariant(product.id, {
        model_id: modelId,
        condition_line: line,
        option_ids: Object.values(chosen).filter(Boolean).map(Number),
      }, companyId);
      onSaved();
    } catch (err) {
      setError(message(err, "No se pudo guardar la variante."));
      setField((err as { field?: string | null }).field ?? null);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mb-6 space-y-4 rounded-lg border border-bd-border bg-background p-4">
      <form onSubmit={find} className="flex flex-wrap items-end gap-3" role="search" aria-label="Buscar producto">
        <label className="block min-w-48 flex-1 text-xs text-muted">
          Producto
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Nombre del producto" className={`mt-1.5 ${internalInputClass}`} />
        </label>
        <button type="submit" className={internalButtonClass} disabled={searching}>{searching ? "Buscando…" : "Buscar"}</button>
      </form>

      {results.length > 0 ? (
        <fieldset>
          <legend className="text-xs text-muted">Resultados</legend>
          <div className="mt-2 max-h-56 space-y-1 overflow-y-auto">
            {results.map((row) => (
              <label key={row.id} className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-foreground hover:bg-foreground/[0.03]">
                <input type="radio" name="variant-product" checked={product?.id === row.id} onChange={() => setProduct(row)} />
                <span className="min-w-0 flex-1 truncate">{row.name}</span>
                <span className="text-xs text-muted">{formatSoles(row.price)}</span>
              </label>
            ))}
          </div>
        </fieldset>
      ) : null}

      <form onSubmit={save} className="grid gap-3 md:grid-cols-3" aria-label="Agregar variante">
        <p className="text-sm text-foreground md:col-span-3">
          {product ? <>Producto elegido: <strong>{product.name}</strong></> : <span className="text-muted">Busca y elige un producto.</span>}
          {field === "product" ? <span className="ml-2 text-danger">← revisa este dato</span> : null}
        </p>
        <label className="block text-xs text-muted">
          Línea
          <select value={line} onChange={(e) => setLine(e.target.value as CatalogLine)} className={`mt-1.5 ${internalInputClass}`} aria-invalid={field === "condition_line" ? true : undefined}>
            <option value="new">{lineLabels.new}</option>
            <option value="used">{lineLabels.used}</option>
          </select>
        </label>
        {attributes.map((attribute) => (
          <label key={attribute.id} className="block text-xs text-muted">
            {attribute.name}
            <select
              value={chosen[attribute.id] ?? ""}
              onChange={(e) => setChosen({ ...chosen, [attribute.id]: e.target.value })}
              aria-invalid={field === "option_ids" ? true : undefined}
              className={`mt-1.5 ${internalInputClass}`}
            >
              <option value="">No aplica</option>
              {[...attribute.options].sort((a, b) => a.sort_order - b.sort_order).map((option) => (
                <option key={option.id} value={option.id}>{option.label}</option>
              ))}
            </select>
          </label>
        ))}
        {error ? <p role="alert" className="text-sm text-danger md:col-span-3">{error}</p> : null}
        <div className="flex justify-end gap-2 md:col-span-3">
          <button type="button" className={internalButtonClass} onClick={onCancel} disabled={saving}>Cancelar</button>
          <button type="submit" className={internalPrimaryButtonClass} disabled={saving || !product}>{saving ? "Guardando…" : "Guardar variante"}</button>
        </div>
      </form>
    </div>
  );
}

export function CatalogModelEditor({
  modelId, canManage, companyId,
}: { modelId: number; canManage: boolean; companyId: number | null }) {
  const [model, setModel] = useState<AdminCatalogModelDetail | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [categories, setCategories] = useState<AdminCategory[]>([]);
  const [attributes, setAttributes] = useState<AdminCatalogAttribute[]>([]);
  const [lineLabels, setLineLabels] = useState<Record<CatalogLine, string>>(DEFAULT_LINE_LABELS);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [adding, setAdding] = useState(false);

  const load = useCallback(() => {
    fetchAdminCatalogModel(modelId, companyId).then(
      (loaded) => { setModel(loaded); setDraft(toDraft(loaded)); setError(null); },
      (err) => setError(message(err, "No se pudo cargar el modelo.")),
    );
  }, [modelId, companyId]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    fetchAdminCategories(companyId).then(setCategories, () => {});
    fetchCatalogAttributes(companyId).then(setAttributes, () => {});
    fetchCatalogSettings(companyId).then(
      (s) => setLineLabels({
        new: s.new_line_label?.trim() || DEFAULT_LINE_LABELS.new,
        used: s.used_line_label?.trim() || DEFAULT_LINE_LABELS.used,
      }),
      () => {},
    );
  }, [companyId]);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!draft) return;
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const saved = await updateAdminCatalogModel(modelId, {
        name: draft.name.trim(),
        slug: draft.slug.trim(),
        category_id: draft.category_id ? Number(draft.category_id) : null,
        description: draft.description,
        image_url: draft.image_url.trim(),
        is_published_online: draft.is_published_online,
        sort_order: Number(draft.sort_order) || 0,
      }, companyId);
      if (saved && typeof saved === "object" && "variants" in saved) { setModel(saved); setDraft(toDraft(saved)); }
      else load();
      setNotice("Modelo guardado.");
    } catch (err) {
      setError(message(err, "No se pudo guardar el modelo."));
    } finally {
      setSaving(false);
    }
  }

  async function removeVariant(productId: number, line: CatalogLine) {
    setError(null);
    setNotice(null);
    try {
      await setProductVariant(productId, { model_id: null, condition_line: line, option_ids: [] }, companyId);
      setNotice("El producto ya no es una variante de este modelo.");
      load();
    } catch (err) {
      setError(message(err, "No se pudo quitar la variante."));
    }
  }

  if (!model || !draft) {
    return error ? <ErrorBox message={error} /> : <Spinner label="Cargando modelo…" />;
  }

  return (
    <div className="space-y-6">
      <p className="text-sm">
        <Link href="/admin/products/models" className="text-muted underline-offset-4 hover:text-foreground hover:underline">← Todos los modelos</Link>
      </p>
      {notice ? <p role="status" className="rounded-lg border border-success-border px-4 py-3 text-sm text-success">{notice}</p> : null}
      {error ? <ErrorBox message={error} /> : null}

      <Panel title={model.name} description="Los datos que la tienda muestra en la página del modelo.">
        <form onSubmit={save} className="grid gap-3 md:grid-cols-2">
          <fieldset disabled={!canManage} className="contents">
            <label className="block text-xs text-muted">
              Nombre
              <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} className={`mt-1.5 ${internalInputClass}`} />
            </label>
            <label className="block text-xs text-muted">
              Dirección
              <input value={draft.slug} onChange={(e) => setDraft({ ...draft, slug: e.target.value })} className={`mt-1.5 font-mono ${internalInputClass}`} />
            </label>
            <label className="block text-xs text-muted">
              Categoría
              <select value={draft.category_id} onChange={(e) => setDraft({ ...draft, category_id: e.target.value })} className={`mt-1.5 ${internalInputClass}`}>
                <option value="">Sin categoría</option>
                {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </label>
            <label className="block text-xs text-muted">
              Orden
              <input type="number" value={draft.sort_order} onChange={(e) => setDraft({ ...draft, sort_order: e.target.value })} className={`mt-1.5 ${internalInputClass}`} />
            </label>
            <label className="block text-xs text-muted md:col-span-2">
              Imagen (dirección)
              <input value={draft.image_url} onChange={(e) => setDraft({ ...draft, image_url: e.target.value })} className={`mt-1.5 ${internalInputClass}`} />
            </label>
            <label className="block text-xs text-muted md:col-span-2">
              Descripción
              <textarea value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} rows={3} className={`mt-1.5 ${internalInputClass}`} />
            </label>
            <label className="flex items-center gap-2 text-sm text-foreground">
              <input type="checkbox" checked={draft.is_published_online} onChange={(e) => setDraft({ ...draft, is_published_online: e.target.checked })} />
              Publicado en la tienda en línea
            </label>
          </fieldset>
          {canManage ? (
            <div className="flex justify-end md:col-span-2">
              <button type="submit" className={internalPrimaryButtonClass} disabled={saving}>{saving ? "Guardando…" : "Guardar modelo"}</button>
            </div>
          ) : null}
        </form>
      </Panel>

      <Panel
        title="Variantes"
        description="Los productos que son opciones de este modelo, con su línea y sus opciones."
        action={canManage && !adding ? (
          <button type="button" className={internalPrimaryButtonClass} onClick={() => setAdding(true)}>+ Agregar variante</button>
        ) : null}
      >
        {canManage && adding ? (
          <AddVariantForm
            modelId={modelId}
            companyId={companyId}
            attributes={attributes}
            lineLabels={lineLabels}
            onCancel={() => setAdding(false)}
            onSaved={() => { setAdding(false); setNotice("Variante guardada."); load(); }}
          />
        ) : null}
        {model.variants.length === 0 ? (
          <EmptyBox message="Este modelo todavía no tiene variantes." />
        ) : (
          <TableWrap>
            <thead>
              <tr className="border-b border-bd-border">
                <Th>Producto</Th><Th>Línea</Th><Th>Opciones</Th><Th right>Precio</Th><Th right>Stock</Th>
                {canManage ? <Th right>Acciones</Th> : null}
              </tr>
            </thead>
            <tbody>
              {model.variants.map((variant) => (
                <tr key={variant.product_id} className="border-b border-bd-border/60">
                  <Td>
                    <Link href={`/admin/products/${variant.product_id}`} className="underline-offset-4 hover:underline">{variant.name}</Link>
                    {variant.sku || variant.code ? <span className="block font-mono text-xs text-muted">{variant.sku || variant.code}</span> : null}
                  </Td>
                  <Td muted>{lineLabels[variant.condition_line] ?? variant.condition_line}</Td>
                  <Td>
                    <span className="flex flex-wrap gap-1.5">
                      {variant.options.map((option) => (
                        <span key={option.option_id} className="inline-flex items-center gap-1 rounded-full border border-bd-border px-2 py-0.5 text-xs">
                          {option.swatch_hex ? (
                            <span aria-hidden="true" className="h-3 w-3 rounded-full border border-foreground/25" style={{ backgroundColor: option.swatch_hex }} />
                          ) : null}
                          <span className="text-muted">{option.attribute_name}:</span> {option.label}
                        </span>
                      ))}
                      {variant.options.length === 0 ? <span className="text-muted">—</span> : null}
                    </span>
                  </Td>
                  <Td right muted>{formatSoles(variant.price)}</Td>
                  <Td right>{variant.stock}</Td>
                  {canManage ? (
                    <Td right>
                      <button
                        type="button"
                        className="rounded-lg border border-bd-border px-2.5 py-1.5 text-xs font-semibold text-foreground transition-colors hover:border-foreground/25"
                        onClick={() => void removeVariant(variant.product_id, variant.condition_line)}
                        aria-label={`Quitar ${variant.name} del modelo`}
                      >
                        Quitar del modelo
                      </button>
                    </Td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </TableWrap>
        )}
      </Panel>
    </div>
  );
}
