"use client";

/**
 * Ventas — resumen comercial. Commercial Phase C1.
 *
 * Two things this screen is careful to keep apart.
 *
 *   MONEY AND UNITS COME FROM DIFFERENT PLACES. Turnover is read from paid
 *   orders at the price charged that day; units are read from stock that
 *   physically left a shelf. They answer different questions and a single
 *   blended figure would answer neither.
 *
 *   A RANKING IS NOT A FORECAST. "Best sellers" is what already happened;
 *   coverage and reorder points are an estimate of what happens next. They sit
 *   in separate sections and are labelled as what they are — an estimate is
 *   never printed as though it were a fact.
 *
 * There is no margin, no profit and no ROI here, because the platform does not
 * record what anything cost. A number invented for a dashboard is worse than an
 * absent one.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { AdminShell } from "../components/AdminShell";
import {
  InternalControlGuard,
  type InternalContext,
} from "../components/InternalControlGuard";
import { DashboardSection } from "../components/dashboard-ui";
import {
  fetchReplenishment,
  fetchSalesDashboard,
  type ReplenishmentReport,
  type ReplenishmentRow,
  type SalesDashboard,
} from "../lib/internal-api";

function money(value: string | number) {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n)
    ? n.toLocaleString("es-PE", { style: "currency", currency: "PEN" })
    : String(value);
}

const RISK_LABEL: Record<string, { text: string; className: string }> = {
  out_of_stock: { text: "Sin stock", className: "text-red-400" },
  critical: { text: "Crítico", className: "text-red-400" },
  reorder: { text: "Reponer", className: "text-amber-300" },
  low: { text: "Bajo", className: "text-amber-400/80" },
  insufficient_data: { text: "Sin historial", className: "text-muted" },
  ok: { text: "OK", className: "text-emerald-400/70" },
};

const TREND_MARK: Record<string, string> = {
  up: "▲",
  down: "▼",
  stable: "=",
  unknown: "",
};

function Kpi({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-xl border border-bd-border bg-surface p-4">
      <p className="text-[11px] uppercase tracking-widest text-muted">{label}</p>
      <p className="mt-1 font-display text-xl text-foreground">{value}</p>
      {hint ? <p className="mt-0.5 text-[11px] text-muted">{hint}</p> : null}
    </div>
  );
}

function Sparkline({ points }: { points: { date: string; revenue: string }[] }) {
  const values = points.map((p) => Number(p.revenue) || 0);
  const max = Math.max(...values, 1);
  return (
    <div
      className="flex h-24 items-end gap-[2px]"
      role="img"
      aria-label={`Ingresos diarios de los últimos ${points.length} días`}
    >
      {points.map((p) => (
        <div
          key={p.date}
          title={`${p.date}: ${money(p.revenue)}`}
          style={{ height: `${Math.max((Number(p.revenue) / max) * 100, 1.5)}%` }}
          className="flex-1 rounded-t bg-surface-2"
        />
      ))}
    </div>
  );
}

function SalesContent({ ctx }: { ctx: InternalContext }) {
  const companyId = ctx.selectedCompanyId;

  const [data, setData] = useState<SalesDashboard | null>(null);
  const [plan, setPlan] = useState<ReplenishmentReport | null>(null);
  const [planDenied, setPlanDenied] = useState(false);
  const [branch, setBranch] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const dashboard = await fetchSalesDashboard(companyId, branch);
    setData(dashboard);
    try {
      setPlan(await fetchReplenishment(companyId, branch));
      setPlanDenied(false);
    } catch {
      // Replenishment needs `inventory.reports` on top. Not having it hides one
      // section; it must not cost the other five.
      setPlan(null);
      setPlanDenied(true);
    }
  }, [companyId, branch]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      setLoading(true);
      try {
        await load();
        if (!cancelled) setError(null);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "No se pudo cargar la analítica.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [load]);

  if (loading) {
    return (
      <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
        <p className="py-8 text-sm text-muted">Cargando analítica…</p>
      </AdminShell>
    );
  }
  if (error || !data) {
    return (
      <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
        <div className="rounded-xl border border-red-500/20 bg-red-500/5 px-5 py-4 text-sm text-red-400">
          {error ?? "Sin datos."}
        </div>
      </AdminShell>
    );
  }

  const pos = data.channels.by_channel.pos ?? { orders: 0, revenue: "0.00", units: 0 };
  const online = data.channels.by_channel.online ?? { orders: 0, revenue: "0.00", units: 0 };
  const rows: ReplenishmentRow[] = plan?.results ?? [];
  const acting = rows.filter((r) => r.risk !== "ok");

  return (
    <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
      <div className="space-y-8">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-3">
            <label htmlFor="admin-sales-page-sucursal" className="text-[11px] uppercase tracking-widest text-muted">
              Sucursal
            </label>
            <select id="admin-sales-page-sucursal"
              value={branch ?? ""}
              onChange={(e) => setBranch(e.target.value ? Number(e.target.value) : null)}
              className="rounded-lg border border-bd-border bg-background/40 px-3 py-1.5 text-sm text-foreground outline-none"
            >
              <option value="">Todas las sucursales</option>
              {data.branches.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </select>
          </div>
          <Link
            href="/admin/sales/pos"
            className="rounded-lg border border-bd-border px-3 py-1.5 text-sm text-foreground transition hover:border-bd-border hover:text-foreground"
          >
            Punto de venta
          </Link>
        </div>

        <DashboardSection
          title="Resumen"
          description="Sólo pedidos pagados. Un carrito abandonado no es una venta."
        >
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Kpi label="Hoy" value={money(data.kpis.today.revenue)} hint={`${data.kpis.today.orders} pedidos`} />
            <Kpi label="7 días" value={money(data.kpis.last_7d.revenue)} hint={`${data.kpis.last_7d.units} unidades`} />
            <Kpi label="30 días" value={money(data.kpis.last_30d.revenue)} hint={`${data.kpis.last_30d.orders} pedidos`} />
            <Kpi
              label="Ticket promedio (30 d)"
              value={money(data.kpis.last_30d.average_ticket)}
            />
          </div>
        </DashboardSection>

        <DashboardSection title="Tendencia" description="Ingresos diarios, últimos 30 días.">
          {data.trend.some((p) => Number(p.revenue) > 0) ? (
            <Sparkline points={data.trend} />
          ) : (
            <p className="rounded-xl border border-bd-border bg-surface px-5 py-8 text-center text-sm text-muted">
              No hay ventas en este periodo.
            </p>
          )}
        </DashboardSection>

        <DashboardSection
          title="Canales"
          description={`Mostrador y tienda online, últimos ${data.channels.window_days} días.`}
        >
          <div className="grid gap-4 sm:grid-cols-2">
            {([["Punto de venta", pos], ["Tienda online", online]] as const).map(
              ([label, c]) => (
                <div
                  key={label}
                  className="rounded-xl border border-bd-border bg-surface p-4"
                >
                  <p className="text-[11px] uppercase tracking-widest text-muted">
                    {label}
                  </p>
                  <p className="mt-1 font-display text-xl text-foreground">{money(c.revenue)}</p>
                  <p className="mt-0.5 text-xs text-muted">
                    {c.orders} pedidos · {c.units} unidades
                  </p>
                </div>
              ),
            )}
          </div>
        </DashboardSection>

        <DashboardSection
          title="Más vendidos"
          description={`Unidades que salieron de estante, últimos ${data.top_products.window_days} días.`}
        >
          {data.top_products.results.length === 0 ? (
            <p className="rounded-xl border border-bd-border bg-surface px-5 py-8 text-center text-sm text-muted">
              Todavía no hay ventas registradas en este periodo.
            </p>
          ) : (
            <div className="overflow-x-auto rounded-xl border border-bd-border">
              <table className="w-full min-w-[40rem] text-left text-sm">
                <thead className="border-b border-bd-border text-[11px] uppercase tracking-widest text-muted">
                  <tr>
                    <th className="px-4 py-3 font-semibold">Producto</th>
                    <th className="px-4 py-3 text-right font-semibold">Unidades</th>
                    <th className="px-4 py-3 text-right font-semibold">Ingresos</th>
                    <th className="px-4 py-3 text-right font-semibold">Stock</th>
                    <th className="px-4 py-3 text-right font-semibold">Cobertura</th>
                  </tr>
                </thead>
                <tbody>
                  {data.top_products.results.map((p) => (
                    <tr key={p.product_id} className="border-b border-bd-border last:border-0">
                      <td className="px-4 py-3 text-foreground">{p.product_name}</td>
                      <td className="px-4 py-3 text-right font-mono text-foreground/85">
                        {p.units_sold}
                      </td>
                      <td className="px-4 py-3 text-right font-mono text-muted">
                        {money(p.revenue)}
                      </td>
                      <td className="px-4 py-3 text-right font-mono text-muted">
                        {p.current_stock}
                      </td>
                      <td className="px-4 py-3 text-right text-xs text-muted">
                        {p.days_of_cover === null
                          ? "sin consumo reciente"
                          : `${p.days_of_cover} días`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </DashboardSection>

        {data.stock_alerts ? (
          <DashboardSection title="Inventario en riesgo">
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <Kpi label="Sin stock" value={String(data.stock_alerts.out_of_stock)} />
              <Kpi label="Stock bajo" value={String(data.stock_alerts.low)} />
              <Kpi
                label="Reponer ahora"
                value={String(rows.filter((r) => r.risk === "reorder").length)}
              />
              <Kpi
                label="Riesgo de quiebre"
                value={String(rows.filter((r) => r.risk === "critical").length)}
              />
            </div>
          </DashboardSection>
        ) : null}

        <DashboardSection
          title="Reposición sugerida"
          description={
            plan
              ? plan.method.note
              : "Requiere permiso de reportes de inventario."
          }
        >
          {planDenied ? (
            <p className="rounded-xl border border-bd-border bg-surface px-5 py-6 text-sm text-muted">
              No tienes permiso para ver el detalle de inventario. El resto del resumen
              comercial sigue disponible.
            </p>
          ) : acting.length === 0 ? (
            <p className="rounded-xl border border-bd-border bg-surface px-5 py-8 text-center text-sm text-muted">
              Sin alertas de reposición.
            </p>
          ) : (
            <>
              <div className="overflow-x-auto rounded-xl border border-bd-border">
                <table className="w-full min-w-[62rem] text-left text-sm">
                  <thead className="border-b border-bd-border text-[11px] uppercase tracking-widest text-muted">
                    <tr>
                      <th className="px-3 py-3 font-semibold">Producto</th>
                      <th className="px-3 py-3 font-semibold">Sucursal</th>
                      <th className="px-3 py-3 text-right font-semibold">Stock</th>
                      <th className="px-3 py-3 text-right font-semibold">Venta/día</th>
                      <th className="px-3 py-3 text-right font-semibold">Cobertura</th>
                      <th className="px-3 py-3 text-right font-semibold">Entrega</th>
                      <th className="px-3 py-3 text-right font-semibold">P. reposición</th>
                      <th className="px-3 py-3 text-right font-semibold">Sugerido</th>
                      <th className="px-3 py-3 font-semibold">Riesgo</th>
                    </tr>
                  </thead>
                  <tbody>
                    {acting.map((r) => {
                      const risk = RISK_LABEL[r.risk] ?? RISK_LABEL.ok;
                      return (
                        <tr
                          key={`${r.branch_id}-${r.product_id}`}
                          className="border-b border-bd-border last:border-0"
                        >
                          <td className="px-3 py-3 text-foreground">
                            {r.product_name}
                            {r.transfer_options?.length ? (
                              <span className="block text-[11px] text-muted">
                                {r.transfer_options[0].branch_name} podría transferir{" "}
                                {r.transfer_options[0].can_transfer}
                              </span>
                            ) : null}
                          </td>
                          <td className="px-3 py-3 text-muted">{r.branch_name}</td>
                          <td className="px-3 py-3 text-right font-mono text-foreground/85">
                            {r.quantity}
                          </td>
                          <td className="px-3 py-3 text-right font-mono text-muted">
                            {r.forecast.sufficient ? (
                              <>
                                {r.forecast.daily.toFixed(2)}{" "}
                                <span className="text-muted">
                                  {TREND_MARK[r.forecast.trend]}
                                </span>
                              </>
                            ) : (
                              <span className="text-muted">—</span>
                            )}
                          </td>
                          <td className="px-3 py-3 text-right text-xs text-muted">
                            {r.days_of_cover === null ? "—" : `${r.days_of_cover} d`}
                          </td>
                          <td className="px-3 py-3 text-right text-xs text-muted">
                            {r.lead_time_days > 0 ? `${r.lead_time_days} d` : "sin configurar"}
                          </td>
                          <td className="px-3 py-3 text-right font-mono text-muted">
                            {r.reorder_point ?? (
                              <span className="text-[11px] text-muted">
                                {r.reorder_state === "configuration_required"
                                  ? "falta entrega"
                                  : "sin historial"}
                              </span>
                            )}
                          </td>
                          <td className="px-3 py-3 text-right font-mono text-foreground">
                            {r.suggested_quantity || "—"}
                          </td>
                          <td className={`px-3 py-3 text-xs ${risk.className}`}>{risk.text}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <p className="text-[11px] text-muted">
                {plan?.method.formula} · demanda desde {plan?.method.demand_source}. La
                sugerencia no compra nada: la decisión es tuya.
              </p>
            </>
          )}
        </DashboardSection>
      </div>
    </AdminShell>
  );
}

export default function SalesPage() {
  return <InternalControlGuard>{(ctx) => <SalesContent ctx={ctx} />}</InternalControlGuard>;
}
