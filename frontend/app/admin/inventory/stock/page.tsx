"use client";

/**
 * Inventario › Stock y disponibilidad — COMMERCE-ADMIN-UX-01.
 *
 * El estante de cada sucursal, producto por producto: lo que «Resumen» sólo
 * muestra en muestras de ocho. Lee `/api/admin/inventory/stock/` (búsqueda,
 * estado y páginas), el mismo endpoint de siempre, y no cuenta nada por su
 * cuenta. Quien vende en mostrador tiene además «Consultar disponibilidad»,
 * con los equipos y las ofertas de proveedores; se enlaza si tiene permiso.
 *
 * `inventory.view`, dentro de las sucursales de quien mira.
 */

import Link from "next/link";
import { useEffect, useState } from "react";
import { AccessGuard } from "../../components/AccessGuard";
import { AdminShell } from "../../components/AdminShell";
import { BranchSelector, ScopeNote } from "../../components/BranchSelector";
import { BranchStockTable, ErrorBox, Spinner } from "../../components/InventoryUi";
import { PageHeader, internalButtonClass, internalInputClass } from "../../components/internal-ui";
import type { InternalAccess } from "../../lib/internal-access";
import { useBranchScope } from "../../lib/use-branch-scope";
import { fetchBranchStock, type BranchStockRow, type InventoryScope } from "../../../lib/inventory";
import type { AuthUser } from "../../../lib/auth";

const PAGE_SIZE = 50;
type StockStatus = "" | "in_stock" | "low_stock" | "out_of_stock";

function StockContent({ user, access }: { user: AuthUser; access: InternalAccess }) {
  const scope = useBranchScope({ preferAggregate: true });
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<StockStatus>("");
  const [page, setPage] = useState(1);
  // The answer belongs to the request that asked for it (no stale branch's rows).
  const [loaded, setLoaded] = useState<{
    key: string; rows: BranchStockRow[]; count: number; scope: InventoryScope;
  } | null>(null);
  const [failure, setFailure] = useState<{ key: string; message: string } | null>(null);
  const requestKey = JSON.stringify([scope.branch, search.trim(), status, page]);

  useEffect(() => {
    if (!scope.ready) return;
    let alive = true;
    fetchBranchStock({
      branch: scope.branch, search: search.trim() || undefined, status: status || undefined, page, page_size: PAGE_SIZE,
    })
      .then((data) => {
        if (alive) setLoaded({ key: requestKey, rows: data.results, count: data.count, scope: data.scope });
      })
      .catch((err) => {
        if (alive) setFailure({ key: requestKey, message: err instanceof Error ? err.message : "No se pudo cargar el stock." });
      });
    return () => { alive = false; };
  }, [scope.ready, scope.branch, search, status, page, requestKey]);
  const current = loaded?.key === requestKey ? loaded : null;
  const rows = current?.rows ?? null;
  const count = current?.count ?? 0;
  const scopeInfo = current?.scope ?? null;
  const error = failure?.key === requestKey ? failure.message : null;

  const pages = Math.max(1, Math.ceil(count / PAGE_SIZE));

  return (
    <AdminShell user={user}>
      <div className="space-y-6">
        <PageHeader
          eyebrow="Inventario"
          title="Stock y disponibilidad"
          description="Cuánto hay de cada producto en cada sucursal. Los equipos con serie se ven uno por uno en «Equipos nuevos» y «Equipos seminuevos»."
          actions={scope.access ? (
            <BranchSelector access={scope.access} value={scope.branch} onChange={(value) => { scope.setBranch(value); setPage(1); }} />
          ) : null}
        />
        {access.can("sales.pos.use") ? (
          <p className="text-sm text-muted">
            ¿Buscas también equipos por IMEI u ofertas de proveedores?{" "}
            <Link href="/admin/sales/availability" className="font-semibold text-foreground underline underline-offset-4">Consultar disponibilidad</Link>
          </p>
        ) : null}
        {scope.error ? <ErrorBox message={scope.error} /> : null}
        {scope.loading ? <Spinner label="Cargando sucursales…" /> : null}
        {!scope.loading && !scope.ready && !scope.error ? (
          <p className="text-sm text-muted">Todavía no tienes ninguna sucursal asignada.</p>
        ) : null}
        {scope.ready ? (
          <section className="space-y-4 rounded-xl border border-bd-border bg-surface p-5">
            <div className="flex flex-col gap-3 md:flex-row md:items-end">
              <div className="flex-1">
                <label htmlFor="stock-search" className="mb-1.5 block text-xs text-muted">Buscar producto</label>
                <input id="stock-search" value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); }} className={internalInputClass} />
              </div>
              <div className="md:w-56">
                <label htmlFor="stock-status" className="mb-1.5 block text-xs text-muted">Estado</label>
                <select id="stock-status" value={status} onChange={(e) => { setStatus(e.target.value as StockStatus); setPage(1); }} className={internalInputClass}>
                  <option value="">Todos</option>
                  <option value="in_stock">Con stock</option>
                  <option value="low_stock">Stock bajo</option>
                  <option value="out_of_stock">Sin stock</option>
                </select>
              </div>
            </div>
            {scopeInfo ? <ScopeNote scope={scopeInfo} /> : null}
            {error ? <ErrorBox message={error} /> : null}
            {rows === null && !error ? <Spinner label="Cargando stock…" /> : null}
            {rows ? <BranchStockTable rows={rows} emptyMessage="Ningún producto coincide." showSuggested /> : null}
            {pages > 1 ? (
              <div className="flex items-center justify-between text-sm">
                <button type="button" disabled={page <= 1} onClick={() => setPage((p) => p - 1)} className={internalButtonClass}>Anterior</button>
                <span className="text-muted">Página {page} de {pages}</span>
                <button type="button" disabled={page >= pages} onClick={() => setPage((p) => p + 1)} className={internalButtonClass}>Siguiente</button>
              </div>
            ) : null}
          </section>
        ) : null}
      </div>
    </AdminShell>
  );
}

export default function InventoryStockPage() {
  return (
    <AccessGuard capability="inventory.view" legacyRoles={["inventory", "admin", "superadmin"]}>
      {(access) => <StockContent user={access.user} access={access} />}
    </AccessGuard>
  );
}
