"use client";

/**
 * ECOM-READY-D1 — «Publicar listos»: la publicación es una decisión por producto.
 *
 * Un producto oculto con precio y categoría es igual a uno que aún no se publicó
 * y a uno que se dejó oculto a propósito (una prueba, un borrador). Por eso nada
 * viene marcado: se ve CADA candidato (nombre, código, precio, categoría) y se
 * marcan los que se quieren. Se publica por sus ids (la acción en bloque de
 * siempre: rechaza lo que tiene problemas y deja `AdminAuditLog`), nunca por
 * «todo lo que coincide». De a un lote de la página (50) por vez.
 */
import { useEffect, useId, useRef, useState } from "react";
import type { AdminProduct } from "../../lib/admin";
import { internalButtonClass, internalPrimaryButtonClass } from "./internal-ui";
import { useModalBehaviour } from "./useModalBehaviour";

export function PublishReadyDialog({
  candidates, total, pageSize, busy, onPublish, onCancel,
}: {
  /** Los candidatos de ESTA página, todos. */
  candidates: AdminProduct[];
  /** Cuántos coinciden en total (puede superar la página). */
  total: number;
  pageSize: number;
  busy: boolean;
  onPublish: (ids: number[]) => void;
  onCancel: () => void;
}) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const [checked, setChecked] = useState<Set<number>>(() => new Set());

  useModalBehaviour(panel, () => { if (!busy) onCancel(); });
  // Enters by «Cancelar»: Enter without looking publishes nothing.
  useEffect(() => { cancel.current?.focus(); }, []);

  const toggle = (id: number) => setChecked((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const allChecked = candidates.length > 0 && checked.size === candidates.length;
  const ids = candidates.filter((c) => checked.has(c.id)).map((c) => c.id).sort((a, b) => a - b);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="max-h-[90vh] w-full max-w-2xl overflow-y-auto overscroll-contain rounded-xl border border-bd-border bg-surface p-5 shadow-xl sm:p-6"
      >
        <h2 id={titleId} className="text-lg font-bold text-foreground">Publicar listos en e-commerce</h2>
        <p className="mt-2 text-sm text-muted">
          Estos productos están ocultos y no les falta nada. Un producto de prueba o un borrador se ve igual:
          marca sólo los que quieras que el cliente vea. Los demás siguen ocultos.
        </p>
        {total > candidates.length ? (
          <p className="mt-2 text-sm font-semibold text-foreground">
            Hay {total} productos listos; se publican de a {pageSize}. Al terminar, la lista muestra los siguientes.
          </p>
        ) : null}

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => setChecked(allChecked ? new Set() : new Set(candidates.map((c) => c.id)))}
            className={internalButtonClass}
          >
            {allChecked ? "Quitar todos" : "Seleccionar todos los visibles"}
          </button>
          <p className="text-sm text-muted" aria-live="polite">
            {ids.length === 1 ? "1 marcado" : `${ids.length} marcados`} de {candidates.length}
          </p>
        </div>

        <ul aria-label="Productos listos para publicar" className="mt-3 divide-y divide-bd-border/60 rounded-lg border border-bd-border">
          {candidates.map((item) => (
            <li key={item.id} className="p-3">
              <label className="flex cursor-pointer items-start gap-3">
                <input
                  type="checkbox"
                  aria-label={`Publicar ${item.name}`}
                  checked={checked.has(item.id)}
                  onChange={() => toggle(item.id)}
                  className="mt-1"
                />
                <span className="min-w-0 flex-1 text-sm">
                  <span className="block break-words font-semibold text-foreground">{item.name}</span>
                  <span className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted">
                    <span>{item.primary_code || "Sin código"}</span>
                    <span>S/ {item.price}</span>
                    <span>{item.category_name || "Sin categoría"}</span>
                  </span>
                </span>
              </label>
            </li>
          ))}
        </ul>

        <div className="mt-5 flex flex-wrap justify-end gap-2">
          <button ref={cancel} type="button" onClick={onCancel} disabled={busy} className={internalButtonClass}>Cancelar</button>
          <button
            type="button"
            onClick={() => onPublish(ids)}
            disabled={busy || ids.length === 0}
            className={internalPrimaryButtonClass}
          >
            {busy ? "Publicando…" : `Publicar ${ids.length}`}
          </button>
        </div>
      </div>
    </div>
  );
}
