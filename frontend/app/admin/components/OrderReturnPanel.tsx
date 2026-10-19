"use client";

/**
 * POST-DELIVERY-RETURN (fase 1) — la devolución de un pedido cuya mercadería
 * ya salió (enviado o entregado, o una venta de caja) y el cliente la trae.
 * Siempre el pedido completo (D1). `docs/diseno-devolucion-post-entrega.md`.
 *
 * Tres pasos, cada uno una vez y por quien corresponde (lo dice el servidor en
 * `return_block`):
 *
 *  1. Registrar (`can_request`): tipo y motivo. Nada se mueve. Fuera del plazo
 *     de la empresa, sólo un superior (se avisa).
 *  2. Recibir (`can_receive`): la sucursal, cada equipo por su IMEI o serie —
 *     que tiene que ser uno de los que salieron con este pedido— y el estado
 *     en que llega; cada cantidad, entera. El stock entra al costo de la venta.
 *  3. Cerrar (`can_close`, un superior): lo cobrado menos un descuento opcional
 *     con motivo. Cancela la venta y deja el reembolso PENDIENTE.
 *
 *  Antes de recibir, la devolución se puede retirar (`can_withdraw`) con motivo.
 *
 * Nada de esto devuelve dinero ni llama a la pasarela. Los errores del
 * servidor se enseñan tal cual; tras cada paso, el pedido es el que respondió.
 */
import { useId, useMemo, useState } from "react";
import {
  AdminOrderDetail,
  AdminOrderItem,
  OrderRefundMethod,
  OrderReturn,
  OrderReturnBlock,
  OrderReturnCondition,
  OrderReturnKind,
  OrderReturnReceiveLine,
  REFUND_METHOD_LABELS,
  RETURN_CONDITION_LABELS,
  RETURN_DEDUCTION_REASON_MAX,
  RETURN_DEDUCTION_REASON_MIN,
  RETURN_KIND_LABELS,
  RETURN_REASON_MAX,
  RETURN_REASON_MIN,
  closeOrderReturn,
  formatAdminDate,
  receiveOrderReturn,
  requestOrderReturn,
  withdrawOrderReturn,
} from "../../lib/admin";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";
import { normaliseReason } from "./OrderCancelPanel";

const dangerButtonClass =
  "inline-flex items-center justify-center rounded-lg border border-danger-border px-4 py-2 text-sm font-semibold text-danger transition hover:bg-danger-surface disabled:cursor-not-allowed disabled:opacity-40";
const selectClass =
  "mt-1 block w-full rounded-lg border border-bd-border bg-background px-2 py-2 text-sm text-foreground";
const textareaClass =
  "mt-1 w-full rounded-lg border border-bd-border bg-background px-3 py-2 text-sm text-foreground";

const DEVICE_CONDITIONS: OrderReturnCondition[] = ["sellable", "review", "damaged"];
/** Un producto por cantidad no tiene dónde apartarse: «a revisión» queda para la fase 2. */
const QUANTITY_CONDITIONS: OrderReturnCondition[] = ["sellable", "damaged"];

type Props = {
  order: AdminOrderDetail;
  /** El pedido como quedó en el servidor tras un paso de la devolución. */
  onChanged: (order: AdminOrderDetail) => void;
};

function errorText(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

// ── importes, en céntimos para no perder nada en el camino ──────────────────

/** «10», «10.5», «10,50» → céntimos; null si no es un importe con hasta dos decimales. */
export function toCents(text: string): number | null {
  const value = text.trim().replace(",", ".");
  if (!/^\d+(\.\d{1,2})?$/.test(value)) return null;
  const [whole, fraction = ""] = value.split(".");
  return parseInt(whole, 10) * 100 + parseInt(fraction.padEnd(2, "0"), 10);
}

export function fromCents(cents: number): string {
  return `${Math.floor(cents / 100)}.${String(cents % 100).padStart(2, "0")}`;
}

// ── el IMEI o la serie, como los lee el servidor ─────────────────────────────

type DeviceCandidate = { id: number; imei: string; imei2: string; serial_number: string };

/** El equipo del pedido que nombra `code` (IMEI, segundo IMEI o serie), o null. */
export function matchDevice(code: string, candidates: DeviceCandidate[]): DeviceCandidate | null {
  const text = code.trim();
  if (!text) return null;
  const digits = text.replace(/[\s.\-/]/g, "");
  const serial = text.replace(/\s+/g, "").toUpperCase();
  return candidates.find((unit) =>
    (digits && (digits === unit.imei || digits === unit.imei2))
    || (serial && serial === (unit.serial_number || "").toUpperCase()),
  ) ?? null;
}

export function OrderReturnPanel({ order, onChanged }: Props) {
  const block = order.return_block;
  const [notice, setNotice] = useState<string | null>(null);
  if (!block) return null;
  const shown = block.return;
  if (!block.eligible && !shown) return null;

  const open = shown && (shown.status === "requested" || shown.status === "received") ? shown : null;
  const anyAction = block.can_request || block.can_receive || block.can_close || block.can_withdraw;
  // «Ya tiene una devolución» no le dice nada a quien la está viendo.
  const blockers = open || shown?.status === "closed" ? [] : block.blockers;

  function done(updated: AdminOrderDetail, message: string) {
    setNotice(message);
    onChanged(updated);
  }

  return (
    <section
      aria-labelledby="order-return-title"
      data-testid="order-return-panel"
      className="rounded-xl border border-bd-border bg-surface p-6"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="order-return-title" className="text-sm font-semibold text-foreground">Devolución</h2>
          <p className="mt-1 text-xs text-muted">
            Del pedido completo, cuando la mercadería ya salió y el cliente la trae. Plazo simple de la empresa:{" "}
            {block.window_days} día{block.window_days === 1 ? "" : "s"} desde la entrega.
          </p>
        </div>
        {shown ? (
          <span
            data-testid="order-return-status"
            className="inline-flex rounded-full border border-bd-border bg-background px-3 py-1 text-xs font-semibold text-foreground"
          >
            {shown.status_label}
          </span>
        ) : null}
      </div>

      {shown ? <ReturnSummary ret={shown} /> : (
        <p className="mt-3 text-sm text-muted">Este pedido no tiene una devolución registrada.</p>
      )}

      {blockers.length > 0 ? (
        <div data-testid="order-return-blockers" className="mt-4 text-sm">
          <p className="text-xs font-semibold text-muted">No se puede registrar una devolución:</p>
          <ul className="mt-1 list-disc space-y-1 pl-5 text-sm text-muted">
            {blockers.map((blocker) => <li key={blocker.code}>{blocker.detail}</li>)}
          </ul>
        </div>
      ) : null}

      {block.can_request ? (
        <RequestForm order={order} block={block} onDone={(updated) => done(updated, "Devolución registrada.")} />
      ) : null}
      {block.can_receive && open ? (
        <ReceiveForm order={order} block={block} onDone={(updated) => done(updated, "Mercadería recibida.")} />
      ) : null}
      {block.can_close && open ? (
        <CloseForm order={order} block={block} onDone={(updated) => done(updated, "Devolución cerrada.")} />
      ) : null}
      {block.can_withdraw && open ? (
        <WithdrawForm order={order} onDone={(updated) => done(updated, "Devolución retirada.")} />
      ) : null}

      {!anyAction && blockers.length === 0 ? (
        <p data-testid="order-return-read-only" className="mt-4 text-xs text-muted">
          {open?.status === "requested"
            ? "Sólo lectura: la mercadería la recibe quien maneja el inventario en la sucursal."
            : open?.status === "received"
              ? "Sólo lectura: la devolución la cierra un superior (administrador de la empresa)."
              : "Sólo lectura."}
        </p>
      ) : null}

      {notice ? <p role="status" className="mt-4 text-sm text-muted">{notice}</p> : null}
    </section>
  );
}

// ── lo que ya pasó ───────────────────────────────────────────────────────────

type Step = { key: string; label: string; at: string | null; actor: string; done: boolean };

function returnSteps(ret: OrderReturn): Step[] {
  const requested = { key: "requested", label: "Solicitada", at: ret.requested_at, actor: ret.requested_by, done: true };
  if (ret.status === "withdrawn") {
    return [requested, { key: "withdrawn", label: "Retirada", at: ret.withdrawn_at, actor: ret.withdrawn_by, done: true }];
  }
  return [
    requested,
    { key: "received", label: "Recibida", at: ret.received_at, actor: ret.received_by, done: Boolean(ret.received_at) },
    { key: "closed", label: "Cerrada", at: ret.closed_at, actor: ret.closed_by, done: Boolean(ret.closed_at) },
  ];
}

function money(value: string | null) {
  return value == null ? "—" : `S/ ${value}`;
}

function ReturnSummary({ ret }: { ret: OrderReturn }) {
  const steps = returnSteps(ret);
  const deduction = ret.deduction_amount != null && toCents(ret.deduction_amount) ? ret.deduction_amount : null;
  return (
    <>
      <ol aria-label="Pasos de la devolución" className="mt-5 flex flex-col sm:flex-row">
        {steps.map((step, index) => {
          const last = index === steps.length - 1;
          return (
            <li
              key={step.key}
              data-testid={`order-return-step-${step.key}`}
              data-state={step.done ? "done" : "todo"}
              className="flex gap-3 sm:flex-1 sm:flex-col sm:gap-2"
            >
              <div className="flex flex-col items-center sm:flex-row">
                <span
                  aria-hidden="true"
                  className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full border-2 text-[11px] font-bold ${
                    step.done ? "border-foreground bg-foreground text-background" : "border-bd-border bg-background text-muted"
                  }`}
                >
                  {step.done ? "✓" : index + 1}
                </span>
                {!last ? (
                  <span
                    aria-hidden="true"
                    className={`my-1 min-h-6 w-px flex-1 sm:mx-2 sm:my-0 sm:h-px sm:min-h-0 sm:w-auto ${
                      step.done ? "bg-foreground/50" : "bg-bd-border"
                    }`}
                  />
                ) : null}
              </div>
              <div className={`min-w-0 ${last ? "" : "pb-4 sm:pb-0"} sm:pr-3`}>
                <p className={`text-sm ${step.done ? "font-semibold text-foreground" : "text-muted"}`}>{step.label}</p>
                <p className="text-xs text-muted">
                  <span className="sr-only">{step.done ? "Hecho" : "Pendiente"}. </span>
                  {step.at ? formatAdminDate(step.at) : ""}
                </p>
                {step.actor ? <p className="text-xs text-muted">{step.actor}</p> : null}
              </div>
            </li>
          );
        })}
      </ol>

      <dl className="mt-4 grid grid-cols-1 gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-xs text-muted">Tipo</dt>
          <dd className="text-foreground">{ret.kind_label}</dd>
        </div>
        {ret.receiving_branch ? (
          <div>
            <dt className="text-xs text-muted">Recibida en</dt>
            <dd className="text-foreground">{ret.receiving_branch.name}</dd>
          </div>
        ) : null}
        <div className="sm:col-span-2">
          <dt className="text-xs text-muted">Motivo</dt>
          <dd className="whitespace-pre-wrap text-foreground">{ret.reason}</dd>
        </div>
        {ret.status === "withdrawn" && ret.withdrawn_reason ? (
          <div className="sm:col-span-2">
            <dt className="text-xs text-muted">Motivo del retiro</dt>
            <dd className="whitespace-pre-wrap text-foreground">{ret.withdrawn_reason}</dd>
          </div>
        ) : null}
        {ret.status === "closed" ? (
          <>
            <div>
              <dt className="text-xs text-muted">Cobrado</dt>
              <dd className="text-foreground">{money(ret.paid_amount)}</dd>
            </div>
            {deduction ? (
              <div>
                <dt className="text-xs text-muted">Descuento</dt>
                <dd className="text-foreground">
                  {money(deduction)}{ret.deduction_reason ? ` — ${ret.deduction_reason}` : ""}
                </dd>
              </div>
            ) : null}
            <div>
              <dt className="text-xs text-muted">A devolver (pendiente)</dt>
              <dd data-testid="order-return-refund" className="font-semibold text-foreground">
                {money(ret.refund_amount)}{ret.refund_method_label ? ` · ${ret.refund_method_label}` : ""}
              </dd>
            </div>
          </>
        ) : null}
      </dl>

      {ret.lines.length > 0 ? (
        <div className="mt-4">
          <h3 className="text-xs font-semibold text-muted">Lo que volvió</h3>
          <ul data-testid="order-return-lines" className="mt-2 space-y-1 text-sm">
            {ret.lines.map((line) => {
              const code = line.imei || line.serial_number;
              return (
                <li key={line.id} className="flex flex-wrap gap-x-2 text-foreground">
                  <span>{line.product_name}</span>
                  {code ? <span className="font-mono text-xs text-muted">{code}</span> : (
                    <span className="text-muted">× {line.quantity}</span>
                  )}
                  <span className="text-muted">· {line.condition_label}</span>
                  {line.entered_by_hand ? <span className="text-muted">· ya ingresado a mano (sin mover stock)</span> : null}
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
    </>
  );
}

// ── 1. registrar ─────────────────────────────────────────────────────────────

function RequestForm({
  order, block, onDone,
}: {
  order: AdminOrderDetail;
  block: OrderReturnBlock;
  onDone: (order: AdminOrderDetail) => void;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<OrderReturnKind | "">("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ready = kind !== "" && normaliseReason(reason).length >= RETURN_REASON_MIN;

  async function submit() {
    if (!ready || busy) return;
    setBusy(true);
    setError(null);
    try {
      onDone(await requestOrderReturn(order.id, kind, normaliseReason(reason)));
    } catch (err) {
      setError(errorText(err, "No se pudo registrar la devolución."));
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <div className="mt-4">
        <button type="button" onClick={() => setOpen(true)} className={internalPrimaryButtonClass}>
          Registrar devolución
        </button>
      </div>
    );
  }
  return (
    <div data-testid="order-return-request" className="mt-4 space-y-3 border-t border-bd-border pt-4">
      <h3 className="text-sm font-semibold text-foreground">Registrar devolución</h3>
      {block.superior_needed ? (
        <div
          data-testid="order-return-superior"
          className="rounded-lg border border-warning-border bg-warning-surface px-3 py-2 text-sm text-foreground"
        >
          <p className="font-semibold">Requiere autorización superior</p>
          <ul className="mt-1 list-disc space-y-1 pl-5">
            {block.superior_conditions.map((condition) => <li key={condition.code}>{condition.detail}</li>)}
          </ul>
          <p className="mt-1 text-xs text-muted">Queda registrada con tu usuario y estas condiciones.</p>
        </div>
      ) : null}
      <div>
        <label htmlFor={`${id}-kind`} className="block text-sm font-semibold text-foreground">Tipo de devolución</label>
        <select
          id={`${id}-kind`}
          value={kind}
          onChange={(event) => setKind(event.target.value as OrderReturnKind | "")}
          disabled={busy}
          className={selectClass}
        >
          <option value="">Elige el tipo…</option>
          {(Object.keys(RETURN_KIND_LABELS) as OrderReturnKind[]).map((code) => (
            <option key={code} value={code}>{RETURN_KIND_LABELS[code]}</option>
          ))}
        </select>
      </div>
      <div>
        <label htmlFor={`${id}-reason`} className="block text-sm font-semibold text-foreground">Motivo</label>
        <textarea
          id={`${id}-reason`}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          maxLength={RETURN_REASON_MAX}
          rows={3}
          required
          disabled={busy}
          className={textareaClass}
        />
        <p className="mt-1 text-xs text-muted">
          Obligatorio, al menos {RETURN_REASON_MIN} caracteres. Registrarla no mueve stock ni dinero: eso pasa al
          recibir la mercadería y al cerrar la devolución.
        </p>
      </div>
      {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}
      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={() => void submit()} disabled={!ready || busy} className={internalPrimaryButtonClass}>
          {busy ? "Registrando…" : "Registrar devolución"}
        </button>
        <button type="button" onClick={() => setOpen(false)} disabled={busy} className={internalButtonClass}>
          Descartar
        </button>
      </div>
    </div>
  );
}

// ── 2. recibir ───────────────────────────────────────────────────────────────

type DeviceRow = {
  key: string;
  item: AdminOrderItem;
  candidates: DeviceCandidate[];
  /** «equipo 2 de 3» del mismo producto. */
  ordinal: number;
  count: number;
};

/** Lo que se vendió, fila por fila: un equipo por fila; una línea por cantidad, entera. */
function receptionRows(items: AdminOrderItem[]) {
  const unitsByProduct = new Map<number, DeviceCandidate[]>();
  for (const item of items) {
    // Dos líneas de un producto comparten sus equipos: el servidor los da en la primera.
    const known = unitsByProduct.get(item.product_id) ?? [];
    unitsByProduct.set(item.product_id, [...known, ...(item.units ?? [])]);
  }
  const devices: DeviceRow[] = [];
  const quantities: AdminOrderItem[] = [];
  const counted = new Map<number, number>();
  for (const item of items) {
    if (item.is_serialized) {
      for (let index = 0; index < item.quantity; index += 1) {
        const ordinal = (counted.get(item.product_id) ?? 0) + 1;
        counted.set(item.product_id, ordinal);
        devices.push({
          key: `${item.id}-${index}`, item, candidates: unitsByProduct.get(item.product_id) ?? [], ordinal, count: 0,
        });
      }
    } else {
      quantities.push(item);
    }
  }
  for (const row of devices) row.count = counted.get(row.item.product_id) ?? 1;
  return { devices, quantities };
}

function ReceiveForm({
  order, block, onDone,
}: {
  order: AdminOrderDetail;
  block: OrderReturnBlock;
  onDone: (order: AdminOrderDetail) => void;
}) {
  const id = useId();
  const { devices, quantities } = useMemo(() => receptionRows(order.items), [order.items]);
  const branches = block.receive_branches;
  const [branch, setBranch] = useState<string>(branches.length === 1 ? String(branches[0].id) : "");
  const [codes, setCodes] = useState<Record<string, string>>({});
  const [conditions, setConditions] = useState<Record<string, OrderReturnCondition | "">>({});
  // Líneas que ya entraron con una devolución manual: un superior las recibe sin mover stock.
  const flagged = new Set(block.manual_return_lines ?? []);
  const [byHand, setByHand] = useState<Record<number, boolean>>({});
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Cada equipo escaneado tiene que ser uno de los que salieron, y no el de otra fila.
  const matched = new Map<string, DeviceCandidate | null>();
  const used = new Set<number>();
  for (const row of devices) {
    const code = codes[row.key] ?? "";
    const unit = matchDevice(code, row.candidates);
    if (unit && !used.has(unit.id)) {
      used.add(unit.id);
      matched.set(row.key, unit);
    } else {
      matched.set(row.key, null);
    }
  }
  const deviceReady = (row: DeviceRow) => {
    const code = (codes[row.key] ?? "").trim();
    // Sin equipos conocidos en el detalle, lo comprueba sólo el servidor.
    return row.candidates.length === 0 ? code.length > 0 : matched.get(row.key) != null;
  };
  const conditionKey = (kind: "d" | "q", key: string | number) => `${kind}:${key}`;
  const ready = branch !== ""
    && devices.every((row) => deviceReady(row) && conditions[conditionKey("d", row.key)])
    && quantities.every((item) => (flagged.has(item.id)
      ? Boolean(byHand[item.id])
      : Boolean(conditions[conditionKey("q", item.id)])));

  function setCondition(key: string, value: OrderReturnCondition | "") {
    setConditions((current) => ({ ...current, [key]: value }));
    setConfirming(false);
  }

  async function submit() {
    if (!ready || busy) return;
    setBusy(true);
    setError(null);
    const lines: OrderReturnReceiveLine[] = [
      ...devices.map((row) => ({
        order_item: row.item.id,
        quantity: 1,
        condition: conditions[conditionKey("d", row.key)] as OrderReturnCondition,
        code: (codes[row.key] ?? "").trim(),
      })),
      ...quantities.map((item) => (byHand[item.id]
        ? { order_item: item.id, quantity: item.quantity, condition: "sellable" as OrderReturnCondition, already_in_stock: true }
        : {
          order_item: item.id,
          quantity: item.quantity,
          condition: conditions[conditionKey("q", item.id)] as OrderReturnCondition,
        })),
    ];
    try {
      onDone(await receiveOrderReturn(order.id, { branch: Number(branch), lines }));
    } catch (err) {
      setError(errorText(err, "No se pudo recibir la devolución."));
      setConfirming(false);
      setBusy(false);
    }
  }

  return (
    <div data-testid="order-return-receive" className="mt-5 space-y-4 border-t border-bd-border pt-4">
      <div>
        <h3 className="text-sm font-semibold text-foreground">Recibir la mercadería</h3>
        <p className="mt-1 text-xs text-muted">
          Vuelve el pedido completo. El stock entra al Kardex al costo con que salió en la venta. «Vendible» vuelve a
          la venta; «A revisión» entra como «Apartado» hasta que alguien lo libere; «Dañado» entra y se da de baja.
        </p>
      </div>
      <div>
        <label htmlFor={`${id}-branch`} className="block text-sm font-semibold text-foreground">Sucursal que recibe</label>
        <select
          id={`${id}-branch`}
          value={branch}
          onChange={(event) => { setBranch(event.target.value); setConfirming(false); }}
          disabled={busy}
          className={selectClass}
        >
          {branches.length !== 1 ? <option value="">Elige la sucursal…</option> : null}
          {branches.map((option) => <option key={option.id} value={option.id}>{option.name}</option>)}
        </select>
      </div>

      <ul className="space-y-3">
        {devices.map((row) => {
          const code = codes[row.key] ?? "";
          const unit = matched.get(row.key);
          const typed = code.trim().length > 0;
          const key = conditionKey("d", row.key);
          return (
            <li
              key={row.key}
              data-testid={`order-return-device-${row.key}`}
              className="rounded-lg border border-bd-border bg-background p-3 text-sm"
            >
              <p className="font-semibold text-foreground">
                {row.item.product_name}
                {row.count > 1
                  ? <span className="ml-1 font-normal text-muted">(equipo {row.ordinal} de {row.count})</span> : null}
              </p>
              <div className="mt-2 grid gap-3 sm:grid-cols-2">
                <div>
                  <label htmlFor={`${id}-code-${row.key}`} className="block text-xs font-semibold text-foreground">
                    IMEI o serie del equipo que vuelve
                  </label>
                  <input
                    id={`${id}-code-${row.key}`}
                    type="text"
                    value={code}
                    onChange={(event) => {
                      const value = event.target.value;
                      setCodes((current) => ({ ...current, [row.key]: value }));
                      setConfirming(false);
                    }}
                    autoComplete="off"
                    spellCheck={false}
                    disabled={busy}
                    className={`mt-1 ${internalInputClass}`}
                  />
                  {typed && row.candidates.length > 0 ? (
                    unit ? (
                      <p className="mt-1 text-xs text-success">Coincide con un equipo que salió con este pedido.</p>
                    ) : (
                      <p className="mt-1 text-xs text-danger">
                        {matchDevice(code, row.candidates)
                          ? "Ese equipo ya está en otra fila."
                          : "No es uno de los equipos que salieron con este pedido."}
                      </p>
                    )
                  ) : null}
                </div>
                <div>
                  <label htmlFor={`${id}-cond-${row.key}`} className="block text-xs font-semibold text-foreground">
                    Estado en que llega
                  </label>
                  <select
                    id={`${id}-cond-${row.key}`}
                    value={conditions[key] ?? ""}
                    onChange={(event) => setCondition(key, event.target.value as OrderReturnCondition | "")}
                    disabled={busy}
                    className={selectClass}
                  >
                    <option value="">Elige el estado…</option>
                    {DEVICE_CONDITIONS.map((condition) => (
                      <option key={condition} value={condition}>{RETURN_CONDITION_LABELS[condition]}</option>
                    ))}
                  </select>
                </div>
              </div>
            </li>
          );
        })}
        {quantities.map((item) => {
          const key = conditionKey("q", item.id);
          return (
            <li
              key={item.id}
              data-testid={`order-return-quantity-${item.id}`}
              className="rounded-lg border border-bd-border bg-background p-3 text-sm"
            >
              <p className="font-semibold text-foreground">{item.product_name}</p>
              <div className="mt-2 grid gap-3 sm:grid-cols-2">
                <div>
                  <label htmlFor={`${id}-qty-${item.id}`} className="block text-xs font-semibold text-foreground">
                    Cantidad que vuelve
                  </label>
                  <input
                    id={`${id}-qty-${item.id}`}
                    type="number"
                    value={item.quantity}
                    readOnly
                    aria-describedby={`${id}-qty-${item.id}-hint`}
                    className={`mt-1 ${internalInputClass}`}
                  />
                  <p id={`${id}-qty-${item.id}-hint`} className="mt-1 text-xs text-muted">
                    Lo vendido, entero: la devolución es del pedido completo.
                  </p>
                </div>
                <div>
                  <label htmlFor={`${id}-qcond-${item.id}`} className="block text-xs font-semibold text-foreground">
                    Estado en que llega
                  </label>
                  <select
                    id={`${id}-qcond-${item.id}`}
                    value={conditions[key] ?? ""}
                    onChange={(event) => setCondition(key, event.target.value as OrderReturnCondition | "")}
                    disabled={busy || Boolean(byHand[item.id])}
                    className={selectClass}
                  >
                    <option value="">Elige el estado…</option>
                    {QUANTITY_CONDITIONS.map((condition) => (
                      <option key={condition} value={condition}>{RETURN_CONDITION_LABELS[condition]}</option>
                    ))}
                  </select>
                  <p className="mt-1 text-xs text-muted">Por cantidad: vendible o dañado.</p>
                </div>
              </div>
              {flagged.has(item.id) ? (
                <div data-testid={`order-return-by-hand-${item.id}`} className="mt-3 rounded-lg border border-warning-border bg-warning-surface p-3 text-xs text-foreground">
                  <p>
                    Este producto tuvo una devolución manual en esta sucursal después de la venta. Si esa entrada
                    fue esta misma devolución, ya está en el stock y no debe entrar otra vez.
                  </p>
                  {block.can_receive_by_hand ? (
                    <label className="mt-2 flex items-start gap-2">
                      <input
                        type="checkbox"
                        checked={Boolean(byHand[item.id])}
                        onChange={(event) => {
                          setByHand((current) => ({ ...current, [item.id]: event.target.checked }));
                          setConfirming(false);
                        }}
                        disabled={busy}
                        className="mt-0.5"
                      />
                      <span>Ya ingresó a mano con esta devolución: recibirla sin mover stock</span>
                    </label>
                  ) : (
                    <p className="mt-2 font-semibold">Recibir esta línea corresponde a un superior.</p>
                  )}
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>

      {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}
      {confirming ? (
        <div
          role="group"
          aria-label="Confirmar recepción"
          data-testid="order-return-receive-confirm"
          className="rounded-lg border border-warning-border bg-warning-surface px-3 py-3 text-sm text-foreground"
        >
          <p>
            Vas a recibir la mercadería en <strong>{branches.find((b) => String(b.id) === branch)?.name}</strong>: el
            stock entra al Kardex y el despacho queda como devuelto en el historial. No se puede repetir.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button type="button" onClick={() => void submit()} disabled={busy} className={internalPrimaryButtonClass}>
              {busy ? "Recibiendo…" : "Sí, recibir"}
            </button>
            <button type="button" onClick={() => setConfirming(false)} disabled={busy} className={internalButtonClass}>
              Volver
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setConfirming(true)}
          disabled={!ready || busy}
          className={internalPrimaryButtonClass}
        >
          Recibir mercadería
        </button>
      )}
    </div>
  );
}

// ── 3. cerrar ────────────────────────────────────────────────────────────────

function CloseForm({
  order, block, onDone,
}: {
  order: AdminOrderDetail;
  block: OrderReturnBlock;
  onDone: (order: AdminOrderDetail) => void;
}) {
  const id = useId();
  const [deduction, setDeduction] = useState("");
  const [deductionReason, setDeductionReason] = useState("");
  const [method, setMethod] = useState<OrderRefundMethod | "">("");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const paid = toCents(block.paid_amount) ?? 0;
  const deductionCents = deduction.trim() === "" ? 0 : toCents(deduction);
  const deductionProblem = deductionCents == null
    ? "Escribe el descuento como un importe con hasta dos decimales."
    : deductionCents > paid
      ? `El descuento no puede pasar de lo cobrado (S/ ${fromCents(paid)}).`
      : null;
  const hasDeduction = deductionCents != null && deductionCents > 0;
  const reasonReady = !hasDeduction || normaliseReason(deductionReason).length >= RETURN_DEDUCTION_REASON_MIN;
  const refund = deductionCents != null && !deductionProblem ? paid - deductionCents : null;
  const ready = refund != null && reasonReady && method !== "";

  function edit<T>(setter: (value: T) => void) {
    return (value: T) => { setter(value); setConfirming(false); };
  }

  async function submit() {
    if (!ready || busy) return;
    setBusy(true);
    setError(null);
    const body: Parameters<typeof closeOrderReturn>[1] = { refund_amount: fromCents(refund), refund_method: method };
    if (hasDeduction && deductionCents != null) {
      body.deduction_amount = fromCents(deductionCents);
      body.deduction_reason = normaliseReason(deductionReason);
    }
    try {
      onDone(await closeOrderReturn(order.id, body));
    } catch (err) {
      setError(errorText(err, "No se pudo cerrar la devolución."));
      setConfirming(false);
      setBusy(false);
    }
  }

  return (
    <div data-testid="order-return-close" className="mt-5 space-y-4 border-t border-bd-border pt-4">
      <div>
        <h3 className="text-sm font-semibold text-foreground">Cerrar la devolución</h3>
        <p className="mt-1 text-xs text-muted">
          La mercadería ya se recibió. Confirma cuánto se devuelve: lo cobrado, menos un descuento opcional (por
          ejemplo, el flete de vuelta). Nunca más de lo cobrado.
        </p>
      </div>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
        <div>
          <dt className="text-xs text-muted">Cobrado</dt>
          <dd data-testid="order-return-paid" className="text-foreground">S/ {fromCents(paid)}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted">A devolver</dt>
          <dd data-testid="order-return-computed-refund" className="font-semibold text-foreground">
            {refund == null ? "—" : `S/ ${fromCents(refund)}`}
          </dd>
        </div>
      </dl>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor={`${id}-deduction`} className="block text-sm font-semibold text-foreground">
            Descuento (opcional)
          </label>
          <input
            id={`${id}-deduction`}
            type="text"
            inputMode="decimal"
            value={deduction}
            onChange={(event) => edit(setDeduction)(event.target.value)}
            placeholder="0.00"
            disabled={busy}
            className={`mt-1 ${internalInputClass}`}
          />
          {deduction.trim() && deductionProblem ? (
            <p className="mt-1 text-xs text-danger">{deductionProblem}</p>
          ) : null}
        </div>
        <div>
          <label htmlFor={`${id}-method`} className="block text-sm font-semibold text-foreground">
            Medio del reembolso
          </label>
          <select
            id={`${id}-method`}
            value={method}
            onChange={(event) => edit(setMethod)(event.target.value as OrderRefundMethod | "")}
            disabled={busy}
            className={selectClass}
          >
            <option value="">Elige el medio…</option>
            {(Object.keys(REFUND_METHOD_LABELS) as OrderRefundMethod[]).map((code) => (
              <option key={code} value={code}>{REFUND_METHOD_LABELS[code]}</option>
            ))}
          </select>
        </div>
      </div>
      {hasDeduction ? (
        <div>
          <label htmlFor={`${id}-deduction-reason`} className="block text-sm font-semibold text-foreground">
            Motivo del descuento
          </label>
          <textarea
            id={`${id}-deduction-reason`}
            value={deductionReason}
            onChange={(event) => edit(setDeductionReason)(event.target.value)}
            maxLength={RETURN_DEDUCTION_REASON_MAX}
            rows={2}
            required
            disabled={busy}
            className={textareaClass}
          />
          <p className="mt-1 text-xs text-muted">
            Obligatorio con descuento, al menos {RETURN_DEDUCTION_REASON_MIN} caracteres.
          </p>
        </div>
      ) : null}

      {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}
      {confirming && refund != null && method !== "" ? (
        <div
          role="group"
          aria-label="Confirmar cierre"
          data-testid="order-return-close-confirm"
          className="rounded-lg border border-warning-border bg-warning-surface px-3 py-3 text-sm text-foreground"
        >
          <p className="font-semibold">Al cerrar la devolución:</p>
          <ul className="mt-1 list-disc space-y-1 pl-5">
            <li>La venta se cancela («Cancelado» por devolución) y deja de contar en ventas y margen.</li>
            <li>
              El reembolso de <strong>S/ {fromCents(refund)}</strong> ({REFUND_METHOD_LABELS[method]}) queda{" "}
              <strong>PENDIENTE</strong>: se confirma después, con la evidencia del dinero devuelto.
            </li>
            <li>Si tiene nota de venta interna, queda anulada (se conserva).</li>
            <li>La boleta o factura se conserva y queda pendiente su nota de crédito, en el flujo fiscal.</li>
            <li>La comisión de la venta se anula o, si ya se liquidó, se compensa.</li>
          </ul>
          <p className="mt-2 text-xs text-muted">No se devuelve dinero desde aquí ni se llama a la pasarela.</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button type="button" onClick={() => void submit()} disabled={busy} className={dangerButtonClass}>
              {busy ? "Cerrando…" : "Sí, cerrar devolución"}
            </button>
            <button type="button" onClick={() => setConfirming(false)} disabled={busy} className={internalButtonClass}>
              Volver
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setConfirming(true)}
          disabled={!ready || busy}
          className={dangerButtonClass}
        >
          Cerrar devolución
        </button>
      )}
    </div>
  );
}

// ── retirar ──────────────────────────────────────────────────────────────────

function WithdrawForm({ order, onDone }: { order: AdminOrderDetail; onDone: (order: AdminOrderDetail) => void }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ready = normaliseReason(reason).length >= RETURN_REASON_MIN;

  async function submit() {
    if (!ready || busy) return;
    setBusy(true);
    setError(null);
    try {
      onDone(await withdrawOrderReturn(order.id, normaliseReason(reason)));
    } catch (err) {
      setError(errorText(err, "No se pudo retirar la devolución."));
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <div className="mt-4">
        <button type="button" onClick={() => setOpen(true)} className={internalButtonClass}>
          Retirar devolución
        </button>
      </div>
    );
  }
  return (
    <div
      role="group"
      aria-label="Retirar devolución"
      data-testid="order-return-withdraw"
      className="mt-4 rounded-lg border border-bd-border bg-background p-3"
    >
      <label htmlFor={`${id}-reason`} className="block text-sm font-semibold text-foreground">
        Motivo para retirar la devolución
      </label>
      <textarea
        id={`${id}-reason`}
        value={reason}
        onChange={(event) => setReason(event.target.value)}
        maxLength={RETURN_REASON_MAX}
        rows={2}
        required
        disabled={busy}
        className={textareaClass}
      />
      <p className="mt-1 text-xs text-muted">
        Obligatorio, al menos {RETURN_REASON_MIN} caracteres. La mercadería no se recibió: no se mueve stock ni dinero.
      </p>
      {error ? <p role="alert" className="mt-2 text-sm text-danger">{error}</p> : null}
      <div className="mt-3 flex flex-wrap gap-2">
        <button type="button" onClick={() => void submit()} disabled={!ready || busy} className={dangerButtonClass}>
          {busy ? "Retirando…" : "Sí, retirar devolución"}
        </button>
        <button type="button" onClick={() => { setOpen(false); setReason(""); }} disabled={busy} className={internalButtonClass}>
          Descartar
        </button>
      </div>
    </div>
  );
}
