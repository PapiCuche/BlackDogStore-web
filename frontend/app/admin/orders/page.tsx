"use client";

import { useEffect, useState, useCallback } from "react";
import { AccessGuard } from "../components/AccessGuard";
import { AdminShell } from "../components/AdminShell";
import { OrdersTable } from "../components/OrdersTable";
import {
  FilterBar,
  PageHeader,
  internalButtonClass,
  internalInputClass,
} from "../components/internal-ui";
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
  shortfall: string;
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
    shortfall: "",
    date_from: "",
    date_to: "",
  });
  const [searchInput, setSearchInput] = useState("");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await fetchAdminOrders({
        search: filters.search || undefined,
        status: filters.status || undefined,
        fulfillment_status: filters.fulfillment_status || undefined,
        paid: (filters.paid || undefined) as "true" | "false" | undefined,
        shortfall: (filters.shortfall || undefined) as "true" | undefined,
        date_from: filters.date_from || undefined,
        date_to: filters.date_to || undefined,
        page,
        page_size: 25,
      }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error al cargar órdenes.");
    } finally {
      setLoading(false);
    }
  }, [filters, page]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setFilters((current) => ({ ...current, search: searchInput }));
      setPage(1);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  useEffect(() => {
    void load();
  }, [load]);

  function setFilter(key: keyof Filters, value: string) {
    setFilters((prev) => ({ ...prev, [key]: value }));
    setPage(1);
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.count / data.page_size)) : 1;

  return (
    <AdminShell user={user}>
      <div className="space-y-6">
        <PageHeader
          eyebrow="Comercial"
          title="Órdenes"
          description={data ? `${data.count} órdenes en total. Filtra por pago, despacho y fecha.` : "Pedidos del e-commerce y su estado operativo."}
        />

        <FilterBar>
          <div className="grid gap-3 xl:grid-cols-[minmax(0,1.3fr)_repeat(6,minmax(0,auto))]">
            <label>
              <span className="sr-only">Buscar órdenes</span>
              <input
                type="search"
                placeholder="Cliente, email o #ID"
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                className={internalInputClass}
              />
            </label>
            <label>
              <span className="sr-only">Estado de pago</span>
              <select value={filters.status} onChange={(e) => setFilter("status", e.target.value)} className={internalInputClass}>
                <option value="">Todos los pagos</option>
                {Object.entries(PAYMENT_STATUS_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
            </label>
            <label>
              <span className="sr-only">Estado de despacho</span>
              <select value={filters.fulfillment_status} onChange={(e) => setFilter("fulfillment_status", e.target.value)} className={internalInputClass}>
                <option value="">Todos los despachos</option>
                {Object.entries(FULFILLMENT_STATUS_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
            </label>
            <label>
              <span className="sr-only">Pagado o no pagado</span>
              <select value={filters.paid} onChange={(e) => setFilter("paid", e.target.value)} className={internalInputClass}>
                <option value="">Pagado / No pagado</option>
                <option value="true">Pagado</option>
                <option value="false">No pagado</option>
              </select>
            </label>
            <label>
              <span className="sr-only">Fecha desde</span>
              <input type="date" value={filters.date_from} onChange={(e) => setFilter("date_from", e.target.value)} className={internalInputClass} />
            </label>
            <label>
              <span className="sr-only">Fecha hasta</span>
              <input type="date" value={filters.date_to} onChange={(e) => setFilter("date_to", e.target.value)} className={internalInputClass} />
            </label>
            <label className="inline-flex cursor-pointer select-none items-center gap-2 text-sm text-foreground/85">
              <input
                type="checkbox"
                checked={filters.shortfall === "true"}
                onChange={(e) => setFilter("shortfall", e.target.checked ? "true" : "")}
                className="accent-warning"
              />
              Solo con faltante de stock
            </label>
          </div>
        </FilterBar>

        <section className="rounded-xl border border-bd-border bg-surface">
          {error ? <p className="px-5 pt-5 text-sm text-danger" role="alert">{error}</p> : null}
          {loading ? (
            <p className="px-5 py-10 text-center text-sm text-muted">Cargando órdenes…</p>
          ) : (
            <OrdersTable orders={data?.results ?? []} />
          )}
        </section>

        {totalPages > 1 ? (
          <div className="flex flex-col gap-3 border-t border-bd-border pt-4 text-sm text-muted sm:flex-row sm:items-center sm:justify-between">
            <button type="button" onClick={() => setPage((current) => Math.max(1, current - 1))} disabled={page <= 1} className={internalButtonClass}>
              Anterior
            </button>
            <span>Página {page} de {totalPages}</span>
            <button type="button" onClick={() => setPage((current) => Math.min(totalPages, current + 1))} disabled={page >= totalPages} className={internalButtonClass}>
              Siguiente
            </button>
          </div>
        ) : null}
      </div>
    </AdminShell>
  );
}

export default function AdminOrdersPage() {
  return (
    <AccessGuard capability="sales.orders.view" legacyRoles={["inventory", "sales", "admin", "superadmin"]}>
      {(access) => <OrdersContent user={access.user} />}
    </AccessGuard>
  );
}
