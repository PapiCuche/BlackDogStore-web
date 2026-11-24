"use client";

/**
 * SALES-QUOTE-01 — la lista de cotizaciones: filtros por estado, sucursal y búsqueda (número, cliente,
 * documento). La tabla se desplaza dentro de su contenedor; la página no (AGENTS.md §7).
 */
import Link from "next/link";
import { useEffect, useState } from "react";
import { formatAdminDate } from "../../../lib/admin";
import {
  QUOTE_STATUS_FILTERS,
  fetchQuotes,
  formatMoney,
  type QuoteContext,
  type QuoteRow,
} from "../../lib/sales-quotes-api";
import { FilterBar, TableShell, internalInputClass, internalPrimaryButtonClass } from "../internal-ui";
import { QuoteStatusBadge } from "./QuoteStatusBadge";

export function SalesQuotesList({ context }: { context: QuoteContext | null }) {
  const [status, setStatus] = useState("");
  const [branch, setBranch] = useState<number | null>(null);
  const [term, setTerm] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [rows, setRows] = useState<QuoteRow[] | null>(null);
  const [count, setCount] = useState(0);
  const [pageSize, setPageSize] = useState(25);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => { setSearch(term.trim()); setPage(1); }, 300);
    return () => window.clearTimeout(timer);
  }, [term]);

  useEffect(() => {
    let cancelled = false;
    fetchQuotes({ status, branch, q: search, page })
      .then((data) => {
        if (cancelled) return;
        setError(null);
        setRows(data.results);
        setCount(data.count);
        setPageSize(data.page_size);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "No se pudieron cargar las cotizaciones.");
      });
    return () => { cancelled = true; };
  }, [status, branch, search, page]);

  const pages = Math.max(1, Math.ceil(count / pageSize));

  return (
    <div className="space-y-5">
      <FilterBar>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <label className="block text-xs font-semibold text-muted">
            Estado
            <select
              value={status}
              onChange={(event) => { setStatus(event.target.value); setPage(1); }}
              className={`${internalInputClass} mt-1`}
            >
              {QUOTE_STATUS_FILTERS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
          <label className="block text-xs font-semibold text-muted">
            Sucursal
            <select
              value={branch ?? ""}
              onChange={(event) => { setBranch(event.target.value ? Number(event.target.value) : null); setPage(1); }}
              className={`${internalInputClass} mt-1`}
            >
              <option value="">Todas mis sucursales</option>
              {(context?.branches ?? []).map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
            </select>
          </label>
          <label className="block text-xs font-semibold text-muted sm:col-span-2">
            Buscar
            <input
              type="search"
              value={term}
              onChange={(event) => setTerm(event.target.value)}
              placeholder="Número, cliente o documento"
              className={`${internalInputClass} mt-1`}
            />
          </label>
        </div>
      </FilterBar>

      {context?.can_manage ? (
        <div className="flex justify-end">
          <Link href="/admin/sales/quotes/new" className={internalPrimaryButtonClass}>Nueva cotización</Link>
        </div>
      ) : null}

      {error ? (
        <div role="alert" className="rounded-xl border border-danger-border bg-danger-surface px-5 py-4 text-sm text-danger">{error}</div>
      ) : null}

      {rows === null && !error ? (
        <p className="rounded-xl border border-bd-border bg-surface px-5 py-10 text-center text-sm text-muted">Cargando cotizaciones…</p>
      ) : null}

      {rows && rows.length === 0 ? (
        <p className="rounded-xl border border-bd-border bg-surface px-5 py-10 text-center text-sm text-muted">
          No hay cotizaciones con estos filtros.
        </p>
      ) : null}

      {rows && rows.length > 0 ? (
        <TableShell>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-sm">
              <caption className="sr-only">Cotizaciones</caption>
              <thead className="border-b border-bd-border text-[11px] uppercase tracking-widest text-muted">
                <tr>
                  <th scope="col" className="px-4 py-3">Número</th>
                  <th scope="col" className="px-4 py-3">Cliente</th>
                  <th scope="col" className="px-4 py-3">Estado</th>
                  <th scope="col" className="px-4 py-3">Vigencia</th>
                  <th scope="col" className="px-4 py-3">Sucursal</th>
                  <th scope="col" className="px-4 py-3 text-right">Total</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id} className="border-b border-bd-border/60 last:border-0">
                    <td className="px-4 py-3 font-semibold">
                      <Link href={`/admin/sales/quotes/${row.id}`} className="underline-offset-2 hover:underline focus-visible:underline">
                        {row.label}
                      </Link>
                      <span className="block text-xs font-normal text-muted">{formatAdminDate(row.created_at)}</span>
                    </td>
                    <td className="px-4 py-3">
                      {row.customer_name || <span className="text-muted">Sin identificar</span>}
                      {row.customer_document ? <span className="block text-xs text-muted">{row.customer_document}</span> : null}
                    </td>
                    <td className="px-4 py-3"><QuoteStatusBadge status={row.status} /></td>
                    <td className="px-4 py-3 tabular-nums">{row.valid_until ?? "—"}</td>
                    <td className="px-4 py-3">{row.branch_name}</td>
                    <td className="px-4 py-3 text-right font-semibold tabular-nums">{formatMoney(row.total, row.currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </TableShell>
      ) : null}

      {pages > 1 ? (
        <nav aria-label="Páginas" className="flex items-center justify-between text-sm">
          <button type="button" className="rounded-lg border border-bd-border px-3 py-2 disabled:opacity-40" disabled={page <= 1} onClick={() => setPage(page - 1)}>Anterior</button>
          <span className="text-muted">Página {page} de {pages}</span>
          <button type="button" className="rounded-lg border border-bd-border px-3 py-2 disabled:opacity-40" disabled={page >= pages} onClick={() => setPage(page + 1)}>Siguiente</button>
        </nav>
      ) : null}
    </div>
  );
}
