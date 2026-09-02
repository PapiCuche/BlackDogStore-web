"use client";

/**
 * H2 — the technical-service console.
 *
 * The backend has had this surface since M8 and Mobile has consumed it since
 * then; the Web had no screen at all, and its own module registry called the
 * whole group "pending" while the capabilities behind it were ACTIVE. This is
 * that gap, closed against the SAME endpoints Mobile calls.
 *
 * WHAT DECIDES WHAT YOU SEE. Capabilities from the internal dashboard, and
 * nothing else. No `role === "technician"`, no `isAdmin`. The server re-checks
 * every request, so a 403 here is a normal outcome — the permission may have
 * been revoked between drawing a button and pressing it — and the answer is to
 * reload the context rather than to log anybody out.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { AdminShell } from "../components/AdminShell";
import { InternalControlGuard, type InternalContext } from "../components/InternalControlGuard";
import {
  CAP_ORDERS_VIEW,
  ServiceApiError,
  fetchServiceContext,
  fetchServiceOrders,
  type ServiceContext,
  type ServiceOrderRow,
} from "../../lib/service-console";

function Panel({ children }: { children: React.ReactNode }) {
  return (
    <section className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6">
      {children}
    </section>
  );
}

function StatusPill({ label }: { label: string }) {
  return (
    <span className="rounded-full border border-white/[0.12] px-2.5 py-1 text-[11px] text-white/70">
      {label}
    </span>
  );
}

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
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!slug || !mayView) return;
    setLoading(true);
    setError(null);
    try {
      const [ctxData, pageData] = await Promise.all([
        fetchServiceContext(slug),
        fetchServiceOrders(slug, { status, branch_id: branchId, search, page }),
      ]);
      setContext(ctxData);
      setRows(pageData.results);
      setCount(pageData.count);
    } catch (err) {
      if (err instanceof ServiceApiError && err.isForbidden) {
        // The capability went away while this screen was open. Re-read the
        // context rather than guessing: the server is the one that knows.
        ctx.reload();
      }
      setError(err instanceof Error ? err.message : "No se pudo cargar.");
    } finally {
      setLoading(false);
    }
  }, [slug, mayView, status, branchId, search, page, ctx]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!slug) {
    return (
      <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
        <Panel>
          <p className="text-sm text-white/60">
            Selecciona una empresa para ver sus órdenes de servicio.
          </p>
        </Panel>
      </AdminShell>
    );
  }

  if (!mayView) {
    return (
      <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
        <Panel>
          <h1 className="text-lg font-semibold">Servicio técnico</h1>
          <p className="mt-2 text-sm text-white/60">
            Tu cuenta no tiene permiso para ver las órdenes de servicio de esta
            empresa.
          </p>
        </Panel>
      </AdminShell>
    );
  }

  return (
    <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
      <div className="space-y-6">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold">Órdenes de servicio</h1>
            <p className="mt-1 text-sm text-white/50">
              {count} orden(es) en el alcance que tu cuenta alcanza.
            </p>
          </div>
        </div>

        <Panel>
          <div className="grid gap-3 md:grid-cols-4">
            <label className="text-xs text-white/50">
              Estado
              {/* The list comes from the SERVER, per tenant. A company that
                  renamed "Recibido" sees its own word here. */}
              <select
                value={status}
                onChange={(e) => { setStatus(e.target.value); setPage(1); }}
                className="mt-1 w-full rounded-lg border border-white/[0.08] bg-black/40 px-3 py-2 text-sm text-white"
              >
                <option value="">Todos</option>
                {(context?.statuses ?? []).map((s) => (
                  <option key={s.code} value={s.code}>{s.label}</option>
                ))}
              </select>
            </label>

            <label className="text-xs text-white/50">
              Sucursal
              <select
                value={branchId ?? ""}
                onChange={(e) => {
                  setBranchId(e.target.value ? Number(e.target.value) : null);
                  setPage(1);
                }}
                className="mt-1 w-full rounded-lg border border-white/[0.08] bg-black/40 px-3 py-2 text-sm text-white"
              >
                {/* Only the branches this member reaches — the server decides
                    that, and an id outside it is not found rather than
                    refused. */}
                <option value="">Todas las que alcanzo</option>
                {(context?.available_branches ?? []).map((b) => (
                  <option key={b.id} value={b.id}>{b.name}</option>
                ))}
              </select>
            </label>

            <label className="text-xs text-white/50 md:col-span-2">
              Buscar
              <input
                value={search}
                onChange={(e) => { setSearch(e.target.value); setPage(1); }}
                placeholder="Número, cliente o equipo"
                className="mt-1 w-full rounded-lg border border-white/[0.08] bg-black/40 px-3 py-2 text-sm text-white"
              />
            </label>
          </div>
        </Panel>

        {error ? (
          <Panel>
            <p className="text-sm text-rose-300">{error}</p>
          </Panel>
        ) : null}

        <Panel>
          {loading ? (
            <p className="text-sm text-white/50">Cargando…</p>
          ) : rows.length === 0 ? (
            <p className="text-sm text-white/50">
              No hay órdenes que coincidan con ese filtro.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-xs uppercase tracking-wide text-white/40">
                  <tr>
                    <th className="py-2 pr-4">Número</th>
                    <th className="py-2 pr-4">Cliente</th>
                    <th className="py-2 pr-4">Equipo</th>
                    <th className="py-2 pr-4">Sucursal</th>
                    <th className="py-2 pr-4">Técnico</th>
                    <th className="py-2 pr-4">Estado</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.id} className="border-t border-white/[0.06]">
                      <td className="py-3 pr-4">
                        <Link
                          href={`/admin/service/orders/${row.id}`}
                          className="font-medium text-white hover:underline"
                        >
                          {row.number}
                        </Link>
                      </td>
                      <td className="py-3 pr-4 text-white/70">{row.customer_name}</td>
                      <td className="py-3 pr-4 text-white/70">{row.device_summary}</td>
                      <td className="py-3 pr-4 text-white/50">{row.branch_name}</td>
                      <td className="py-3 pr-4 text-white/50">
                        {row.technician_name || "—"}
                      </td>
                      <td className="py-3 pr-4">
                        <StatusPill label={row.status_label} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {count > rows.length ? (
            <div className="mt-4 flex items-center gap-2">
              <button
                type="button"
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                className="rounded-lg border border-white/[0.08] px-3 py-1.5 text-xs disabled:opacity-30"
              >
                Anterior
              </button>
              <span className="text-xs text-white/40">Página {page}</span>
              <button
                type="button"
                disabled={rows.length === 0}
                onClick={() => setPage((p) => p + 1)}
                className="rounded-lg border border-white/[0.08] px-3 py-1.5 text-xs disabled:opacity-30"
              >
                Siguiente
              </button>
            </div>
          ) : null}
        </Panel>
      </div>
    </AdminShell>
  );
}

export default function ServiceOrdersPage() {
  return (
    <InternalControlGuard>{(ctx) => <ServiceOrdersContent ctx={ctx} />}</InternalControlGuard>
  );
}
