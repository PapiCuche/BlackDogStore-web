"use client";

/**
 * COMMERCE-ADMIN-UX-01 — los reembolsos de un pedido cancelado.
 *
 * REGISTRAR NO ES DEVOLVER. Este panel anota que se debe dinero (pendiente) y,
 * cuando alguien lo devolvió por su medio (la pasarela, caja, una
 * transferencia), lo confirma con su referencia. No llama a la pasarela ni
 * cambia ningún pago. Lo que se puede hacer lo decide el servidor
 * (`can_record`, `can_confirm`, `refundable`).
 */
import { useId, useState } from "react";
import {
  AdminOrderDetail,
  OrderRefund,
  OrderRefundMethod,
  REFUND_EVIDENCE_MAX,
  REFUND_EVIDENCE_MIN,
  REFUND_METHOD_LABELS,
  confirmOrderRefund,
  formatAdminDate,
  recordOrderRefund,
} from "../../lib/admin";
import { internalButtonClass } from "./internal-ui";

const inputClass =
  "mt-1 block w-full rounded-lg border border-bd-border bg-background px-2 py-1.5 text-sm text-foreground";
const MIN_REFERENCE = 3;

function money(value: string) {
  return `S/ ${Number(value).toFixed(2)}`;
}

type Props = { order: AdminOrderDetail; onChanged: (order: AdminOrderDetail) => void };

export function OrderRefundsPanel({ order, onChanged }: Props) {
  const refunds = order.refunds;
  if (order.status !== "cancelled" || !refunds) return null;
  const mayRecord = refunds.can_record && Number(refunds.refundable) > 0;
  return (
    <section aria-labelledby="order-refunds-title" className="rounded-xl border border-bd-border bg-surface p-6">
      <h2 id="order-refunds-title" className="text-sm font-semibold text-foreground">Reembolsos</h2>
      <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-4">
        <div>
          <dt className="text-xs text-muted">Cobrado</dt>
          <dd className="text-foreground">{money(refunds.collected)}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted">Pendiente de devolver</dt>
          <dd className="text-foreground">{money(refunds.pending)}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted">Devuelto (confirmado)</dt>
          <dd className="text-foreground">{money(refunds.confirmed)}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted">Aún en la tienda</dt>
          <dd className="font-semibold text-foreground">{money(refunds.held)}</dd>
        </div>
      </dl>
      {refunds.items.length ? (
        <ul className="mt-4 space-y-3">
          {refunds.items.map((refund) => (
            <RefundRow key={refund.id} order={order} refund={refund} mayConfirm={refunds.can_confirm}
              onChanged={onChanged} />
          ))}
        </ul>
      ) : null}
      {mayRecord ? <RecordForm order={order} refundable={refunds.refundable} onChanged={onChanged} /> : null}
      <p className="mt-4 text-xs text-muted">
        Registrar un reembolso no devuelve el dinero: el de la pasarela se devuelve desde su panel y aquí se anota
        su referencia. Los pagos confirmados no se borran.
      </p>
    </section>
  );
}

function RefundRow({ order, refund, mayConfirm, onChanged }: {
  order: AdminOrderDetail; refund: OrderRefund; mayConfirm: boolean; onChanged: Props["onChanged"];
}) {
  const referenceId = useId();
  const evidenceId = useId();
  const [reference, setReference] = useState("");
  const [evidence, setEvidence] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cleanEvidence = evidence.split(/\s+/).filter(Boolean).join(" ");
  const ready = reference.trim().length >= MIN_REFERENCE && cleanEvidence.length >= REFUND_EVIDENCE_MIN;

  async function confirm() {
    if (!ready || busy) return;
    setBusy(true);
    setError(null);
    try {
      // El importe es exactamente el del pendiente: uno parcial se registra antes como pendiente.
      onChanged(await confirmOrderRefund(order.id, refund.id, {
        reference: reference.trim(), evidence: cleanEvidence, amount: refund.amount }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo confirmar el reembolso.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="rounded-lg border border-bd-border bg-background p-3 text-sm">
      <p className="text-foreground">
        <span className="font-semibold">{money(refund.amount)}</span> · {refund.method_label} ·{" "}
        <span className={refund.status === "confirmed" ? "text-success" : "text-warning"}>{refund.status_label}</span>
      </p>
      <p className="mt-1 text-xs text-muted">
        Registrado por {refund.created_by || "—"} el {formatAdminDate(refund.created_at)}
        {refund.confirmed_at
          ? ` · confirmado por ${refund.confirmed_by || "—"} el ${formatAdminDate(refund.confirmed_at)}, referencia ${refund.reference}`
          : ""}
      </p>
      {refund.evidence ? <p className="mt-1 text-xs text-muted">Evidencia: {refund.evidence}</p> : null}
      {refund.note ? <p className="mt-1 text-xs text-muted">{refund.note}</p> : null}
      {refund.status === "pending" && mayConfirm ? (
        <div className="mt-2 flex flex-wrap items-end gap-2">
          <label htmlFor={referenceId} className="block min-w-[12rem] flex-1 text-xs text-muted">
            Referencia del reembolso #{refund.id}
            <input
              id={referenceId}
              value={reference}
              maxLength={120}
              onChange={(event) => setReference(event.target.value)}
              disabled={busy}
              placeholder="N.º de operación o voucher"
              className={inputClass}
            />
          </label>
          <label htmlFor={evidenceId} className="block min-w-[16rem] flex-[2] text-xs text-muted">
            Evidencia del reembolso #{refund.id}
            <input
              id={evidenceId}
              value={evidence}
              maxLength={REFUND_EVIDENCE_MAX}
              onChange={(event) => setEvidence(event.target.value)}
              disabled={busy}
              placeholder="Operación, banco o canal y fecha"
              className={inputClass}
            />
          </label>
          <button type="button" onClick={() => void confirm()} disabled={!ready || busy} className={internalButtonClass}>
            {busy ? "Confirmando…" : "Confirmar reembolso"}
          </button>
          <p className="w-full text-xs text-muted">
            Se confirma exactamente {money(refund.amount)}. Para devolver menos, registra antes un reembolso pendiente
            por ese importe. La evidencia (mín. {REFUND_EVIDENCE_MIN} caracteres) no debe llevar datos de otros
            clientes.
          </p>
        </div>
      ) : null}
      {error ? <p role="alert" className="mt-2 text-xs text-danger">{error}</p> : null}
    </li>
  );
}

function RecordForm({ order, refundable, onChanged }: {
  order: AdminOrderDetail; refundable: string; onChanged: Props["onChanged"];
}) {
  const amountId = useId();
  const methodId = useId();
  const noteId = useId();
  const [amount, setAmount] = useState(Number(refundable).toFixed(2));
  const [method, setMethod] = useState<OrderRefundMethod>("gateway");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function record() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      onChanged(await recordOrderRefund(order.id, { amount: amount.trim(), method, note: note.trim() }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo registrar el reembolso.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-4 grid gap-2 sm:grid-cols-[8rem_10rem_1fr_auto] sm:items-end">
      <label htmlFor={amountId} className="block text-xs text-muted">
        Importe
        <input id={amountId} inputMode="decimal" value={amount} onChange={(event) => setAmount(event.target.value)}
          disabled={busy} className={inputClass} />
      </label>
      <div className="text-xs text-muted">
        <label htmlFor={methodId}>Medio</label>
        <select id={methodId} value={method} onChange={(event) => setMethod(event.target.value as OrderRefundMethod)}
          disabled={busy} className={inputClass}>
          {(Object.keys(REFUND_METHOD_LABELS) as OrderRefundMethod[]).map((value) => (
            <option key={value} value={value}>{REFUND_METHOD_LABELS[value]}</option>
          ))}
        </select>
      </div>
      <label htmlFor={noteId} className="block text-xs text-muted">
        Nota (opcional)
        <input id={noteId} value={note} maxLength={500} onChange={(event) => setNote(event.target.value)}
          disabled={busy} className={inputClass} />
      </label>
      <button type="button" onClick={() => void record()} disabled={busy || !amount.trim()} className={internalButtonClass}>
        {busy ? "Registrando…" : "Registrar reembolso pendiente"}
      </button>
      {error ? <p role="alert" className="text-xs text-danger sm:col-span-4">{error}</p> : null}
    </div>
  );
}
