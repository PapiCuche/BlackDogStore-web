"use client";

/**
 * SALES-NOTE-CONTROL-01 — la marca de estado de una nota de venta interna.
 *
 * Una nota anulada se conserva y se ve: ANULADA, la misma palabra que llevan
 * sus PDF. Si el motivo fue «prueba», se dice al lado.
 */
import type { SalesNote } from "../../../lib/inventory";

export function SalesNoteStatusBadge({ note }: { note: SalesNote }) {
  if (!note.is_void) {
    return (
      <span className="inline-flex items-center rounded-full border border-bd-border px-2 py-0.5 text-[11px] font-semibold text-foreground/80">
        Vigente
      </span>
    );
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <span className="inline-flex items-center rounded-full border border-danger-border bg-danger-surface px-2 py-0.5 text-[11px] font-bold tracking-wide text-danger">
        ANULADA
      </span>
      {note.void_reason_type === "prueba" ? (
        <span className="inline-flex items-center rounded-full border border-warning-border bg-warning-surface px-2 py-0.5 text-[11px] font-semibold text-foreground">
          Prueba
        </span>
      ) : null}
    </span>
  );
}
