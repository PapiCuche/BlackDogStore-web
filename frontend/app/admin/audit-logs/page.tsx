"use client";

import { useCallback, useEffect, useState } from "react";
import { AccessGuard } from "../components/AccessGuard";
import { AdminShell } from "../components/AdminShell";
import { AuditLogTable } from "../components/AuditLogTable";
import {
  FilterBar,
  PageHeader,
  internalButtonClass,
  internalInputClass,
} from "../components/internal-ui";
import {
  fetchAuditLogs,
  type AuditLogEntry,
  type PaginatedResponse,
} from "../../lib/admin";
import type { AuthUser } from "../../lib/auth";

function Pagination({
  page,
  pageSize,
  count,
  onPage,
}: {
  page: number;
  pageSize: number;
  count: number;
  onPage: (p: number) => void;
}) {
  const totalPages = Math.max(1, Math.ceil(count / pageSize));
  if (totalPages <= 1) return null;

  return (
    <div className="flex flex-col gap-3 border-t border-bd-border pt-4 text-xs text-muted sm:flex-row sm:items-center sm:justify-between">
      <span>Página {page} de {totalPages} · {count} registros</span>
      <div className="flex gap-2">
        <button type="button" onClick={() => onPage(page - 1)} disabled={page <= 1} className={internalButtonClass}>
          Anterior
        </button>
        <button type="button" onClick={() => onPage(page + 1)} disabled={page >= totalPages} className={internalButtonClass}>
          Siguiente
        </button>
      </div>
    </div>
  );
}

function AuditLogsContent({ user }: { user: AuthUser }) {
  const [data, setData] = useState<PaginatedResponse<AuditLogEntry> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [action, setAction] = useState("");
  const [actor, setActor] = useState("");
  const [page, setPage] = useState(1);
  const PAGE_SIZE = 25;

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await fetchAuditLogs({ action, actor, page, page_size: PAGE_SIZE }));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Error al cargar registros.");
    } finally {
      setLoading(false);
    }
  }, [action, actor, page]);

  useEffect(() => {
    void load();
  }, [load]);

  function handleSearch(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setPage(1);
    void load();
  }

  return (
    <AdminShell user={user}>
      <div className="space-y-6">
        <PageHeader
          eyebrow="Seguridad"
          title="Auditoría"
          description="Historial de acciones administrativas para seguimiento y trazabilidad."
        />

        <FilterBar>
          <form onSubmit={handleSearch} className="grid gap-3 sm:grid-cols-[1fr_1fr_auto]">
            <label>
              <span className="sr-only">Filtrar por actor</span>
              <input
                type="search"
                value={actor}
                onChange={(e) => setActor(e.target.value)}
                placeholder="Filtrar por actor…"
                className={internalInputClass}
              />
            </label>
            <label>
              <span className="sr-only">Filtrar por acción</span>
              <input
                type="search"
                value={action}
                onChange={(e) => setAction(e.target.value)}
                placeholder="Filtrar por acción…"
                className={internalInputClass}
              />
            </label>
            <button type="submit" className={internalButtonClass}>Filtrar</button>
          </form>
        </FilterBar>

        {loading ? (
          <div className="rounded-xl border border-bd-border bg-surface px-5 py-10 text-center text-sm text-muted">
            Cargando auditoría…
          </div>
        ) : null}

        {error && !loading ? (
          <div className="rounded-xl border border-danger-border bg-danger-surface px-5 py-4 text-sm text-danger" role="alert">
            {error}
          </div>
        ) : null}

        {data && !loading ? (
          <>
            <AuditLogTable logs={data.results} />
            <Pagination
              page={data.page}
              pageSize={data.page_size}
              count={data.count}
              onPage={setPage}
            />
          </>
        ) : null}
      </div>
    </AdminShell>
  );
}

export default function AuditLogsPage() {
  // La bitácora pide `memberships.view` en la empresa y NO tiene puente
  // legacy: sin membresía, el backend responde 403 venga el rol que venga.
  return (
    <AccessGuard capability="memberships.view" legacyRoles={[]}>
      {(access) => <AuditLogsContent user={access.user} />}
    </AccessGuard>
  );
}
