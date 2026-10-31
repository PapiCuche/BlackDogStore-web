"use client";

/**
 * The detail of one obligation, in a side panel: what is owed and when, the payments, the adjustments, the
 * reminders that went out and the history. It is where `/admin/finance?obligation=<id>` lands, which is where the
 * bell sends people, so a row that does not exist, belongs to another company or branch, or is not theirs to
 * see is explained instead of left blank. (The server answers all three with the same 404 on purpose.)
 */
import { useCallback, useEffect, useId, useRef, useState } from "react";
import {
  ADJUSTMENT_LABELS,
  FinanceApiError,
  HISTORY_LABELS,
  isManagedHere,
  METHOD_LABELS,
  fetchObligation,
  money,
  reversePayment,
  type ObligationDetail as Detail,
  type Payment,
} from "../../lib/finance-api";
import { formatDate, formatInstant, relativeDue } from "../../lib/finance-dates";
import { internalButtonClass, internalInputClass } from "../../components/internal-ui";
import { FinanceModal } from "./FinanceModal";
import { StateBadge, hintClass, labelClass } from "./FinanceBits";

export type DetailPermissions = {
  canRecord: boolean;
  canReverse: boolean;
  canManage: boolean;
};

/** Can this obligation still receive a payment or a reminder? Open, with something owed. */
export function isOwed(item: Pick<Detail, "state" | "balance" | "lifecycle">): boolean {
  return item.lifecycle === "open" && Number(item.balance) > 0
    && ["pending", "due_soon", "due_today", "overdue"].includes(item.state);
}

function ReversePayment({
  companyId, obligationId, payment, onDone,
}: {
  companyId: number | null; obligationId: number; payment: Payment; onDone: () => void;
}) {
  const id = useId();
  const sending = useRef(false);
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (sending.current) return;
    if (!reason.trim()) {
      setError("Escribe el motivo de la reversión.");
      return;
    }
    sending.current = true;
    setBusy(true);
    setError(null);
    try {
      await reversePayment(companyId, obligationId, payment.id, reason.trim());
      // Done on the server. Whatever the re-read does next (it may fail), this form is finished: it must not stay
      // locked on «Revirtiendo…» over a payment that is already reversed.
      setOpen(false);
      setReason("");
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo revertir el pago.");
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)} className="text-xs font-semibold text-danger underline">
        Revertir este pago
      </button>
    );
  }
  return (
    <form onSubmit={submit} className="mt-2 space-y-2 rounded-lg border border-danger-border bg-danger-surface p-3">
      <label htmlFor={id} className={labelClass}>Motivo de la reversión</label>
      <input id={id} value={reason} maxLength={300} disabled={busy} onChange={(e) => setReason(e.target.value)}
             className={internalInputClass} />
      <p className={hintClass}>Marca el pago como registrado por error y devuelve el saldo. No mueve dinero.</p>
      {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}
      <div className="flex gap-2">
        <button type="submit" disabled={busy} className={internalButtonClass}>
          {busy ? "Revirtiendo…" : "Confirmar reversión"}
        </button>
        <button type="button" disabled={busy} onClick={() => setOpen(false)} className={internalButtonClass}>Cancelar</button>
      </div>
    </form>
  );
}

export function ObligationDetail({
  companyId,
  id,
  today,
  permissions,
  refreshKey,
  section,
  onClose,
  onPay,
  onSnooze,
  onEditRule,
  onChanged,
}: {
  companyId: number | null;
  id: number;
  today: string;
  permissions: DetailPermissions;
  /** Bumps when something outside changed the obligation (a payment, a snooze): the panel reads it again. */
  refreshKey: number;
  /** «Ver historial» opens the panel already scrolled to the history. */
  section?: "history";
  onClose: () => void;
  onPay: (item: Detail) => void;
  onSnooze: (item: Detail) => void;
  onEditRule: (ruleId: number) => void;
  /** A reversal changed the books: the lists behind the panel must read again. */
  onChanged: () => void;
}) {
  // The answer carries the request it belongs to, so a reload keeps showing the last good detail of the same
  // payment while the new one is on its way, and a stale error is never shown for a newer request.
  const [result, setResult] = useState<{ key: string; id: number; detail: Detail | null; error: FinanceApiError | null } | null>(null);
  const [local, setLocal] = useState(0);
  const reload = useCallback(() => setLocal((n) => n + 1), []);
  const key = `${companyId}|${id}|${refreshKey}|${local}`;

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const data = await fetchObligation(companyId, id);
        if (!cancelled) setResult({ key, id, detail: data, error: null });
      } catch (err) {
        if (cancelled) return;
        // The last good detail of this same payment stays on screen, with a warning, rather than vanishing.
        setResult((previous) => ({
          key, id, detail: previous?.id === id ? previous.detail : null,
          error: err instanceof FinanceApiError ? err : new FinanceApiError("No se pudo cargar la obligación.", 0),
        }));
      }
    })();
    return () => { cancelled = true; };
  }, [companyId, id, key]);

  const detail = result?.id === id ? result.detail : null;
  const error = result?.key === key ? result.error : null;
  const loading = result?.key !== key;

  useEffect(() => {
    if (section === "history" && detail) {
      document.getElementById("fin-detail-history")?.scrollIntoView?.({ block: "start" });
    }
  }, [section, detail?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const notFound = error?.status === 404;
  const forbidden = error?.status === 403;
  const title = detail ? detail.concept : "Detalle del pago";
  const managed = detail ? isManagedHere(detail) : true;
  const when = detail ? relativeDue(detail.due_date, today) : "";

  return (
    <FinanceModal title={title} onClose={onClose} variant="drawer" description={detail ? detail.reference : undefined}>
      {loading && !detail ? <p role="status" className="text-sm text-muted">Cargando el detalle…</p> : null}

      {error && detail ? (
        <div role="alert" className="mb-4 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-warning-border bg-warning-surface p-3 text-sm text-foreground">
          <span>No se pudo actualizar este detalle; lo que ves puede estar desactualizado. {error.message}</span>
          <button type="button" onClick={reload} className={internalButtonClass}>Reintentar</button>
        </div>
      ) : null}

      {error && !detail ? (
        <div role="alert" className="space-y-3 rounded-lg border border-warning-border bg-warning-surface p-4 text-sm text-foreground">
          <p className="font-semibold">
            {notFound ? "No encontramos este pago" : forbidden ? "No tienes permiso para verlo" : "No se pudo cargar"}
          </p>
          <p className="text-muted">
            {notFound
              ? "Puede que ya no exista, que sea de otra empresa o de una sucursal a la que no tienes acceso. Si llegaste desde un aviso, es posible que la empresa seleccionada no sea la del aviso."
              : error.message}
          </p>
          <button type="button" onClick={onClose} className={internalButtonClass}>Volver a la lista</button>
        </div>
      ) : null}

      {detail ? (
        <div className="space-y-5">
          <section className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <StateBadge state={detail.state} />
              {detail.disputed ? (
                <span className="rounded-full border border-warning-border bg-warning-surface px-2.5 py-0.5 text-xs font-semibold text-warning">
                  En disputa
                </span>
              ) : null}
              {detail.snoozed_until ? (
                <span className="rounded-full border border-info-border bg-info-surface px-2.5 py-0.5 text-xs font-semibold text-info">
                  Mis avisos pospuestos hasta {formatDate(detail.snoozed_until)}
                </span>
              ) : null}
            </div>
            <dl className="grid gap-x-4 gap-y-2 text-sm sm:grid-cols-2">
              <div><dt className="text-xs text-muted">Vence</dt>
                <dd className="font-semibold text-foreground">
                  {formatDate(detail.due_date)} {when ? <span className="font-normal text-muted">({when})</span> : null}
                </dd></div>
              <div><dt className="text-xs text-muted">Saldo pendiente</dt>
                <dd className="font-semibold tabular-nums text-foreground">{money(detail.balance, detail.currency)}</dd></div>
              <div><dt className="text-xs text-muted">Importe</dt>
                <dd className="tabular-nums text-foreground">{money(detail.effective, detail.currency)}</dd></div>
              <div><dt className="text-xs text-muted">Pagado</dt>
                <dd className="tabular-nums text-foreground">{money(detail.paid, detail.currency)}</dd></div>
              <div><dt className="text-xs text-muted">Beneficiario</dt>
                <dd className="text-foreground">{detail.payee_name || detail.supplier?.name || "—"}</dd></div>
              <div><dt className="text-xs text-muted">Categoría</dt>
                <dd className="text-foreground">{detail.category?.name ?? "—"}</dd></div>
              <div><dt className="text-xs text-muted">Sucursal</dt>
                <dd className="text-foreground">{detail.branch?.name ?? "General (toda la empresa)"}</dd></div>
              <div><dt className="text-xs text-muted">Responsable</dt>
                <dd className="text-foreground">{detail.owner?.name ?? "—"}</dd></div>
            </dl>
            {detail.notes ? <p className="text-sm text-muted">{detail.notes}</p> : null}
            {detail.disputed && detail.dispute_reason ? (
              <p className="text-sm text-warning">Motivo de la disputa: {detail.dispute_reason}</p>
            ) : null}
          </section>

          {!managed ? (
            <p role="note" className="rounded-lg border border-bd-border bg-surface-2 p-3 text-sm text-muted">
              {detail.direction !== "payable"
                ? "Es una cuenta por cobrar: no se gestiona en este tablero de pagos."
                : "Es el saldo de una reparación: se cobra desde su orden, no en este tablero."}
              {" "}Aquí sólo puedes consultarla.
            </p>
          ) : null}

          <div className="flex flex-wrap gap-2">
            {managed && permissions.canRecord && isOwed(detail) ? (
              <button type="button" onClick={() => onPay(detail)} className={internalButtonClass}>Registrar pago</button>
            ) : null}
            {managed && isOwed(detail) ? (
              <button type="button" onClick={() => onSnooze(detail)} className={internalButtonClass}>Posponer aviso</button>
            ) : null}
            {permissions.canManage && detail.rule_id ? (
              <button type="button" onClick={() => onEditRule(detail.rule_id!)} className={internalButtonClass}>Editar regla</button>
            ) : null}
          </div>

          <section aria-labelledby="fin-detail-payments">
            <h3 id="fin-detail-payments" className="text-sm font-semibold text-foreground">Pagos registrados</h3>
            {detail.payments.length === 0 ? (
              <p className="mt-1 text-sm text-muted">Todavía no se registró ningún pago.</p>
            ) : (
              <ul className="mt-2 space-y-2">
                {detail.payments.map((p) => (
                  <li key={p.id} className="rounded-lg border border-bd-border p-3 text-sm">
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <span className={`font-semibold tabular-nums ${p.reversed ? "text-muted line-through" : "text-foreground"}`}>
                        {money(p.amount, p.currency)}
                      </span>
                      <span className="text-xs text-muted">
                        {formatDate(p.paid_on)} · {METHOD_LABELS[p.method] ?? p.method}
                        {p.recorded_by ? ` · ${p.recorded_by.name}` : ""}
                      </span>
                    </div>
                    {p.reference ? <p className="text-xs text-muted">Ref. {p.reference}</p> : null}
                    {p.reversed ? (
                      <p className="mt-1 text-xs text-danger">Revertido: {p.reversal_reason}</p>
                    ) : permissions.canReverse ? (
                      <div className="mt-1">
                        <ReversePayment
                          companyId={companyId} obligationId={detail.id} payment={p}
                          onDone={() => { reload(); onChanged(); }}
                        />
                      </div>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </section>

          {detail.adjustments.length > 0 ? (
            <section aria-labelledby="fin-detail-adjust">
              <h3 id="fin-detail-adjust" className="text-sm font-semibold text-foreground">Ajustes</h3>
              <ul className="mt-2 space-y-1 text-sm text-muted">
                {detail.adjustments.map((a) => (
                  <li key={a.id}>{formatDate(a.created_at.slice(0, 10))} · {ADJUSTMENT_LABELS[a.kind] ?? a.kind} · {money(a.amount, detail.currency)} · {a.reason}</li>
                ))}
              </ul>
            </section>
          ) : null}

          <section aria-labelledby="fin-detail-reminders">
            <h3 id="fin-detail-reminders" className="text-sm font-semibold text-foreground">Avisos enviados</h3>
            <p className="mt-1 text-sm text-muted">
              {detail.reminders.sent === 0
                ? "Todavía no se envió ningún aviso de este pago."
                : `Se enviaron ${detail.reminders.sent} aviso(s) de este pago.`}
              {" "}Un aviso es sólo una notificación: leerlo o posponerlo no cambia el pago.
            </p>
            {detail.reminders.mine.length > 0 ? (
              <ul className="mt-2 space-y-1 text-sm">
                {detail.reminders.mine.map((r, index) => (
                  <li key={`${r.at}-${index}`} className="flex flex-wrap justify-between gap-2">
                    <span className="min-w-0 break-words text-foreground">{r.title}</span>
                    <span className="text-xs text-muted">{formatInstant(r.at)} · {r.read ? "leído" : "sin leer"}</span>
                  </li>
                ))}
              </ul>
            ) : null}
          </section>

          <section aria-labelledby="fin-detail-history">
            <h3 id="fin-detail-history" className="text-sm font-semibold text-foreground">Historial</h3>
            {detail.history.length === 0 ? (
              <p className="mt-1 text-sm text-muted">Sin movimientos registrados.</p>
            ) : (
              <ol className="mt-2 space-y-1 text-sm">
                {detail.history.map((h, index) => (
                  <li key={`${h.at}-${index}`} className="flex flex-wrap justify-between gap-2">
                    <span className="text-foreground">{HISTORY_LABELS[h.action] ?? h.action}</span>
                    <span className="text-xs text-muted">{formatInstant(h.at)}{h.actor ? ` · ${h.actor.name}` : ""}</span>
                  </li>
                ))}
              </ol>
            )}
          </section>
        </div>
      ) : null}
    </FinanceModal>
  );
}
