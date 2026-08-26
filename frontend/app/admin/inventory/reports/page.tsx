"use client";

// Phase 6.0 — operational stock and sales reports.

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { AdminShell } from "../../components/AdminShell";
import { StaffGuard } from "../../components/StaffGuard";
import {
  EmptyBox,
  ErrorBox,
  Panel,
  Spinner,
  StatCard,
  StockBadge,
  TableWrap,
  Td,
  Th,
  formatSoles,
} from "../../components/InventoryUi";
import {
  fetchBestSelling,
  fetchHighStock,
  fetchInventorySummary,
  fetchLowStock,
  fetchStaleStock,
  type BestSellingRow,
  type InventoryProduct,
  type InventorySummary,
} from "../../../lib/inventory";
import type { AuthUser } from "../../../lib/auth";

type Report = {
  summary: InventorySummary;
  low: InventoryProduct[];
  high: InventoryProduct[];
  outOfStock: InventoryProduct[];
  best: BestSellingRow[];
  stale: InventoryProduct[];
};

function ProductTable({
  rows,
  threshold,
  emptyMessage,
}: {
  rows: InventoryProduct[];
  threshold: number;
  emptyMessage: string;
}) {
  if (rows.length === 0) return <EmptyBox message={emptyMessage} />;
  return (
    <TableWrap>
      <thead>
        <tr className="border-b border-white/[0.06]">
          <Th>Producto</Th>
          <Th>Categoría</Th>
          <Th right>Precio</Th>
          <Th right>Stock</Th>
        </tr>
      </thead>
      <tbody>
        {rows.map((p) => (
          <tr key={p.id} className="border-b border-white/[0.03]">
            <Td>
              <Link href={`/admin/products/${p.id}/stock-card`} className="transition hover:text-white">
                {p.name}
              </Link>
            </Td>
            <Td muted>{p.category_name ?? "—"}</Td>
            <Td right muted>{formatSoles(p.price)}</Td>
            <Td right><StockBadge value={p.inventory} threshold={threshold} /></Td>
          </tr>
        ))}
      </tbody>
    </TableWrap>
  );
}

function ReportsContent({ user }: { user: AuthUser }) {
  const [threshold, setThreshold] = useState(5);
  const [staleDays, setStaleDays] = useState(60);
  const [data, setData] = useState<Report | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const load = useCallback(async (): Promise<Report> => {
    const [summary, low, high, best, stale] = await Promise.all([
      fetchInventorySummary(threshold),
      fetchLowStock({ threshold, limit: 50 }),
      fetchHighStock({ limit: 20 }),
      fetchBestSelling({ limit: 20 }),
      fetchStaleStock({ days: staleDays, limit: 50 }),
    ]);
    return {
      summary,
      low: low.results.filter((p) => p.inventory > 0),
      outOfStock: low.results.filter((p) => p.inventory <= 0),
      high: high.results,
      best: best.results,
      stale: stale.results,
    };
  }, [threshold, staleDays]);

  useEffect(() => {
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
  }, [load, reloadKey]);

  const fieldClass =
    "w-24 rounded-lg border border-white/[0.08] bg-black/40 px-3 py-2 text-sm text-zinc-200 outline-none transition focus:border-white/25";

  return (
    <AdminShell user={user}>
      <div className="space-y-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold text-white">Reportes de inventario</h1>
            <p className="mt-1 text-sm text-zinc-500">
              Stock crítico, rotación y ventas por producto.
            </p>
          </div>
          <Link
            href="/admin/inventory"
            className="rounded-lg border border-white/10 px-3.5 py-2 text-sm text-zinc-300 transition hover:border-white/20 hover:text-white"
          >
            ← Inventario
          </Link>
        </div>

        <div className="flex flex-wrap items-end gap-4 rounded-xl border border-white/[0.06] bg-white/[0.02] p-5">
          <div>
            <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-zinc-500" htmlFor="r-threshold">
              Umbral bajo stock
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
            <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-zinc-500" htmlFor="r-days">
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
            className="rounded-lg bg-white px-4 py-2 text-sm font-semibold text-black transition hover:bg-zinc-200"
          >
            Actualizar
          </button>
        </div>

        {loading ? <Spinner label="Generando reportes…" /> : null}
        {error ? <ErrorBox message={error} /> : null}

        {data ? (
          <>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <StatCard label="Valor del inventario" value={formatSoles(data.summary.inventory_value)} />
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
              <ProductTable
                rows={data.outOfStock}
                threshold={threshold}
                emptyMessage="Ningún producto agotado."
              />
            </Panel>

            <Panel title="Productos con menos stock" description={`Al o por debajo de ${threshold} unidades`}>
              <ProductTable
                rows={data.low}
                threshold={threshold}
                emptyMessage="Ningún producto por debajo del umbral."
              />
            </Panel>

            <Panel title="Productos con más stock" description="Mayor cantidad inmovilizada">
              <ProductTable
                rows={data.high}
                threshold={threshold}
                emptyMessage="Sin productos activos."
              />
            </Panel>

            <Panel title="Productos más vendidos" description="Unidades e ingresos de órdenes pagadas">
              {data.best.length === 0 ? (
                <EmptyBox message="Todavía no hay ventas pagadas." />
              ) : (
                <TableWrap>
                  <thead>
                    <tr className="border-b border-white/[0.06]">
                      <Th>Producto</Th>
                      <Th right>Unidades vendidas</Th>
                      <Th right>Ingresos</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.best.map((row) => (
                      <tr key={row.product_id} className="border-b border-white/[0.03]">
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
              <ProductTable
                rows={data.stale}
                threshold={threshold}
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
