"use client";

// Decisión D — search inside the draft of a transfer.
//
// The filters only choose which rows are SHOWN. The quantities belong to the
// page (keyed by product id) and «Guardar líneas» sends all of them, hidden or
// not, exactly as before this screen had a search. Nothing here touches stock
// rules or permissions: the server still decides every line.

import { useId, useMemo, useState } from "react";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "../../../components/internal-ui";
import { EmptyBox } from "../../../components/InventoryUi";
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
};

export function TransferDraftLines({ rows, draft, onQuantityChange, onSave, busy }: Props) {
  const ids = useId();
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

  let emptyMessage = "No hay productos para transferir.";
  if (trimmed) {
    emptyMessage = onlyWithQuantity
      ? `Ningún producto con cantidad coincide con «${trimmed}».`
      : `Ningún producto coincide con «${trimmed}».`;
  } else if (onlyWithQuantity) {
    emptyMessage = "Todavía no hay productos con cantidad.";
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
              id={`${ids}-search`}
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Nombre o código"
              autoComplete="off"
              aria-describedby={`${ids}-count`}
              className={`${internalInputClass} min-w-0`}
            />
            {query ? (
              <button type="button" onClick={() => setQuery("")} className={`${internalButtonClass} shrink-0`}>
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
                  <span id={`ti-${row.id}-code`} className="block truncate font-mono text-[11px] text-muted/80">
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

      <p className="mb-4 text-xs text-muted">
        Los equipos con número de serie no se transfieren desde aquí.
      </p>

      <button type="button" disabled={busy} onClick={onSave} className={internalPrimaryButtonClass}>
        Guardar líneas
      </button>
    </>
  );
}
