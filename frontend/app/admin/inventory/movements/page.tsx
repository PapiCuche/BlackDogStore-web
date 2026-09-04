"use client";

// Phase 6.0 — full Kardex with filters plus the manual entry/exit form.
// Phase 2D — everything is scoped to a branch.
//
// The manual form writes to ONE branch, so it never offers "todas": units are
// added to a place, not to a set. The history may aggregate, and then the branch
// column says which shelf each line moved.

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { AdminShell } from "../../components/AdminShell";
import { StaffGuard } from "../../components/StaffGuard";
import { StockMovementForm } from "../../components/StockMovementForm";
import {
  EmptyBox,
  ErrorBox,
  Panel,
  Spinner,
  TableWrap,
  Td,
  Th,
  MovementBadge,
  SignedQty,
  formatDateTime,
  movementReference,
} from "../../components/InventoryUi";
import {
  MOVEMENT_TYPE_LABELS,
  fetchStockMovements,
  type InventoryScope,
  type MovementType,
  type StockMovement,
} from "../../../lib/inventory";
import { fetchAdminProducts, type AdminProduct } from "../../../lib/admin";
import { BranchSelector, ScopeNote } from "../../components/BranchSelector";
import { useBranchScope } from "../../lib/use-branch-scope";
import { canManageInventory, type AuthUser } from "../../../lib/auth";

type Filters = {
  product: string;
  movement_type: string;
  date_from: string;
  date_to: string;
  search: string;
};

const EMPTY_FILTERS: Filters = {
  product: "",
  movement_type: "",
  date_from: "",
  date_to: "",
  search: "",
};

function MovementsContent({ user }: { user: AuthUser }) {
  const scope = useBranchScope({ preferAggregate: true });
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [applied, setApplied] = useState<Filters>(EMPTY_FILTERS);
  const [page, setPage] = useState(1);

  const [movements, setMovements] = useState<StockMovement[]>([]);
  const [total, setTotal] = useState(0);
  const [pageSize, setPageSize] = useState(25);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [products, setProducts] = useState<AdminProduct[]>([]);
  const [reloadKey, setReloadKey] = useState(0);
  const [resultScope, setResultScope] = useState<InventoryScope | null>(null);

  const mayMoveStock = canManageInventory(user);
  const branch = scope.branch;

  const loadMovements = useCallback(async () => {
    return fetchStockMovements({
      branch,
      page,
      page_size: 25,
      product: applied.product ? Number(applied.product) : undefined,
      movement_type: applied.movement_type || undefined,
      date_from: applied.date_from || undefined,
      date_to: applied.date_to || undefined,
      search: applied.search || undefined,
    });
  }, [applied, page, branch]);

  useEffect(() => {
    if (!scope.ready) return;
    let cancelled = false;
    void (async () => {
      try {
        const data = await loadMovements();
        if (cancelled) return;
        setMovements(data.results);
        setTotal(data.count);
        setPageSize(data.page_size);
        setResultScope(data.scope);
        setError(null);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "No se pudieron cargar los movimientos.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadMovements, reloadKey, scope.ready]);

  // Product list feeds both the filter dropdown and the manual movement form.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const data = await fetchAdminProducts({ page_size: 100 });
        if (!cancelled) setProducts(data.results);
      } catch {
        /* the movements table still works without the product list */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  function applyFilters(e: React.FormEvent) {
    e.preventDefault();
    setPage(1);
    setLoading(true);
    setApplied(filters);
  }

  function resetFilters() {
    setFilters(EMPTY_FILTERS);
    setApplied(EMPTY_FILTERS);
    setPage(1);
    setLoading(true);
  }

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const fieldClass =
    "w-full rounded-lg border border-bd-border bg-background/40 px-3 py-2 text-sm text-foreground outline-none transition focus:border-bd-border";
  const labelClass = "mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-muted";

  return (
    <AdminShell user={user}>
      <div className="space-y-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold text-foreground">Movimientos de inventario</h1>
            <p className="mt-1 text-sm text-muted">
              Kardex completo. Cada línea registra el stock antes y después.
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

        <ScopeNote scope={resultScope} />
        {scope.error ? <ErrorBox message={scope.error} /> : null}

        {mayMoveStock ? (
          <Panel
            title="Registrar entrada o salida"
            description="Todo movimiento manual queda con responsable, motivo y auditoría."
          >
            <StockMovementForm
              products={products.map((p) => ({
                id: p.id,
                name: p.name,
                inventory: p.inventory,
              }))}
              branches={scope.access?.results ?? []}
              defaultBranch={
                typeof scope.branch === "number"
                  ? scope.branch
                  : (scope.access?.default_branch?.id ?? null)
              }
              onCreated={() => {
                setLoading(true);
                setPage(1);
                setReloadKey((k) => k + 1);
              }}
            />
          </Panel>
        ) : (
          <div className="rounded-lg border border-bd-border bg-surface px-4 py-3">
            <p className="text-sm text-muted">
              Tu rol permite consultar el Kardex pero no modificar stock.
            </p>
          </div>
        )}

        <Panel title="Historial" description={`${total} movimiento(s)`}>
          <form onSubmit={applyFilters} className="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <div>
              <label className={labelClass} htmlFor="f-product">Producto</label>
              <select
                id="f-product"
                className={fieldClass}
                value={filters.product}
                onChange={(e) => setFilters((f) => ({ ...f, product: e.target.value }))}
              >
                <option value="">Todos</option>
                {products.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </div>

            <div>
              <label className={labelClass} htmlFor="f-type">Tipo</label>
              <select
                id="f-type"
                className={fieldClass}
                value={filters.movement_type}
                onChange={(e) => setFilters((f) => ({ ...f, movement_type: e.target.value }))}
              >
                <option value="">Todos</option>
                {(Object.keys(MOVEMENT_TYPE_LABELS) as MovementType[]).map((key) => (
                  <option key={key} value={key}>{MOVEMENT_TYPE_LABELS[key]}</option>
                ))}
              </select>
            </div>

            <div>
              <label className={labelClass} htmlFor="f-from">Desde</label>
              <input
                id="f-from"
                type="date"
                className={fieldClass}
                value={filters.date_from}
                onChange={(e) => setFilters((f) => ({ ...f, date_from: e.target.value }))}
              />
            </div>

            <div>
              <label className={labelClass} htmlFor="f-to">Hasta</label>
              <input
                id="f-to"
                type="date"
                className={fieldClass}
                value={filters.date_to}
                onChange={(e) => setFilters((f) => ({ ...f, date_to: e.target.value }))}
              />
            </div>

            <div>
              <label className={labelClass} htmlFor="f-search">Búsqueda</label>
              <input
                id="f-search"
                type="text"
                placeholder="Nombre del producto"
                className={fieldClass}
                value={filters.search}
                onChange={(e) => setFilters((f) => ({ ...f, search: e.target.value }))}
              />
            </div>

            <div className="flex items-end gap-2 sm:col-span-2 lg:col-span-5">
              <button
                type="submit"
                className="rounded-lg bg-foreground px-4 py-2 text-sm font-semibold text-background transition hover:bg-foreground/90"
              >
                Filtrar
              </button>
              <button
                type="button"
                onClick={resetFilters}
                className="rounded-lg border border-bd-border px-4 py-2 text-sm text-foreground/85 transition hover:border-bd-border hover:text-foreground"
              >
                Limpiar
              </button>
            </div>
          </form>

          {loading ? <Spinner label="Cargando movimientos…" /> : null}
          {error ? <ErrorBox message={error} /> : null}

          {!loading && !error && movements.length === 0 ? (
            <EmptyBox message="No hay movimientos que coincidan con los filtros." />
          ) : null}

          {!loading && !error && movements.length > 0 ? (
            <>
              <TableWrap>
                <thead>
                  <tr className="border-b border-bd-border">
                    <Th>Fecha</Th>
                    <Th>Sucursal</Th>
                    <Th>Producto</Th>
                    <Th>Tipo</Th>
                    <Th right>Cant.</Th>
                    <Th right>Antes</Th>
                    <Th right>Después</Th>
                    <Th>Motivo</Th>
                    <Th>Responsable</Th>
                    <Th>Referencia</Th>
                  </tr>
                </thead>
                <tbody>
                  {movements.map((m) => (
                    <tr key={m.id} className="border-b border-bd-border">
                      <Td muted>{formatDateTime(m.created_at)}</Td>
                      <Td muted>{m.branch_name}</Td>
                      <Td>
                        <Link
                          href={`/admin/products/${m.product}/stock-card`}
                          className="transition hover:text-foreground"
                        >
                          {m.product_name}
                        </Link>
                      </Td>
                      <Td><MovementBadge movement={m} /></Td>
                      <Td right><SignedQty movement={m} /></Td>
                      <Td right muted>{m.stock_before}</Td>
                      <Td right>{m.stock_after}</Td>
                      <Td muted>{m.reason || "—"}</Td>
                      <Td muted>{m.actor_username ?? "Sistema"}</Td>
                      <Td muted>{movementReference(m)}</Td>
                    </tr>
                  ))}
                </tbody>
              </TableWrap>

              <div className="mt-5 flex items-center justify-between">
                <p className="text-xs text-muted">
                  Página {page} de {totalPages}
                </p>
                <div className="flex gap-2">
                  <button
                    type="button"
                    disabled={page <= 1}
                    onClick={() => {
                      setLoading(true);
                      setPage((p) => Math.max(1, p - 1));
                    }}
                    className="rounded-lg border border-bd-border px-3 py-1.5 text-sm text-foreground/85 transition hover:border-bd-border hover:text-foreground disabled:opacity-30"
                  >
                    Anterior
                  </button>
                  <button
                    type="button"
                    disabled={page >= totalPages}
                    onClick={() => {
                      setLoading(true);
                      setPage((p) => Math.min(totalPages, p + 1));
                    }}
                    className="rounded-lg border border-bd-border px-3 py-1.5 text-sm text-foreground/85 transition hover:border-bd-border hover:text-foreground disabled:opacity-30"
                  >
                    Siguiente
                  </button>
                </div>
              </div>
            </>
          ) : null}
        </Panel>
      </div>
    </AdminShell>
  );
}

export default function MovementsPage() {
  return <StaffGuard>{(user) => <MovementsContent user={user} />}</StaffGuard>;
}
