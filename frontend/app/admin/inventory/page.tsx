"use client";

// Phase 6.0 — operational inventory dashboard. Rebuilt per branch in Phase 2D.
//
// Everything on this screen is scoped to the branch selected above it. When the
// selection is "todas", the figures cover the branches THIS operator reaches,
// which is not necessarily the whole company — <ScopeNote> spells that out
// rather than letting a heading overclaim.

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { AdminShell } from "../components/AdminShell";
import { StaffGuard } from "../components/StaffGuard";
import { BranchSelector, ScopeNote } from "../components/BranchSelector";
import { useBranchScope } from "../lib/use-branch-scope";
import { HorizontalBarChart, VerticalBarChart } from "../components/charts";
import {
  BranchStockTable,
  EmptyBox,
  ErrorBox,
  Panel,
  Spinner,
  StatCard,
  TableWrap,
  Td,
  Th,
  MovementBadge,
  SignedQty,
  formatDateTime,
  formatSoles,
  movementReference,
} from "../components/InventoryUi";
import {
  fetchBranchStock,
  fetchInventoryDashboard,
  fetchLowStock,
  fetchStockMovements,
  type BranchStockRow,
  type InventoryDashboard,
  type StockMovement,
} from "../../lib/inventory";
import type { AuthUser } from "../../lib/auth";

type Data = {
  dashboard: InventoryDashboard;
  low: BranchStockRow[];
  high: BranchStockRow[];
  movements: StockMovement[];
};

function InventoryDashboardPage({ user }: { user: AuthUser }) {
  const scope = useBranchScope({ preferAggregate: true });
  const [data, setData] = useState<Data | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const branch = scope.branch;

  const load = useCallback(async (): Promise<Data> => {
    const [dashboard, low, high, movements] = await Promise.all([
      fetchInventoryDashboard({ branch }),
      fetchLowStock({ branch, limit: 8 }),
      fetchBranchStock({ branch, status: "in_stock", page_size: 8 }),
      fetchStockMovements({ branch, page_size: 8 }),
    ]);
    return {
      dashboard,
      low: low.results,
      high: high.results,
      movements: movements.results,
    };
  }, [branch]);

  useEffect(() => {
    if (!scope.ready) return;
    let cancelled = false;
    void (async () => {
      try {
        const result = await load();
        if (!cancelled) {
          setData(result);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Error inesperado.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [load, scope.ready]);

  const summary = data?.dashboard.summary;
  const charts = data?.dashboard.charts;

  return (
    <AdminShell user={user}>
      <div className="space-y-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold text-foreground">Inventario</h1>
            <p className="mt-1 text-sm text-muted">
              Stock por sucursal, movimientos y rotación.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <BranchSelector
              access={scope.access}
              value={scope.branch}
              onChange={(next) => {
                setLoading(true);
                scope.setBranch(next);
              }}
            />
            <Link
              href="/admin/inventory/movements"
              className="rounded-lg border border-bd-border px-3.5 py-2 text-sm text-foreground/85 transition hover:border-bd-border hover:text-foreground"
            >
              Movimientos
            </Link>
            <Link
              href="/admin/inventory/transfers"
              className="rounded-lg border border-bd-border px-3.5 py-2 text-sm text-foreground/85 transition hover:border-bd-border hover:text-foreground"
            >
              Transferencias
            </Link>
            <Link
              href="/admin/inventory/counts"
              className="rounded-lg border border-bd-border px-3.5 py-2 text-sm text-foreground/85 transition hover:border-bd-border hover:text-foreground"
            >
              Recuentos
            </Link>
            <Link
              href="/admin/inventory/replenishment"
              className="rounded-lg border border-bd-border px-3.5 py-2 text-sm text-foreground/85 transition hover:border-bd-border hover:text-foreground"
            >
              Reposición
            </Link>
            <Link
              href="/admin/inventory/reports"
              className="rounded-lg border border-bd-border px-3.5 py-2 text-sm text-foreground/85 transition hover:border-bd-border hover:text-foreground"
            >
              Reportes
            </Link>
          </div>
        </div>

        <ScopeNote scope={data?.dashboard.scope} />

        {scope.error ? <ErrorBox message={scope.error} /> : null}
        {!scope.loading && !scope.ready && !scope.error ? (
          <EmptyBox message="No tienes sucursales asignadas. Pide a un administrador de la empresa que te asigne al menos una." />
        ) : null}
        {loading && scope.ready ? <Spinner label="Cargando inventario…" /> : null}
        {error ? <ErrorBox message={error} /> : null}

        {data && summary && charts ? (
          <>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <StatCard
                label="Unidades en stock"
                value={summary.total_units}
                hint="Solo productos activos"
              />
              <StatCard
                label="Sin stock"
                value={summary.out_of_stock_count}
                emphasis={summary.out_of_stock_count > 0}
                hint="Productos agotados en esta selección"
              />
              <StatCard
                label="Bajo mínimo"
                value={summary.low_stock_count}
                emphasis={summary.low_stock_count > 0}
                hint="Según el mínimo de cada sucursal"
              />
              <StatCard
                label="Transferencias en tránsito"
                value={data.dashboard.transfers_in_transit}
                hint="Enviadas y aún no recibidas"
              />
              <StatCard
                label="Recuentos pendientes"
                value={data.dashboard.pending_counts}
                hint="Sin aprobar ni anular"
              />
              <StatCard
                label="Valor estimado"
                value={formatSoles(summary.inventory_value)}
                // Not "cost" and not "capital invertido": there is no purchase
                // cost in the system, so the only honest basis is sale price.
                hint="A precio de venta — no es costo"
              />
            </div>

            <div className="grid gap-6 lg:grid-cols-2">
              <Panel title="Stock por sucursal" description="Unidades disponibles">
                <HorizontalBarChart
                  series={charts.stock_by_branch}
                  emptyMessage="Sin stock registrado."
                />
              </Panel>
              <Panel title="Bajo mínimo por sucursal" description="Productos a reponer">
                <HorizontalBarChart
                  series={charts.low_stock_by_branch}
                  emptyMessage="Ningún producto bajo mínimo."
                />
              </Panel>
              <Panel title="Entradas (7 días)" description="Unidades que ingresaron">
                <VerticalBarChart
                  series={charts.entries_trend}
                  emptyMessage="Sin entradas en el período."
                />
              </Panel>
              <Panel title="Salidas (7 días)" description="Unidades que salieron">
                <VerticalBarChart
                  series={charts.exits_trend}
                  emptyMessage="Sin salidas en el período."
                />
              </Panel>
            </div>

            <div className="grid gap-6 lg:grid-cols-2">
              <Panel title="Bajo stock" description="Reponer con prioridad">
                <BranchStockTable
                  rows={data.low}
                  emptyMessage="Ningún producto por debajo del mínimo."
                />
              </Panel>

              <Panel title="Con más stock" description="Mayor cantidad en tienda">
                <BranchStockTable rows={data.high} emptyMessage="Sin stock registrado." />
              </Panel>

              <Panel
                title="Últimos movimientos"
                description="Kardex reciente"
                action={
                  <Link
                    href="/admin/inventory/movements"
                    className="text-xs text-muted transition hover:text-foreground"
                  >
                    Ver todos →
                  </Link>
                }
              >
                {data.movements.length === 0 ? (
                  <EmptyBox message="Todavía no hay movimientos registrados." />
                ) : (
                  <TableWrap>
                    <thead>
                      <tr className="border-b border-bd-border">
                        <Th>Fecha</Th>
                        <Th>Sucursal</Th>
                        <Th>Producto</Th>
                        <Th>Tipo</Th>
                        <Th right>Cant.</Th>
                        <Th>Referencia</Th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.movements.map((m) => (
                        <tr key={m.id} className="border-b border-bd-border">
                          <Td muted>{formatDateTime(m.created_at)}</Td>
                          <Td muted>{m.branch_name}</Td>
                          <Td>{m.product_name}</Td>
                          <Td><MovementBadge movement={m} /></Td>
                          <Td right><SignedQty movement={m} /></Td>
                          <Td muted>{movementReference(m)}</Td>
                        </tr>
                      ))}
                    </tbody>
                  </TableWrap>
                )}
              </Panel>

              <Panel title="Distribución de movimientos" description="Últimos 30 días">
                <HorizontalBarChart
                  series={charts.movement_types}
                  emptyMessage="Sin movimientos en el período."
                />
              </Panel>
            </div>
          </>
        ) : null}
      </div>
    </AdminShell>
  );
}

export default function InventoryPage() {
  return <StaffGuard>{(user) => <InventoryDashboardPage user={user} />}</StaffGuard>;
}
