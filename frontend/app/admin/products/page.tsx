"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { StaffGuard } from "../components/StaffGuard";
import { AdminShell } from "../components/AdminShell";
import { ProductsTable } from "../components/ProductsTable";
import {
  AdminProduct,
  AdminCategory,
  fetchAdminProducts,
  fetchAdminCategories,
  PaginatedResponse,
} from "../../lib/admin";
import type { AuthUser } from "../../lib/auth";

type Filters = {
  search: string;
  category: string;
  is_active: string;
  stock: string;
};

function ProductsContent({ user }: { user: AuthUser }) {
  const [data, setData] = useState<PaginatedResponse<AdminProduct> | null>(null);
  const [categories, setCategories] = useState<AdminCategory[]>([]);
  const [filters, setFilters] = useState<Filters>({
    search: "",
    category: "",
    is_active: "",
    stock: "",
  });
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const canManage = user.role === "admin" || user.role === "superadmin";

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = {
        search: filters.search || undefined,
        category: filters.category || undefined,
        is_active: (filters.is_active || undefined) as "true" | "false" | undefined,
        stock: (filters.stock || undefined) as "in_stock" | "out_of_stock" | "low_stock" | undefined,
        page,
        page_size: 20,
      };
      const result = await fetchAdminProducts(params);
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error al cargar productos.");
    } finally {
      setLoading(false);
    }
  }, [filters, page]);

  useEffect(() => {
    fetchAdminCategories()
      .then(setCategories)
      .catch(() => {});
  }, []);

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
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold text-foreground">Productos</h1>
            <p className="mt-1 text-sm text-muted">
              {data ? `${data.count} productos en total` : "Cargando…"}
            </p>
          </div>
          {canManage && (
            <Link
              href="/admin/products/new"
              className="shrink-0 px-4 py-2 bg-foreground text-background text-sm font-medium rounded hover:bg-foreground/90 transition-colors"
            >
              + Nuevo producto
            </Link>
          )}
        </div>

        <div className="flex flex-wrap gap-3">
          <input
            type="text"
            placeholder="Buscar por nombre o slug…"
            value={filters.search}
            onChange={(e) => setFilter("search", e.target.value)}
            className="flex-1 min-w-48 bg-surface border border-bd-border rounded px-3 py-2 text-sm text-foreground placeholder-muted focus:outline-none focus:border-bd-border"
          />
          <select
            value={filters.category}
            onChange={(e) => setFilter("category", e.target.value)}
            className="bg-surface border border-bd-border rounded px-3 py-2 text-sm text-foreground/85 focus:outline-none focus:border-bd-border"
          >
            <option value="">Todas las categorías</option>
            {categories.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
          <select
            value={filters.is_active}
            onChange={(e) => setFilter("is_active", e.target.value)}
            className="bg-surface border border-bd-border rounded px-3 py-2 text-sm text-foreground/85 focus:outline-none focus:border-bd-border"
          >
            <option value="">Todos los estados</option>
            <option value="true">Activos</option>
            <option value="false">Inactivos</option>
          </select>
          <select
            value={filters.stock}
            onChange={(e) => setFilter("stock", e.target.value)}
            className="bg-surface border border-bd-border rounded px-3 py-2 text-sm text-foreground/85 focus:outline-none focus:border-bd-border"
          >
            <option value="">Cualquier stock</option>
            <option value="in_stock">En stock</option>
            <option value="out_of_stock">Sin stock</option>
            <option value="low_stock">Stock bajo (≤5)</option>
          </select>
        </div>

        <div className="rounded-xl border border-bd-border bg-surface p-6">
          {error && <p className="text-sm text-red-400 mb-4">{error}</p>}
          {loading ? (
            <p className="text-muted text-sm py-6 text-center">Cargando…</p>
          ) : (
            <ProductsTable
              products={data?.results ?? []}
              currentUser={user}
              onChanged={load}
            />
          )}
        </div>

        {totalPages > 1 && (
          <div className="flex items-center justify-between text-sm text-muted">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1}
              className="px-3 py-1.5 rounded border border-bd-border hover:border-bd-border disabled:opacity-40 disabled:cursor-not-allowed transition"
            >
              ← Anterior
            </button>
            <span>
              Página {page} de {totalPages}
            </span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
              className="px-3 py-1.5 rounded border border-bd-border hover:border-bd-border disabled:opacity-40 disabled:cursor-not-allowed transition"
            >
              Siguiente →
            </button>
          </div>
        )}
      </div>
    </AdminShell>
  );
}

export default function AdminProductsPage() {
  return (
    <StaffGuard>{(user) => <ProductsContent user={user} />}</StaffGuard>
  );
}
