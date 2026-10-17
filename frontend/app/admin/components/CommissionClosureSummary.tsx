"use client";

/**
 * Cierre financiero (propietario, 2026-10-16) — el estado de las comisiones
 * del periodo. Una comisión no liquidada de una venta anulada se ANULA; una ya
 * LIQUIDADA no se toca y queda su ajuste compensatorio («saldo a recuperar»).
 * Cada cifra la calcula el servidor; aquí sólo se nombra.
 */
import type { CommissionReport } from "../lib/internal-api";

function money(value: string) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toLocaleString("es-PE", { style: "currency", currency: "PEN" }) : value;
}

const FIGURES = [
  { key: "accrued", label: "Devengada (por pagar)" },
  { key: "settled", label: "Liquidada (pagada)" },
  { key: "voided", label: "Anulada (no se debe)" },
  { key: "recoverable", label: "Saldo a recuperar" },
] as const;

export function CommissionClosureSummary({ report }: { report: CommissionReport }) {
  const totals = report.totals;
  if (!totals) return null;
  const adjustments = report.adjustments ?? [];
  return (
    <section aria-labelledby="commission-closure-title" className="space-y-4">
      <h3 id="commission-closure-title" className="text-sm font-semibold text-foreground">
        Estado de las comisiones
      </h3>
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {FIGURES.map(({ key, label }) => (
          <div key={key} className="rounded-xl border border-bd-border bg-surface px-4 py-3">
            <dt className="text-[11px] uppercase tracking-widest text-muted">{label}</dt>
            <dd data-testid={`commission-${key}`} className="mt-1 font-mono text-sm text-foreground">
              {money(totals[key])}
            </dd>
          </div>
        ))}
      </dl>
      {adjustments.length ? (
        <div className="overflow-x-auto rounded-xl border border-bd-border bg-surface">
          <table aria-label="Ajustes por anulación" className="w-full min-w-[36rem] text-left text-sm">
            <thead className="border-b border-bd-border text-[11px] uppercase tracking-widest text-muted">
              <tr>
                <th className="px-4 py-3 font-semibold">Venta</th>
                <th className="px-4 py-3 font-semibold">Vendedor</th>
                <th className="px-4 py-3 font-semibold">Ajuste</th>
                <th className="px-4 py-3 text-right font-semibold">Importe</th>
              </tr>
            </thead>
            <tbody>
              {adjustments.map((row) => (
                <tr key={row.id} className="border-b border-bd-border/70 last:border-0">
                  <td className="px-4 py-3 text-foreground">#{row.order_id}</td>
                  <td className="px-4 py-3 text-muted">{row.seller_name}</td>
                  <td className="px-4 py-3 text-xs text-muted">{row.label}</td>
                  <td className="px-4 py-3 text-right font-mono text-foreground">{money(row.amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
