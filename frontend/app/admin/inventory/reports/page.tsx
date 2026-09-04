"use client";

// Phase 6.0 — operational stock and sales reports. Branch-scoped in Phase 2D.
//
// "Bajo stock" now means "at or below THIS branch's minimum for this product",
// with the threshold field below acting only as the fallback for rows nobody has
// configured. One company-wide number could never say that a charger running low
// at 20 units downtown is perfectly stocked at 3 in a satellite shop.

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { AdminShell } from "../../components/AdminShell";
import { StaffGuard } from "../../components/StaffGuard";
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
  formatSoles,
} from "../../components/InventoryUi";
import { BranchSelector, ScopeNote } from "../../components/BranchSelector";
import { useBranchScope } from "../../lib/use-branch-scope";
import {
  fetchBestSelling,
  fetchHighStock,
  fetchInventorySummary,
  fetchLowStock,
  fetchStaleStock,
  type BestSellingRow,
  type BranchStockRow,
  type InventoryScope,
  type InventorySummary,
} from "../../../lib/inventory";
import type { AuthUser } from "../../../lib/auth";

type Report = {
  summary: InventorySummary;
  scope: InventoryScope;
  low: BranchStockRow[];
  high: BranchStockRow[];
  outOfStock: BranchStockRow[];
  best: BestSellingRow[];
  stale: BranchStockRow[];
};

function ReportsContent({ user }: { user: AuthUser }) {
  const scope = useBranchScope({ preferAggregate: true });
  const [threshold, setThreshold] = useState(5);
  const [staleDays, setStaleDays] = useState(60);
  const [data, setData] = useState<Report | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const branch = scope.branch;

  const load = useCallback(async (): Promise<Report> => {
    const [summary, low, high, best, stale] = await Promise.all([
      fetchInventorySummary(threshold, branch),
      fetchLowStock({ threshold, limit: 50, branch }),
      fetchHighStock({ limit: 20, branch }),
      fetchBestSelling({ limit: 20, branch }),
      fetchStaleStock({ days: staleDays, limit: 50, branch }),
    ]);
    return {
      summary,
      scope: summary.scope,
      low: low.results.filter((r) => r.quantity > 0),
      outOfStock: low.results.filter((r) => r.quantity <= 0),
      high: high.results,
      best: best.results,
      stale: stale.results,
    };
  }, [threshold, staleDays, branch]);

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
  }, [load, reloadKey, scope.ready]);

  const fieldClass =
    "w-24 rounded-lg border border-bd-border bg-background/40 px-3 py-2 text-sm text-foreground outline-none transition focus:border-bd-border";

  return (
    <AdminShell user={user}>
      <div className="space-y-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold text-foreground">Reportes de inventario</h1>
            <p className="mt-1 text-sm text-muted">
              Stock crítico, rotación y ventas por producto.
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
              href="/admin/inventory"
              className="rounded-lg border border-bd-border px-3.5 py-2 text-sm text-foreground/85 transition hover:border-bd-border hover:text-foreground"
            >
              ← Inventario
            </Link>
          </div>
        </div>

        <ScopeNote scope={data?.scope} />

        <div className="flex flex-wrap items-end gap-4 rounded-xl border border-bd-border bg-surface p-5">
          <div>
            <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-muted" htmlFor="r-threshold">
              Umbral por defecto
            </label>
            <input
              id="r-threshold"
              type="number"
              min={0}
              className={fieldClass}
              value={threshold}
              onChange={(e) => setThreshold(Math.max(0, parseInt(e.target.value, 10) || 0))}
            />
          </div>
          <div>
            <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-muted" htmlFor="r-days">
              Días sin movimiento
            </label>
            <input
              id="r-days"
              type="number"
              min={1}
              className={fieldClass}
              value={staleDays}
              onChange={(e) => setStaleDays(Math.max(1, parseInt(e.target.value, 10) || 1))}
            />
          </div>
          <button
            type="button"
            onClick={() => {
              setLoading(true);
              setReloadKey((k) => k + 1);
            }}
            className="rounded-lg bg-foreground px-4 py-2 text-sm font-semibold text-background transition hover:bg-foreground/90"
          >
            Actualizar
          </button>
        </div>

        {scope.error ? <ErrorBox message={scope.error} /> : null}
        {!scope.loading && !scope.ready && !scope.error ? (
          <EmptyBox message="No tienes sucursales asignadas." />
        ) : null}
        {loading && scope.ready ? <Spinner label="Generando reportes…" /> : null}
        {error ? <ErrorBox message={error} /> : null}

        {data ? (
          <>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <StatCard
                label="Valor estimado"
                value={formatSoles(data.summary.inventory_value)}
                hint="A precio de venta — no es costo"
              />
              <StatCard label="Unidades totales" value={data.summary.total_units} />
              <StatCard
                label="Agotados"
                value={data.summary.out_of_stock_count}
                emphasis={data.summary.out_of_stock_count > 0}
              />
              <StatCard
                label="Bajo stock"
                value={data.summary.low_stock_count}
                emphasis={data.summary.low_stock_count > 0}
                hint={`Umbral: ${threshold} u.`}
              />
            </div>

            <Panel title="Productos agotados" description="Stock en cero — sin disponibilidad">
              <BranchStockTable rows={data.outOfStock} emptyMessage="Ningún producto agotado." />
            </Panel>

            <Panel title="Productos con menos stock" description="Al o por debajo del mínimo de su sucursal">
              <BranchStockTable
                rows={data.low}
                emptyMessage="Ningún producto por debajo del mínimo."
              />
            </Panel>

            <Panel title="Productos con más stock" description="Mayor cantidad inmovilizada">
              <BranchStockTable rows={data.high} emptyMessage="Sin stock registrado." />
            </Panel>

            <Panel title="Productos más vendidos" description="Unidades e ingresos de órdenes pagadas">
              {data.best.length === 0 ? (
                <EmptyBox message="Todavía no hay ventas pagadas." />
              ) : (
                <TableWrap>
                  <thead>
                    <tr className="border-b border-bd-border">
                      <Th>Producto</Th>
                      <Th right>Unidades vendidas</Th>
                      <Th right>Ingresos</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.best.map((row) => (
                      <tr key={row.product_id} className="border-b border-bd-border">
                        <Td>{row.product_name}</Td>
                        <Td right>{row.units_sold}</Td>
                        <Td right muted>{formatSoles(row.revenue)}</Td>
                      </tr>
                    ))}
                  </tbody>
                </TableWrap>
              )}
            </Panel>

            <Panel
              title="Productos sin movimiento"
              description={`Sin entradas ni salidas en los últimos ${staleDays} días`}
            >
              <BranchStockTable
                rows={data.stale}
                emptyMessage="Todos los productos tuvieron movimiento en el período."
              />
            </Panel>
          </>
        ) : null}
      </div>
    </AdminShell>
  );
}

export default function ReportsPage() {
  return <StaffGuard>{(user) => <ReportsContent user={user} />}</StaffGuard>;
}
