"use client";

import { useCallback, useEffect, useState } from "react";
import { AdminGuard } from "../components/AdminGuard";
import { AdminShell } from "../components/AdminShell";
import { UsersTable } from "../components/UsersTable";
import {
  fetchAdminUsers,
  changeUserRole,
  ALL_ROLES,
  type AdminUser,
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
        Página {page} de {totalPages} ({count} usuarios)
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

function UsersPageContent({ user }: { user: AuthUser }) {
  const [data, setData] = useState<PaginatedResponse<AdminUser> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [search, setSearch] = useState("");
  const [role, setRole] = useState("");
  const [page, setPage] = useState(1);
  const PAGE_SIZE = 25;

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchAdminUsers({ search, role, page, page_size: PAGE_SIZE });
      setData(result);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Error al cargar usuarios.");
    } finally {
      setLoading(false);
    }
  }, [search, role, page]);

  useEffect(() => {
    load();
  }, [load]);

  function handleSearch(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setPage(1);
    load();
  }

  async function handleRoleChange(userId: number, newRole: string) {
    await changeUserRole(userId, newRole);
    await load();
  }

  return (
    <AdminShell user={user}>
      <div className="space-y-6">
        <div>
          <h1 className="text-xl font-semibold text-white">Usuarios</h1>
          <p className="mt-1 text-sm text-zinc-500">
            Gestión de cuentas y roles.
          </p>
        </div>

        {/* Filters */}
        <form
          onSubmit={handleSearch}
          className="flex flex-wrap gap-3"
        >
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Buscar por usuario o email…"
            className="min-w-[220px] flex-1 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-zinc-200 placeholder-zinc-600 focus:border-white/20 focus:outline-none"
          />
          <select
            value={role}
            onChange={(e) => { setRole(e.target.value); setPage(1); }}
            className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-zinc-200 focus:border-white/20 focus:outline-none"
          >
            <option value="">Todos los roles</option>
            {ALL_ROLES.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
          <button
            type="submit"
            className="rounded-lg border border-white/10 px-4 py-2 text-sm font-semibold text-zinc-300 transition hover:border-white/20 hover:text-white"
          >
            Buscar
          </button>
        </form>

        {/* Table */}
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
            <UsersTable
              users={data.results}
              currentUser={user}
              onRoleChange={handleRoleChange}
            />
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

export default function UsersPage() {
  return <AdminGuard>{(user) => <UsersPageContent user={user} />}</AdminGuard>;
}
