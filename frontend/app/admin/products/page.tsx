"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { StaffGuard } from "../components/StaffGuard";
import { AdminShell } from "../components/AdminShell";
import { ProductsTable } from "../components/ProductsTable";
import {
  FilterBar,
  PageHeader,
  internalButtonClass,
  internalInputClass,
  internalPrimaryButtonClass,
} from "../components/internal-ui";
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
  const [searchInput, setSearchInput] = useState("");
  const [categoryError, setCategoryError] = useState(false);
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
      setData(await fetchAdminProducts(params));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error al cargar productos.");
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
    fetchAdminCategories()
      .then((result) => {
        setCategories(result);
        setCategoryError(false);
      })
      .catch(() => {
        setCategories([]);
        setCategoryError(true);
      });
  }, []);

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
          eyebrow="Catálogo"
          title="Productos"
          description={data ? `${data.count} productos en total. Filtra por categoría, publicación y disponibilidad.` : "Gestiona el catálogo de esta empresa."}
          actions={
            canManage ? (
              <Link href="/admin/products/new" className={internalPrimaryButtonClass}>
                Nuevo producto
              </Link>
            ) : null
          }
        />

        <FilterBar>
          <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_repeat(3,auto)]">
            <label>
              <span className="sr-only">Buscar productos</span>
              <input
                type="search"
                placeholder="Buscar por nombre o slug…"
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                className={internalInputClass}
              />
            </label>
            <label>
              <span className="sr-only">Categoría</span>
              <select
                value={filters.category}
                onChange={(e) => setFilter("category", e.target.value)}
                className={internalInputClass}
              >
                <option value="">Todas las categorías</option>
                {categories.map((category) => (
                  <option key={category.id} value={category.id}>{category.name}</option>
                ))}
              </select>
            </label>
            <label>
              <span className="sr-only">Estado del producto</span>
              <select
                value={filters.is_active}
                onChange={(e) => setFilter("is_active", e.target.value)}
                className={internalInputClass}
              >
                <option value="">Todos los estados</option>
                <option value="true">Activos</option>
                <option value="false">Inactivos</option>
              </select>
            </label>
            <label>
              <span className="sr-only">Estado de stock</span>
              <select
                value={filters.stock}
                onChange={(e) => setFilter("stock", e.target.value)}
                className={internalInputClass}
              >
                <option value="">Cualquier stock</option>
                <option value="in_stock">En stock</option>
                <option value="out_of_stock">Sin stock</option>
                <option value="low_stock">Stock bajo (≤5)</option>
              </select>
            </label>
          </div>
          {categoryError ? (
            <p className="mt-3 text-xs text-amber-200/80">
              No se pudieron cargar las categorías. Los demás filtros siguen disponibles.
            </p>
          ) : null}
        </FilterBar>

        <section className="rounded-2xl border border-bd-border bg-surface p-5 sm:p-6">
          {error ? <p className="mb-4 text-sm text-red-300" role="alert">{error}</p> : null}
          {loading ? (
            <p className="py-8 text-center text-sm text-muted">Cargando productos…</p>
          ) : (
            <ProductsTable
              products={data?.results ?? []}
              currentUser={user}
              onChanged={load}
            />
          )}
        </section>

        {totalPages > 1 ? (
          <div className="flex flex-col gap-3 border-t border-bd-border pt-4 text-sm text-muted sm:flex-row sm:items-center sm:justify-between">
            <button
              type="button"
              onClick={() => setPage((current) => Math.max(1, current - 1))}
              disabled={page <= 1}
              className={internalButtonClass}
            >
              Anterior
            </button>
            <span>Página {page} de {totalPages}</span>
            <button
              type="button"
              onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
              disabled={page >= totalPages}
              className={internalButtonClass}
            >
              Siguiente
            </button>
          </div>
        ) : null}
      </div>
    </AdminShell>
  );
}

export default function AdminProductsPage() {
  return <StaffGuard>{(user) => <ProductsContent user={user} />}</StaffGuard>;
}
