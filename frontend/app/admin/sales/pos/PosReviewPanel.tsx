"use client";

import type { PosPreview } from "../../lib/internal-api";
import type { PickedUnit, PosLine } from "../../lib/unit-entry";

function money(value: string | number) {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n.toLocaleString("es-PE", { style: "currency", currency: "PEN" }) : String(value);
}

/** The last four characters of an identifier: enough to tell two devices apart, not to copy the IMEI. */
function masked(value: string) {
  return value.length > 4 ? `••••${value.slice(-4)}` : value;
}

function unitDetail(unit: PickedUnit) {
  const parts: string[] = [];
  if (unit.condition_label) parts.push(unit.condition_label);
  if (unit.battery_health != null) parts.push(`Batería ${unit.battery_health} %`);
  if (unit.cosmetic_grade) parts.push(`Grado ${unit.cosmetic_grade}`);
  parts.push(unit.imei ? `IMEI ${masked(unit.imei)}` : `Serie ${masked(unit.serial_number)}`);
  return parts.join(" · ");
}

export type ReviewDocument = { value: string; label: string };

/**
 * SALES-UX-02 — the last look before «Cobrar»: what is sold (each device with its own price and a masked
 * identifier), the discounts, the taxes and the total THE SERVER computed, how it is paid and which document is
 * handed over. It shows; it prices nothing. While the server has not answered it says so instead of inventing a figure.
 */
export function PosReviewPanel({ lines, preview, customerName, sellerName, paymentLabel, received, document }: {
  lines: PosLine[];
  preview: PosPreview | null;
  customerName: string | null;
  sellerName: string;
  paymentLabel: string;
  /** Cash typed, only for a cash sale. */
  received: string | null;
  document: ReviewDocument | null;
}) {
  const unitsById = new Map<number, PickedUnit>();
  for (const line of lines) for (const unit of line.units) unitsById.set(unit.id, unit);
  const total = preview ? Number(preview.total) : null;
  const change = received !== null && received !== "" && total !== null ? Number(received) - total : null;

  return (
    <section aria-label="Revisar la venta" className="space-y-3 rounded-xl border border-bd-border bg-background p-3 text-sm">
      {preview ? (
        <ul className="space-y-2">
          {preview.lines.map((row, index) => {
            const unit = row.unit ? unitsById.get(row.unit) : undefined;
            return (
              <li key={`${row.product}-${row.unit ?? index}`} className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="break-words text-foreground">{row.name}</p>
                  {unit ? <p className="text-[11px] text-muted">{unitDetail(unit)}</p> : null}
                  {row.quantity > 1 ? <p className="text-[11px] text-muted">{row.quantity} × {money(row.price)}</p> : null}
                  {row.on_order ? <p className="text-[11px] text-warning">Bajo pedido</p> : null}
                </div>
                <span className="shrink-0 font-mono text-foreground">{money(Number(row.price) * row.quantity)}</span>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="text-muted">Calculando el total…</p>
      )}

      {preview ? (
        <dl className="space-y-1 border-t border-bd-border pt-2">
          <div className="flex justify-between text-muted"><dt>Subtotal</dt><dd className="font-mono">{money(preview.subtotal)}</dd></div>
          {preview.promotions?.length ? preview.promotions.map((p) => (
            <div key={p.id} className="flex justify-between text-success">
              <dt>✓ {p.name}</dt><dd className="font-mono">−{money(p.discount_amount)}</dd>
            </div>
          )) : Number(preview.discount) > 0 ? (
            <div className="flex justify-between text-success">
              <dt>Descuento{preview.discount_source === "coupon" ? " (cupón)" : ""}</dt>
              <dd className="font-mono">−{money(preview.discount)}</dd>
            </div>
          ) : null}
          {preview.tax ? (
            <>
              <div className="flex justify-between text-muted"><dt>{preview.tax.base_label}</dt><dd className="font-mono">{money(preview.tax.taxable_amount)}</dd></div>
              {preview.tax.tax_treatment === "taxed" ? (
                <div className="flex justify-between text-muted"><dt>{preview.tax.tax_label}</dt><dd className="font-mono">{money(preview.tax.tax_amount)}</dd></div>
              ) : null}
            </>
          ) : null}
          <div className="flex items-baseline justify-between pt-1">
            <dt className="text-foreground">Total</dt><dd className="font-display text-xl text-foreground">{money(preview.total)}</dd>
          </div>
        </dl>
      ) : null}

      <dl className="space-y-1 border-t border-bd-border pt-2 text-xs text-muted">
        <div className="flex justify-between gap-3"><dt>Cliente</dt><dd className="text-right text-foreground">{customerName ?? "Venta al público"}</dd></div>
        <div className="flex justify-between gap-3"><dt>Vendedor</dt><dd className="text-right text-foreground">{sellerName}</dd></div>
        <div className="flex justify-between gap-3"><dt>Forma de pago</dt><dd className="text-right text-foreground">{paymentLabel}</dd></div>
        {change !== null && change >= 0 ? (
          <div className="flex justify-between gap-3"><dt>Vuelto</dt><dd className="text-right font-mono text-foreground">{money(change)}</dd></div>
        ) : null}
        <div className="flex justify-between gap-3">
          <dt>Documento</dt>
          <dd className="text-right text-foreground">
            {document ? document.label : "Sin elegir"}
            {document ? (
              <span className={`ml-2 rounded px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide ${
                document.value === "sales_note" ? "bg-warning-surface text-warning" : "bg-info-surface text-info"}`}>
                {document.value === "sales_note" ? "Interno · no fiscal" : "Comprobante electrónico · BETA"}
              </span>
            ) : null}
          </dd>
        </div>
      </dl>
    </section>
  );
}
