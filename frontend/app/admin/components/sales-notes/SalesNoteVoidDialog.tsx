"use client";

/**
 * SALES-NOTE-CONTROL-01 — «Anular nota de venta», sólo para el usuario Master.
 *
 * Tipo de motivo y motivo obligatorios. El diálogo dice qué NO es anular la
 * nota: no cancela el pedido, no devuelve dinero, no mueve stock y no es una
 * baja ni una nota de crédito SUNAT. La nota conserva su número, que nunca se
 * reutiliza.
 *
 * Mismo comportamiento de foco que la cancelación de pedidos: entra por el
 * primer campo, el Tab no sale del diálogo y Esc cierra.
 */
import { useEffect, useId, useRef, useState } from "react";
import {
  SALES_NOTE_VOID_REASON_MAX,
  SALES_NOTE_VOID_REASON_MIN,
  SALES_NOTE_VOID_REASON_TYPES,
  type SalesNote,
  type SalesNoteDetail,
  type SalesNoteVoidReasonType,
  voidSalesNote,
} from "../../../lib/inventory";
import { internalButtonClass } from "../internal-ui";

export const dangerButtonClass =
  "inline-flex items-center justify-center rounded-lg border border-danger-border px-3 py-1.5 text-sm font-semibold text-danger transition hover:bg-danger-surface disabled:cursor-not-allowed disabled:opacity-40";

/** El motivo como lo guarda el servidor: sin espacios de sobra, también dentro. */
export function normaliseVoidReason(text: string): string {
  return text.split(/\s+/).filter(Boolean).join(" ");
}

export function SalesNoteVoidDialog({
  note, onClose, onVoided,
}: {
  note: SalesNote;
  onClose: () => void;
  onVoided: (note: SalesNoteDetail) => void;
}) {
  const titleId = useId();
  const reasonId = useId();
  const panel = useRef<HTMLDivElement>(null);
  const [reasonType, setReasonType] = useState<SalesNoteVoidReasonType | "">("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ready = reasonType !== "" && normaliseVoidReason(reason).length >= SALES_NOTE_VOID_REASON_MIN;

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    panel.current?.querySelector<HTMLElement>("input")?.focus();
    return () => { if (previous?.isConnected) previous.focus(); };
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (!busy) onClose();
        return;
      }
      if (event.key !== "Tab" || !panel.current) return;
      const stops = [...panel.current.querySelectorAll<HTMLElement>(
        "input:not([disabled]), textarea:not([disabled]), button:not([disabled])",
      )];
      if (stops.length === 0) return;
      const at = stops.indexOf(document.activeElement as HTMLElement);
      const next = event.shiftKey ? (at <= 0 ? stops.length - 1 : at - 1) : (at + 1) % stops.length;
      event.preventDefault();
      stops[next].focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  async function confirm() {
    if (!ready || busy) return;
    setBusy(true);
    setError(null);
    try {
      onVoided(await voidSalesNote(note.id, { reason_type: reasonType, reason: normaliseVoidReason(reason) }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo anular la nota de venta.");
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-xl border border-bd-border bg-surface p-5 shadow-xl sm:p-6"
      >
        <h2 id={titleId} className="text-lg font-bold text-foreground">
          Anular nota de venta {note.number}
        </h2>
        <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-muted">
          <li>La nota se conserva, marcada ANULADA, y conserva su número: nunca se reutiliza.</li>
          <li>No cancela el pedido, no devuelve dinero y no mueve stock.</li>
          <li>No es una baja ni una nota de crédito SUNAT: los comprobantes electrónicos van por el flujo fiscal.</li>
        </ul>

        <fieldset className="mt-4" disabled={busy}>
          <legend className="text-sm font-semibold text-foreground">Tipo de motivo</legend>
          <div className="mt-2 flex flex-wrap gap-3">
            {SALES_NOTE_VOID_REASON_TYPES.map((option) => (
              <label key={option.value} className="inline-flex items-center gap-2 text-sm text-foreground">
                <input
                  type="radio"
                  name={`${reasonId}-type`}
                  value={option.value}
                  checked={reasonType === option.value}
                  onChange={() => setReasonType(option.value)}
                />
                {option.label}
              </label>
            ))}
          </div>
        </fieldset>

        <label htmlFor={reasonId} className="mt-4 block text-sm font-semibold text-foreground">
          Motivo
        </label>
        <textarea
          id={reasonId}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          maxLength={SALES_NOTE_VOID_REASON_MAX}
          rows={3}
          required
          disabled={busy}
          aria-describedby={`${reasonId}-hint`}
          className="mt-1 w-full rounded-lg border border-bd-border bg-background px-3 py-2 text-sm text-foreground"
        />
        <p id={`${reasonId}-hint`} className="mt-1 text-xs text-muted">
          Obligatorio, al menos {SALES_NOTE_VOID_REASON_MIN} caracteres. Queda registrado con tu usuario y la fecha.
        </p>
        {error ? <p role="alert" className="mt-3 text-sm text-danger">{error}</p> : null}
        <div className="mt-5 flex flex-wrap justify-end gap-2">
          <button type="button" onClick={onClose} disabled={busy} className={internalButtonClass}>
            Volver
          </button>
          <button type="button" onClick={() => void confirm()} disabled={!ready || busy} className={dangerButtonClass}>
            {busy ? "Anulando…" : "Anular nota"}
          </button>
        </div>
      </div>
    </div>
  );
}
