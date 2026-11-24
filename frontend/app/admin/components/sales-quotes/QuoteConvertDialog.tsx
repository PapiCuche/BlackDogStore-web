"use client";

/**
 * SALES-QUOTE-01 — «Convertir en venta». Es una venta de caja: se cobra aquí, con las reglas de la caja
 * (stock, equipos identificados, comprobante, idempotencia). La cotización queda como estaba y enlazada a la venta.
 *
 * SI EL PRECIO CAMBIÓ desde la cotización, el servidor no cobra (409) y aquí se ven los dos totales: se cobra el
 * precio de hoy, o —sólo quien tiene permiso de descuentos— se mantiene el cotizado como un descuento auditado.
 *
 * La clave de idempotencia se crea al abrir y se reutiliza en cada reintento: un doble clic o una respuesta
 * perdida nunca cobran dos veces.
 *
 * TRADE-IN-QUOTE-00: si la cotización trae un equipo ofrecido en parte de pago, aquí sólo se AVISA. La parte de pago
 * aún no se aplica (decisiones D1–D12 pendientes): la venta se cobra completa y la cotización conserva el bloque.
 */
import { useId, useMemo, useRef, useState } from "react";
import { posUnitLookup } from "../../lib/internal-api";
import {
  convertQuote,
  formatMoney,
  linesNeedingDevices,
  newQuoteKey,
  priceChangedOf,
  TRADE_IN_NOT_APPLIED,
  type ConvertResult,
  type PriceChanged,
  type QuoteContext,
  type QuoteDetail,
} from "../../lib/sales-quotes-api";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "../internal-ui";
import { useModalBehaviour } from "../useModalBehaviour";

type Device = { id: number; label: string; method: "scanner" | "manual" };

export function QuoteConvertDialog({
  quote, context, onClose, onConverted,
}: {
  quote: QuoteDetail;
  context: QuoteContext;
  onClose: () => void;
  onConverted: (result: ConvertResult) => void;
}) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);
  const key = useRef(newQuoteKey("cot-venta"));
  const [payment, setPayment] = useState(context.payment_methods[0]?.value ?? "cash");
  const [received, setReceived] = useState("");
  const [reference, setReference] = useState("");
  const [receipt, setReceipt] = useState("");
  const [terms, setTerms] = useState(false);
  const [devices, setDevices] = useState<Record<number, Device[]>>({});
  const [codes, setCodes] = useState<Record<number, string>>({});
  const [methods, setMethods] = useState<Record<number, "scanner" | "manual">>({});
  const [priceChange, setPriceChange] = useState<PriceChanged | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useModalBehaviour(panel, () => { if (!busy) onClose(); });

  const needing = useMemo(() => linesNeedingDevices(quote), [quote]);
  const receipts = context.receipt_options.filter((o) => o.enabled && o.branches.includes(quote.branch));
  const missing = needing.some((item) => (devices[item.id]?.length ?? 0) !== item.quantity);
  const cashShort = payment === "cash" && (!received || Number(received) < Number(priceChange?.current_total ?? quote.total));
  const blocked = !terms || missing || (payment === "cash" && !received) || busy;

  async function identify(itemId: number, product: number) {
    const code = (codes[itemId] ?? "").trim();
    if (!code) return;
    const method = methods[itemId] ?? "manual";
    setError(null);
    try {
      const found = await posUnitLookup(null, quote.branch, code, method);
      if (found.unit.product_id !== product) {
        setError("Ese equipo es de otro producto.");
        return;
      }
      setDevices((current) => {
        const list = current[itemId] ?? [];
        if (list.some((d) => d.id === found.unit.unit_id)) return current;
        const label = found.unit.imei ? `IMEI ${found.unit.imei}` : `Serie ${found.unit.serial_number}`;
        return { ...current, [itemId]: [...list, { id: found.unit.unit_id, label, method }] };
      });
      setCodes((current) => ({ ...current, [itemId]: "" }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo comprobar ese equipo.");
    }
  }

  async function submit(decision: "" | "current" | "quoted" = "") {
    if (blocked) return;
    setBusy(true);
    setError(null);
    try {
      const units: Record<number, { id: number; method: "scanner" | "manual" }[]> = {};
      for (const [itemId, list] of Object.entries(devices)) {
        units[Number(itemId)] = list.map((d) => ({ id: d.id, method: d.method }));
      }
      const result = await convertQuote(quote.id, {
        payment_method: payment,
        amount_received: received,
        payment_reference: reference,
        receipt_type: receipt || null,
        terms_confirmed: terms,
        units,
        price_decision: decision,
        ...(decision === "current" && priceChange ? { expected_total: priceChange.current_total } : {}),
      }, key.current);
      onConverted(result);
    } catch (err) {
      const changed = priceChangedOf(err);
      if (changed) setPriceChange(changed);
      else setError(err instanceof Error ? err.message : "No se pudo convertir en venta.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-0 sm:items-center sm:p-4">
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="max-h-[92vh] w-full max-w-lg overflow-y-auto rounded-t-2xl border border-bd-border bg-background p-5 shadow-xl sm:rounded-2xl"
      >
        <h2 id={titleId} className="text-lg font-bold">Convertir {quote.label} en venta</h2>
        <p className="mt-1 text-sm text-muted">
          Se cobra ahora, en {quote.branch_name}, y se descuenta el stock como en la caja. Total cotizado:{" "}
          <span className="font-semibold text-foreground tabular-nums">{formatMoney(quote.total, quote.currency)}</span>
        </p>

        {quote.trade_in ? (
          <div role="note" className="mt-4 space-y-1 rounded-xl border border-warning-border bg-warning-surface p-4 text-sm">
            <p className="font-semibold">{TRADE_IN_NOT_APPLIED}.</p>
            <p>
              El cliente ofreció {quote.trade_in.device_description} con una valorización referencial de{" "}
              <span className="tabular-nums">{formatMoney(quote.trade_in.estimated_valuation, quote.currency)}</span>. No se descuenta:
              se cobra el total de la venta y el equipo no se recibe ni entra al stock.
            </p>
          </div>
        ) : null}

        {priceChange ? (
          <div role="alert" className="mt-4 space-y-3 rounded-xl border border-warning-border bg-warning-surface p-4 text-sm">
            <p className="font-semibold">El precio cambió desde la cotización.</p>
            <dl className="grid grid-cols-2 gap-1 tabular-nums">
              <dt>Cotizado</dt><dd className="text-right">{formatMoney(priceChange.quoted_total)}</dd>
              <dt>Hoy</dt><dd className="text-right font-semibold">{formatMoney(priceChange.current_total)}</dd>
            </dl>
            <div className="flex flex-wrap gap-2">
              <button type="button" className={internalPrimaryButtonClass} disabled={blocked || cashShort} onClick={() => void submit("current")}>
                Cobrar precio de hoy
              </button>
              {priceChange.can_keep_quoted ? (
                <button type="button" className={internalButtonClass} disabled={blocked} onClick={() => void submit("quoted")}>
                  Mantener precio cotizado
                </button>
              ) : null}
            </div>
            {!priceChange.can_keep_quoted ? (
              <p className="text-xs text-muted">Mantener el precio cotizado es un descuento: lo decide quien tiene permiso de descuentos.</p>
            ) : null}
          </div>
        ) : null}

        <div className="mt-4 space-y-4">
          {needing.map((item) => (
            <fieldset key={item.id} className="rounded-xl border border-bd-border p-3">
              <legend className="px-1 text-sm font-semibold">{item.description}</legend>
              <p className="text-xs text-muted">Identifica {item.quantity} equipo(s) por su IMEI o serie.</p>
              <ul className="mt-2 space-y-1 text-sm">
                {(devices[item.id] ?? []).map((d) => (
                  <li key={d.id} className="flex items-center justify-between gap-2">
                    <span>{d.label}</span>
                    <button
                      type="button"
                      className="text-xs underline"
                      onClick={() => setDevices((c) => ({ ...c, [item.id]: (c[item.id] ?? []).filter((x) => x.id !== d.id) }))}
                    >
                      Quitar
                    </button>
                  </li>
                ))}
              </ul>
              {(devices[item.id]?.length ?? 0) < item.quantity ? (
                <div className="mt-2 flex flex-wrap gap-2">
                  <input
                    value={codes[item.id] ?? ""}
                    onChange={(e) => setCodes((c) => ({ ...c, [item.id]: e.target.value }))}
                    onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void identify(item.id, item.product); } }}
                    placeholder="IMEI o serie"
                    aria-label={`IMEI o serie para ${item.description}`}
                    className={`${internalInputClass} min-w-0 flex-1`}
                  />
                  <select
                    value={methods[item.id] ?? "manual"}
                    onChange={(e) => setMethods((c) => ({ ...c, [item.id]: e.target.value as "scanner" | "manual" }))}
                    aria-label="Cómo se leyó"
                    className={`${internalInputClass} w-auto`}
                  >
                    <option value="manual">A mano</option>
                    <option value="scanner">Lector</option>
                  </select>
                  <button type="button" className={internalButtonClass} onClick={() => void identify(item.id, item.product)}>Agregar</button>
                </div>
              ) : null}
            </fieldset>
          ))}

          <label className="block text-sm">
            <span className="text-xs font-semibold uppercase tracking-widest text-muted">Medio de pago</span>
            <select value={payment} onChange={(e) => setPayment(e.target.value)} className={internalInputClass}>
              {context.payment_methods.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
            </select>
          </label>
          {payment === "cash" ? (
            <label className="block text-sm">
              <span className="text-xs font-semibold uppercase tracking-widest text-muted">Efectivo recibido</span>
              <input type="number" min={0} step="0.01" inputMode="decimal" value={received} onChange={(e) => setReceived(e.target.value)} className={internalInputClass} />
            </label>
          ) : (
            <label className="block text-sm">
              <span className="text-xs font-semibold uppercase tracking-widest text-muted">Referencia del pago (opcional)</span>
              <input value={reference} onChange={(e) => setReference(e.target.value)} className={internalInputClass} maxLength={100} />
            </label>
          )}
          {receipts.length > 0 ? (
            <label className="block text-sm">
              <span className="text-xs font-semibold uppercase tracking-widest text-muted">Documento de la venta</span>
              <select value={receipt} onChange={(e) => setReceipt(e.target.value)} className={internalInputClass}>
                <option value="">Sin documento ahora</option>
                {receipts.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </label>
          ) : null}
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" checked={terms} onChange={(e) => setTerms(e.target.checked)} className="mt-1" />
            <span>Informé al cliente las condiciones de venta y la política de garantía.</span>
          </label>
        </div>

        {error ? <p role="alert" className="mt-4 rounded-lg border border-danger-border bg-danger-surface px-3 py-2 text-sm text-danger">{error}</p> : null}

        <div className="mt-5 flex flex-wrap justify-end gap-2">
          <button type="button" className={internalButtonClass} onClick={onClose} disabled={busy}>Cancelar</button>
          {!priceChange ? (
            <button type="button" className={internalPrimaryButtonClass} disabled={blocked || cashShort} onClick={() => void submit("")}>
              {busy ? "Cobrando…" : `Cobrar ${formatMoney(quote.total, quote.currency)}`}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
