"use client";

/**
 * «Posponer aviso». Personal: it silences the signed-in person's own reminders of this obligation for 1, 3, 7
 * or 14 days. It does not touch the payment, the balance, the state or anybody else's notices, and when the time
 * is up the person gets one «aviso pospuesto» if the payment is still owed.
 */
import { useRef, useState } from "react";
import {
  snoozeObligation,
  unsnoozeObligation,
  type Obligation,
} from "../../lib/finance-api";
import { formatDate } from "../../lib/finance-dates";
import { internalButtonClass } from "../../components/internal-ui";
import { FinanceModal } from "./FinanceModal";

export const SNOOZE_CHOICES = [1, 3, 7, 14] as const;

export function SnoozeDialog({
  companyId,
  obligation,
  onClose,
  onChanged,
}: {
  companyId: number | null;
  obligation: Pick<Obligation, "id" | "concept" | "snoozed_until">;
  onClose: () => void;
  /** The new `snoozed_until` for this person (null: no snooze). */
  onChanged: (snoozedUntil: string | null) => void;
}) {
  const sending = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(work: () => Promise<string | null>) {
    if (sending.current) return;
    sending.current = true;
    setBusy(true);
    setError(null);
    try {
      onChanged(await work());
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cambiar el aplazamiento.");
      sending.current = false;
      setBusy(false);
    }
  }

  return (
    <FinanceModal
      title="Posponer aviso"
      description={obligation.concept}
      onClose={onClose}
      busy={busy}
    >
      <div className="space-y-4">
        <p className="rounded-lg border border-info-border bg-info-surface p-3 text-sm text-foreground">
          Esto sólo silencia <strong>tus</strong> avisos de este pago. <strong>No cambia el pago</strong>: el saldo, el
          vencimiento y el estado siguen igual, y los demás siguen recibiendo sus avisos. Cuando termine el
          plazo recibirás un aviso si todavía se debe.
        </p>
        {obligation.snoozed_until ? (
          <p className="text-sm text-muted">
            Ahora tus avisos están pospuestos hasta el <strong className="text-foreground">{formatDate(obligation.snoozed_until)}</strong>.
          </p>
        ) : null}
        <fieldset disabled={busy}>
          <legend className="text-sm font-semibold text-foreground">Posponer mis avisos por</legend>
          <div className="mt-2 flex flex-wrap gap-2">
            {SNOOZE_CHOICES.map((days) => (
              <button
                key={days}
                type="button"
                onClick={() => void run(async () => (await snoozeObligation(companyId, obligation.id, days)).snoozed_until)}
                className={internalButtonClass}
              >
                {days === 1 ? "1 día" : `${days} días`}
              </button>
            ))}
          </div>
        </fieldset>
        {obligation.snoozed_until ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => void run(async () => {
              await unsnoozeObligation(companyId, obligation.id);
              return null;
            })}
            className={internalButtonClass}
          >
            Quitar pospuesto
          </button>
        ) : null}
        {error ? (
          <p role="alert" className="rounded-lg border border-danger-border bg-danger-surface p-3 text-sm text-danger">
            {error}
          </p>
        ) : null}
        <div className="flex justify-end">
          <button type="button" onClick={onClose} disabled={busy} className={internalButtonClass}>Cerrar</button>
        </div>
      </div>
    </FinanceModal>
  );
}
