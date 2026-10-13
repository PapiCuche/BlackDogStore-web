"use client";

/**
 * INVENTORY-UNIFIED-01 — publicar u ocultar varios productos a la vez.
 *
 * Tres piezas de la lista de productos: la barra con las dos acciones, la
 * confirmación (dice la acción y la cuenta exacta antes de hacer nada) y el
 * resultado (cuántos cambiaron, cuántos ya estaban, cuáles no se publicaron y
 * por qué). Sólo cambia «en la web»: ni activo, ni precio, ni stock.
 */
import { useEffect, useId, useRef } from "react";
import {
  BulkVisibilityAction,
  BulkVisibilityResult,
  publicationProblemLabel,
} from "../../lib/admin";
import { internalButtonClass, internalPrimaryButtonClass } from "./internal-ui";

const products = (count: number) => (count === 1 ? "1 producto" : `${count} productos`);

export function BulkVisibilityBar({
  count, onAction, onClear,
}: {
  count: number;
  onAction: (action: BulkVisibilityAction) => void;
  onClear: () => void;
}) {
  return (
    <div className="mb-4 flex flex-col gap-3 rounded-lg border border-bd-border bg-background p-3 sm:flex-row sm:items-center sm:justify-between">
      <p className="text-sm font-semibold text-foreground" aria-live="polite">
        {count === 1 ? "1 producto seleccionado" : `${count} productos seleccionados`}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => onAction("publish")} className={internalPrimaryButtonClass}>
          Publicar en e-commerce
        </button>
        <button type="button" onClick={() => onAction("hide")} className={internalButtonClass}>
          Ocultar del e-commerce
        </button>
        <button type="button" onClick={onClear} className="px-2 py-2 text-sm font-semibold text-muted transition hover:text-foreground hover:underline">
          Quitar selección
        </button>
      </div>
    </div>
  );
}

export function BulkVisibilityConfirm({
  action, count, busy, onConfirm, onCancel,
}: {
  action: BulkVisibilityAction;
  count: number;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    // Entra por «Cancelar»: Enter sin mirar no cambia cientos de productos.
    const previous = document.activeElement as HTMLElement | null;
    cancel.current?.focus();
    return () => { if (previous?.isConnected) previous.focus(); };
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (!busy) onCancel();
        return;
      }
      if (event.key !== "Tab" || !panel.current) return;
      // El foco da la vuelta dentro del diálogo: lo de detrás no se alcanza.
      const stops = [...panel.current.querySelectorAll<HTMLElement>("button:not([disabled])")];
      if (stops.length === 0) return;
      const at = stops.indexOf(document.activeElement as HTMLElement);
      const next = event.shiftKey ? (at <= 0 ? stops.length - 1 : at - 1) : (at + 1) % stops.length;
      event.preventDefault();
      stops[next].focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onCancel]);

  const one = count === 1;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="w-full max-w-md rounded-xl border border-bd-border bg-surface p-5 shadow-xl sm:p-6"
      >
        <h2 id={titleId} className="text-lg font-bold text-foreground">
          {action === "publish"
            ? `Publicar ${products(count)} en e-commerce`
            : `Ocultar ${products(count)} del e-commerce`}
        </h2>
        <p className="mt-3 text-sm text-muted">
          {action === "publish"
            ? "Se publican en la tienda en línea los que estén listos (activos, con precio, imagen y categoría). Los que no, se quedan como están y se indica el motivo."
            : one
              ? "Dejará de verse y de venderse en la tienda en línea. Seguirá en inventario, en caja y en servicio técnico."
              : "Dejarán de verse y de venderse en la tienda en línea. Seguirán en inventario, en caja y en servicio técnico."}
        </p>
        <p className="mt-2 text-sm text-muted">No cambia si están activos, ni su precio, ni su stock.</p>
        <div className="mt-5 flex flex-wrap justify-end gap-2">
          <button ref={cancel} type="button" onClick={onCancel} disabled={busy} className={internalButtonClass}>
            Cancelar
          </button>
          <button type="button" onClick={onConfirm} disabled={busy} className={internalPrimaryButtonClass}>
            {busy ? "Aplicando…" : "Confirmar"}
          </button>
        </div>
      </div>
    </div>
  );
}

function changedText(result: BulkVisibilityResult): string {
  const { action, changed } = result;
  if (action === "publish") {
    if (changed === 0) return "No se publicó ningún producto.";
    return changed === 1 ? "Se publicó 1 producto en e-commerce." : `Se publicaron ${changed} productos en e-commerce.`;
  }
  if (changed === 0) return "No se ocultó ningún producto.";
  return changed === 1 ? "Se ocultó 1 producto del e-commerce." : `Se ocultaron ${changed} productos del e-commerce.`;
}

function unchangedText(result: BulkVisibilityResult): string {
  const { action, unchanged } = result;
  if (unchanged === 0) return "";
  if (action === "publish") return unchanged === 1 ? "1 ya estaba publicado." : `${unchanged} ya estaban publicados.`;
  return unchanged === 1 ? "1 ya estaba oculto." : `${unchanged} ya estaban ocultos.`;
}

export function BulkVisibilityResultPanel({
  result, onClose,
}: {
  result: BulkVisibilityResult;
  onClose: () => void;
}) {
  const refused = result.refused;
  return (
    <div role="status" className="mb-4 rounded-lg border border-bd-border bg-background p-4 text-sm text-foreground">
      <div className="flex items-start justify-between gap-3">
        <p>
          <span className="font-semibold">{changedText(result)}</span>
          {unchangedText(result) ? <span className="text-muted"> {unchangedText(result)}</span> : null}
        </p>
        <button type="button" onClick={onClose} aria-label="Cerrar resultado" className="shrink-0 text-xs font-semibold text-muted transition hover:text-foreground hover:underline">
          Cerrar
        </button>
      </div>
      {refused.length > 0 ? (
        <div className="mt-3">
          <p className="font-semibold text-warning">
            {refused.length === 1
              ? "1 no se publicó porque le falta algo:"
              : `${refused.length} no se publicaron porque les falta algo:`}
          </p>
          <ul className="mt-2 max-h-60 list-disc space-y-1 overflow-y-auto pl-5 text-muted">
            {refused.map((item) => (
              <li key={item.id}>{item.name}: {item.problems.map(publicationProblemLabel).join(", ")}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
