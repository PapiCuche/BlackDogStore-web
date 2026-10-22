"use client";

/**
 * ORDER-CANCEL-01 — cancelar un pedido PAGADO cuyos productos no han salido.
 * COMMERCE-ADMIN-UX-01 — también un pedido web SIN PAGAR y una venta POS
 * («Anular venta»: hay que marcar que la mercadería volvió al local). El modo
 * lo dice el servidor (`cancel_mode`); el diálogo cambia sus palabras con él.
 *
 * Tres piezas: el botón (sólo si el servidor dice `can_cancel`), el diálogo de
 * confirmación (motivo obligatorio, foco atrapado, Esc cierra) y, una vez
 * cancelado, el bloque que dice quién, cuándo, por qué y qué hizo.
 *
 * CANCELAR NO ES DEVOLVER EL DINERO. El diálogo y el bloque lo dicen con esas
 * palabras: el pedido sigue figurando como pagado y el reembolso se gestiona
 * aparte, en la pasarela.
 */
import { useEffect, useId, useRef, useState } from "react";
import {
  AdminOrderDetail,
  CANCEL_REASON_MAX,
  CANCEL_REASON_MIN,
  FULFILLMENT_STATUS_LABELS,
  OrderCancelMode,
  OrderCancellation,
  OrderRefundMethod,
  REFUND_METHOD_LABELS,
  SupplyCancelCause,
  cancelAdminOrder,
  formatAdminDate,
  waitsForSupplier,
} from "../../lib/admin";
import { internalButtonClass } from "./internal-ui";

const dangerButtonClass =
  "inline-flex items-center justify-center rounded-lg border border-danger-border px-4 py-2 text-sm font-semibold text-danger transition hover:bg-danger-surface disabled:cursor-not-allowed disabled:opacity-40";

type Props = {
  order: AdminOrderDetail;
  /** Si quien mira tiene la autoridad para cancelar pedidos en esta empresa. */
  mayCancel: boolean;
  onCancelled: (order: AdminOrderDetail) => void;
};

/** El motivo como lo guarda el servidor: sin espacios de sobra, también dentro. */
export function normaliseReason(text: string): string {
  return text.split(/\s+/).filter(Boolean).join(" ");
}

export function OrderCancelPanel({ order, mayCancel, onCancelled }: Props) {
  const [open, setOpen] = useState(false);

  if (order.cancellation) {
    return <OrderCancellationBlock cancellation={order.cancellation} />;
  }
  const blockers = order.cancel_blockers ?? [];
  if (!order.can_cancel) {
    // Sólo se explica a quien podría cancelar un pedido pagado: a un pedido sin
    // pagar no le falta nada de esto, y a quien sólo mira no le sirve.
    if (!mayCancel || !order.paid || blockers.length === 0) return null;
    return (
      <p className="text-xs text-muted" data-testid="order-cancel-blockers">
        No se puede cancelar: {blockers.map((blocker) => blocker.detail).join(" ")}
      </p>
    );
  }
  return (
    <>
      <button type="button" onClick={() => setOpen(true)} className={dangerButtonClass}>
        {order.cancel_mode === "pos" ? "Anular venta" : "Cancelar pedido"}
      </button>
      {open ? (
        <OrderCancelDialog
          order={order}
          onClose={() => setOpen(false)}
          onCancelled={(updated) => {
            setOpen(false);
            onCancelled(updated);
          }}
        />
      ) : null}
    </>
  );
}

export function OrderCancelDialog({
  order, onClose, onCancelled,
}: {
  order: AdminOrderDetail;
  onClose: () => void;
  onCancelled: (order: AdminOrderDetail) => void;
}) {
  const titleId = useId();
  const reasonId = useId();
  const panel = useRef<HTMLDivElement>(null);
  const field = useRef<HTMLTextAreaElement>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mode = order.cancel_mode ?? "paid";
  const pos = mode === "pos";
  const [goodsBack, setGoodsBack] = useState(false);
  const [skipStock, setSkipStock] = useState(false);
  const [withRefund, setWithRefund] = useState(false);
  const [refundMethod, setRefundMethod] = useState<OrderRefundMethod>(pos ? "cash" : "gateway");
  // D3: con una línea bajo pedido que aún espera al proveedor, hay que decir por qué se cancela.
  const waiting = waitsForSupplier(order);
  const [supplyCause, setSupplyCause] = useState<SupplyCancelCause | null>(null);
  const ready = normaliseReason(reason).length >= CANCEL_REASON_MIN && (!pos || goodsBack)
    && (!waiting || supplyCause !== null);
  const verb = pos ? "Anular venta" : "Cancelar pedido";

  useEffect(() => {
    // Entra por el motivo: es lo primero que hay que escribir, y Enter en un
    // cuadro de texto no confirma nada.
    const previous = document.activeElement as HTMLElement | null;
    field.current?.focus();
    return () => { if (previous?.isConnected) previous.focus(); };
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (!busy) onClose();
        return;
      }
      if (event.key !== "Tab" || !panel.current) return;
      // El foco da la vuelta dentro del diálogo: lo de detrás no se alcanza.
      const stops = [...panel.current.querySelectorAll<HTMLElement>(
        "textarea:not([disabled]), input:not([disabled]), select:not([disabled]), button:not([disabled])",
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
    const options: {
      goodsReturned?: boolean; refundMethod?: OrderRefundMethod; restoreStock?: boolean; supplyCause?: SupplyCancelCause;
      expectedMode?: OrderCancelMode;
    } = {};
    // CANCEL-CROSSED-PAYMENT: lo que se vio; si llegó un pago entre medio, el servidor responde 409.
    if (order.cancel_mode) options.expectedMode = order.cancel_mode;
    if (waiting && supplyCause) options.supplyCause = supplyCause;
    if (pos) options.goodsReturned = true;
    if (pos && skipStock) options.restoreStock = false;
    if (mode !== "unpaid" && withRefund) options.refundMethod = refundMethod;
    try {
      onCancelled(await (Object.keys(options).length
        ? cancelAdminOrder(order.id, normaliseReason(reason), options)
        : cancelAdminOrder(order.id, normaliseReason(reason))));
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cancelar el pedido.");
      setBusy(false);
    }
  }

  const fiscal = order.cancel_fiscal_document ?? "";
  // Cierre financiero: el servidor dice por qué esta venta la anula un superior.
  const superior = order.cancel_superior_conditions ?? [];
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
          {verb} #{order.id}
        </h2>
        {mode === "unpaid" ? (
          <p className="mt-3 rounded-lg border border-bd-border bg-background px-3 py-2 text-sm text-foreground">
            <span className="font-semibold">Sin pago</span> — el pedido queda cancelado en el historial y no se
            registra ningún ingreso.
          </p>
        ) : (
          <p className="mt-3 rounded-lg border border-warning-border bg-warning-surface px-3 py-2 text-sm text-foreground">
            <span className="font-semibold">Pagado</span> — el dinero NO se devuelve al cancelar; el reembolso se
            gestiona aparte.
          </p>
        )}
        {superior.length ? (
          <div
            data-testid="order-cancel-superior"
            className="mt-3 rounded-lg border border-warning-border bg-warning-surface px-3 py-2 text-sm text-foreground"
          >
            <p className="font-semibold">Requiere autorización superior</p>
            <ul className="mt-1 list-disc space-y-1 pl-5">
              {superior.map((condition) => <li key={condition.code}>{condition.detail}</li>)}
            </ul>
            <p className="mt-1 text-xs text-muted">La anulación queda registrada con tu usuario y estas condiciones.</p>
          </div>
        ) : null}
        <p className="mt-3 text-sm font-semibold text-foreground">{pos ? "Al anular:" : "Al cancelar:"}</p>
        <ul className="mt-1 list-disc space-y-1 pl-5 text-sm text-muted">
          {mode === "unpaid" ? (
            <>
              <li>Los equipos que el comprador tenía apartados vuelven a la tienda.</li>
              <li>Si llega un pago después, queda registrado para devolverlo; el pedido no se reabre.</li>
            </>
          ) : (
            <>
              <li>El stock de sus productos vuelve a la sucursal, una sola vez.</li>
              <li>Si tiene nota de venta interna, queda anulada (se conserva).</li>
            </>
          )}
          {fiscal ? (
            <li className="text-warning">
              El comprobante {fiscal} se conserva: queda pendiente su nota de crédito o baja, que se gestiona en el
              flujo fiscal.
            </li>
          ) : null}
          <li>El pedido ya no cambia de estado de despacho.</li>
        </ul>
        <label htmlFor={reasonId} className="mt-4 block text-sm font-semibold text-foreground">
          {pos ? "Motivo de la anulación" : "Motivo de la cancelación"}
        </label>
        <textarea
          id={reasonId}
          ref={field}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          maxLength={CANCEL_REASON_MAX}
          rows={3}
          required
          disabled={busy}
          aria-describedby={`${reasonId}-hint`}
          className="mt-1 w-full rounded-lg border border-bd-border bg-background px-3 py-2 text-sm text-foreground"
        />
        <p id={`${reasonId}-hint`} className="mt-1 text-xs text-muted">
          Obligatorio, al menos {CANCEL_REASON_MIN} caracteres. Queda registrado con tu usuario y la fecha.
        </p>
        {pos ? (
          <label className="mt-3 flex items-start gap-2 text-sm text-foreground">
            <input
              type="checkbox"
              checked={goodsBack}
              onChange={(event) => setGoodsBack(event.target.checked)}
              disabled={busy}
              className="mt-0.5"
            />
            <span>La mercadería volvió al local</span>
          </label>
        ) : null}
        {pos ? (
          <label className="mt-2 flex items-start gap-2 text-sm text-foreground">
            <input
              type="checkbox"
              checked={skipStock}
              onChange={(event) => setSkipStock(event.target.checked)}
              disabled={busy}
              className="mt-0.5"
            />
            <span>
              Anular sin reponer stock — sólo si la mercadería ya volvió al stock por una devolución manual
              (revisa el Kardex); requiere autorización superior
            </span>
          </label>
        ) : null}
        {waiting ? (
          <fieldset className="mt-3 rounded-lg border border-warning-border bg-warning-surface p-3 text-sm">
            <legend className="px-1 text-xs font-semibold text-warning">Productos bajo pedido sin entregar</legend>
            <label className="flex items-start gap-2 text-foreground">
              <input
                type="radio"
                name={`${reasonId}-supply-cause`}
                checked={supplyCause === "supplier_failure"}
                onChange={() => setSupplyCause("supplier_failure")}
                disabled={busy}
                className="mt-0.5"
              />
              <span>El proveedor no pudo abastecer</span>
            </label>
            <label className="mt-1 flex items-start gap-2 text-foreground">
              <input
                type="radio"
                name={`${reasonId}-supply-cause`}
                checked={supplyCause === "ordinary"}
                onChange={() => setSupplyCause("ordinary")}
                disabled={busy}
                className="mt-0.5"
              />
              <span>Se cancela por otra razón; el proveedor sigue teniendo el equipo</span>
            </label>
            <p className="mt-2 text-xs text-muted">
              Si el proveedor no pudo, lo disponible de su oferta no cambia; si fue otra razón, esas unidades vuelven a
              estar disponibles para vender bajo pedido.
            </p>
          </fieldset>
        ) : null}
        {mode !== "unpaid" ? (
          <div className="mt-3 rounded-lg border border-bd-border bg-background p-3 text-sm">
            <label className="flex items-start gap-2 text-foreground">
              <input
                type="checkbox"
                checked={withRefund}
                onChange={(event) => setWithRefund(event.target.checked)}
                disabled={busy}
                className="mt-0.5"
              />
              <span>Registrar reembolso pendiente por lo cobrado</span>
            </label>
            {withRefund ? (
              <div className="mt-2 text-xs text-muted">
                <label htmlFor={`${reasonId}-refund-method`}>Medio del reembolso</label>
                <select
                  id={`${reasonId}-refund-method`}
                  value={refundMethod}
                  onChange={(event) => setRefundMethod(event.target.value as OrderRefundMethod)}
                  disabled={busy}
                  className="mt-1 block w-full rounded-lg border border-bd-border bg-surface px-2 py-1.5 text-sm text-foreground"
                >
                  {(Object.keys(REFUND_METHOD_LABELS) as OrderRefundMethod[]).map((method) => (
                    <option key={method} value={method}>{REFUND_METHOD_LABELS[method]}</option>
                  ))}
                </select>
              </div>
            ) : null}
            <p className="mt-2 text-xs text-muted">
              Sólo queda anotado como pendiente; el dinero se devuelve aparte y se confirma con su referencia.
            </p>
          </div>
        ) : null}
        {error ? (
          <p role="alert" className="mt-3 text-sm text-danger">{error}</p>
        ) : null}
        <div className="mt-5 flex flex-wrap justify-end gap-2">
          <button type="button" onClick={onClose} disabled={busy} className={internalButtonClass}>
            Volver
          </button>
          <button type="button" onClick={() => void confirm()} disabled={!ready || busy} className={dangerButtonClass}>
            {busy ? (pos ? "Anulando…" : "Cancelando…") : verb}
          </button>
        </div>
      </div>
    </div>
  );
}

export function OrderCancellationBlock({ cancellation }: { cancellation: OrderCancellation }) {
  const before = FULFILLMENT_STATUS_LABELS[cancellation.previous_fulfillment_status]
    ?? cancellation.previous_fulfillment_status;
  const unpaid = cancellation.was_paid === false;
  const held = cancellation.holds_released ?? 0;
  return (
    <section
      aria-labelledby="order-cancellation-title"
      className="rounded-xl border border-danger-border bg-surface p-6"
    >
      <h2 id="order-cancellation-title" className="text-sm font-semibold text-danger">
        {cancellation.by_return ? "Cancelado por devolución" : cancellation.goods_returned ? "Venta anulada" : "Pedido cancelado"}
      </h2>
      <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-xs text-muted">{cancellation.goods_returned ? "Anulada por" : "Cancelado por"}</dt>
          <dd className="text-foreground">{cancellation.cancelled_by || "—"}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted">Fecha</dt>
          <dd className="text-foreground">{formatAdminDate(cancellation.cancelled_at)}</dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs text-muted">Motivo</dt>
          <dd className="whitespace-pre-wrap text-foreground">{cancellation.reason}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted">Despacho antes de cancelar</dt>
          <dd className="text-foreground">{before}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted">Stock</dt>
          <dd className="text-foreground">
            {cancellation.by_return
              ? "Entró al recibir la devolución (Kardex: devolución de cliente)"
              : cancellation.stock_restored ? "Repuesto en la sucursal" : "No había stock que reponer"}
          </dd>
        </div>
        {cancellation.goods_returned ? (
          <div>
            <dt className="text-xs text-muted">Mercadería</dt>
            <dd className="text-foreground">
              {cancellation.by_return
                ? "Recibida con la devolución posterior a la entrega"
                : "Mercadería devuelta al local, confirmada al anular"}
            </dd>
          </div>
        ) : null}
        {held > 0 ? (
          <div>
            <dt className="text-xs text-muted">Equipos apartados</dt>
            <dd className="text-foreground">
              {held === 1 ? "1 equipo apartado volvió a la tienda" : `${held} equipos apartados volvieron a la tienda`}
            </dd>
          </div>
        ) : null}
        {cancellation.sales_note_voided ? (
          <div>
            <dt className="text-xs text-muted">Nota de venta interna</dt>
            <dd className="text-foreground">Anulada (se conserva)</dd>
          </div>
        ) : null}
      </dl>
      {cancellation.fiscal_pending ? (
        <p role="alert" className="mt-4 rounded-lg border border-warning-border bg-warning-surface px-3 py-2 text-sm text-foreground">
          Comprobante fiscal {cancellation.fiscal_document}: se conserva y queda <strong>pendiente su nota de
          crédito o baja</strong>, que se gestiona en el flujo fiscal.
        </p>
      ) : null}
      <p className="mt-4 text-xs text-muted">
        {unpaid
          ? "Cancelado sin pago: no se registró ningún ingreso. Si llega un pago después, queda registrado para devolverlo."
          : "El pedido sigue figurando como pagado: cancelar no devuelve el dinero. El reembolso se registra aparte."}
      </p>
    </section>
  );
}
