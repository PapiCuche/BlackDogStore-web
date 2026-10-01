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
  internalPrimaryButtonClass,
} from "../components/internal-ui";
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
  if (!row.document_number) return <span className="text-muted">—</span>;

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
        <PageHeader
          eyebrow="CRM"
          title="Clientes"
          description="Ficha e historial comercial. La lista evita mostrar notas internas y mantiene archivados separados del flujo activo."
          actions={
            canManage && !creating ? (
              <button
                type="button"
                onClick={() => {
                  setCreating(true);
                  setNotice(null);
                }}
                className={internalPrimaryButtonClass}
              >
                Nuevo cliente
              </button>
            ) : null
          }
        />

        {creating ? (
          <section className="rounded-2xl border border-bd-border bg-surface p-5 sm:p-6">
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
          </section>
        ) : null}

        {notice ? (
          <p className="rounded-xl border border-bd-border bg-surface px-4 py-3 text-sm text-muted" role="status">
            {notice}
          </p>
        ) : null}

        <FilterBar>
          <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto_auto]">
            <label>
              <span className="sr-only">Buscar clientes</span>
              <input
                type="search"
                placeholder="Buscar por nombre, documento, teléfono o email…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className={internalInputClass}
              />
            </label>

            <div className="flex gap-1 rounded-xl border border-bd-border bg-background p-1" aria-label="Estado del cliente">
              {STATE_LABELS.map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => {
                    setState(value);
                    setPage(1);
                  }}
                  aria-pressed={state === value}
                  className={`rounded-lg px-3 py-2 text-xs font-semibold transition ${
                    state === value
                      ? "bg-foreground/[0.08] text-foreground"
                      : "text-muted hover:text-foreground"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>

            <label>
              <span className="sr-only">Tipo de cliente</span>
              <select
                value={type}
                onChange={(e) => {
                  setType(e.target.value as CustomerType | "");
                  setPage(1);
                }}
                className={internalInputClass}
              >
                <option value="">Persona y empresa</option>
                <option value="person">Sólo personas</option>
                <option value="business">Sólo empresas</option>
              </select>
            </label>
          </div>
        </FilterBar>

        {error ? (
          <div className="rounded-xl border border-red-500/25 bg-red-500/10 px-5 py-4 text-sm text-red-200" role="alert">
            {error}
          </div>
        ) : loading ? (
          <div className="rounded-2xl border border-bd-border bg-surface px-5 py-8 text-sm text-muted">
            Cargando clientes…
          </div>
        ) : !data || data.results.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-bd-border px-5 py-10 text-center text-sm text-muted">
            {debounced
              ? "Ningún cliente coincide con esa búsqueda."
              : "Todavía no hay clientes registrados en esta empresa."}
          </div>
        ) : (
          <TableShell>
            <table className="w-full min-w-[46rem] text-left text-sm">
              <thead className="border-b border-bd-border text-[11px] uppercase tracking-[0.1em] text-muted">
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
                    className="border-b border-bd-border/70 last:border-0 hover:bg-foreground/[0.025]"
                  >
                    <td className="px-4 py-3">
                      <Link
                        href={`/admin/customers/${row.id}`}
                        className="font-medium text-foreground transition hover:underline"
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
                      <span
                        className={`inline-flex rounded-full border px-2.5 py-1 text-[11px] font-semibold ${
                          row.is_active
                            ? "border-emerald-400/25 bg-emerald-400/10 text-emerald-200"
                            : "border-bd-border text-muted"
                        }`}
                      >
                        {row.is_active ? "Activo" : "Archivado"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableShell>
        )}

        {data && data.count > data.page_size ? (
          <div className="flex flex-col gap-3 border-t border-bd-border pt-4 text-sm text-muted sm:flex-row sm:items-center sm:justify-between">
            <span>
              {data.count} cliente{data.count === 1 ? "" : "s"} · página {data.page} de {totalPages}
            </span>
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
                disabled={page >= totalPages}
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

export default function CustomersPage() {
  return (
    <InternalControlGuard>{(ctx) => <CustomersContent ctx={ctx} />}</InternalControlGuard>
  );
}
