"use client";

/**
 * Clientes — Phase 4.
 *
 * The CRM list. Three things it is careful about:
 *
 *   1. NO INTERNAL NOTES IN THE LIST. A list is skimmed at a counter, sometimes
 *      with the client on the other side of it. Notes are one click away, in the
 *      detail, where the person reading them chose to look.
 *
 *   2. ARCHIVED CLIENTS ARE HIDDEN, NOT GONE. The default filter is active, and
 *      "Archivados" is right there. Archiving is not deletion and the UI should
 *      not imply it is.
 *
 *   3. SEARCH IS DEBOUNCED. Typing a surname should not fire eight requests.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { AdminShell } from "../components/AdminShell";
import { InternalControlGuard, type InternalContext } from "../components/InternalControlGuard";
import { DashboardSection } from "../components/dashboard-ui";
import { CustomerForm } from "../components/CustomerForm";
import {
  fetchCustomers,
  type CustomerList,
  type CustomerRow,
  type CustomerType,
} from "../lib/internal-api";

type StateFilter = "active" | "archived" | "all";

const STATE_LABELS: [StateFilter, string][] = [
  ["active", "Activos"],
  ["archived", "Archivados"],
  ["all", "Todos"],
];

function DocumentCell({ row }: { row: CustomerRow }) {
  if (!row.document_number) {
    return <span className="text-muted">—</span>;
  }
  return (
    <span className="font-mono text-xs">
      <span className="text-muted">{row.document_type.toUpperCase()} </span>
      {row.document_number}
    </span>
  );
}

function CustomersContent({ ctx }: { ctx: InternalContext }) {
  const companyId = ctx.selectedCompanyId;

  const [data, setData] = useState<CustomerList | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const [state, setState] = useState<StateFilter>("active");
  const [type, setType] = useState<CustomerType | "">("");
  const [page, setPage] = useState(1);

  const [creating, setCreating] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => {
      setDebounced(search);
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [search]);

  const load = useCallback(async () => {
    setData(
      await fetchCustomers(companyId, {
        search: debounced,
        state,
        type: type || undefined,
        page,
      }),
    );
  }, [companyId, debounced, state, type, page]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      // Inside the async body, not the effect body: setting state synchronously
      // while the effect runs schedules a second render before the first has
      // committed, which is what react-hooks/set-state-in-effect warns about.
      setLoading(true);
      try {
        await load();
        if (!cancelled) setError(null);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "No se pudieron cargar los clientes.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [load]);

  const canManage = data?.can_manage ?? false;
  const totalPages = data ? Math.max(1, Math.ceil(data.count / data.page_size)) : 1;

  return (
    <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
      <div className="space-y-6">
        <DashboardSection
          title="Clientes"
          description="Ficha e historial comercial. Datos privados de esta empresa."
          action={
            canManage && !creating ? (
              <button
                type="button"
                onClick={() => {
                  setCreating(true);
                  setNotice(null);
                }}
                className="rounded-lg border border-bd-border px-3 py-1.5 text-sm text-foreground transition hover:border-bd-border hover:text-foreground"
              >
                Nuevo cliente
              </button>
            ) : null
          }
        >
          {creating ? (
            <div className="rounded-xl border border-bd-border bg-surface p-5">
              <CustomerForm
                companyId={companyId}
                customer={null}
                onCancel={() => setCreating(false)}
                onSaved={(saved, duplicates) => {
                  setCreating(false);
                  setNotice(
                    duplicates.length
                      ? `Cliente creado. Hay ${duplicates.length} ficha(s) con el mismo email o teléfono — revísalas por si fueran la misma persona.`
                      : "Cliente creado.",
                  );
                  void load();
                  void saved;
                }}
              />
            </div>
          ) : null}

          {notice ? (
            <p className="rounded-lg border border-bd-border bg-surface px-4 py-3 text-sm text-muted">
              {notice}
            </p>
          ) : null}

          <div className="flex flex-wrap items-center gap-3">
            <input
              type="search"
              placeholder="Buscar por nombre, documento, teléfono o email…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="min-w-[16rem] flex-1 rounded-lg border border-bd-border bg-background/40 px-3 py-2 text-sm text-foreground outline-none transition focus:border-bd-border"
            />
            <div className="flex gap-1 rounded-lg border border-bd-border p-1">
              {STATE_LABELS.map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => {
                    setState(value);
                    setPage(1);
                  }}
                  className={`rounded px-2.5 py-1 text-xs transition ${
                    state === value
                      ? "bg-surface-2 text-foreground"
                      : "text-muted hover:text-foreground/85"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
            <select
              value={type}
              onChange={(e) => {
                setType(e.target.value as CustomerType | "");
                setPage(1);
              }}
              className="rounded-lg border border-bd-border bg-background/40 px-3 py-2 text-sm text-foreground outline-none"
            >
              <option value="">Persona y empresa</option>
              <option value="person">Sólo personas</option>
              <option value="business">Sólo empresas</option>
            </select>
          </div>

          {error ? (
            <div className="rounded-xl border border-red-500/20 bg-red-500/5 px-5 py-4 text-sm text-red-400">
              {error}
            </div>
          ) : loading ? (
            <p className="py-6 text-sm text-muted">Cargando clientes…</p>
          ) : !data || data.results.length === 0 ? (
            <p className="rounded-xl border border-bd-border bg-surface px-5 py-8 text-center text-sm text-muted">
              {debounced
                ? "Ningún cliente coincide con esa búsqueda."
                : "Todavía no hay clientes registrados en esta empresa."}
            </p>
          ) : (
            <div className="overflow-x-auto rounded-xl border border-bd-border">
              <table className="w-full min-w-[46rem] text-left text-sm">
                <thead className="border-b border-bd-border text-[11px] uppercase tracking-widest text-muted">
                  <tr>
                    <th className="px-4 py-3 font-semibold">Cliente</th>
                    <th className="px-4 py-3 font-semibold">Documento</th>
                    <th className="px-4 py-3 font-semibold">Teléfono</th>
                    <th className="px-4 py-3 font-semibold">Email</th>
                    <th className="px-4 py-3 font-semibold">Estado</th>
                  </tr>
                </thead>
                <tbody>
                  {data.results.map((row) => (
                    <tr
                      key={row.id}
                      className="border-b border-bd-border last:border-0 hover:bg-surface"
                    >
                      <td className="px-4 py-3">
                        <Link
                          href={`/admin/customers/${row.id}`}
                          className="text-foreground transition hover:text-foreground"
                        >
                          {row.display_name}
                        </Link>
                        <span className="ml-2 text-[11px] text-muted">
                          {row.customer_type === "business" ? "Empresa" : "Persona"}
                          {row.has_account ? " · con cuenta" : ""}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-muted">
                        <DocumentCell row={row} />
                      </td>
                      <td className="px-4 py-3 text-muted">{row.phone || "—"}</td>
                      <td className="px-4 py-3 text-muted">{row.email || "—"}</td>
                      <td className="px-4 py-3">
                        {row.is_active ? (
                          <span className="text-xs text-emerald-400/80">Activo</span>
                        ) : (
                          <span className="text-xs text-muted">Archivado</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {data && data.count > data.page_size ? (
            <div className="flex items-center justify-between text-sm text-muted">
              <span>
                {data.count} cliente{data.count === 1 ? "" : "s"} · página {data.page} de{" "}
                {totalPages}
              </span>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={page <= 1}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  className="rounded-lg border border-bd-border px-3 py-1.5 text-xs transition hover:border-bd-border disabled:opacity-30"
                >
                  Anterior
                </button>
                <button
                  type="button"
                  disabled={page >= totalPages}
                  onClick={() => setPage((p) => p + 1)}
                  className="rounded-lg border border-bd-border px-3 py-1.5 text-xs transition hover:border-bd-border disabled:opacity-30"
                >
                  Siguiente
                </button>
              </div>
            </div>
          ) : null}
        </DashboardSection>
      </div>
    </AdminShell>
  );
}

export default function CustomersPage() {
  return (
    <InternalControlGuard>{(ctx) => <CustomersContent ctx={ctx} />}</InternalControlGuard>
  );
}
