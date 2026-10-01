"use client";

/**
 * H2 — the technical-service console, one queue at a time (SVC-NAV-01).
 *
 * The backend has had this surface since M8 and Mobile has consumed it since
 * then. The Web showed it as ONE screen behind six sidebar entries; each stage
 * of the workshop now has its own route, and they all draw this component with
 * a different queue from `../queues`.
 *
 * WHAT DECIDES WHAT YOU SEE. Capabilities from the internal dashboard, and
 * nothing else. No `role === "technician"`, no `isAdmin`. The server re-checks
 * every request, so a 403 here is a normal outcome — the permission may have
 * been revoked between drawing a button and pressing it — and the answer is to
 * reload the context rather than to log anybody out.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { AdminShell } from "../../components/AdminShell";
import { InternalControlGuard, type InternalContext } from "../../components/InternalControlGuard";
import {
  FilterBar,
  PageHeader,
  TableShell,
  internalButtonClass,
  internalInputClass,
} from "../../components/internal-ui";
import {
  CAP_ORDERS_VIEW,
  ServiceApiError,
  fetchServiceContext,
  fetchServiceOrders,
  type ServiceContext,
  type ServiceOrderRow,
} from "../../../lib/service-console";
import { SERVICE_QUEUES, type ServiceQueueConfig, type ServiceQueueKey } from "../queues";
import { ServiceIntake } from "./ServiceIntake";
import { Panel, Pill } from "./ServiceUi";

const INTAKE_CAPABILITIES = ["service.orders.create", "service.customers.view", "service.devices.view"];

const TOGGLE = "rounded-lg border px-3.5 py-2 text-sm font-semibold transition";
const TOGGLE_ON = "border-foreground/25 bg-foreground/[0.08] text-foreground";
const TOGGLE_OFF = "border-bd-border text-muted hover:border-foreground/20 hover:text-foreground";

function ServiceQueueContent({ ctx, queue }: { ctx: InternalContext; queue: ServiceQueueConfig }) {
  const router = useRouter();
  const slug = ctx.dashboard?.company?.slug ?? null;
  const capabilities = ctx.dashboard?.access.capabilities ?? [];
  const mayView = capabilities.includes(CAP_ORDERS_VIEW);
  const mayIntake = queue.intake !== "none"
    && INTAKE_CAPABILITIES.every((cap) => capabilities.includes(cap));

  const [intake, setIntake] = useState(queue.intake === "open");
  const [context, setContext] = useState<ServiceContext | null>(null);
  const [rows, setRows] = useState<ServiceOrderRow[]>([]);
  const [count, setCount] = useState(0);
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const [history, setHistory] = useState(false);
  const [branchId, setBranchId] = useState<number | null>(null);
  // What is typed, and what is asked of the server once the typing pauses.
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  // Technical staff start with assigned repairs; reception sees the authorized
  // workshop. Both filters remain subject to server-side tenant/branch scope.
  const [mine, setMine] = useState(
    queue.mine === "technician" && capabilities.includes("service.repair.manage"),
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // The codes this screen may list right now. The whole workshop has no limit.
  const scope = history && queue.history ? queue.history.statuses : queue.statuses;
  // One code when the operator picked it; otherwise the whole queue. The server
  // filters; this only says which of ITS codes are being asked for.
  const statusQuery = status || (scope ? scope.join(",") : "");

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
        fetchServiceOrders(slug, { status: statusQuery, branch_id: branchId, search, page, mine }),
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
  }, [slug, mayView, statusQuery, branchId, search, page, mine, ctx]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!slug) {
    return (
      <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
        <PageHeader eyebrow="Taller" title={queue.title} />
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
        <PageHeader eyebrow="Taller" title={queue.title} />
        <Panel>
          <p className="text-sm text-muted">
            Tu cuenta no tiene permiso para ver las órdenes de servicio de esta empresa.
          </p>
        </Panel>
      </AdminShell>
    );
  }

  // The list comes from the SERVER, per tenant. A company that renamed
  // "Recibido" sees its own word here; a queue only narrows which ones appear.
  const statusOptions = (context?.statuses ?? []).filter(
    (s) => !scope || scope.includes(s.code),
  );

  return (
    <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
      <div className="space-y-6">
        <PageHeader
          eyebrow="Taller"
          title={queue.title}
          description={`${queue.description} ${loading
            ? "Cargando el alcance actual del taller…"
            : `${count} orden${count === 1 ? "" : "es"} dentro del alcance actual de tu cuenta.`}`}
          actions={mayIntake && queue.intake === "toggle" ? (
            <button type="button" className={internalButtonClass}
              onClick={() => setIntake(!intake)}>{intake ? "Cerrar recepción" : "Nueva orden"}</button>
          ) : null}
        />

        {intake && mayIntake && context ? <ServiceIntake key={slug} slug={slug} context={context}
          may={(cap) => capabilities.includes(cap)} onCreated={(id) => router.push(`/admin/service/orders/${id}`)} /> : null}

        <FilterBar>
          {/* M12A — "Mis reparaciones" primero.
              El técnico entra a trabajar lo suyo; la vista del taller completo
              sigue a un clic, porque supervisar también es parte del trabajo. */}
          <div className="mb-4 flex flex-wrap items-center gap-2">
            {([
              { value: true, label: "Mis reparaciones" },
              { value: false, label: "Todo el taller" },
            ] as const).map((option) => (
              <button
                key={String(option.value)}
                type="button"
                aria-pressed={mine === option.value}
                onClick={() => { setMine(option.value); setPage(1); }}
                className={`${TOGGLE} ${mine === option.value ? TOGGLE_ON : TOGGLE_OFF}`}
              >
                {option.label}
              </button>
            ))}
            {queue.history ? (
              <button
                type="button"
                aria-pressed={history}
                onClick={() => { setHistory(!history); setStatus(""); setPage(1); }}
                className={`${TOGGLE} ${history ? TOGGLE_ON : TOGGLE_OFF}`}
              >
                {queue.history.label}
              </button>
            ) : null}
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
                onChange={(e) => { setStatus(e.target.value); setPage(1); }}
                className={`mt-1.5 ${internalInputClass}`}
              >
                <option value="">Todos</option>
                {statusOptions.map((s) => (
                  <option key={s.code} value={s.code}>{s.label}</option>
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
                {/* Only the branches this member reaches — the server decides
                    that, and an id outside it is not found rather than
                    refused. */}
                <option value="">Todas las que alcanzo</option>
                {(context?.available_branches ?? []).map((b) => (
                  <option key={b.id} value={b.id}>{b.name}</option>
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
          <div className="rounded-xl border border-danger-border bg-danger-surface px-5 py-4 text-sm text-danger" role="alert">
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

export function ServiceQueuePage({ queue }: { queue: ServiceQueueKey }) {
  return (
    <InternalControlGuard>
      {(ctx) => <ServiceQueueContent ctx={ctx} queue={SERVICE_QUEUES[queue]} />}
    </InternalControlGuard>
  );
}
