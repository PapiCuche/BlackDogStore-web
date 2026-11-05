"use client";

// Decisión D — search inside the draft of a transfer.
//
// The filters only choose which rows are SHOWN. The quantities belong to the
// page (keyed by product id) and «Guardar líneas» sends all of them, hidden or
// not, exactly as before this screen had a search. Nothing here touches stock
// rules or permissions: the server still decides every line.

import { useId, useMemo, useRef, useState } from "react";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "../../../components/internal-ui";
import { EmptyBox, Spinner } from "../../../components/InventoryUi";
import {
  draftLines,
  hasQuantity,
  matchesSearch,
  type DraftRow,
  type TransferDraft,
} from "./transfer-draft";

type Props = {
  rows: DraftRow[];
  draft: TransferDraft;
  onQuantityChange: (productId: number, value: string) => void;
  onSave: () => void;
  busy: boolean;
  /** The catalogue is still being read: no row count or «nothing matches» is true yet. */
  loading?: boolean;
  /** A page of the catalogue failed: what is shown is only part of it. */
  incomplete?: boolean;
  onRetry?: () => void;
};

export function TransferDraftLines({
  rows, draft, onQuantityChange, onSave, busy, loading = false, incomplete = false, onRetry,
}: Props) {
  const ids = useId();
  const searchRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [onlyWithQuantity, setOnlyWithQuantity] = useState(false);
  // Rows edited while «Sólo con cantidad» is on stay put until the filter is
  // applied again: clearing a quantity to retype it must not make the row vanish.
  const [kept, setKept] = useState<ReadonlySet<number>>(() => new Set());

  const visible = useMemo(
    () =>
      rows.filter(
        (row) =>
          matchesSearch(row, query) &&
          (!onlyWithQuantity || hasQuantity(draft[row.id]) || kept.has(row.id)),
      ),
    [rows, query, onlyWithQuantity, draft, kept],
  );

  const lines = draftLines(draft);
  const units = lines.reduce((sum, line) => sum + line.quantity, 0);
  const trimmed = query.trim();

  function changeQuantity(productId: number, value: string) {
    if (onlyWithQuantity && !kept.has(productId)) setKept((prev) => new Set(prev).add(productId));
    onQuantityChange(productId, value);
  }

  function toggleOnlyWithQuantity(next: boolean) {
    setKept(new Set());
    setOnlyWithQuantity(next);
  }

  // An incomplete list must not claim that a product does not exist.
  const scope = incomplete ? "producto cargado" : "producto";
  let emptyMessage = incomplete ? "No se pudo cargar la lista de productos." : "No hay productos para transferir.";
  if (trimmed) {
    emptyMessage = onlyWithQuantity
      ? `Ningún ${scope} con cantidad coincide con «${trimmed}».`
      : `Ningún ${scope} coincide con «${trimmed}».`;
  } else if (onlyWithQuantity) {
    emptyMessage = "Todavía no hay productos con cantidad.";
  }

  function clearSearch() {
    setQuery("");
    // The button unmounts with the query: give the focus back to the field, not to <body>.
    searchRef.current?.focus();
  }

  return (
    <>
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <div className="min-w-0 flex-1 basis-60">
          <label htmlFor={`${ids}-search`} className="mb-1 block text-xs font-semibold text-muted">
            Buscar producto
          </label>
          <div className="flex gap-2">
            <input
              ref={searchRef}
              id={`${ids}-search`}
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Nombre o código"
              autoComplete="off"
              aria-describedby={loading ? undefined : `${ids}-count`}
              className={`${internalInputClass} min-w-0`}
            />
            {query ? (
              <button type="button" onClick={clearSearch} className={`${internalButtonClass} shrink-0`}>
                Limpiar búsqueda
              </button>
            ) : null}
          </div>
        </div>
        <label className="flex min-h-[42px] cursor-pointer items-center gap-2 text-sm text-foreground">
          <input
            type="checkbox"
            checked={onlyWithQuantity}
            onChange={(e) => toggleOnlyWithQuantity(e.target.checked)}
            className="h-4 w-4 accent-current"
          />
          Sólo con cantidad
        </label>
      </div>

      {incomplete ? (
        <div role="alert" className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-warning-border bg-warning-surface px-4 py-3">
          <p className="text-sm text-warning">La lista de productos está incompleta.</p>
          {onRetry ? (
            <button type="button" onClick={onRetry} disabled={loading} className={internalButtonClass}>
              Reintentar
            </button>
          ) : null}
        </div>
      ) : null}

      {loading ? (
        <div className="mb-4">
          <Spinner label="Cargando productos…" />
        </div>
      ) : (
        <>
          <p className="mb-3 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted">
            <span id={`${ids}-count`} aria-live="polite">
              Mostrando {visible.length} de {rows.length}
            </span>
            <span>
              {lines.length} línea(s) con cantidad · {units} unidad(es)
            </span>
          </p>

          {visible.length === 0 ? (
            <div className="mb-4">
              <EmptyBox message={emptyMessage} />
            </div>
          ) : (
            <ul className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {visible.map((row) => (
                <li key={row.id} className="flex min-w-0 items-center gap-2">
                  <div className="min-w-0 flex-1">
                    <label
                      className="block truncate text-sm text-muted"
                      htmlFor={`ti-${row.id}`}
                      title={row.name}
                    >
                      {row.name}
                    </label>
                    {row.code ? (
                      <span id={`ti-${row.id}-code`} className="block truncate font-mono text-[11px] text-muted">
                        {row.code}
                      </span>
                    ) : null}
                  </div>
                  {/* The width lives on the wrapper: the shared input class is `w-full`. */}
                  <div className="w-20 shrink-0">
                    <input
                      id={`ti-${row.id}`}
                      type="number"
                      min={0}
                      inputMode="numeric"
                      aria-describedby={row.code ? `ti-${row.id}-code` : undefined}
                      className={internalInputClass}
                      value={draft[row.id] ?? ""}
                      onChange={(e) => changeQuantity(row.id, e.target.value)}
                    />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      <p className="mb-4 text-xs text-muted">
        Los equipos con número de serie no se transfieren desde aquí.
      </p>

      <button type="button" disabled={busy} onClick={onSave} className={internalPrimaryButtonClass}>
        Guardar líneas
      </button>
    </>
  );
}
