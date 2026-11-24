"use client";

/**
 * TRADE-IN-QUOTE-00 — el equipo que el cliente ofrece en parte de pago, dentro de una cotización.
 *
 * Es una valorización REFERENCIAL y no vinculante: no baja el precio ni el IGV de la cotización, no es un
 * descuento, no es pago ni compra y no entra al stock. El saldo estimado (total − valorización) sólo se muestra;
 * al convertir, la venta se cobra completa. Del IMEI se anotan sólo los últimos 4 dígitos; nunca DNI ni fotos.
 */
import { useId, useState } from "react";
import { formatAdminDate } from "../../../lib/admin";
import {
  TRADE_IN_CONDITIONS,
  TRADE_IN_HEADING,
  TRADE_IN_LEGEND,
  emptyTradeIn,
  estimatedBalance,
  formatMoney,
  type QuoteTradeIn,
  type QuoteTradeInDraft,
} from "../../lib/sales-quotes-api";
import { internalButtonClass, internalInputClass } from "../internal-ui";

const LABEL = "mb-1 block text-[11px] font-semibold uppercase tracking-widest text-muted";

/** The editor's optional, collapsible block. `shownTotal` is the editor's reference total (the server's rules). */
export function TradeInEditor({
  value, shownTotal, onChange,
}: {
  value: QuoteTradeInDraft | null;
  shownTotal: number;
  onChange: (value: QuoteTradeInDraft | null) => void;
}) {
  const ids = useId();
  const [open, setOpen] = useState(value !== null);
  const panelId = `${ids}-trade-in`;

  function patch(change: Partial<QuoteTradeInDraft>) {
    onChange({ ...(value ?? emptyTradeIn()), ...change });
  }

  function toggle() {
    if (!open && value === null) onChange(emptyTradeIn());
    setOpen(!open);
  }

  const valuation = value ? Number(value.estimated_valuation) : NaN;
  const hasValuation = value !== null && value.estimated_valuation.trim() !== "" && Number.isFinite(valuation);

  return (
    <section className="space-y-3 rounded-xl border border-bd-border bg-surface p-4" aria-labelledby={`${ids}-heading`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id={`${ids}-heading`} className={LABEL}>{TRADE_IN_HEADING}</h2>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className={internalButtonClass}
            aria-expanded={open}
            aria-controls={panelId}
            onClick={toggle}
          >
            {value === null ? "Agregar equipo en parte de pago" : open ? "Ocultar detalle" : "Ver detalle"}
          </button>
          {value !== null ? (
            <button type="button" className={internalButtonClass} onClick={() => { onChange(null); setOpen(false); }}>
              Quitar parte de pago
            </button>
          ) : null}
        </div>
      </div>
      <p className="text-sm text-muted">
        Opcional. Valorización referencial y no vinculante: no descuenta el precio ni el IGV, no es pago ni compra y
        no entra al stock. Al convertir en venta, se cobra el total completo.
      </p>
      {value !== null && !open && hasValuation ? (
        <p className="text-sm">
          {value.device_description || "Equipo"} · valorización {formatMoney(valuation)}
        </p>
      ) : null}
      <div id={panelId} hidden={!open || value === null} className="grid gap-3 sm:grid-cols-2">
        {value !== null ? (
          <>
            <label className="block sm:col-span-2">
              <span className={LABEL}>Equipo (modelo)</span>
              <input value={value.device_description} onChange={(e) => patch({ device_description: e.target.value })}
                className={internalInputClass} maxLength={200} />
            </label>
            <label className="block">
              <span className={LABEL}>Capacidad</span>
              <input value={value.capacity} onChange={(e) => patch({ capacity: e.target.value })}
                className={internalInputClass} maxLength={40} />
            </label>
            <label className="block">
              <span className={LABEL}>Color</span>
              <input value={value.color} onChange={(e) => patch({ color: e.target.value })}
                className={internalInputClass} maxLength={40} />
            </label>
            <label className="block">
              <span className={LABEL}>Estado declarado</span>
              <select value={value.condition}
                onChange={(e) => patch({ condition: e.target.value as QuoteTradeInDraft["condition"] })}
                className={internalInputClass}>
                <option value="">Elige el estado</option>
                {TRADE_IN_CONDITIONS.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
              </select>
            </label>
            <label className="block">
              <span className={LABEL}>Batería declarada (%)</span>
              <input type="number" min={0} max={100} step={1} inputMode="numeric" value={value.battery_health}
                onChange={(e) => patch({ battery_health: e.target.value })} className={internalInputClass} />
            </label>
            <label className="block">
              <span className={LABEL}>Accesorios</span>
              <input value={value.accessories} onChange={(e) => patch({ accessories: e.target.value })}
                className={internalInputClass} maxLength={200} />
            </label>
            {/* A pasted IMEI keeps only its LAST four digits: the rest never reaches the state or the server. */}
            <div>
              <label className="block">
                <span className={LABEL}>IMEI: últimos 4 dígitos</span>
                <input value={value.imei_last4} inputMode="numeric" pattern="[0-9]{4}" autoComplete="off"
                  onChange={(e) => patch({ imei_last4: e.target.value.replace(/\D/g, "").slice(-4) })}
                  className={internalInputClass} aria-describedby={`${ids}-imei-note`} />
              </label>
              <span id={`${ids}-imei-note`} className="mt-1 block text-xs text-muted">Nunca el IMEI completo ni el DNI.</span>
            </div>
            <label className="block">
              <span className={LABEL}>Precio que pide el cliente (interno)</span>
              <input type="number" min={0} step="0.01" inputMode="decimal" value={value.customer_asking_price}
                onChange={(e) => patch({ customer_asking_price: e.target.value })} className={internalInputClass} />
            </label>
            <label className="block">
              <span className={LABEL}>Valorización estimada</span>
              <input type="number" min={0} step="0.01" inputMode="decimal" value={value.estimated_valuation}
                onChange={(e) => patch({ estimated_valuation: e.target.value })} className={internalInputClass} />
            </label>
            <label className="block sm:col-span-2">
              <span className={LABEL}>Notas de la valorización (internas, no se imprimen)</span>
              <textarea rows={2} value={value.valuation_notes} onChange={(e) => patch({ valuation_notes: e.target.value })}
                className={internalInputClass} maxLength={1000} />
            </label>
            {hasValuation ? (
              <dl className="grid grid-cols-[1fr_auto] gap-x-6 gap-y-1 rounded-lg border border-bd-border px-3 py-2 text-sm tabular-nums sm:col-span-2"
                aria-label="Saldo estimado (referencial)">
                <dt>Total referencial</dt><dd className="text-right">{formatMoney(shownTotal)}</dd>
                <dt>Valorización estimada</dt><dd className="text-right">- {formatMoney(valuation)}</dd>
                <dt className="font-semibold">Saldo estimado a pagar</dt>
                <dd className="text-right font-semibold">{formatMoney(estimatedBalance(shownTotal, valuation))}</dd>
              </dl>
            ) : null}
          </>
        ) : null}
      </div>
    </section>
  );
}

/** The block of a saved quote: what the customer offered, the valuation and the balance it would leave. */
export function TradeInSummary({ block, total, currency }: { block: QuoteTradeIn; total: string; currency: string }) {
  const rows: [string, string][] = [
    ["Equipo", block.device_description],
    ["Capacidad", block.capacity],
    ["Color", block.color],
    ["Estado declarado", block.condition_label],
    ["Batería", block.battery_health === null ? "" : `${block.battery_health} %`],
    ["Accesorios", block.accessories],
    ["IMEI", block.imei_last4 ? `****${block.imei_last4}` : ""],
  ];
  return (
    <section className="space-y-3 rounded-xl border border-bd-border bg-surface p-4 text-sm" aria-labelledby="quote-trade-in">
      <h2 id="quote-trade-in" className="text-[11px] font-semibold uppercase tracking-widest text-muted">{TRADE_IN_HEADING}</h2>
      <dl className="grid grid-cols-[minmax(7rem,auto)_1fr] gap-x-4 gap-y-1">
        {rows.filter(([, value]) => value).map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-muted">{label}</dt><dd className="min-w-0 break-words">{value}</dd>
          </div>
        ))}
      </dl>
      <dl className="grid grid-cols-[1fr_auto] gap-x-6 gap-y-1 border-t border-bd-border pt-2 tabular-nums">
        <dt>Total cotizado</dt><dd className="text-right">{formatMoney(total, currency)}</dd>
        <dt>Valorización estimada</dt><dd className="text-right">- {formatMoney(block.estimated_valuation, currency)}</dd>
        <dt className="font-bold">Saldo estimado a pagar</dt>
        <dd className="text-right font-bold">{formatMoney(block.estimated_balance, currency)}</dd>
      </dl>
      <p className="rounded-lg bg-foreground/[0.04] px-3 py-2 text-xs font-semibold">{TRADE_IN_LEGEND}</p>
      {block.customer_asking_price !== null || block.valuation_notes ? (
        <div className="text-xs text-muted">
          <p className="font-semibold uppercase tracking-widest">Interno (no se imprime)</p>
          {block.customer_asking_price !== null ? <p>El cliente pide {formatMoney(block.customer_asking_price, currency)}</p> : null}
          {block.valuation_notes ? <p className="whitespace-pre-line">{block.valuation_notes}</p> : null}
        </div>
      ) : null}
      {block.valued_by ? (
        <p className="text-xs text-muted">
          Valorizado por {block.valued_by}{block.valued_at ? ` · ${formatAdminDate(block.valued_at)}` : ""}
        </p>
      ) : null}
    </section>
  );
}
