"use client";

/**
 * DEVICE-COMMERCE-01 · DC1 — «Margen comercial» del resumen de ventas.
 *
 * Sólo para `costs.view`, y sólo se pide al servidor si se tiene: un 403 que
 * se sabe de antemano no es una petición que valga la pena hacer. Si aun así
 * el servidor lo niega, la sección desaparece y el resto de la página sigue.
 *
 * MARGEN COMERCIAL, NUNCA «UTILIDAD». Es lo vendido sin IGV menos lo que costó
 * esa mercadería; no descuenta gastos, comisiones, pasarela ni impuestos. Y
 * se calcula sólo sobre las líneas con costo conocido: las demás se cuentan
 * aparte como «costo pendiente», en vez de contarlas como si no costaran nada.
 */

import { useEffect, useState } from "react";

import { fetchSalesMargin, type SalesMargin, type SalesMarginRow } from "../lib/internal-api";
import { DashboardSection } from "./dashboard-ui";

function money(value: string | number) {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n)
    ? n.toLocaleString("es-PE", { style: "currency", currency: "PEN" })
    : String(value);
}

function percent(value: string | null) {
  return value === null ? "—" : `${value} %`;
}

function Figure({ label, value, hint, testId }: { label: string; value: string; hint?: string; testId?: string }) {
  return (
    <div className="rounded-xl border border-bd-border bg-surface p-4">
      <p className="text-[11px] uppercase tracking-widest text-muted">{label}</p>
      <p className="mt-1 font-display text-xl text-foreground" data-testid={testId}>{value}</p>
      {hint ? <p className="mt-0.5 text-[11px] text-muted">{hint}</p> : null}
    </div>
  );
}

function Breakdown({ title, rows }: { title: string; rows: SalesMarginRow[] }) {
  if (rows.length === 0) return null;
  return (
    <div className="overflow-x-auto rounded-xl border border-bd-border bg-surface">
      <table className="w-full min-w-[36rem] text-left text-sm">
        <caption className="px-4 pt-3 text-left text-[11px] uppercase tracking-widest text-muted">{title}</caption>
        <thead className="border-b border-bd-border text-[11px] uppercase tracking-widest text-muted">
          <tr>
            <th className="px-4 py-3 font-semibold">Nombre</th>
            <th className="px-4 py-3 text-right font-semibold">Unidades</th>
            <th className="px-4 py-3 text-right font-semibold">Ventas netas</th>
            <th className="px-4 py-3 text-right font-semibold">Costo</th>
            <th className="px-4 py-3 text-right font-semibold">Margen</th>
            <th className="px-4 py-3 text-right font-semibold">%</th>
            <th className="px-4 py-3 text-right font-semibold">Costo pendiente</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.key)} className="border-b border-bd-border/70 last:border-0">
              <td className="px-4 py-3 text-foreground">{row.name}</td>
              <td className="px-4 py-3 text-right font-mono text-foreground">{row.units}</td>
              <td className="px-4 py-3 text-right font-mono text-foreground">
                {money(row.net_sales)}
                {row.net_estimated ? (
                  <span className="ml-1 block text-xs font-sans text-muted">incluye neto estimado</span>
                ) : null}
              </td>
              <td className="px-4 py-3 text-right font-mono text-foreground">{money(row.cost)}</td>
              <td className="px-4 py-3 text-right font-mono text-foreground">{money(row.margin)}</td>
              <td className="px-4 py-3 text-right font-mono text-muted">{percent(row.margin_percent)}</td>
              <td className="px-4 py-3 text-right font-mono text-muted">{row.pending_lines || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SalesMarginSection({
  companyId, branchId, canSeeCosts,
}: { companyId: number | null; branchId: number | null; canSeeCosts: boolean }) {
  const [report, setReport] = useState<SalesMargin | null>(null);

  useEffect(() => {
    if (!canSeeCosts) return;
    let cancelled = false;
    fetchSalesMargin(companyId, branchId)
      .then((data) => { if (!cancelled) setReport(data); })
      .catch(() => { if (!cancelled) setReport(null); });
    return () => { cancelled = true; };
  }, [companyId, branchId, canSeeCosts]);

  if (!canSeeCosts || report === null) return null;

  const pending = report.pending_cost;
  return (
    <section role="region" aria-label="Margen comercial">
      <DashboardSection
        title="Margen comercial"
        description={`Ventas pagadas de los últimos ${report.window_days} días, sin IGV, menos el costo de lo vendido. Es un margen comercial estimado: no descuenta gastos, comisiones ni impuestos.`}
      >
        {pending.lines > 0 ? (
          <p role="status" className="rounded-xl border border-warning/40 bg-warning/10 px-4 py-3 text-sm text-foreground">
            {pending.lines} {pending.lines === 1 ? "línea" : "líneas"} de {pending.orders}{" "}
            {pending.orders === 1 ? "venta" : "ventas"} con costo pendiente ({money(pending.amount)} sin IGV):
            no entran en el margen hasta que se conozca su costo.
          </p>
        ) : null}
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Figure label="Ventas netas (sin IGV)" value={money(report.sales.net)} hint={`${money(report.sales.gross)} con IGV · ${report.sales.orders} ventas`} />
          <Figure label="Cobros confirmados" value={money(report.collected.amount)} hint={`${report.collected.orders} pedidos pagados`} />
          <Figure label="Costo de lo vendido" value={money(report.cost_of_goods_sold)} hint={`sobre ${money(report.net_sales_with_cost)} vendidos con costo conocido`} />
          <Figure label="Margen comercial estimado" value={money(report.margin)} />
          <Figure label="Margen %" value={percent(report.margin_percent)} testId="margin-percent" />
          <Figure label="Reembolsos" value={money(report.refunds.amount)} hint={`${report.refunds.orders} pedidos, fuera de las ventas`} />
        </div>
        <Breakdown title="Por producto" rows={report.by_product} />
        <Breakdown title="Por sucursal" rows={report.by_branch} />
        <Breakdown title="Por canal" rows={report.by_channel} />
      </DashboardSection>
    </section>
  );
}
