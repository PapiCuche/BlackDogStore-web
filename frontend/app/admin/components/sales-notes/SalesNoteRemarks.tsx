"use client";

/**
 * SALES-NOTE-CONTROL-01 — observaciones internas de una nota de venta.
 *
 * Un historial: se añade, nunca se edita ni se borra. No se imprimen en la
 * nota; el papel dice lo que dijo al emitirse.
 */
import { useId, useState } from "react";
import { formatAdminDate } from "../../../lib/admin";
import {
  SALES_NOTE_REMARK_MAX,
  addSalesNoteRemark,
  type SalesNoteRemark,
} from "../../../lib/inventory";
import { internalButtonClass } from "../internal-ui";

export function SalesNoteRemarks({
  noteId, remarks, canRemark,
}: {
  noteId: number;
  remarks: SalesNoteRemark[];
  canRemark: boolean;
}) {
  const fieldId = useId();
  const [history, setHistory] = useState<SalesNoteRemark[]>(remarks);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function add(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const clean = text.trim();
    if (!clean || busy) return;
    setBusy(true);
    setError(null);
    try {
      const remark = await addSalesNoteRemark(noteId, clean);
      setHistory((current) => [...current, remark]);
      setText("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo guardar la observación.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="Observaciones internas" className="space-y-3">
      <h3 className="text-sm font-semibold text-foreground">Observaciones internas</h3>
      <p className="text-xs text-muted">Sólo para el equipo. No se imprimen en la nota y no se editan.</p>
      {history.length === 0 ? (
        <p className="text-sm text-muted">Sin observaciones.</p>
      ) : (
        <ol className="space-y-2">
          {history.map((remark) => (
            <li key={remark.id} className="rounded-lg border border-bd-border bg-background px-3 py-2">
              <p className="whitespace-pre-wrap text-sm text-foreground">{remark.text}</p>
              <p className="mt-1 text-xs text-muted">
                {remark.author ?? "—"} · {formatAdminDate(remark.created_at)}
              </p>
            </li>
          ))}
        </ol>
      )}
      {canRemark ? (
        <form onSubmit={(event) => void add(event)} className="space-y-2">
          <label htmlFor={fieldId} className="block text-xs font-semibold text-foreground">
            Nueva observación
          </label>
          <textarea
            id={fieldId}
            value={text}
            onChange={(event) => setText(event.target.value)}
            maxLength={SALES_NOTE_REMARK_MAX}
            rows={2}
            disabled={busy}
            className="w-full rounded-lg border border-bd-border bg-background px-3 py-2 text-sm text-foreground"
          />
          {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}
          <button type="submit" disabled={busy || !text.trim()} className={internalButtonClass}>
            {busy ? "Guardando…" : "Añadir observación"}
          </button>
        </form>
      ) : null}
    </section>
  );
}
