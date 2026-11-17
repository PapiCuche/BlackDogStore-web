"use client";

/**
 * Productos › Modelos › un modelo › «Accesorios compatibles» — P2 · F3.
 *
 * The accessories (products WITHOUT a model) that fit this model, in the order the shop will show
 * them. Searching, adding, moving and removing only change the draft on screen; nothing is sent
 * until «Guardar accesorios», which sends the WHOLE ordered list (`PUT …/accessories/`). The server
 * refuses a repeated id, a device or a product of another company, and audits only a real change.
 * Without `products.manage` the list is read-only.
 */

import { useCallback, useEffect, useId, useRef, useState } from "react";

import {
  CatalogApiError,
  fetchModelAccessories,
  saveModelAccessories,
  searchAccessoryCandidates,
  type ModelAccessory,
} from "../lib/internal-api";
import { EmptyBox, Panel, Spinner, formatSoles } from "./InventoryUi";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";

const message = (error: unknown, fallback: string) => (error instanceof Error && error.message ? error.message : fallback);

const touch = "min-h-[44px]";
const smallButton =
  "inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-lg border border-bd-border px-3 text-xs font-semibold text-foreground transition-colors hover:border-foreground/25 disabled:cursor-not-allowed disabled:opacity-40";

const sameOrder = (a: ModelAccessory[], b: ModelAccessory[]) =>
  a.length === b.length && a.every((item, index) => item.id === b[index].id);

export function ModelAccessoriesPanel({
  modelId, companyId, canManage,
}: { modelId: number; companyId: number | null; canManage: boolean }) {
  const ids = useId();
  // What the server last answered, and the draft the person is editing.
  const [saved, setSaved] = useState<ModelAccessory[] | null>(null);
  const [draft, setDraft] = useState<ModelAccessory[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [results, setResults] = useState<ModelAccessory[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const loadRequest = useRef(0);
  const searchRequest = useRef(0);
  // The control that takes the focus after the list re-renders: the one clicked may be disabled or gone.
  const pendingFocus = useRef<string | null>(null);
  const searchId = `${ids}-search`;
  const controlId = (kind: "up" | "down" | "remove", id: number) => `${ids}-${kind}-${id}`;

  useEffect(() => {
    const target = pendingFocus.current;
    if (target === null) return;
    pendingFocus.current = null;
    document.getElementById(target)?.focus();
  }, [draft]);

  const load = useCallback(() => {
    const request = ++loadRequest.current;
    return fetchModelAccessories(modelId, companyId).then(
      (answer) => {
        if (request !== loadRequest.current) return;
        setLoadError(null);
        setSaved(answer.accessories);
        setDraft(answer.accessories);
      },
      (err) => {
        if (request === loadRequest.current) setLoadError(message(err, "No se pudieron cargar los accesorios compatibles."));
      },
    );
  }, [modelId, companyId]);

  useEffect(() => { void load(); }, [load]);

  const dirty = saved !== null && !sameOrder(saved, draft);
  const chosen = new Set(draft.map((item) => item.id));

  const edit = (next: ModelAccessory[]) => { setDraft(next); setNotice(null); setSaveError(null); };

  const move = (index: number, step: -1 | 1) => {
    const target = index + step;
    if (target < 0 || target >= draft.length) return;
    const next = [...draft];
    [next[index], next[target]] = [next[target], next[index]];
    const id = draft[index].id;
    // At the top, «Subir» is disabled: its «Bajar» takes the focus (and the other way round at the bottom).
    const kind = step === -1 ? (target === 0 ? "down" : "up") : (target === draft.length - 1 ? "up" : "down");
    pendingFocus.current = controlId(kind, id);
    edit(next);
  };

  const remove = (index: number) => {
    const following = draft[index + 1];
    pendingFocus.current = following ? controlId("remove", following.id) : searchId;
    edit(draft.filter((_, position) => position !== index));
  };

  const add = (item: ModelAccessory) => {
    pendingFocus.current = searchId;
    edit([...draft, item]);
  };

  const runSearch = async (event: React.FormEvent) => {
    event.preventDefault();
    const request = ++searchRequest.current;
    setSearching(true);
    setSearchError(null);
    try {
      const found = await searchAccessoryCandidates(search, companyId);
      if (request === searchRequest.current) setResults(found);
    } catch (err) {
      if (request === searchRequest.current) setSearchError(message(err, "No se pudieron buscar accesorios."));
    } finally {
      if (request === searchRequest.current) setSearching(false);
    }
  };

  const save = async () => {
    setSaving(true);
    setSaveError(null);
    setNotice(null);
    try {
      const answer = await saveModelAccessories(
        modelId, draft.map((item) => item.id), companyId, (saved ?? []).map((item) => item.id),
      );
      loadRequest.current += 1; // a read still in flight must not overwrite what was just saved
      setSaved(answer.accessories);
      setDraft(answer.accessories);
      setNotice(answer.changed === false ? "No había cambios que guardar." : "Accesorios guardados.");
    } catch (err) {
      setSaveError(message(err, "No se pudieron guardar los accesorios compatibles."));
      // 409: somebody saved since this list was read. The draft was built on a stale list: it is dropped
      // and the saved list read again, so the next save starts from what is really there.
      if (err instanceof CatalogApiError && err.status === 409) void load();
    } finally {
      setSaving(false);
    }
  };

  return (
    <Panel
      id="accesorios"
      title="Accesorios compatibles"
      description="Los accesorios que la página del modelo ofrece junto al equipo, en este orden. Sólo se muestran los activos y publicados."
    >
      {loadError ? (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-danger-border bg-danger-surface px-4 py-3">
          <p className="text-sm text-danger">{loadError}</p>
          <button type="button" className={`${touch} ${internalButtonClass}`} onClick={() => { setLoadError(null); setSaved(null); void load(); }}>Reintentar</button>
        </div>
      ) : saved === null ? (
        <Spinner label="Cargando accesorios…" />
      ) : (
        <div className="space-y-5">
          {draft.length === 0 ? (
            <EmptyBox message="Este modelo aún no tiene accesorios compatibles." />
          ) : (
            <ol aria-label="Accesorios de este modelo" className="divide-y divide-bd-border rounded-xl border border-bd-border">
              {draft.map((item, index) => (
                <li key={item.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                  <span className="w-6 text-xs tabular-nums text-muted" aria-hidden="true">{index + 1}.</span>
                  <div className="min-w-0 flex-1">
                    <p data-testid="accessory-name" className="truncate text-sm font-medium text-foreground">{item.name}</p>
                    <p className="text-xs text-muted">
                      <span>{formatSoles(item.price)}</span>
                      {!item.shown_online ? (
                        <span className="ml-2 inline-flex rounded-full border border-warning-border bg-warning-surface px-2 py-0.5 text-[11px] font-semibold text-warning">
                          No se muestra en la tienda
                        </span>
                      ) : null}
                    </p>
                  </div>
                  {canManage ? (
                    <div className="flex gap-2">
                      <button type="button" id={controlId("up", item.id)} className={smallButton} disabled={saving || index === 0}
                        aria-label={`Subir «${item.name}»`} onClick={() => move(index, -1)}>↑</button>
                      <button type="button" id={controlId("down", item.id)} className={smallButton}
                        disabled={saving || index === draft.length - 1}
                        aria-label={`Bajar «${item.name}»`} onClick={() => move(index, 1)}>↓</button>
                      <button type="button" id={controlId("remove", item.id)} className={smallButton} disabled={saving}
                        aria-label={`Quitar «${item.name}»`} onClick={() => remove(index)}>
                        Quitar
                      </button>
                    </div>
                  ) : null}
                </li>
              ))}
            </ol>
          )}

          {canManage ? (
            <>
              <form onSubmit={runSearch} className="flex flex-wrap items-end gap-2" role="search" aria-label="Buscar accesorios para agregar">
                <label htmlFor={searchId} className="block min-w-[14rem] flex-1 text-xs text-muted">
                  Buscar accesorio
                  <input
                    id={searchId}
                    type="search"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="Nombre del accesorio"
                    className={`mt-1.5 ${internalInputClass}`}
                  />
                </label>
                <button type="submit" className={`${touch} ${internalButtonClass}`} disabled={searching}>
                  {searching ? "Buscando…" : "Buscar"}
                </button>
              </form>
              {searchError ? <p role="alert" className="text-sm text-danger">{searchError}</p> : null}
              {results !== null && !searchError ? (
                results.length === 0 ? (
                  <p className="text-sm text-muted">Ningún accesorio coincide con la búsqueda.</p>
                ) : (
                  <ul aria-label="Resultados de la búsqueda" className="divide-y divide-bd-border rounded-xl border border-dashed border-bd-border">
                    {results.map((item) => {
                      const already = chosen.has(item.id);
                      return (
                        <li key={item.id} className="flex flex-wrap items-center gap-3 px-4 py-2">
                          <div className="min-w-0 flex-1">
                            <p className="truncate text-sm text-foreground">{item.name}</p>
                            <p className="text-xs text-muted">
                              {formatSoles(item.price)}
                              {!item.shown_online ? " · No se muestra en la tienda" : ""}
                            </p>
                          </div>
                          <button type="button" className={smallButton} disabled={already || saving}
                            aria-label={`Agregar «${item.name}»`} onClick={() => add(item)}>
                            {already ? "Ya agregado" : "Agregar"}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                )
              ) : null}

              <div className="flex flex-wrap items-center justify-end gap-2 border-t border-bd-border pt-4">
                <p className="mr-auto text-sm" aria-live="polite">
                  {dirty ? <span className="text-warning">Hay cambios sin guardar.</span> : null}
                  {!dirty && notice ? <span role="status" className="text-success">{notice}</span> : null}
                </p>
                {saveError ? <p role="alert" className="w-full text-sm text-danger">{saveError}</p> : null}
                <button type="button" className={`${touch} ${internalButtonClass}`} disabled={!dirty || saving}
                  onClick={() => { if (saved) edit(saved); }}>
                  Descartar cambios
                </button>
                <button type="button" className={`${touch} ${internalPrimaryButtonClass}`} disabled={!dirty || saving}
                  onClick={() => void save()}>
                  {saving ? "Guardando…" : "Guardar accesorios"}
                </button>
              </div>
            </>
          ) : null}
        </div>
      )}
    </Panel>
  );
}
