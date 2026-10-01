"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { AdminShell } from "../components/AdminShell";
import { InternalControlGuard, type InternalContext } from "../components/InternalControlGuard";
import {
  FilterBar,
  PageHeader,
  TableShell,
  internalButtonClass,
  internalInputClass,
} from "../components/internal-ui";
import { Panel, Pill } from "./components/ServiceUi";
import {
  CAP_ORDERS_VIEW,
  ServiceApiError,
  fetchServiceContext,
  fetchServiceOrders,
  type ServiceContext,
  type ServiceOrderRow,
} from "../../lib/service-console";

function ServiceOrdersContent({ ctx }: { ctx: InternalContext }) {
  const slug = ctx.dashboard?.company?.slug ?? null;
  const capabilities = ctx.dashboard?.access.capabilities ?? [];
  const mayView = capabilities.includes(CAP_ORDERS_VIEW);

  const [context, setContext] = useState<ServiceContext | null>(null);
  const [rows, setRows] = useState<ServiceOrderRow[]>([]);
  const [count, setCount] = useState(0);
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const [branchId, setBranchId] = useState<number | null>(null);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [mine, setMine] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSearch(searchInput);
      setPage(1);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  const load = useCallback(async () => {
    if (!slug || !mayView) return;
    setLoading(true);
    setError(null);
    try {
      const [ctxData, pageData] = await Promise.all([
        fetchServiceContext(slug),
        fetchServiceOrders(slug, { status, branch_id: branchId, search, page, mine }),
      ]);
      setContext(ctxData);
      setRows(pageData.results);
      setCount(pageData.count);
    } catch (err) {
      if (err instanceof ServiceApiError && err.isForbidden) {
        ctx.reload();
      }
      setError(err instanceof Error ? err.message : "No se pudo cargar.");
    } finally {
      setLoading(false);
    }
  }, [slug, mayView, status, branchId, search, page, mine, ctx]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!slug) {
    return (
      <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
        <Panel>
          <p className="text-sm text-muted">
            Selecciona una empresa para ver sus órdenes de servicio.
          </p>
        </Panel>
      </AdminShell>
    );
  }

  if (!mayView) {
    return (
      <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
        <Panel title="Servicio técnico">
          <p className="text-sm text-muted">
            Tu cuenta no tiene permiso para ver las órdenes de servicio de esta empresa.
          </p>
        </Panel>
      </AdminShell>
    );
  }

  return (
    <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
      <div className="space-y-6">
        <PageHeader
          eyebrow="Taller"
          title="Órdenes de servicio"
          description={loading ? "Cargando el alcance actual del taller…" : `${count} orden${count === 1 ? "" : "es"} dentro del alcance actual de tu cuenta. La vista abre en tus reparaciones asignadas y permite pasar al taller completo cuando tu acceso lo permite.`}
        />

        <FilterBar>
          <div className="mb-4 flex flex-wrap items-center gap-2">
            {([
              { value: true, label: "Mis reparaciones" },
              { value: false, label: "Todo el taller" },
            ] as const).map((option) => (
              <button
                key={String(option.value)}
                type="button"
                onClick={() => {
                  setMine(option.value);
                  setPage(1);
                }}
                aria-pressed={mine === option.value}
                className={`rounded-lg border px-3.5 py-2 text-sm font-semibold transition ${
                  mine === option.value
                    ? "border-foreground/25 bg-foreground/[0.08] text-foreground"
                    : "border-bd-border text-muted hover:border-foreground/20 hover:text-foreground"
                }`}
              >
                {option.label}
              </button>
            ))}
            {mine ? (
              <span className="text-xs text-muted">
                Órdenes donde figuras como técnico asignado.
              </span>
            ) : null}
          </div>

          <div className="grid gap-3 md:grid-cols-4">
            <label className="text-xs font-semibold text-muted">
              Estado
              <select
                value={status}
                onChange={(e) => {
                  setStatus(e.target.value);
                  setPage(1);
                }}
                className={`mt-1.5 ${internalInputClass}`}
              >
                <option value="">Todos</option>
                {(context?.statuses ?? []).map((item) => (
                  <option key={item.code} value={item.code}>{item.label}</option>
                ))}
              </select>
            </label>

            <label className="text-xs font-semibold text-muted">
              Sucursal
              <select
                value={branchId ?? ""}
                onChange={(e) => {
                  setBranchId(e.target.value ? Number(e.target.value) : null);
                  setPage(1);
                }}
                className={`mt-1.5 ${internalInputClass}`}
              >
                <option value="">Todas las que alcanzo</option>
                {(context?.available_branches ?? []).map((branch) => (
                  <option key={branch.id} value={branch.id}>{branch.name}</option>
                ))}
              </select>
            </label>

            <label className="text-xs font-semibold text-muted md:col-span-2">
              Buscar
              <input
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                placeholder="Número, cliente o equipo"
                className={`mt-1.5 ${internalInputClass}`}
              />
            </label>
          </div>
        </FilterBar>

        {error ? (
          <div className="rounded-xl border border-red-500/25 bg-red-500/10 px-5 py-4 text-sm text-red-200" role="alert">
            {error}
          </div>
        ) : null}

        {loading ? (
          <div className="rounded-xl border border-bd-border bg-surface px-5 py-8 text-sm text-muted">
            Cargando órdenes…
          </div>
        ) : rows.length === 0 ? (
          <div className="rounded-xl border border-dashed border-bd-border px-5 py-10 text-center text-sm text-muted">
            {mine
              ? "No tienes órdenes asignadas con ese filtro. Prueba «Todo el taller»."
              : "No hay órdenes que coincidan con ese filtro."}
          </div>
        ) : (
          <TableShell>
            <table className="w-full min-w-[58rem] text-left text-sm">
              <thead className="border-b border-bd-border text-[11px] uppercase tracking-[0.1em] text-muted">
                <tr>
                  <th className="px-4 py-3 font-semibold">Número</th>
                  <th className="px-4 py-3 font-semibold">Cliente</th>
                  <th className="px-4 py-3 font-semibold">Equipo</th>
                  <th className="px-4 py-3 font-semibold">Sucursal</th>
                  <th className="px-4 py-3 font-semibold">Técnico</th>
                  <th className="px-4 py-3 font-semibold">Estado</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id} className="border-b border-bd-border/70 last:border-0 hover:bg-foreground/[0.025]">
                    <td className="px-4 py-3">
                      <Link
                        href={`/admin/service/orders/${row.id}`}
                        className="font-semibold text-foreground transition hover:underline"
                      >
                        {row.number}
                      </Link>
                    </td>
                    <td className="px-4 py-3 text-foreground/80">{row.customer_name}</td>
                    <td className="px-4 py-3 text-foreground/80">{row.device_summary}</td>
                    <td className="px-4 py-3 text-muted">{row.branch_name}</td>
                    <td className="px-4 py-3 text-muted">{row.technician_name || "—"}</td>
                    <td className="px-4 py-3">
                      <Pill label={row.status_label} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableShell>
        )}

        {count > rows.length ? (
          <div className="flex flex-col gap-3 border-t border-bd-border pt-4 text-sm text-muted sm:flex-row sm:items-center sm:justify-between">
            <span>Página {page}</span>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                className={internalButtonClass}
              >
                Anterior
              </button>
              <button
                type="button"
                disabled={rows.length === 0}
                onClick={() => setPage((p) => p + 1)}
                className={internalButtonClass}
              >
                Siguiente
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </AdminShell>
  );
}

export default function ServiceOrdersPage() {
  return (
    <InternalControlGuard>{(ctx) => <ServiceOrdersContent ctx={ctx} />}</InternalControlGuard>
  );
}
