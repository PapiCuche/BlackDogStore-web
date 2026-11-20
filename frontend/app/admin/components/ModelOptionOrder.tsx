"use client";

/**
 * Productos › Modelos › un modelo › «Orden en la tienda» — CATALOG-REFERENCE-01 · PR E.
 *
 * The order in which THIS model shows its options (its colours, say) on its public page. It never changes the
 * company's own order nor any other model. Only the options of the model's palette are ordered; one added later goes
 * after the ordered ones until it is placed. Each move is saved when made (`PUT …/option-order/`, `products.manage`)
 * and focus stays on the option that moved, so the keyboard does it as well as the mouse.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  fetchModelOptionOrder, saveModelOptionOrder, type ModelOptionOrder as OrderPayload, type OrderedAttribute,
} from "../lib/catalog-presentation-api";
import { ErrorBox, Panel, Spinner } from "./InventoryUi";
import { internalButtonClass } from "./internal-ui";

const message = (error: unknown, fallback: string) => (error instanceof Error ? error.message : fallback);

const smallButton = `${internalButtonClass} min-h-[44px] min-w-[44px] px-3 py-1.5 text-xs`;

export function ModelOptionOrder({
  modelId, canManage, companyId, refreshKey, onSaved,
}: {
  modelId: number; canManage: boolean; companyId: number | null;
  /** Changes when the palette changed elsewhere on the page: the order is read again. */
  refreshKey?: unknown;
  onSaved?: () => void;
}) {
  const [order, setOrder] = useState<OrderPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState<number | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const focusAfter = useRef<{ optionId: number; move: "up" | "down" } | null>(null);

  const load = useCallback(() => {
    fetchModelOptionOrder(modelId, companyId).then((data) => { setOrder(data); setError(null); }, (err) => setError(message(err, "No se pudo leer el orden de las opciones.")));
  }, [modelId, companyId]);

  useEffect(() => { load(); }, [load, refreshKey]);

  useEffect(() => {
    const wanted = focusAfter.current;
    if (!wanted || saving !== null || !root.current) return;
    focusAfter.current = null;
    const row = root.current.querySelector<HTMLElement>(`[data-option-id="${wanted.optionId}"]`);
    const same = row?.querySelector<HTMLButtonElement>(`[data-move="${wanted.move}"]`);
    const other = row?.querySelector<HTMLButtonElement>(`[data-move="${wanted.move === "up" ? "down" : "up"}"]`);
    (same && !same.disabled ? same : other)?.focus();
  }, [order, saving]);

  async function move(attribute: OrderedAttribute, index: number, step: -1 | 1) {
    const ids = attribute.options.map((option) => option.id);
    const target = index + step;
    if (target < 0 || target >= ids.length) return;
    [ids[index], ids[target]] = [ids[target], ids[index]];
    const moved = attribute.options[index];
    focusAfter.current = { optionId: moved.id, move: step === -1 ? "up" : "down" };
    setSaving(attribute.id);
    setSaveError(null);
    setNotice(null);
    try {
      setOrder(await saveModelOptionOrder(modelId, attribute.id, ids, companyId));
      setNotice(`«${moved.label}» quedó en la posición ${target + 1} de ${attribute.name}.`);
      onSaved?.();
    } catch (err) {
      setSaveError(message(err, "No se pudo guardar el orden."));
      load();
    } finally {
      setSaving(null);
    }
  }

  const attributes = (order?.attributes ?? []).filter((attribute) => attribute.options.length > 1);

  return (
    <Panel
      id="orden-opciones"
      title="Orden en la tienda"
      description="Cómo ordena este modelo sus colores y demás opciones en su página. No cambia el orden de la empresa ni el de otros modelos."
    >
      <div ref={root} className="space-y-4">
        {error ? (
          <div className="space-y-2">
            <ErrorBox message={error} />
            <button type="button" className={smallButton} onClick={load}>Reintentar</button>
          </div>
        ) : order === null ? <Spinner label="Cargando el orden…" /> : !order.has_palette ? (
          <p className="text-sm text-muted">Primero marca las opciones del modelo en «Opciones de este modelo»: luego podrás ordenarlas aquí.</p>
        ) : attributes.length === 0 ? (
          <p className="text-sm text-muted">Cada atributo de este modelo tiene una sola opción: no hay nada que ordenar.</p>
        ) : (
          <>
            {notice ? <p role="status" className="rounded-lg border border-success-border px-3 py-2 text-xs text-success">{notice}</p> : null}
            {saveError ? <p role="alert" className="rounded-lg border border-danger-border bg-danger-surface px-3 py-2 text-xs text-danger">{saveError}</p> : null}
            <div className="grid gap-4 md:grid-cols-2">
              {attributes.map((attribute) => (
                <section key={attribute.id} aria-labelledby={`orden-${attribute.id}`} className="min-w-0 rounded-lg border border-bd-border p-3">
                  <h3 id={`orden-${attribute.id}`} className="text-sm font-semibold text-foreground">{attribute.name}</h3>
                  {attribute.options.some((option) => !option.placed) && attribute.options.some((option) => option.placed) ? (
                    <p className="mt-1 text-xs text-muted">Las opciones sin ordenar van al final, en el orden de la empresa.</p>
                  ) : null}
                  <ol className="mt-2 space-y-1.5" aria-label={`Orden de ${attribute.name}`}>
                    {attribute.options.map((option, index) => (
                      <li key={option.id} data-option-id={option.id} className="flex min-w-0 items-center gap-2 rounded-lg border border-bd-border bg-background px-2 py-1">
                        <span className="w-5 shrink-0 text-right text-xs tabular-nums text-muted">{index + 1}</span>
                        {/^#[0-9A-Fa-f]{6}$/.test(option.swatch_hex) ? (
                          <span aria-hidden="true" className="h-4 w-4 shrink-0 rounded-full border border-foreground/25" style={{ backgroundColor: option.swatch_hex }} />
                        ) : null}
                        <span className="min-w-0 flex-1 break-words text-sm text-foreground">{option.label}</span>
                        {canManage ? (
                          <span className="flex shrink-0 gap-1">
                            <button
                              type="button"
                              data-move="up"
                              className={smallButton}
                              aria-label={`Subir ${option.label}`}
                              disabled={saving !== null || index === 0}
                              onClick={() => void move(attribute, index, -1)}
                            >
                              <span aria-hidden="true">↑</span>
                            </button>
                            <button
                              type="button"
                              data-move="down"
                              className={smallButton}
                              aria-label={`Bajar ${option.label}`}
                              disabled={saving !== null || index === attribute.options.length - 1}
                              onClick={() => void move(attribute, index, 1)}
                            >
                              <span aria-hidden="true">↓</span>
                            </button>
                          </span>
                        ) : null}
                      </li>
                    ))}
                  </ol>
                </section>
              ))}
            </div>
          </>
        )}
      </div>
    </Panel>
  );
}
