"use client";

/**
 * The period's totals, exactly as the server added them. Pending refunds are
 * said, not subtracted; TEST payments are shown apart, never inside income.
 */
import { money, type Totals } from "../../lib/accounting-api";

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="min-w-0 rounded-xl border border-bd-border bg-surface p-4">
      <dt className="text-[11px] font-semibold uppercase tracking-wide text-muted">{label}</dt>
      <dd className="mt-2 break-words font-display text-xl font-bold tabular-nums text-foreground sm:text-2xl">{value}</dd>
      {hint ? <dd className="mt-1 text-xs text-muted">{hint}</dd> : null}
    </div>
  );
}

export function TotalsPanel({ totals, pending }: { totals: Totals; pending: Record<string, number> }) {
  const currency = totals.currencies.length === 1 ? totals.currencies[0] : "PEN";
  const issues = Object.values(pending).reduce((a, b) => a + b, 0);
  return (
    <section aria-labelledby="accounting-totals-title">
      <h2 id="accounting-totals-title" className="sr-only">Totales del período</h2>
      <dl className="grid grid-cols-1 gap-3 min-[360px]:grid-cols-2 lg:grid-cols-4">
        <Stat label="Ingresos cobrados" value={money(totals.income, currency)}
              hint={`${totals.operations} operaciones`} />
        <Stat label="Ventas brutas" value={money(totals.gross, currency)}
              hint={`Base ${money(totals.sales_taxable, currency)} · impuesto ${money(totals.sales_tax, currency)}`} />
        <Stat label="Reembolsos confirmados" value={money(totals.refunds_confirmed, currency)}
              hint={`Pendientes ${money(totals.refunds_pending, currency)} (no se restan)`} />
        <Stat label="Cobros de servicio" value={money(totals.service_collections, currency)}
              hint={`Neto de ventas ${money(totals.net, currency)}`} />
      </dl>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
        {totals.excluded_test.operations > 0 ? (
          <span data-testid="accounting-excluded-test">
            Fuera del ingreso por pagos de prueba: {totals.excluded_test.operations} ·{" "}
            {money(totals.excluded_test.gross, currency)}
          </span>
        ) : null}
        <span>Avisos en la vista C: {issues}</span>
        {totals.currencies.length > 1 ? (
          <span className="text-warning">Hay más de una moneda en el período: {totals.currencies.join(", ")}.</span>
        ) : null}
      </div>
    </section>
  );
}
