"use client";

/**
 * DEVICE-COMMERCE-01 · DC3 — the supply of what an order sold «bajo pedido».
 *
 * Each supplier line moves: to confirm → ordered from the supplier → received
 * (stock enters, serialised devices one by one) — or it fails. Which buttons
 * appear is the SERVER's answer for this person (`can_manage_supply`,
 * `can_receive_supply`), never a role read here. Every action answers with the
 * line; the order is then read again, since stock and status may have moved.
 *
 * A failed supply does not refund anything by itself: the refund or the
 * cancellation goes through the order's usual flow, and the panel says so.
 */

import { useEffect, useState } from "react";

import {
  SUPPLY_STATUS_LABELS,
  fetchAdminOrderDetail,
  type AdminOrderDetail,
  type AdminOrderItem,
} from "../../lib/admin";
import {
  confirmOrderSupply,
  failOrderSupply,
  fetchSupplierOffers,
  receiveOrderSupply,
  type ReceivedUnitInput,
  type SupplierOffer,
} from "../lib/internal-api";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";

const message = (error: unknown, fallback: string) => (error instanceof Error ? error.message : fallback);

const STATUS_TONE: Record<string, string> = {
  to_confirm: "border-warning-border bg-warning-surface text-warning",
  pending: "border-bd-border text-foreground",
  received: "border-success-border bg-success-surface text-success",
  failed: "border-danger-border bg-danger-surface text-danger",
  awaiting_payment: "border-bd-border text-muted",
};

export function SupplyStatusBadge({ status }: { status: string | undefined | null }) {
  if (!status) return null;
  return (
    <span className={`inline-flex items-center rounded border px-2 py-0.5 text-[11px] font-medium ${STATUS_TONE[status] ?? "border-bd-border text-muted"}`}>
      {SUPPLY_STATUS_LABELS[status] ?? status}
    </span>
  );
}

type Action = { kind: "confirm" | "receive" | "fail"; itemId: number } | null;

function ConfirmForm({
  orderId, item, onDone, onCancel,
}: { orderId: number; item: AdminOrderItem; onDone: () => Promise<void>; onCancel: () => void }) {
  const [offers, setOffers] = useState<SupplierOffer[] | null>(null);
  const [offerId, setOfferId] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetchSupplierOffers({ product: item.product_id, state: "active" }).then(
      (rows) => setOffers(rows),
      () => setOffers([]),
    );
  }, [item.product_id]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await confirmOrderSupply(orderId, item.id, offerId ? Number(offerId) : null);
      await onDone();
    } catch (err) {
      setError(message(err, "No se pudo confirmar con el proveedor."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} aria-label={`Confirmar con proveedor: ${item.product_name}`} className="mt-3 flex flex-wrap items-end gap-3 rounded-lg border border-bd-border bg-background p-3">
      <label className="block min-w-60 flex-1 text-xs text-muted">
        Oferta (opcional)
        <select value={offerId} onChange={(e) => setOfferId(e.target.value)} className={`mt-1.5 ${internalInputClass}`}>
          <option value="">{item.supplier_name ? `La de ${item.supplier_name}` : "La oferta de la venta"}</option>
          {(offers ?? []).map((offer) => (
            <option key={offer.id} value={offer.id}>
              {offer.supplier_name} · S/ {offer.commercial_price} · {offer.lead_time_days} {offer.lead_time_days === 1 ? "día" : "días"}
              {offer.remaining !== null ? ` · quedan ${offer.remaining}` : ""}
            </option>
          ))}
        </select>
      </label>
      <button type="button" className={internalButtonClass} onClick={onCancel} disabled={saving}>Cancelar</button>
      <button type="submit" className={internalPrimaryButtonClass} disabled={saving}>{saving ? "Confirmando…" : "Confirmar"}</button>
      {error ? <p role="alert" className="w-full text-xs text-danger">{error}</p> : null}
    </form>
  );
}

function ReceiveForm({
  orderId, item, onDone, onCancel,
}: { orderId: number; item: AdminOrderItem; onDone: () => Promise<void>; onCancel: () => void }) {
  const serialized = Boolean(item.product_is_serialized);
  const [units, setUnits] = useState<ReceivedUnitInput[]>(() =>
    serialized ? Array.from({ length: item.quantity }, () => ({ serial_number: "", imei: "", imei2: "" })) : [],
  );
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  function setUnit(index: number, key: keyof ReceivedUnitInput, value: string) {
    setUnits((rows) => rows.map((row, at) => (at === index ? { ...row, [key]: value } : row)));
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (serialized && units.some((unit) => !unit.serial_number.trim() && !unit.imei.trim())) {
      setError("Escribe la serie o el IMEI de cada equipo.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await receiveOrderSupply(
        orderId, item.id,
        serialized
          ? units.map((unit) => ({
            serial_number: unit.serial_number.trim(),
            imei: unit.imei.trim(),
            ...(unit.imei2?.trim() ? { imei2: unit.imei2.trim() } : {}),
          }))
          : undefined,
      );
      await onDone();
    } catch (err) {
      setError(message(err, "No se pudo registrar la recepción."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} noValidate aria-label={`Recibir del proveedor: ${item.product_name}`} className="mt-3 space-y-3 rounded-lg border border-bd-border bg-background p-3">
      {serialized ? (
        <>
          <p className="text-xs text-muted">
            Registra cada equipo que llegó: {item.quantity === 1 ? "1 equipo" : `${item.quantity} equipos`}. Entran al stock y quedan para este pedido.
          </p>
          {units.map((unit, index) => (
            <fieldset key={index} className="grid gap-2 sm:grid-cols-3" aria-label={`Equipo ${index + 1}`}>
              <legend className="sr-only">Equipo {index + 1}</legend>
              <label className="block text-xs text-muted">
                Serie (equipo {index + 1})
                <input value={unit.serial_number} onChange={(e) => setUnit(index, "serial_number", e.target.value)} className={`mt-1 font-mono ${internalInputClass}`} />
              </label>
              <label className="block text-xs text-muted">
                IMEI (equipo {index + 1})
                <input value={unit.imei} inputMode="numeric" onChange={(e) => setUnit(index, "imei", e.target.value)} className={`mt-1 font-mono ${internalInputClass}`} />
              </label>
              <label className="block text-xs text-muted">
                IMEI 2 (equipo {index + 1}, opcional)
                <input value={unit.imei2 ?? ""} inputMode="numeric" onChange={(e) => setUnit(index, "imei2", e.target.value)} className={`mt-1 font-mono ${internalInputClass}`} />
              </label>
            </fieldset>
          ))}
        </>
      ) : (
        <p className="text-xs text-muted">
          Confirma que llegaron {item.quantity === 1 ? "1 unidad" : `${item.quantity} unidades`}: entran al stock para este pedido.
        </p>
      )}
      <div className="flex flex-wrap justify-end gap-2">
        <button type="button" className={internalButtonClass} onClick={onCancel} disabled={saving}>Cancelar</button>
        <button type="submit" className={internalPrimaryButtonClass} disabled={saving}>{saving ? "Registrando…" : "Registrar recepción"}</button>
      </div>
      {error ? <p role="alert" className="text-xs text-danger">{error}</p> : null}
    </form>
  );
}

function FailForm({
  orderId, item, onDone, onCancel,
}: { orderId: number; item: AdminOrderItem; onDone: () => Promise<void>; onCancel: () => void }) {
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!reason.trim()) { setError("Escribe el motivo."); return; }
    setSaving(true);
    setError(null);
    try {
      await failOrderSupply(orderId, item.id, reason.trim());
      await onDone();
    } catch (err) {
      setError(message(err, "No se pudo registrar que no se abasteció."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} noValidate aria-label={`No se pudo abastecer: ${item.product_name}`} className="mt-3 flex flex-wrap items-end gap-3 rounded-lg border border-danger-border bg-danger-surface p-3">
      <label className="block min-w-60 flex-1 text-xs text-muted">
        Motivo
        <input value={reason} onChange={(e) => setReason(e.target.value)} className={`mt-1.5 ${internalInputClass}`} />
      </label>
      <button type="button" className={internalButtonClass} onClick={onCancel} disabled={saving}>Cancelar</button>
      <button type="submit" className={internalPrimaryButtonClass} disabled={saving}>{saving ? "Registrando…" : "Registrar"}</button>
      {error ? <p role="alert" className="w-full text-xs text-danger">{error}</p> : null}
    </form>
  );
}

export function OrderSupplyPanel({
  order, onChanged,
}: { order: AdminOrderDetail; onChanged: (order: AdminOrderDetail) => void }) {
  const [action, setAction] = useState<Action>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const lines = order.items.filter((item) => item.supply_origin === "supplier");
  if (lines.length === 0) return null;

  const canManage = Boolean(order.can_manage_supply);
  const canReceive = Boolean(order.can_receive_supply);

  function done(kind: "confirm" | "receive" | "fail") {
    return async () => {
      setAction(null);
      setNotice(
        kind === "fail"
          ? "Registrado como no abastecido. La devolución o la anulación se hace con el flujo habitual de la orden; no ocurre sola."
          : kind === "receive" ? "Recepción registrada." : "Confirmado con el proveedor.",
      );
      try {
        onChanged(await fetchAdminOrderDetail(order.id));
        setError(null);
      } catch (err) {
        setError(message(err, "La acción se registró, pero no se pudo volver a leer la orden."));
      }
    };
  }

  return (
    <section className="rounded-xl border border-bd-border bg-surface p-6" aria-labelledby={`supply-${order.id}`}>
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <h2 id={`supply-${order.id}`} className="text-sm font-semibold text-foreground">Abastecimiento bajo pedido</h2>
        <SupplyStatusBadge status={order.supply_status} />
      </div>
      <p className="mb-4 text-xs text-muted">
        Lo que esta orden vendió sin stock propio, contra la oferta de un proveedor.
      </p>
      {notice ? <p role="status" className="mb-3 rounded-lg border border-bd-border bg-background p-3 text-xs text-foreground">{notice}</p> : null}
      {error ? <p role="alert" className="mb-3 text-xs text-danger">{error}</p> : null}
      <ul className="space-y-3">
        {lines.map((item) => {
          const status = item.supply_status ?? "";
          const mayConfirm = canManage && status === "to_confirm";
          const mayReceive = canReceive && status === "pending";
          const mayFail = canManage && (status === "to_confirm" || status === "pending");
          const open = action && action.itemId === item.id ? action.kind : null;
          return (
            <li key={item.id} className="rounded-lg border border-bd-border p-4" aria-label={`Abastecimiento de ${item.product_name}`}>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 text-sm">
                  <p className="font-semibold text-foreground">{item.product_name} × {item.quantity}</p>
                  <p className="mt-0.5 text-xs text-muted">
                    {[
                      item.supplier_name ? `Proveedor: ${item.supplier_name}` : "",
                      item.lead_time_days !== null && item.lead_time_days !== undefined
                        ? `Entrega en ${item.lead_time_days} ${item.lead_time_days === 1 ? "día" : "días"}` : "",
                    ].filter(Boolean).join(" · ")}
                  </p>
                  {item.supply_note ? <p className="mt-1 text-xs text-muted">Nota: {item.supply_note}</p> : null}
                </div>
                <SupplyStatusBadge status={status} />
              </div>
              {open === null && (mayConfirm || mayReceive || mayFail) ? (
                <div className="mt-3 flex flex-wrap gap-2">
                  {mayConfirm ? (
                    <button type="button" className={internalPrimaryButtonClass} onClick={() => { setNotice(null); setAction({ kind: "confirm", itemId: item.id }); }}>
                      Confirmar con proveedor
                    </button>
                  ) : null}
                  {mayReceive ? (
                    <button type="button" className={internalPrimaryButtonClass} onClick={() => { setNotice(null); setAction({ kind: "receive", itemId: item.id }); }}>
                      Recibir del proveedor
                    </button>
                  ) : null}
                  {mayFail ? (
                    <button type="button" className={internalButtonClass} onClick={() => { setNotice(null); setAction({ kind: "fail", itemId: item.id }); }}>
                      No se pudo abastecer
                    </button>
                  ) : null}
                </div>
              ) : null}
              {open === "confirm" ? <ConfirmForm orderId={order.id} item={item} onDone={done("confirm")} onCancel={() => setAction(null)} /> : null}
              {open === "receive" ? <ReceiveForm orderId={order.id} item={item} onDone={done("receive")} onCancel={() => setAction(null)} /> : null}
              {open === "fail" ? <FailForm orderId={order.id} item={item} onDone={done("fail")} onCancel={() => setAction(null)} /> : null}
              {status === "failed" ? (
                <p className="mt-3 text-xs text-danger">
                  No se abasteció. Si corresponde, haz la devolución o la anulación con el flujo habitual de la orden.
                </p>
              ) : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
