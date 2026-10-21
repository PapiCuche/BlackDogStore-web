"use client";

/**
 * DEVICE-COMMERCE-01 · DC1 — «Margen comercial» del resumen de ventas.
 *
 * Sólo para `costs.view`, y sólo se pide al servidor si se tiene: un 403 que
 * se sabe de antemano no es una petición que valga la pena hacer.
 *
 * A1 (COMMERCE-ADMIN-UX-01) — NUNCA DESAPARECE EN SILENCIO. Antes devolvía
 * `null` mientras cargaba, ante un error y sin datos, y un Administrador no
 * encontraba el margen. Ahora dice «Cargando…», el mensaje del servidor, o que
 * aún no hay ventas con costo conocido; y a quien no tiene el permiso le dice
 * cuál le falta y a quién pedírselo.
 *
 * MARGEN COMERCIAL, NUNCA «UTILIDAD». Es lo vendido sin IGV menos lo que costó
 * esa mercadería; no descuenta gastos, comisiones, pasarela ni impuestos. Y
 * se calcula sólo sobre las líneas con costo conocido: las demás se cuentan
 * aparte como «costo pendiente», en vez de contarlas como si no costaran nada.
 */

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import {
  fetchSalesMargin, type SalesMargin, type SalesMarginRow, type SalesMarginTotals,
} from "../lib/internal-api";
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

function plural(count: number, one: string, many: string) {
  return `${count} ${count === 1 ? one : many}`;
}

function Figure({ label, value, hint, testId, boxTestId }: {
  label: string; value: string; hint?: string; testId?: string; boxTestId?: string;
}) {
  return (
    <div className="rounded-xl border border-bd-border bg-surface p-4" data-testid={boxTestId}>
      <p className="text-[11px] uppercase tracking-widest text-muted">{label}</p>
      <p className="mt-1 font-display text-xl text-foreground" data-testid={testId}>{value}</p>
      {hint ? <p className="mt-0.5 text-[11px] text-muted">{hint}</p> : null}
    </div>
  );
}

/** Un margen con su importe y su %: «—» cuando no hay ventas, nunca 0 %. */
function MarginFigure({ label, totals, hint, percentTestId, boxTestId }: {
  label: string; totals: SalesMarginTotals; hint: string; percentTestId: string; boxTestId: string;
}) {
  return (
    <div className="rounded-xl border border-bd-border bg-surface p-4" data-testid={boxTestId}>
      <p className="text-[11px] uppercase tracking-widest text-muted">{label}</p>
      <p className="mt-1 flex flex-wrap items-baseline gap-x-2 font-display text-xl text-foreground">
        <span>{money(totals.margin)}</span>
        <span className="text-base text-muted" data-testid={percentTestId}>{percent(totals.margin_percent)}</span>
      </p>
      <p className="mt-0.5 text-[11px] text-muted">{hint}</p>
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

/** A1: the one sentence for whoever lacks the permission — same text as the 403 page. */
export const COSTS_PERMISSION_HINT = "Necesitas el permiso Ver costos y márgenes; pídeselo al administrador.";

type MarginState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; report: SalesMargin };

export function SalesMarginSection({
  companyId, branchId, canSeeCosts, costsHref,
}: {
  companyId: number | null;
  branchId: number | null;
  canSeeCosts: boolean;
  /** A1: from Ventas, a link to «Costos y precios». */
  costsHref?: string;
}) {
  // An answer belongs to the request that asked for it: a new branch shows
  // «Cargando…» until its own answer arrives, never the previous one.
  const request = useMemo(() => ({ companyId, branchId }), [companyId, branchId]);
  const [answer, setAnswer] = useState<{ request: object; state: MarginState } | null>(null);

  useEffect(() => {
    if (!canSeeCosts) return;
    let cancelled = false;
    fetchSalesMargin(request.companyId, request.branchId)
      .then((data) => { if (!cancelled) setAnswer({ request, state: { kind: "ready", report: data } }); })
      .catch((err: unknown) => {
        if (!cancelled) {
          setAnswer({
            request,
            state: {
              kind: "error",
              message: err instanceof Error && err.message ? err.message : "No se pudo cargar el margen comercial.",
            },
          });
        }
      });
    return () => { cancelled = true; };
  }, [request, canSeeCosts]);

  const state: MarginState = answer && answer.request === request ? answer.state : { kind: "loading" };

  if (!canSeeCosts) {
    return (
      <p className="rounded-xl border border-dashed border-bd-border px-4 py-3 text-sm text-muted">
        Margen comercial: {COSTS_PERMISSION_HINT}
      </p>
    );
  }

  const link = costsHref ? (
    <Link href={costsHref} className="text-sm font-semibold text-foreground underline underline-offset-4">
      Abrir Costos y precios
    </Link>
  ) : null;

  if (state.kind !== "ready") {
    return (
      <section role="region" aria-label="Margen comercial">
        <DashboardSection title="Margen comercial" description="Ventas pagadas sin IGV menos el costo de lo vendido.">
          {state.kind === "loading" ? (
            <p className="text-sm text-muted">Cargando…</p>
          ) : (
            <p role="alert" className="rounded-xl border border-danger-border bg-danger-surface px-4 py-3 text-sm text-danger">
              {state.message}
            </p>
          )}
          {link}
        </DashboardSection>
      </section>
    );
  }

  const { report } = state;
  const pending = report.pending_cost;
  const { estimated, realised, estimated_only: estimatedOnly, unknown_cost: unknown, refunds } = report;
  return (
    <section role="region" aria-label="Margen comercial">
      <DashboardSection
        title="Margen comercial"
        description={`Ventas pagadas de los últimos ${report.window_days} días, sin IGV, menos el costo de lo vendido. Es un margen comercial estimado: no descuenta gastos, comisiones ni impuestos.`}
      >
        {link}
        {estimated.lines === 0 ? (
          <p role="note" className="rounded-xl border border-dashed border-bd-border px-4 py-3 text-sm text-muted">
            Aún no hay ventas con costo conocido en este periodo.
          </p>
        ) : null}
        {pending.lines > 0 ? (
          <p role="status" className="rounded-xl border border-warning/40 bg-warning/10 px-4 py-3 text-sm text-foreground">
            {pending.lines} {pending.lines === 1 ? "línea" : "líneas"} de {pending.orders}{" "}
            {pending.orders === 1 ? "venta" : "ventas"} con costo pendiente ({money(pending.amount)} sin IGV):
            no entran en el margen hasta que se conozca su costo.
          </p>
        ) : null}
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Figure label="Ventas netas (sin IGV)" value={money(report.sales.net)} hint={`${money(report.sales.gross)} con IGV · ${report.sales.orders} ventas`} />
          <Figure label="Cobro neto" value={money(report.collected.amount)} hint={`${report.collected.orders} pedidos pagados vigentes · menos reembolsos confirmados`} />
          <Figure label="Costo de lo vendido" value={money(report.cost_of_goods_sold)} hint={`sobre ${money(report.net_sales_with_cost)} vendidos con costo conocido`} />
          <MarginFigure
            label="Margen comercial estimado"
            totals={estimated}
            hint={`${plural(estimated.lines, "línea", "líneas")} con costo conocido, sobre ${money(estimated.net_sales)}`}
            percentTestId="margin-percent"
            boxTestId="margin-estimated"
          />
          <MarginFigure
            label="Margen comercial realizado"
            totals={realised}
            hint={`${plural(realised.lines, "línea", "líneas")} con costo confirmado (stock propio vendido o bajo pedido recibido, a su costo real), sobre ${money(realised.net_sales)}`}
            percentTestId="realised-percent"
            boxTestId="margin-realised"
          />
          <Figure
            label="Sólo estimado"
            value={money(estimatedOnly.net_sales)}
            hint={`${plural(estimatedOnly.lines, "línea", "líneas")}: ${estimatedOnly.reference_cost_lines} con costo de referencia · ${estimatedOnly.on_order_not_received_lines} bajo pedido sin recibir${estimatedOnly.on_order_without_real_cost_lines ? ` · ${estimatedOnly.on_order_without_real_cost_lines} recibidas sin costo real` : ""}`}
            boxTestId="margin-estimated-only"
          />
          {report.real_cost_pending && report.real_cost_pending.lines > 0 ? (
            <Figure
              label="Costo real pendiente"
              value={money(report.real_cost_pending.net_sales)}
              hint={`${plural(report.real_cost_pending.lines, "línea", "líneas")} bajo pedido sin costo real (${report.real_cost_pending.not_received_lines} sin recibir · ${report.real_cost_pending.to_confirm_lines ?? 0} por confirmar · ${report.real_cost_pending.received_without_real_cost_lines} recibidas sin costo real registrado), sin IGV · fuera del realizado`}
              boxTestId="margin-real-cost-pending"
            />
          ) : null}
          <Figure
            label="Costo desconocido"
            value={money(unknown.net_sales)}
            hint={`${plural(unknown.lines, "línea", "líneas")} de ${plural(unknown.orders, "venta", "ventas")}, sin IGV · fuera de ambos márgenes`}
            boxTestId="margin-unknown-cost"
          />
          {report.cancellations && report.cancellations.orders > 0 ? (
            <Figure
              label="Cancelados (fuera del margen)"
              value={money(report.cancellations.gross)}
              hint={`${plural(report.cancellations.orders, "pedido pagado", "pedidos pagados")} y cancelados · ${plural(report.cancellations.units, "unidad", "unidades")} · costo ${money(report.cancellations.cost)} · ${money(report.cancellations.refunds_confirmed)} devueltos`}
              boxTestId="margin-cancellations"
            />
          ) : null}
          <Figure
            label="Reembolsos"
            value={money(refunds.amount)}
            hint={`${plural(refunds.orders, "pedido", "pedidos")} · ${money(refunds.net)} sin IGV · fuera de las ventas`}
            boxTestId="margin-refunds"
          />
        </div>
        <p className="text-[11px] text-muted">
          Margen = (venta neta sin IGV − costo neto comparable) ÷ venta neta × 100, sólo sobre líneas con costo conocido.
        </p>
        <Breakdown title="Por producto" rows={report.by_product} />
        <Breakdown title="Por sucursal" rows={report.by_branch} />
        <Breakdown title="Por canal" rows={report.by_channel} />
      </DashboardSection>
    </section>
  );
}
