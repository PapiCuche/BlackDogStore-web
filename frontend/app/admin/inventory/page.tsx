"use client";

// Phase 6.0 — operational inventory dashboard.

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { AdminShell } from "../components/AdminShell";
import { StaffGuard } from "../components/StaffGuard";
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
  MovementBadge,
  SignedQty,
  formatDateTime,
  formatSoles,
  movementReference,
} from "../components/InventoryUi";
import {
  fetchBestSelling,
  fetchHighStock,
  fetchInventorySummary,
  fetchLowStock,
  fetchStockMovements,
  type BestSellingRow,
  type InventoryProduct,
  type InventorySummary,
  type StockMovement,
} from "../../lib/inventory";
import type { AuthUser } from "../../lib/auth";

type Data = {
  summary: InventorySummary;
  low: InventoryProduct[];
  high: InventoryProduct[];
  best: BestSellingRow[];
  movements: StockMovement[];
};

function InventoryDashboard({ user }: { user: AuthUser }) {
  const [data, setData] = useState<Data | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [summary, low, high, best, movements] = await Promise.all([
        fetchInventorySummary(),
        fetchLowStock({ limit: 8 }),
        fetchHighStock({ limit: 8 }),
        fetchBestSelling({ limit: 8 }),
        fetchStockMovements({ page_size: 8 }),
      ]);
      return {
        summary,
        low: low.results,
        high: high.results,
        best: best.results,
        movements: movements.results,
      };
    } catch (err) {
      throw err instanceof Error ? err : new Error("No se pudo cargar el inventario.");
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const result = await load();
        if (!cancelled) setData(result);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Error inesperado.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [load]);

  return (
    <AdminShell user={user}>
      <div className="space-y-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold text-white">Inventario</h1>
            <p className="mt-1 text-sm text-zinc-500">
              Resumen operativo de stock, movimientos y rotación.
            </p>
          </div>
          <div className="flex gap-2">
            <Link
              href="/admin/inventory/movements"
              className="rounded-lg border border-white/10 px-3.5 py-2 text-sm text-zinc-300 transition hover:border-white/20 hover:text-white"
            >
              Movimientos
            </Link>
            <Link
              href="/admin/inventory/reports"
              className="rounded-lg border border-white/10 px-3.5 py-2 text-sm text-zinc-300 transition hover:border-white/20 hover:text-white"
            >
              Reportes
            </Link>
          </div>
        </div>

        {loading ? <Spinner label="Cargando inventario…" /> : null}
        {error ? <ErrorBox message={error} /> : null}

        {data ? (
          <>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <StatCard
                label="Productos"
                value={data.summary.total_products}
                hint={`${data.summary.active_products} activos`}
              />
              <StatCard
                label="Unidades en stock"
                value={data.summary.total_units}
                hint="Solo productos activos con stock"
              />
              <StatCard
                label="Sin stock"
                value={data.summary.out_of_stock_count}
                emphasis={data.summary.out_of_stock_count > 0}
                hint="Productos activos agotados"
              />
              <StatCard
                label="Bajo stock"
                value={data.summary.low_stock_count}
                emphasis={data.summary.low_stock_count > 0}
                hint={`Umbral: ${data.summary.low_stock_threshold} u.`}
              />
              <StatCard
                label="Valor del inventario"
                value={formatSoles(data.summary.inventory_value)}
                hint="Stock × precio de venta"
              />
              <StatCard
                label="Más vendido"
                value={data.summary.best_selling_product?.product_name ?? "—"}
                hint={
                  data.summary.best_selling_product
                    ? `${data.summary.best_selling_product.units_sold} u. vendidas`
                    : "Sin ventas registradas"
                }
              />
            </div>

            <div className="grid gap-6 lg:grid-cols-2">
              <Panel title="Bajo stock" description="Reponer con prioridad">
                {data.low.length === 0 ? (
                  <EmptyBox message="Ningún producto por debajo del umbral." />
                ) : (
                  <TableWrap>
                    <thead>
                      <tr className="border-b border-white/[0.06]">
                        <Th>Producto</Th>
                        <Th right>Stock</Th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.low.map((p) => (
                        <tr key={p.id} className="border-b border-white/[0.03]">
                          <Td>
                            <Link
                              href={`/admin/products/${p.id}/stock-card`}
                              className="transition hover:text-white"
                            >
                              {p.name}
                            </Link>
                          </Td>
                          <Td right>
                            <StockBadge
                              value={p.inventory}
                              threshold={data.summary.low_stock_threshold}
                            />
                          </Td>
                        </tr>
                      ))}
                    </tbody>
                  </TableWrap>
                )}
              </Panel>

              <Panel title="Alto stock" description="Mayor cantidad en tienda">
                {data.high.length === 0 ? (
                  <EmptyBox message="Sin productos activos." />
                ) : (
                  <TableWrap>
                    <thead>
                      <tr className="border-b border-white/[0.06]">
                        <Th>Producto</Th>
                        <Th right>Stock</Th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.high.map((p) => (
                        <tr key={p.id} className="border-b border-white/[0.03]">
                          <Td>
                            <Link
                              href={`/admin/products/${p.id}/stock-card`}
                              className="transition hover:text-white"
                            >
                              {p.name}
                            </Link>
                          </Td>
                          <Td right>
                            <span className="tabular-nums text-zinc-300">{p.inventory} u.</span>
                          </Td>
                        </tr>
                      ))}
                    </tbody>
                  </TableWrap>
                )}
              </Panel>

              <Panel title="Más vendidos" description="Derivado de órdenes pagadas">
                {data.best.length === 0 ? (
                  <EmptyBox message="Todavía no hay ventas pagadas." />
                ) : (
                  <TableWrap>
                    <thead>
                      <tr className="border-b border-white/[0.06]">
                        <Th>Producto</Th>
                        <Th right>Unidades</Th>
                        <Th right>Ingresos</Th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.best.map((row) => (
                        <tr key={row.product_id} className="border-b border-white/[0.03]">
                          <Td>{row.product_name}</Td>
                          <Td right>
                            <span className="tabular-nums text-zinc-300">{row.units_sold}</span>
                          </Td>
                          <Td right muted>{formatSoles(row.revenue)}</Td>
                        </tr>
                      ))}
                    </tbody>
                  </TableWrap>
                )}
              </Panel>

              <Panel
                title="Últimos movimientos"
                description="Kardex reciente"
                action={
                  <Link
                    href="/admin/inventory/movements"
                    className="text-xs text-zinc-500 transition hover:text-white"
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
                      <tr className="border-b border-white/[0.06]">
                        <Th>Fecha</Th>
                        <Th>Producto</Th>
                        <Th>Tipo</Th>
                        <Th right>Cant.</Th>
                        <Th>Referencia</Th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.movements.map((m) => (
                        <tr key={m.id} className="border-b border-white/[0.03]">
                          <Td muted>{formatDateTime(m.created_at)}</Td>
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
            </div>
          </>
        ) : null}
      </div>
    </AdminShell>
  );
}

export default function InventoryPage() {
  return <StaffGuard>{(user) => <InventoryDashboard user={user} />}</StaffGuard>;
}
