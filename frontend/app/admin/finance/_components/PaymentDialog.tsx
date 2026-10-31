"use client";

/**
 * «Registrar pago». This IS a money operation (unlike a reminder): it needs `finance.payments.record`, the
 * server re-checks it, and it moves the balance.
 *
 * ONE Idempotency-Key PER OPEN OF THE DIALOG. A double click, a retry after a dropped answer, or Enter twice
 * send the same key, so the server records the payment once. Closing and opening again is a new payment and a
 * new key. While the request is in flight the form is locked and a second submit is ignored.
 */
import { useId, useRef, useState } from "react";
import {
  METHOD_LABELS,
  newIdempotencyKey,
  recordPayment,
  money,
  type Obligation,
  type PaymentResult,
} from "../../lib/finance-api";
import { internalInputClass, internalPrimaryButtonClass, internalButtonClass } from "../../components/internal-ui";
import { FinanceModal } from "./FinanceModal";
import { hintClass, labelClass } from "./FinanceBits";

const METHODS = ["transfer", "cash", "card", "other"] as const;

export function PaymentDialog({
  companyId,
  obligation,
  today,
  onClose,
  onRecorded,
}: {
  companyId: number | null;
  obligation: Obligation;
  /** The company's today (from the server). It is the default and the latest date a payment may carry. */
  today: string;
  onClose: () => void;
  onRecorded: (result: PaymentResult) => void;
}) {
  const ids = useId();
  const [key] = useState(() => newIdempotencyKey());
  const sending = useRef(false);
  const [amount, setAmount] = useState(obligation.balance);
  const [method, setMethod] = useState<string>("transfer");
  const [paidOn, setPaidOn] = useState(today);
  const [reference, setReference] = useState("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const amountOk = /^\d+(\.\d{1,2})?$/.test(amount.trim()) && Number(amount) > 0;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (sending.current) return; // double click / double Enter
    if (!amountOk) {
      setError("Escribe un importe mayor que cero, con hasta dos decimales.");
      return;
    }
    if (!paidOn || paidOn > today) {
      setError("La fecha del pago no puede ser futura.");
      return;
    }
    sending.current = true;
    setBusy(true);
    setError(null);
    try {
      const result = await recordPayment(
        companyId, obligation.id,
        { amount: amount.trim(), method, paid_on: paidOn, reference: reference.trim(), notes: notes.trim() },
        key,
      );
      onRecorded(result);
    } catch (err) {
      const message = err instanceof Error ? err.message : "No se pudo registrar el pago.";
      setError(message);
      sending.current = false;
      setBusy(false);
    }
  }

  return (
    <FinanceModal
      title="Registrar pago"
      description={`${obligation.concept} · saldo pendiente ${money(obligation.balance, obligation.currency)}`}
      onClose={onClose}
      busy={busy}
    >
      <form onSubmit={submit} className="space-y-4" noValidate>
        <p className="rounded-lg border border-bd-border bg-surface-2 p-3 text-xs text-muted">
          Esto sí registra un pago y baja el saldo. Un aviso o un aplazamiento nunca lo hacen. Si te equivocas, el
          pago se revierte desde el detalle, con motivo.
        </p>
        <div>
          <label htmlFor={`${ids}-amount`} className={labelClass}>Importe ({obligation.currency})</label>
          <input
            id={`${ids}-amount`} inputMode="decimal" value={amount} disabled={busy}
            onChange={(e) => setAmount(e.target.value)} className={`${internalInputClass} mt-1`}
          />
          <p className={hintClass}>Por defecto, el saldo pendiente. Un pago parcial deja el resto pendiente.</p>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor={`${ids}-method`} className={labelClass}>Forma de pago</label>
            <select
              id={`${ids}-method`} value={method} disabled={busy} onChange={(e) => setMethod(e.target.value)}
              className={`${internalInputClass} mt-1`}
            >
              {METHODS.map((m) => <option key={m} value={m}>{METHOD_LABELS[m]}</option>)}
            </select>
          </div>
          <div>
            <label htmlFor={`${ids}-date`} className={labelClass}>Fecha del pago</label>
            <input
              id={`${ids}-date`} type="date" value={paidOn} max={today} disabled={busy}
              onChange={(e) => setPaidOn(e.target.value)} className={`${internalInputClass} mt-1`}
            />
          </div>
        </div>
        <div>
          <label htmlFor={`${ids}-ref`} className={labelClass}>Referencia (opcional)</label>
          <input
            id={`${ids}-ref`} value={reference} maxLength={80} disabled={busy}
            onChange={(e) => setReference(e.target.value)} className={`${internalInputClass} mt-1`}
          />
          <p className={hintClass}>Número de operación o de recibo. Nunca un número de tarjeta.</p>
        </div>
        <div>
          <label htmlFor={`${ids}-notes`} className={labelClass}>Notas (opcional)</label>
          <textarea
            id={`${ids}-notes`} value={notes} rows={2} maxLength={1000} disabled={busy}
            onChange={(e) => setNotes(e.target.value)} className={`${internalInputClass} mt-1`}
          />
        </div>
        {error ? (
          <p role="alert" className="rounded-lg border border-danger-border bg-danger-surface p-3 text-sm text-danger">
            {error}
          </p>
        ) : null}
        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" onClick={onClose} disabled={busy} className={internalButtonClass}>Cancelar</button>
          <button type="submit" disabled={busy || !amountOk} className={internalPrimaryButtonClass}>
            {busy ? "Registrando…" : "Registrar pago"}
          </button>
        </div>
      </form>
    </FinanceModal>
  );
}
