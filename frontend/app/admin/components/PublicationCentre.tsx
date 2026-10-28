"use client";

/**
 * Productos › Publicación e-commerce — COMMERCE-ADMIN-UX-01.
 *
 * UNA pantalla para todos los tipos de artículo. No trae reglas propias:
 *
 *   · productos y variantes (una variante de DC2 ES un producto) → la acción en
 *     bloque de INVENTORY-UNIFIED (`bulkSetProductVisibility`), que publica lo
 *     que está listo y devuelve lo que no con su motivo;
 *   · modelos → `bulkSetModelVisibility`: un modelo oculto saca su página y sus
 *     equipos de la tienda; sus variantes no cambian.
 *
 * Lo que le falta a cada producto para publicarse viene del servidor
 * (`publication_problems`, la misma regla de siempre). Ver: `products.view`;
 * cambiar: `products.manage` (el servidor lo vuelve a comprobar).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  bulkSetProductVisibility,
  fetchAdminProducts,
  publicationProblemLabel,
  type AdminProduct,
  type BulkVisibilityAction,
  type BulkVisibilityResult,
  type ProductListFilter,
} from "../../lib/admin";
import { bulkSetModelVisibility } from "../../lib/registration";
import { fetchAdminCatalogModels, type AdminCatalogModel } from "../lib/internal-api";
import { CompleteProductPanel } from "./CompleteProductPanel";
import { PublishReadyDialog } from "./PublishReadyDialog";
import { BulkVisibilityBar, BulkVisibilityConfirm, BulkVisibilityResultPanel } from "./ProductBulkVisibility";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";

type KindFilter = "" | "new" | "used" | "accessory" | "on_order";

const KIND_FILTERS: { value: KindFilter; label: string; filter: ProductListFilter }[] = [
  { value: "", label: "Todos los tipos", filter: {} },
  { value: "new", label: "Equipos nuevos", filter: { is_serialized: "true", condition_line: "new" } },
  { value: "used", label: "Equipos seminuevos", filter: { condition_line: "used" } },
  { value: "accessory", label: "Accesorios y repuestos", filter: { is_serialized: "false", sale_mode: "stock" } },
  { value: "on_order", label: "Bajo pedido (proveedor)", filter: { sale_mode: "stock_or_order" } },
];

export const MANAGE_HINT = "Publicar u ocultar requiere el permiso «Gestionar productos».";

/** El tipo de un producto, en las palabras del registro. */
export function articleKindLabel(product: AdminProduct): string {
  if (product.condition_line === "used") return "Equipo seminuevo";
  if (product.sale_mode === "stock_or_order" && !product.is_serialized) return "Bajo pedido";
  if (product.is_serialized) return product.sale_mode === "stock_or_order" ? "Equipo nuevo · bajo pedido" : "Equipo nuevo";
  return "Accesorio o repuesto";
}

const PAGE_SIZE = 50;

export function PublicationCentre({
  canManage, companyId, initialSearch = "",
}: {
  canManage: boolean;
  companyId: number | null;
  /** INV-UX-A1: «Siguiente paso» opens the list already filtered to the product (`?search=`). */
  initialSearch?: string;
}) {
  const [kindFilter, setKindFilter] = useState<KindFilter>("");
  const [online, setOnline] = useState<"" | "true" | "false">("");
  const [search, setSearch] = useState(initialSearch);
  const [page, setPage] = useState(1);
  // The answer belongs to the request that asked for it: until the current one
  // answers, the list says «Cargando…» instead of showing another filter's rows.
  const [loaded, setLoaded] = useState<{ key: string; filterKey: string; rows: AdminProduct[]; count: number } | null>(null);
  const [failure, setFailure] = useState<{ key: string; message: string } | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [pending, setPending] = useState<BulkVisibilityAction | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<BulkVisibilityResult | null>(null);
  const [reload, setReload] = useState(0);
  // ECOM-READY-D1: «Listos para publicar», y la ficha abierta.
  const [readyOnly, setReadyOnly] = useState(false);
  const [publishingReady, setPublishingReady] = useState(false);
  const [openProduct, setOpenProduct] = useState<AdminProduct | null>(null);
  const opener = useRef<HTMLElement | null>(null);

  const [models, setModels] = useState<AdminCatalogModel[] | null>(null);
  const [modelError, setModelError] = useState<string | null>(null);
  const [modelSelected, setModelSelected] = useState<number[]>([]);
  const [modelNotice, setModelNotice] = useState<string | null>(null);
  const [modelReload, setModelReload] = useState(0);

  // The rows belong to the FILTERS that asked for them (`filterKey`). A reload with the same filters
  // (after saving a sheet) keeps showing them while the new answer comes, so the table — and the button
  // that opened the sheet — stay mounted and focus is not lost.
  const filterKey = JSON.stringify([kindFilter, online, readyOnly, search.trim(), page, companyId]);
  const requestKey = JSON.stringify([filterKey, reload]);
  useEffect(() => {
    let alive = true;
    const filter = KIND_FILTERS.find((k) => k.value === kindFilter)?.filter ?? {};
    fetchAdminProducts({
      ...filter,
      is_published_online: online || undefined,
      ready_to_publish: readyOnly ? "true" : undefined,
      search: search.trim() || undefined,
      page,
      page_size: PAGE_SIZE,
      company: companyId,
    })
      .then((data) => { if (alive) setLoaded({ key: requestKey, filterKey, rows: data.results, count: data.count }); })
      .catch((err) => {
        if (alive) setFailure({ key: requestKey, message: err instanceof Error ? err.message : "No se pudieron cargar los productos." });
      });
    return () => { alive = false; };
  }, [kindFilter, online, readyOnly, search, page, companyId, requestKey, filterKey]);
  const fresh = loaded?.filterKey === filterKey;
  const rows = fresh ? loaded.rows : null;
  const count = fresh ? loaded.count : 0;
  const error = actionError ?? (failure?.key === requestKey ? failure.message : null);

  useEffect(() => {
    let alive = true;
    fetchAdminCatalogModels(companyId)
      .then((data) => { if (alive) { setModels(data); setModelError(null); } })
      .catch((err) => { if (alive) setModelError(err instanceof Error ? err.message : "No se pudieron cargar los modelos."); });
    return () => { alive = false; };
  }, [companyId, modelReload]);

  const toggle = (id: number) => setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  const toggleModel = (id: number) =>
    setModelSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));

  const confirm = useCallback(async () => {
    if (!pending) return;
    setBusy(true);
    setActionError(null);
    try {
      const answer = await bulkSetProductVisibility(pending, { ids: [...selected].sort((a, b) => a - b) }, companyId);
      setResult(answer);
      setSelected([]);
      setReload((n) => n + 1);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "No se pudo cambiar la visibilidad.");
    } finally {
      setBusy(false);
      setPending(null);
    }
  }, [pending, selected, companyId]);

  // «Publicar listos» publishes the products the operator CHECKED, by id. Never «everything that matches».
  const publishChecked = useCallback(async (ids: number[]) => {
    setBusy(true);
    setActionError(null);
    try {
      const answer = await bulkSetProductVisibility("publish", { ids }, companyId);
      setResult(answer);
      setSelected([]);
      setPage(1);
      setReload((n) => n + 1);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "No se pudo publicar.");
      setReload((n) => n + 1);
    } finally {
      setBusy(false);
      setPublishingReady(false);
    }
  }, [companyId]);

  async function applyModels(action: "publish" | "hide") {
    setModelNotice(null);
    try {
      const answer = await bulkSetModelVisibility(action, [...modelSelected].sort((a, b) => a - b), companyId);
      const n = answer.changed;
      const noun = n === 1 ? "1 modelo" : `${n} modelos`;
      setModelNotice(action === "publish"
        ? (n === 1 ? "Se publicó 1 modelo." : `Se publicaron ${noun}.`)
        : (n === 1 ? "Se ocultó 1 modelo." : `Se ocultaron ${noun}.`));
      setModelSelected([]);
      setModelReload((x) => x + 1);
    } catch (err) {
      setModelNotice(err instanceof Error ? err.message : "No se pudo cambiar la visibilidad de los modelos.");
    }
  }

  const pages = Math.max(1, Math.ceil(count / PAGE_SIZE));

  return (
    <div className="space-y-8">
      {!canManage ? <p className="text-sm text-muted">{MANAGE_HINT}</p> : null}

      <section className="space-y-4 rounded-xl border border-bd-border bg-surface p-5">
        <div className="flex flex-col gap-3 md:flex-row md:items-end">
          <div className="md:w-56">
            <label htmlFor="pub-kind" className="mb-1.5 block text-xs text-muted">Tipo</label>
            <select id="pub-kind" value={kindFilter} onChange={(e) => { setKindFilter(e.target.value as KindFilter); setPage(1); setSelected([]); }} className={internalInputClass}>
              {KIND_FILTERS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
            </select>
          </div>
          <div className="md:w-44">
            <label htmlFor="pub-online" className="mb-1.5 block text-xs text-muted">En e-commerce</label>
            <select id="pub-online" value={online} disabled={readyOnly} onChange={(e) => { setOnline(e.target.value as "" | "true" | "false"); setPage(1); setSelected([]); }} className={internalInputClass}>
              <option value="">Todos</option>
              <option value="true">Publicados</option>
              <option value="false">Ocultos</option>
            </select>
          </div>
          <div className="md:w-auto">
            <button
              type="button"
              aria-pressed={readyOnly}
              onClick={() => { setReadyOnly((v) => !v); setOnline(""); setPage(1); setSelected([]); }}
              className={`${internalButtonClass} w-full md:w-auto ${readyOnly ? "border-foreground/40 bg-foreground/[0.06]" : ""}`}
            >
              Listos para publicar
            </button>
          </div>
          <div className="flex-1">
            <label htmlFor="pub-search" className="mb-1.5 block text-xs text-muted">Buscar</label>
            <input id="pub-search" value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); }} className={internalInputClass} placeholder="Nombre o código" />
          </div>
        </div>

        {readyOnly ? (
          <div className="flex flex-col gap-2 rounded-lg border border-bd-border bg-background p-3 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-sm text-foreground" aria-live="polite">
              <span className="block text-xs text-muted">«Listos para publicar» sólo muestra productos ocultos: el filtro «En e-commerce» no aplica.</span>
              {rows === null ? "Contando los listos…" : count === 1 ? "1 producto oculto y listo para publicar." : `${count} productos ocultos y listos para publicar.`}
            </p>
            {canManage && rows !== null && count > 0 ? (
              <button type="button" onClick={() => setPublishingReady(true)} className={internalPrimaryButtonClass}>
                Publicar listos ({count})
              </button>
            ) : null}
          </div>
        ) : null}
        {result ? <BulkVisibilityResultPanel result={result} onClose={() => setResult(null)} /> : null}
        {canManage && selected.length ? (
          <BulkVisibilityBar count={selected.length} onAction={setPending} onClear={() => setSelected([])} />
        ) : null}
        {pending ? (
          <BulkVisibilityConfirm action={pending} count={selected.length} busy={busy} onConfirm={confirm} onCancel={() => setPending(null)} />
        ) : null}
        {publishingReady && rows ? (
          <PublishReadyDialog
            candidates={rows}
            total={count}
            pageSize={PAGE_SIZE}
            busy={busy}
            onPublish={publishChecked}
            onCancel={() => setPublishingReady(false)}
          />
        ) : null}
        {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}
        {rows === null && !error ? <p className="text-sm text-muted">Cargando…</p> : null}
        {rows && rows.length === 0 ? <p className="text-sm text-muted">Ningún producto coincide.</p> : null}
        {rows && rows.length ? (
          <div className="relative overflow-x-auto">
            <table aria-label="Productos y variantes" className="w-full min-w-[640px] text-left text-sm">
              <thead>
                <tr className="border-b border-bd-border text-xs text-muted">
                  {canManage ? <th className="py-2 pr-3"><span className="sr-only">Seleccionar</span></th> : null}
                  <th className="py-2 pr-3">Producto</th>
                  <th className="py-2 pr-3">Tipo</th>
                  <th className="py-2 pr-3">E-commerce</th>
                  <th className="py-2 pr-3">Para publicarse</th>
                  <th className="py-2"><span className="sr-only">Ficha</span></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const problems = row.publication_problems ?? [];
                  return (
                    <tr key={row.id} className="border-b border-bd-border/60">
                      {canManage ? (
                        <td className="py-2 pr-3">
                          <input type="checkbox" aria-label={`Seleccionar ${row.name}`} checked={selected.includes(row.id)} onChange={() => toggle(row.id)} />
                        </td>
                      ) : null}
                      <td className="py-2 pr-3 text-foreground">
                        <a href={`/admin/products/${row.id}`} className="hover:underline">{row.name}</a>
                        {!row.is_active ? <span className="ml-2 text-xs text-muted">(inactivo)</span> : null}
                      </td>
                      <td className="py-2 pr-3 text-muted">{articleKindLabel(row)}</td>
                      <td className="py-2 pr-3">{row.is_published_online ? "Publicado" : "Oculto"}</td>
                      <td className="py-2 pr-3 text-muted">
                        {problems.length ? `Le falta: ${problems.map(publicationProblemLabel).join(", ")}` : "Listo"}
                      </td>
                      <td className="py-2 text-right">
                        <button
                          type="button"
                          onClick={(event) => { opener.current = event.currentTarget; setOpenProduct(row); }}
                          aria-label={`${canManage ? "Completar ficha" : "Ver ficha"} de ${row.name}`}
                          className="whitespace-nowrap text-sm font-semibold text-foreground hover:underline"
                        >
                          {canManage ? "Completar ficha" : "Ver ficha"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : null}
        {pages > 1 ? (
          <div className="flex items-center justify-between text-sm">
            <button type="button" disabled={page <= 1} onClick={() => setPage((p) => p - 1)} className={internalButtonClass}>Anterior</button>
            <span className="text-muted">Página {page} de {pages}</span>
            <button type="button" disabled={page >= pages} onClick={() => setPage((p) => p + 1)} className={internalButtonClass}>Siguiente</button>
          </div>
        ) : null}
      </section>

      {openProduct ? (
        <CompleteProductPanel
          key={openProduct.id}
          product={openProduct}
          canManage={canManage}
          companyId={companyId}
          returnFocusTo={opener}
          onClose={() => setOpenProduct(null)}
          onChanged={() => setReload((n) => n + 1)}
        />
      ) : null}

      <section className="space-y-4 rounded-xl border border-bd-border bg-surface p-5">
        <div>
          <h2 className="text-base font-semibold text-foreground">Modelos</h2>
          <p className="mt-1 text-xs text-muted">
            Un modelo oculto saca de la tienda su página y sus equipos. Sus variantes, su stock y su historial no cambian.
          </p>
        </div>
        {modelNotice ? <p role="status" className="text-sm text-foreground">{modelNotice}</p> : null}
        {canManage && modelSelected.length ? (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold text-foreground">
              {modelSelected.length === 1 ? "1 modelo seleccionado" : `${modelSelected.length} modelos seleccionados`}
            </span>
            <button type="button" onClick={() => applyModels("publish")} className={internalButtonClass}>Publicar modelos</button>
            <button type="button" onClick={() => applyModels("hide")} className={internalButtonClass}>Ocultar modelos</button>
          </div>
        ) : null}
        {modelError ? <p className="text-sm text-danger">{modelError}</p> : null}
        {models && models.length === 0 ? <p className="text-sm text-muted">Todavía no hay modelos.</p> : null}
        {models && models.length ? (
          <div className="relative overflow-x-auto">
            <table aria-label="Modelos" className="w-full min-w-[480px] text-left text-sm">
              <thead>
                <tr className="border-b border-bd-border text-xs text-muted">
                  {canManage ? <th className="py-2 pr-3"><span className="sr-only">Seleccionar</span></th> : null}
                  <th className="py-2 pr-3">Modelo</th>
                  <th className="py-2 pr-3">Variantes</th>
                  <th className="py-2">E-commerce</th>
                </tr>
              </thead>
              <tbody>
                {models.map((model) => (
                  <tr key={model.id} className="border-b border-bd-border/60">
                    {canManage ? (
                      <td className="py-2 pr-3">
                        <input type="checkbox" aria-label={`Seleccionar modelo ${model.name}`} checked={modelSelected.includes(model.id)} onChange={() => toggleModel(model.id)} />
                      </td>
                    ) : null}
                    <td className="py-2 pr-3 text-foreground">
                      <a href={`/admin/products/models/${model.id}`} className="hover:underline">{model.name}</a>
                    </td>
                    <td className="py-2 pr-3 text-muted">{model.variant_count}</td>
                    <td className="py-2">{model.is_published_online ? "Publicado" : "Oculto"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>
    </div>
  );
}
