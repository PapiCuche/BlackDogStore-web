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

import { useEffect, useRef, useState } from "react";

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
  recordOrderSupplyRealCost,
  SupplyReceiveError,
  type SupplierOffer,
} from "../lib/internal-api";
import { pastedCodes, scanInputProps, useScanRows } from "../lib/scan-rows";
import { imeiProblem } from "../lib/unit-entry";
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

type UnitRow = { serial_number: string; imei: string; imei2: string; notes: string; cost: string };
type UnitKey = keyof UnitRow;
type RowErrors = Record<string, string>;

const emptyRow = (): UnitRow => ({ serial_number: "", imei: "", imei2: "", notes: "", cost: "" });
const rowKey = (index: number, field: string) => `${index}.${field}`;

const soles = (value: string | null | undefined) =>
  value === null || value === undefined || value === "" ? "—" : `S/ ${Number(value).toFixed(2)}`;

/** Whether THIS person sees costs: the server sends the key only to `costs.view`. */
const seesCosts = (item: AdminOrderItem) => item.agreed_unit_cost !== undefined;

/**
 * The server's rule, said before asking it (`stock_unit_services._clean_row`):
 * a serial for every device, an IMEI when the product requires one, the
 * second IMEI only with the first, nothing repeated in the batch. The server
 * still decides; its refusal comes back to the row and the input it names.
 */
export function unitRowErrors(rows: UnitRow[], requiresImei: boolean): RowErrors {
  const errors: RowErrors = {};
  const serials = new Map<string, number>();
  const imeis = new Map<string, number>();
  rows.forEach((row, index) => {
    const serial = row.serial_number.trim();
    const imei = row.imei.trim();
    const imei2 = row.imei2.trim();
    if (!serial) errors[rowKey(index, "serial_number")] = "Falta el número de serie.";
    else if (serials.has(serial.toUpperCase())) errors[rowKey(index, "serial_number")] = "Esta serie ya está en otra fila.";
    if (serial) serials.set(serial.toUpperCase(), index);
    if (imei) {
      const problem = imeiProblem(imei);
      if (problem) errors[rowKey(index, "imei")] = problem;
      else if (imeis.has(imei)) errors[rowKey(index, "imei")] = "Este IMEI ya está en otra fila.";
      imeis.set(imei, index);
    } else if (requiresImei) {
      errors[rowKey(index, "imei")] = "Este producto lleva IMEI: indícalo.";
    }
    if (imei2) {
      const problem = imeiProblem(imei2);
      if (!imei) errors[rowKey(index, "imei2")] = "El segundo IMEI va con el primero.";
      else if (problem) errors[rowKey(index, "imei2")] = problem;
      else if (imei2 === imei || imeis.has(imei2)) errors[rowKey(index, "imei2")] = "Este IMEI ya está en otra fila.";
      imeis.set(imei2, index);
    }
  });
  return errors;
}

/** What is being received, before anybody types: the order, the line and its supply. */
function LineFacts({ orderId, item }: { orderId: number; item: AdminOrderItem }) {
  const facts: [string, string][] = [
    ["Pedido", `#${orderId}`],
    ["Producto", item.product_name],
    ["Proveedor", item.supplier_name || "—"],
    ["Sucursal que recibe", item.fulfillment_branch?.name ?? "—"],
    ["Precio vendido", soles(item.price)],
    ["Cantidad", String(item.quantity)],
  ];
  if (seesCosts(item)) facts.push(["Costo pactado", soles(item.agreed_unit_cost)]);
  return (
    <dl className="grid gap-x-4 gap-y-1 text-xs sm:grid-cols-3" aria-label="Datos de la línea">
      {facts.map(([label, value]) => (
        <div key={label} className="min-w-0">
          <dt className="text-muted">{label}</dt>
          <dd className="truncate font-medium text-foreground">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function FieldError({ id, message }: { id: string; message?: string }) {
  return message ? <span id={id} className="mt-1 block text-[11px] text-danger">{message}</span> : null;
}

function ReceiveForm({
  orderId, item, onDone, onCancel,
}: { orderId: number; item: AdminOrderItem; onDone: (notice?: string) => Promise<void>; onCancel: () => void }) {
  const serialized = Boolean(item.product_is_serialized);
  const requiresImei = Boolean(item.requires_imei);
  const withCosts = seesCosts(item);
  // The scanner's path through a row: the IMEI barcode first when there is one.
  const chain: UnitKey[] = requiresImei ? ["imei", "imei2", "serial_number"] : ["serial_number"];
  const [units, setUnits] = useState<UnitRow[]>(() =>
    serialized ? Array.from({ length: item.quantity }, emptyRow) : [],
  );
  const [unitCost, setUnitCost] = useState("");
  const [errors, setErrors] = useState<RowErrors>({});
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const submitRef = useRef<HTMLButtonElement | null>(null);
  const { bind, focus } = useScanRows(chain.length, units.length, () => submitRef.current?.focus());

  useEffect(() => {
    if (serialized) focus(0, 0);
  }, [serialized, focus]);

  function setUnit(index: number, key: UnitKey, value: string) {
    setUnits((rows) => rows.map((row, at) => (at === index ? { ...row, [key]: value } : row)));
    setErrors((current) => {
      if (!current[rowKey(index, key)]) return current;
      const next = { ...current };
      delete next[rowKey(index, key)];
      return next;
    });
  }

  function paste(index: number, key: UnitKey, event: React.ClipboardEvent<HTMLInputElement>) {
    const codes = pastedCodes(event.clipboardData.getData("text"));
    if (codes.length === 0) return;
    event.preventDefault();
    setUnits((rows) => rows.map((row, at) => {
      const code = codes[at - index];
      return at >= index && code !== undefined ? { ...row, [key]: code } : row;
    }));
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const found = serialized ? unitRowErrors(units, requiresImei) : {};
    if (Object.keys(found).length) {
      setErrors(found);
      setError("Revisa los equipos marcados.");
      return;
    }
    setSaving(true);
    setErrors({});
    setError(null);
    try {
      await receiveOrderSupply(
        orderId, item.id,
        serialized
          ? units.map((unit) => ({
            serial_number: unit.serial_number.trim(),
            imei: unit.imei.trim(),
            ...(unit.imei2.trim() ? { imei2: unit.imei2.trim() } : {}),
            ...(unit.notes.trim() ? { notes: unit.notes.trim() } : {}),
            ...(withCosts && unit.cost.trim() ? { cost: unit.cost.trim() } : {}),
          }))
          : undefined,
        undefined,
        withCosts && unitCost.trim() ? { unitCost: unitCost.trim() } : {},
      );
      await onDone();
    } catch (err) {
      if (err instanceof SupplyReceiveError && err.code === "already_received") {
        await onDone("Esta línea ya estaba recibida: no se registró otra vez.");
        return;
      }
      if (err instanceof SupplyReceiveError && err.line !== null && err.unitField) {
        setErrors({ [rowKey(err.line - 1, err.unitField)]: err.message });
        setError(`Equipo ${err.line}: ${err.message}`);
      } else if (err instanceof SupplyReceiveError && err.field === "unit_cost") {
        setErrors({ unit_cost: err.message });
        setError(err.message);
      } else {
        setError(message(err, "No se pudo registrar la recepción."));
      }
    } finally {
      setSaving(false);
    }
  }

  const label: Record<UnitKey, [string, boolean]> = {
    imei: ["IMEI", !requiresImei],
    imei2: ["IMEI 2", true],
    serial_number: ["Serie", false],
    notes: ["Nota", true],
    cost: ["Costo real", true],
  };
  const shown: UnitKey[] = requiresImei
    ? ["imei", "imei2", "serial_number", "notes"]
    : ["serial_number", "imei", "imei2", "notes"];
  if (withCosts) shown.push("cost");

  function input(index: number, key: UnitKey) {
    const at = chain.indexOf(key);
    const errorId = `supply-${item.id}-${index}-${key}-error`;
    const invalid = Boolean(errors[rowKey(index, key)]);
    const numeric = key === "imei" || key === "imei2";
    return (
      <div key={key} className="min-w-0">
        <label className="block text-xs text-muted">
        {label[key][0]} (equipo {index + 1}{label[key][1] ? ", opcional" : ""})
        <input
          value={units[index][key]}
          onChange={(e) => setUnit(index, key, e.target.value)}
          onPaste={at >= 0 ? (e) => paste(index, key, e) : undefined}
          {...(at >= 0 ? bind(index, at) : {})}
          {...scanInputProps}
          inputMode={numeric ? "numeric" : key === "cost" ? "decimal" : "text"}
          aria-invalid={invalid || undefined}
          aria-describedby={invalid ? errorId : undefined}
          placeholder={key === "cost" ? (item.agreed_unit_cost ?? "") : undefined}
          className={`mt-1 ${key === "notes" || key === "cost" ? "" : "font-mono"} ${internalInputClass} ${invalid ? "border-danger" : ""}`}
        />
        </label>
        <FieldError id={errorId} message={errors[rowKey(index, key)]} />
      </div>
    );
  }

  return (
    <form onSubmit={submit} noValidate aria-label={`Recibir del proveedor: ${item.product_name}`} className="mt-3 space-y-3 rounded-lg border border-bd-border bg-background p-3">
      <LineFacts orderId={orderId} item={item} />
      {serialized ? (
        <>
          <p className="text-xs text-muted">
            Registra cada equipo que llegó: {item.quantity === 1 ? "1 equipo" : `${item.quantity} equipos`}, uno por fila.
            {" "}Serie siempre{requiresImei ? " e IMEI" : ""}. Con el lector, Enter pasa al campo siguiente; pegar una lista (un código por línea) llena las filas siguientes.
          </p>
          {units.map((_unit, index) => (
            <fieldset key={index} className={`grid gap-2 border-t border-bd-border pt-2 first:border-t-0 first:pt-0 sm:grid-cols-2 ${withCosts ? "lg:grid-cols-5" : "lg:grid-cols-4"}`} aria-label={`Equipo ${index + 1}`}>
              <legend className="sr-only">Equipo {index + 1}</legend>
              {shown.map((key) => input(index, key))}
            </fieldset>
          ))}
        </>
      ) : (
        <p className="text-xs text-muted">
          Confirma que llegaron {item.quantity === 1 ? "1 unidad" : `${item.quantity} unidades`}: entran al stock para este pedido.
        </p>
      )}
      {withCosts ? (
        <div className="max-w-xs">
          <label className="block text-xs text-muted">
            {serialized ? "Costo real por equipo (opcional, para los que no lo dicen)" : "Costo real por unidad (opcional)"}
            <input
              value={unitCost}
              onChange={(e) => { setUnitCost(e.target.value); setErrors((c) => ({ ...c, unit_cost: "" })); }}
              inputMode="decimal"
              autoComplete="off"
              placeholder={item.agreed_unit_cost ?? ""}
              aria-invalid={Boolean(errors.unit_cost) || undefined}
              aria-describedby={`supply-${item.id}-unit-cost-hint`}
              className={`mt-1 ${internalInputClass}`}
            />
          </label>
          <span id={`supply-${item.id}-unit-cost-hint`} className="mt-1 block text-[11px] text-muted">
            Vacío: entra al costo pactado. La venta conserva el costo pactado.
          </span>
          <FieldError id={`supply-${item.id}-unit-cost-error`} message={errors.unit_cost} />
        </div>
      ) : null}
      <div className="flex flex-wrap justify-end gap-2">
        <button type="button" className={internalButtonClass} onClick={onCancel} disabled={saving}>Cancelar</button>
        <button ref={submitRef} type="submit" className={internalPrimaryButtonClass} disabled={saving}>{saving ? "Registrando…" : "Registrar recepción"}</button>
      </div>
      {error ? <p role="alert" className="text-xs text-danger">{error}</p> : null}
    </form>
  );
}

/**
 * SUPPLIER-MARGIN (D2) — «Registrar costo real»: a received line whose real
 * cost nobody recorded. Only the line's real cost is written (the margin
 * reads it); the Kardex and the branch average are not touched.
 */
function RealCostForm({
  orderId, item, onDone,
}: { orderId: number; item: AdminOrderItem; onDone: () => Promise<void> }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!value.trim()) { setError("Escribe el costo real por unidad."); return; }
    setSaving(true);
    setError(null);
    try {
      await recordOrderSupplyRealCost(orderId, item.id, value.trim());
      await onDone();
    } catch (err) {
      setError(message(err, "No se pudo registrar el costo real."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} noValidate aria-label={`Registrar costo real: ${item.product_name}`} className="mt-3 flex flex-wrap items-end gap-3 rounded-lg border border-warning-border bg-warning-surface p-3">
      <label className="block min-w-48 text-xs text-muted">
        Costo real por unidad
        <input value={value} onChange={(e) => setValue(e.target.value)} inputMode="decimal" autoComplete="off"
          placeholder={item.agreed_unit_cost ?? ""} className={`mt-1.5 ${internalInputClass}`} />
      </label>
      <button type="submit" className={internalPrimaryButtonClass} disabled={saving}>{saving ? "Registrando…" : "Registrar costo real"}</button>
      <p className="w-full text-[11px] text-muted">Sólo para el margen realizado: no cambia el Kardex ni el costo promedio.</p>
      {error ? <p role="alert" className="w-full text-xs text-danger">{error}</p> : null}
    </form>
  );
}

/** For `costs.view`: what the line was sold against and what it really cost. */
function CostFacts({ item }: { item: AdminOrderItem }) {
  if (!seesCosts(item)) return null;
  const difference = item.unit_cost_difference;
  return (
    <p className="mt-1 text-xs text-muted" aria-label="Costos de la línea">
      Costo pactado {soles(item.agreed_unit_cost)}
      {item.supply_status === "received" && !item.received_unit_cost ? " · Costo real pendiente (sin costo real registrado)" : null}
      {item.received_unit_cost !== null && item.received_unit_cost !== undefined ? (
        <>
          {" · "}Costo real {soles(item.received_unit_cost)}
          {difference !== null && difference !== undefined && Number(difference) !== 0
            ? ` · Diferencia ${Number(difference) > 0 ? "+" : ""}${Number(difference).toFixed(2)} por unidad`
            : " · Sin diferencia"}
        </>
      ) : null}
    </p>
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

  function done(kind: "confirm" | "receive" | "fail" | "real-cost") {
    return async (said?: string) => {
      setAction(null);
      setNotice(
        said ?? (kind === "fail"
          ? "Registrado como no abastecido. La devolución o la anulación se hace con el flujo habitual de la orden; no ocurre sola."
          : kind === "receive" ? "Recepción registrada."
            : kind === "real-cost" ? "Costo real registrado." : "Confirmado con el proveedor."),
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
                      item.fulfillment_branch ? `Sucursal: ${item.fulfillment_branch.name}` : "",
                      `Vendido a ${soles(item.price)} c/u`,
                      item.lead_time_days !== null && item.lead_time_days !== undefined
                        ? `Entrega en ${item.lead_time_days} ${item.lead_time_days === 1 ? "día" : "días"}` : "",
                    ].filter(Boolean).join(" · ")}
                  </p>
                  <CostFacts item={item} />
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
              {canReceive && seesCosts(item) && status === "received" && !item.received_unit_cost ? (
                <RealCostForm orderId={order.id} item={item} onDone={done("real-cost")} />
              ) : null}
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
