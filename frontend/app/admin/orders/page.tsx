"use client";

import { useEffect, useState, useCallback } from "react";
import { StaffGuard } from "../components/StaffGuard";
import { AdminShell } from "../components/AdminShell";
import { OrdersTable } from "../components/OrdersTable";
import {
  AdminOrder,
  PaginatedResponse,
  fetchAdminOrders,
  PAYMENT_STATUS_LABELS,
  FULFILLMENT_STATUS_LABELS,
} from "../../lib/admin";
import type { AuthUser } from "../../lib/auth";

type Filters = {
  search: string;
  status: string;
  fulfillment_status: string;
  paid: string;
  date_from: string;
  date_to: string;
};

function OrdersContent({ user }: { user: AuthUser }) {
  const [data, setData] = useState<PaginatedResponse<AdminOrder> | null>(null);
  const [filters, setFilters] = useState<Filters>({
    search: "",
    status: "",
    fulfillment_status: "",
    paid: "",
    date_from: "",
    date_to: "",
  });
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchAdminOrders({
        search: filters.search || undefined,
        status: filters.status || undefined,
        fulfillment_status: filters.fulfillment_status || undefined,
        paid: (filters.paid || undefined) as "true" | "false" | undefined,
        date_from: filters.date_from || undefined,
        date_to: filters.date_to || undefined,
        page,
        page_size: 25,
      });
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error al cargar órdenes.");
    } finally {
      setLoading(false);
    }
  }, [filters, page]);

  useEffect(() => {
    load();
  }, [load]);

  function setFilter(key: keyof Filters, value: string) {
    setFilters((prev) => ({ ...prev, [key]: value }));
    setPage(1);
  }

  const totalPages = data ? Math.ceil(data.count / data.page_size) : 1;

  return (
    <AdminShell user={user}>
      <div className="space-y-6">
        <div>
          <h1 className="text-xl font-semibold text-white">Órdenes</h1>
          <p className="mt-1 text-sm text-zinc-500">
            {data ? `${data.count} órdenes en total` : "Cargando…"}
          </p>
        </div>

        <div className="flex flex-wrap gap-3">
          <input
            type="text"
            placeholder="Buscar por cliente, email o #ID…"
            value={filters.search}
            onChange={(e) => setFilter("search", e.target.value)}
            className="flex-1 min-w-52 bg-zinc-900 border border-white/[0.1] rounded px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 focus:outline-none focus:border-white/30"
          />
          <select
            value={filters.status}
            onChange={(e) => setFilter("status", e.target.value)}
            className="bg-zinc-900 border border-white/[0.1] rounded px-3 py-2 text-sm text-zinc-300 focus:outline-none focus:border-white/30"
          >
            <option value="">Todos los pagos</option>
            {Object.entries(PAYMENT_STATUS_LABELS).map(([v, l]) => (
              <option key={v} value={v}>{l}</option>
            ))}
          </select>
          <select
            value={filters.fulfillment_status}
            onChange={(e) => setFilter("fulfillment_status", e.target.value)}
            className="bg-zinc-900 border border-white/[0.1] rounded px-3 py-2 text-sm text-zinc-300 focus:outline-none focus:border-white/30"
          >
            <option value="">Todos los despachos</option>
            {Object.entries(FULFILLMENT_STATUS_LABELS).map(([v, l]) => (
              <option key={v} value={v}>{l}</option>
            ))}
          </select>
          <select
            value={filters.paid}
            onChange={(e) => setFilter("paid", e.target.value)}
            className="bg-zinc-900 border border-white/[0.1] rounded px-3 py-2 text-sm text-zinc-300 focus:outline-none focus:border-white/30"
          >
            <option value="">Pagado / No pagado</option>
            <option value="true">Pagado</option>
            <option value="false">No pagado</option>
          </select>
          <input
            type="date"
            value={filters.date_from}
            onChange={(e) => setFilter("date_from", e.target.value)}
            className="bg-zinc-900 border border-white/[0.1] rounded px-3 py-2 text-sm text-zinc-300 focus:outline-none focus:border-white/30"
          />
          <input
            type="date"
            value={filters.date_to}
            onChange={(e) => setFilter("date_to", e.target.value)}
            className="bg-zinc-900 border border-white/[0.1] rounded px-3 py-2 text-sm text-zinc-300 focus:outline-none focus:border-white/30"
          />
        </div>

        <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-6">
          {error && <p className="text-sm text-red-400 mb-4">{error}</p>}
          {loading ? (
            <p className="text-zinc-500 text-sm py-6 text-center">Cargando…</p>
          ) : (
            <OrdersTable orders={data?.results ?? []} />
          )}
        </div>

        {totalPages > 1 && (
          <div className="flex items-center justify-between text-sm text-zinc-500">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1}
              className="px-3 py-1.5 rounded border border-white/[0.08] hover:border-white/20 disabled:opacity-40 disabled:cursor-not-allowed transition"
            >
              ← Anterior
            </button>
            <span>Página {page} de {totalPages}</span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
              className="px-3 py-1.5 rounded border border-white/[0.08] hover:border-white/20 disabled:opacity-40 disabled:cursor-not-allowed transition"
            >
              Siguiente →
            </button>
          </div>
        )}
      </div>
    </AdminShell>
  );
}

export default function AdminOrdersPage() {
  return (
    <StaffGuard>{(user) => <OrdersContent user={user} />}</StaffGuard>
  );
}
