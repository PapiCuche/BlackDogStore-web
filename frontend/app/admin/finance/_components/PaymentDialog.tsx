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
  FinanceApiError,
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
const AMOUNT_SHAPE = /^\d+(\.\d{1,2})?$/;

/** What is wrong with the typed amount, in words; null when it is fine. */
export function amountProblem(text: string): string | null {
  const value = text.trim();
  if (!value) return "Escribe el importe.";
  if (!AMOUNT_SHAPE.test(value)) {
    return "Escribe el importe sólo con números y punto decimal, por ejemplo 1250.00: sin comas, espacios ni «S/».";
  }
  return Number(value) > 0 ? null : "El importe debe ser mayor que cero.";
}

export function PaymentDialog({
  companyId,
  obligation,
  today,
  onClose,
  onRecorded,
  onUncertain,
  onVerify,
}: {
  companyId: number | null;
  obligation: Obligation;
  /** The company's today (from the server). It is the default and the latest date a payment may carry. */
  today: string;
  onClose: () => void;
  onRecorded: (result: PaymentResult) => void;
  /** The answer was lost or the key was refused: the screen reads the lists and the detail again. */
  onUncertain: () => void;
  /** «Ver detalle»: close this dialog and open the payment's detail to check what was recorded. */
  onVerify: (obligation: Obligation) => void;
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
  // `lost`: the answer never came, so the payment may or may not exist. The form freezes: a retry then carries
  // the same bytes and the same key, and the server answers with the payment it already has if it was recorded.
  // `conflict`: the server refused the key for different data; nothing more can be sent from this dialog.
  const [uncertain, setUncertain] = useState<"lost" | "conflict" | null>(null);

  const problem = amount.trim() === "" ? null : amountProblem(amount);
  const frozen = busy || uncertain !== null;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (sending.current || uncertain === "conflict") return; // double click / double Enter
    const wrong = amountProblem(amount);
    if (wrong) {
      setError(wrong);
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
      const status = err instanceof FinanceApiError ? err.status : -1;
      setError(err instanceof Error ? err.message : "No se pudo registrar el pago.");
      if (status === 0 || status === 409) {
        setUncertain(status === 0 ? "lost" : "conflict");
        onUncertain();
      }
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
            id={`${ids}-amount`} inputMode="decimal" value={amount} disabled={frozen}
            onChange={(e) => setAmount(e.target.value)} className={`${internalInputClass} mt-1`}
            aria-invalid={problem ? true : undefined}
            aria-describedby={problem ? `${ids}-amount-problem ${ids}-amount-hint` : `${ids}-amount-hint`}
          />
          {problem ? (
            <p id={`${ids}-amount-problem`} className="mt-1 text-xs font-semibold text-danger">{problem}</p>
          ) : null}
          <p id={`${ids}-amount-hint`} className={hintClass}>Por defecto, el saldo pendiente. Un pago parcial deja el resto pendiente.</p>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor={`${ids}-method`} className={labelClass}>Forma de pago</label>
            <select
              id={`${ids}-method`} value={method} disabled={frozen} onChange={(e) => setMethod(e.target.value)}
              className={`${internalInputClass} mt-1`}
            >
              {METHODS.map((m) => <option key={m} value={m}>{METHOD_LABELS[m]}</option>)}
            </select>
          </div>
          <div>
            <label htmlFor={`${ids}-date`} className={labelClass}>Fecha del pago</label>
            <input
              id={`${ids}-date`} type="date" value={paidOn} max={today} disabled={frozen}
              onChange={(e) => setPaidOn(e.target.value)} className={`${internalInputClass} mt-1`}
            />
          </div>
        </div>
        <div>
          <label htmlFor={`${ids}-ref`} className={labelClass}>Referencia (opcional)</label>
          <input
            id={`${ids}-ref`} value={reference} maxLength={80} disabled={frozen}
            onChange={(e) => setReference(e.target.value)} className={`${internalInputClass} mt-1`}
          />
          <p className={hintClass}>Número de operación o de recibo. Nunca un número de tarjeta.</p>
        </div>
        <div>
          <label htmlFor={`${ids}-notes`} className={labelClass}>Notas (opcional)</label>
          <textarea
            id={`${ids}-notes`} value={notes} rows={2} maxLength={1000} disabled={frozen}
            onChange={(e) => setNotes(e.target.value)} className={`${internalInputClass} mt-1`}
          />
        </div>
        {error ? (
          <p role="alert" className="rounded-lg border border-danger-border bg-danger-surface p-3 text-sm text-danger">
            {error}
          </p>
        ) : null}
        {uncertain ? (
          <div role="note" className="space-y-2 rounded-lg border border-warning-border bg-warning-surface p-3 text-sm text-foreground">
            <p className="font-semibold">Verifica en el detalle si el pago ya se registró.</p>
            <p className="text-muted">
              {uncertain === "lost"
                ? "No llegó la respuesta del servidor, así que el pago pudo haberse guardado. Los datos quedaron congelados: si reintentas, se envía exactamente lo mismo y no se duplica."
                : "El servidor ya usó esta operación con datos distintos. Revisa el detalle; para registrar otro pago, cierra este diálogo y ábrelo de nuevo."}
            </p>
            <button type="button" onClick={() => onVerify(obligation)} className={internalButtonClass}>Ver detalle</button>
          </div>
        ) : null}
        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" onClick={onClose} disabled={busy} className={internalButtonClass}>Cancelar</button>
          <button type="submit" disabled={busy || uncertain === "conflict"} className={internalPrimaryButtonClass}>
            {busy ? "Registrando…" : uncertain === "lost" ? "Reintentar con los mismos datos" : "Registrar pago"}
          </button>
        </div>
      </form>
    </FinanceModal>
  );
}
