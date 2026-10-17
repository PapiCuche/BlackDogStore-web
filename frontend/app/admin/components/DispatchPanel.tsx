"use client";

/**
 * ORDER-FULFILLMENT-SAFETY-01 — el despacho de un pedido.
 *
 * Todo lo que enseña lo decide el servidor (`order.dispatch`): el recorrido de
 * ESTA modalidad (recojo: Pendiente → En preparación → Listo para retiro →
 * Entregado; envío: … → Enviado → Entregado), el paso siguiente y el anterior
 * que QUIEN MIRA puede marcar, por qué el siguiente todavía no se puede, y el
 * historial tal como quedó escrito, aunque sea incoherente.
 *
 * Se ve siempre, también sin permiso o con el pedido terminado o cancelado:
 * entonces sólo se lee. Un paso a la vez; volver atrás exige un motivo;
 * «Entregado» se confirma antes de enviarse. Un superior puede declarar que un
 * «Enviado»/«Entregado» fue un error y que el producto nunca salió: el registro
 * original se conserva y la corrección no cancela, no reembolsa ni mueve stock.
 */
import { useId, useState } from "react";
import {
  AdminOrderDetail,
  DispatchHistoryEntry,
  FULFILLMENT_STATUS_LABELS,
  LEFT_MARKING_REASON_MIN,
  LeftMarking,
  OrderDispatch,
  correctLeftMarking,
  formatAdminDate,
  updateOrderFulfillment,
} from "../../lib/admin";
import { DELIVERY_MODE_LABELS } from "../../lib/delivery";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";
import { normaliseReason } from "./OrderCancelPanel";

const KIND_LABELS: Record<OrderDispatch["kind"], string> = {
  pickup: "Recojo en tienda",
  shipping: "Envío",
};

const STATE_LABELS = { done: "Hecho", current: "Estado actual", todo: "Pendiente" } as const;

function statusLabel(status: string): string {
  return FULFILLMENT_STATUS_LABELS[status] ?? (status || "—");
}

type Props = {
  order: AdminOrderDetail;
  /** El pedido como quedó en el servidor tras un cambio o una corrección. */
  onChanged: (order: AdminOrderDetail) => void;
};

export function DispatchPanel({ order, onChanged }: Props) {
  const dispatch = order.dispatch;
  if (!dispatch) return null;
  const kind = KIND_LABELS[dispatch.kind] ?? dispatch.kind;
  const mode = DELIVERY_MODE_LABELS[dispatch.mode] ?? "";
  return (
    <section
      aria-labelledby="dispatch-title"
      data-testid="dispatch-panel"
      className="rounded-xl border border-bd-border bg-surface p-6"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="dispatch-title" className="text-sm font-semibold text-foreground">Estado de despacho</h2>
          <p className="mt-1 text-sm text-foreground">
            Estado actual: <strong data-testid="dispatch-current">{dispatch.current_label}</strong>
          </p>
        </div>
        <span
          data-testid="dispatch-kind"
          className="inline-flex rounded-full border border-bd-border bg-background px-3 py-1 text-xs font-semibold text-foreground"
        >
          {kind}
          {mode && mode !== kind ? <span className="ml-1 font-normal text-muted">· {mode}</span> : null}
        </span>
      </div>
      {dispatch.notice ? (
        <p
          data-testid="dispatch-notice"
          className="mt-3 rounded-lg border border-warning-border bg-warning-surface px-3 py-2 text-sm text-foreground"
        >
          {dispatch.notice}
        </p>
      ) : null}

      <DispatchSteps dispatch={dispatch} />

      <DispatchActions order={order} dispatch={dispatch} onChanged={onChanged} />

      <DispatchHistory entries={dispatch.history} />

      {dispatch.may_correct && dispatch.left_markings.length > 0 ? (
        <LeftMarkingCorrections
          orderId={order.id}
          markings={dispatch.left_markings}
          reasonMin={dispatch.correction_reason_min ?? LEFT_MARKING_REASON_MIN}
          onChanged={onChanged}
        />
      ) : null}
    </section>
  );
}

function DispatchSteps({ dispatch }: { dispatch: OrderDispatch }) {
  const steps = dispatch.steps;
  return (
    <ol aria-label="Pasos del despacho" className="mt-5 flex flex-col sm:flex-row">
      {steps.map((step, index) => {
        const last = index === steps.length - 1;
        const dot =
          step.state === "done"
            ? "border-foreground bg-foreground text-background"
            : step.state === "current"
              ? "border-primary bg-primary text-background ring-4 ring-primary/20"
              : "border-bd-border bg-background text-muted";
        return (
          <li
            key={step.status}
            data-testid={`dispatch-step-${step.status}`}
            data-state={step.state}
            aria-current={step.state === "current" ? "step" : undefined}
            className="flex gap-3 sm:flex-1 sm:flex-col sm:gap-2"
          >
            <div className="flex flex-col items-center sm:flex-row">
              <span
                aria-hidden="true"
                className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full border-2 text-[11px] font-bold ${dot}`}
              >
                {step.state === "done" ? "✓" : index + 1}
              </span>
              {!last ? (
                <span
                  aria-hidden="true"
                  className={`my-1 min-h-6 w-px flex-1 sm:mx-2 sm:my-0 sm:h-px sm:min-h-0 sm:w-auto ${
                    step.state === "done" ? "bg-foreground/50" : "bg-bd-border"
                  }`}
                />
              ) : null}
            </div>
            <div className={`min-w-0 ${last ? "" : "pb-4 sm:pb-0"} sm:pr-3`}>
              <p className={`text-sm ${step.state === "todo" ? "text-muted" : "font-semibold text-foreground"}`}>
                {step.label}
              </p>
              <p className="text-xs text-muted">
                <span className="sr-only">{STATE_LABELS[step.state]}. </span>
                {step.at ? formatAdminDate(step.at) : step.state === "todo" ? "" : "—"}
              </p>
              {step.actor ? <p className="text-xs text-muted">{step.actor}</p> : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function DispatchActions({
  order, dispatch, onChanged,
}: {
  order: AdminOrderDetail;
  dispatch: OrderDispatch;
  onChanged: (order: AdminOrderDetail) => void;
}) {
  const noteId = useId();
  const reasonId = useId();
  const [note, setNote] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [backOpen, setBackOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  if (!dispatch.may_manage) {
    return (
      <p data-testid="dispatch-read-only" className="mt-4 text-xs text-muted">
        Sólo lectura: no tienes permiso para cambiar el despacho de este pedido.
      </p>
    );
  }
  const { forward, back, blockers } = dispatch;
  if (!forward && !back) {
    return <p className="mt-4 text-xs text-muted">No hay ningún paso que puedas marcar ahora.</p>;
  }
  const blocked = blockers.length > 0;
  const reasonReady = normaliseReason(reason).length >= dispatch.reason_min;

  async function move(status: string, text: string, label: string) {
    if (busy) return;
    setBusy(true);
    setError(null);
    setSaved(null);
    try {
      const updated = await updateOrderFulfillment(order.id, status, text);
      setNote("");
      setReason("");
      setConfirming(false);
      setBackOpen(false);
      setSaved(`Marcado como «${label}».`);
      onChanged(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo actualizar el estado de despacho.");
    } finally {
      setBusy(false);
    }
  }

  function markForward() {
    if (!forward || blocked) return;
    if (forward === "delivered" && !confirming) {
      setConfirming(true);
      return;
    }
    void move(forward, normaliseReason(note), dispatch.forward_label);
  }

  return (
    <div className="mt-6 space-y-4 border-t border-bd-border pt-5">
      {forward ? (
        <div className="space-y-3">
          <div>
            <label htmlFor={noteId} className="block text-xs font-semibold text-foreground">
              Nota (opcional)
            </label>
            <input
              id={noteId}
              type="text"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              maxLength={500}
              disabled={busy}
              className={`mt-1 ${internalInputClass}`}
            />
            <p className="mt-1 text-xs text-muted">
              Queda en el historial si tiene al menos {dispatch.reason_min} caracteres.
            </p>
          </div>
          {confirming ? (
            <div
              role="group"
              aria-label="Confirmar entrega"
              data-testid="dispatch-confirm-delivered"
              className="rounded-lg border border-warning-border bg-warning-surface px-3 py-3 text-sm text-foreground"
            >
              <p>
                Vas a marcarlo como <strong>«{dispatch.forward_label}»</strong>: confirmas que el producto se entregó
                al cliente o a quien lo recibe en su nombre. Después el despacho ya no vuelve atrás.
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={markForward}
                  disabled={busy}
                  className={internalPrimaryButtonClass}
                >
                  {busy ? "Guardando…" : "Sí, se entregó"}
                </button>
                <button
                  type="button"
                  onClick={() => setConfirming(false)}
                  disabled={busy}
                  className={internalButtonClass}
                >
                  Volver
                </button>
              </div>
            </div>
          ) : (
            <button
              type="button"
              onClick={markForward}
              disabled={blocked || busy}
              className={internalPrimaryButtonClass}
            >
              {busy && !backOpen ? "Guardando…" : `Marcar como ${dispatch.forward_label}`}
            </button>
          )}
          {blocked ? (
            <div data-testid="dispatch-blockers" className="text-sm text-foreground">
              <p className="text-xs font-semibold text-muted">
                Todavía no se puede marcar «{dispatch.forward_label}»:
              </p>
              <ul className="mt-1 list-disc space-y-1 pl-5 text-sm text-muted">
                {blockers.map((blocker) => <li key={blocker.code}>{blocker.detail}</li>)}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}

      {back ? (
        <div className="space-y-3">
          {backOpen ? (
            <div className="rounded-lg border border-bd-border bg-background p-3">
              <label htmlFor={reasonId} className="block text-sm font-semibold text-foreground">
                Motivo para volver a «{dispatch.back_label}»
              </label>
              <textarea
                id={reasonId}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                maxLength={500}
                rows={3}
                required
                disabled={busy}
                className="mt-1 w-full rounded-lg border border-bd-border bg-surface px-3 py-2 text-sm text-foreground"
              />
              <p className="mt-1 text-xs text-muted">
                Obligatorio, al menos {dispatch.reason_min} caracteres. Queda en el historial con tu usuario.
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => void move(back, normaliseReason(reason), dispatch.back_label)}
                  disabled={!reasonReady || busy}
                  className={internalButtonClass}
                >
                  {busy ? "Guardando…" : `Volver a ${dispatch.back_label}`}
                </button>
                <button
                  type="button"
                  onClick={() => { setBackOpen(false); setReason(""); }}
                  disabled={busy}
                  className={internalButtonClass}
                >
                  Descartar
                </button>
              </div>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => { setBackOpen(true); setConfirming(false); setSaved(null); }}
              disabled={busy}
              className={internalButtonClass}
            >
              Volver a {dispatch.back_label}
            </button>
          )}
        </div>
      ) : null}

      {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}
      {saved ? <p role="status" className="text-sm text-muted">{saved}</p> : null}
    </div>
  );
}

function historyTitle(entry: DispatchHistoryEntry, byId: Map<number, DispatchHistoryEntry>): string {
  switch (entry.action) {
    case "order_fulfillment_status_changed":
      return `${entry.old_label || statusLabel(entry.old_status)} → ${entry.new_label || statusLabel(entry.new_status)}`;
    case "order_left_marking_corrected": {
      const target = entry.corrects_id != null ? byId.get(entry.corrects_id) : undefined;
      const what = target ? ` (${target.old_label} → ${target.new_label} del ${formatAdminDate(target.at)})` : "";
      const restored = entry.new_label ? `; el pedido volvió a «${entry.new_label}»` : "";
      return `Marcado de salida corregido${what}${restored}`;
    }
    case "order_delivery_updated":
      return "Datos de entrega actualizados";
    case "order_cancelled":
      return "Pedido cancelado";
    default:
      return entry.action;
  }
}

function DispatchHistory({ entries }: { entries: DispatchHistoryEntry[] }) {
  const byId = new Map(entries.map((entry) => [entry.id, entry]));
  return (
    <div className="mt-6 border-t border-bd-border pt-5">
      <h3 className="text-sm font-semibold text-foreground">Historial de despacho</h3>
      {entries.length === 0 ? (
        <p className="mt-2 text-xs text-muted">Todavía no hay movimientos de despacho registrados.</p>
      ) : (
        <ol data-testid="dispatch-history" className="mt-3 space-y-3">
          {entries.map((entry) => {
            const isReason =
              entry.direction === "back"
              || entry.action === "order_left_marking_corrected"
              || entry.action === "order_cancelled";
            return (
              <li
                key={entry.id}
                data-testid={`dispatch-history-${entry.id}`}
                className="rounded-lg border border-bd-border bg-background px-3 py-2 text-sm"
              >
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span className="font-semibold text-foreground">{historyTitle(entry, byId)}</span>
                  {entry.direction === "back" ? (
                    <span className="text-xs text-muted">(vuelta atrás)</span>
                  ) : null}
                  {entry.corrected ? (
                    <span
                      title="Un superior declaró este registro un error. El registro se conserva."
                      className="inline-flex rounded border border-warning-border bg-warning-surface px-1.5 py-0.5 text-[10px] font-semibold text-warning"
                    >
                      Corregido
                    </span>
                  ) : null}
                </div>
                <p className="mt-0.5 text-xs text-muted">
                  {formatAdminDate(entry.at)} · {entry.actor || "—"}
                </p>
                {entry.note ? (
                  <p className="mt-1 whitespace-pre-wrap text-xs text-foreground">
                    <span className="text-muted">{isReason ? "Motivo: " : "Nota: "}</span>
                    {entry.note}
                  </p>
                ) : null}
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}

function LeftMarkingCorrections({
  orderId, markings, reasonMin, onChanged,
}: {
  orderId: number;
  markings: LeftMarking[];
  reasonMin: number;
  onChanged: (order: AdminOrderDetail) => void;
}) {
  const [openId, setOpenId] = useState<number | null>(null);
  return (
    <div data-testid="left-marking-corrections" className="mt-6 border-t border-bd-border pt-5">
      <h3 className="text-sm font-semibold text-foreground">Corregir marcado de salida</h3>
      <p className="mt-1 text-xs text-muted">
        Para un pedido marcado como enviado o entregado por error cuyo producto nunca salió de la tienda. El registro
        original se conserva; corregir NO cancela el pedido, no reembolsa ni mueve stock.
      </p>
      <ul className="mt-3 space-y-3">
        {markings.map((marking) => (
          <li
            key={marking.audit_id}
            data-testid={`left-marking-${marking.audit_id}`}
            className="rounded-lg border border-bd-border bg-background px-3 py-2 text-sm"
          >
            <p className="font-semibold text-foreground">
              {statusLabel(marking.old_status)} → {statusLabel(marking.new_status)}
            </p>
            <p className="text-xs text-muted">{formatAdminDate(marking.at)} · {marking.actor || "—"}</p>
            {marking.corrected && marking.correction ? (
              <p className="mt-1 text-xs text-foreground">
                Corregido el {formatAdminDate(marking.correction.at)} por {marking.correction.actor || "—"}:{" "}
                {marking.correction.reason}
              </p>
            ) : marking.corrected ? (
              <p className="mt-1 text-xs text-foreground">Corregido.</p>
            ) : marking.can_correct ? (
              openId === marking.audit_id ? (
                <LeftMarkingForm
                  orderId={orderId}
                  marking={marking}
                  reasonMin={reasonMin}
                  onClose={() => setOpenId(null)}
                  onChanged={(updated) => { setOpenId(null); onChanged(updated); }}
                />
              ) : (
                <button
                  type="button"
                  onClick={() => setOpenId(marking.audit_id)}
                  className={`mt-2 ${internalButtonClass}`}
                >
                  Corregir este registro
                </button>
              )
            ) : marking.blockers.length > 0 ? (
              <div className="mt-1 text-xs text-muted">
                <p>No se puede corregir:</p>
                <ul className="mt-1 list-disc space-y-1 pl-5">
                  {marking.blockers.map((blocker) => <li key={blocker.code}>{blocker.detail}</li>)}
                </ul>
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function LeftMarkingForm({
  orderId, marking, reasonMin, onClose, onChanged,
}: {
  orderId: number;
  marking: LeftMarking;
  reasonMin: number;
  onClose: () => void;
  onChanged: (order: AdminOrderDetail) => void;
}) {
  const reasonId = useId();
  const [reason, setReason] = useState("");
  const [neverLeft, setNeverLeft] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ready = normaliseReason(reason).length >= reasonMin && neverLeft;

  async function submit() {
    if (!ready || busy) return;
    setBusy(true);
    setError(null);
    try {
      onChanged(await correctLeftMarking(orderId, marking.audit_id, normaliseReason(reason), neverLeft));
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo corregir el registro.");
      setBusy(false);
    }
  }

  return (
    <div className="mt-2 rounded-lg border border-bd-border bg-surface p-3">
      <label htmlFor={reasonId} className="block text-sm font-semibold text-foreground">
        Motivo de la corrección
      </label>
      <textarea
        id={reasonId}
        value={reason}
        onChange={(event) => setReason(event.target.value)}
        maxLength={500}
        rows={3}
        required
        disabled={busy}
        className="mt-1 w-full rounded-lg border border-bd-border bg-background px-3 py-2 text-sm text-foreground"
      />
      <p className="mt-1 text-xs text-muted">
        Obligatorio, al menos {reasonMin} caracteres. Queda registrado con tu usuario y la fecha.
      </p>
      <label className="mt-3 flex items-start gap-2 text-sm text-foreground">
        <input
          type="checkbox"
          checked={neverLeft}
          onChange={(event) => setNeverLeft(event.target.checked)}
          disabled={busy}
          required
          className="mt-0.5"
        />
        <span>Confirmo que el producto nunca salió de la tienda</span>
      </label>
      {error ? <p role="alert" className="mt-2 text-sm text-danger">{error}</p> : null}
      <div className="mt-3 flex flex-wrap gap-2">
        <button type="button" onClick={() => void submit()} disabled={!ready || busy} className={internalPrimaryButtonClass}>
          {busy ? "Corrigiendo…" : "Corregir registro"}
        </button>
        <button type="button" onClick={onClose} disabled={busy} className={internalButtonClass}>
          Descartar
        </button>
      </div>
    </div>
  );
}
