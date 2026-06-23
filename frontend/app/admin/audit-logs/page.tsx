"use client";

import { useCallback, useEffect, useState } from "react";
import { AdminGuard } from "../components/AdminGuard";
import { AdminShell } from "../components/AdminShell";
import { AuditLogTable } from "../components/AuditLogTable";
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
    <div className="mt-4 flex items-center justify-between text-xs text-zinc-500">
      <span>
        Página {page} de {totalPages} ({count} registros)
      </span>
      <div className="flex gap-2">
        <button
          onClick={() => onPage(page - 1)}
          disabled={page <= 1}
          className="rounded border border-white/10 px-3 py-1.5 transition hover:border-white/20 hover:text-white disabled:opacity-30"
        >
          ← Anterior
        </button>
        <button
          onClick={() => onPage(page + 1)}
          disabled={page >= totalPages}
          className="rounded border border-white/10 px-3 py-1.5 transition hover:border-white/20 hover:text-white disabled:opacity-30"
        >
          Siguiente →
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
      const result = await fetchAuditLogs({ action, actor, page, page_size: PAGE_SIZE });
      setData(result);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Error al cargar registros.");
    } finally {
      setLoading(false);
    }
  }, [action, actor, page]);

  useEffect(() => {
    load();
  }, [load]);

  function handleSearch(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setPage(1);
    load();
  }

  return (
    <AdminShell user={user}>
      <div className="space-y-6">
        <div>
          <h1 className="text-xl font-semibold text-white">Auditoría</h1>
          <p className="mt-1 text-sm text-zinc-500">
            Historial de acciones administrativas.
          </p>
        </div>

        {/* Filters */}
        <form onSubmit={handleSearch} className="flex flex-wrap gap-3">
          <input
            type="search"
            value={actor}
            onChange={(e) => setActor(e.target.value)}
            placeholder="Filtrar por actor…"
            className="min-w-[180px] flex-1 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-zinc-200 placeholder-zinc-600 focus:border-white/20 focus:outline-none"
          />
          <input
            type="search"
            value={action}
            onChange={(e) => setAction(e.target.value)}
            placeholder="Filtrar por acción…"
            className="min-w-[180px] flex-1 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-zinc-200 placeholder-zinc-600 focus:border-white/20 focus:outline-none"
          />
          <button
            type="submit"
            className="rounded-lg border border-white/10 px-4 py-2 text-sm font-semibold text-zinc-300 transition hover:border-white/20 hover:text-white"
          >
            Filtrar
          </button>
        </form>

        {loading && (
          <div className="py-12 text-center text-zinc-600">Cargando…</div>
        )}
        {error && !loading && (
          <div className="rounded-xl border border-red-500/20 bg-red-500/5 px-5 py-4 text-sm text-red-400">
            {error}
          </div>
        )}
        {data && !loading && (
          <>
            <AuditLogTable logs={data.results} />
            <Pagination
              page={data.page}
              pageSize={data.page_size}
              count={data.count}
              onPage={setPage}
            />
          </>
        )}
      </div>
    </AdminShell>
  );
}

export default function AuditLogsPage() {
  return <AdminGuard>{(user) => <AuditLogsContent user={user} />}</AdminGuard>;
}
