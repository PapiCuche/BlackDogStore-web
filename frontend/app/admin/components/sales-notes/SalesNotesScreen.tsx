"use client";

/**
 * SALES-NOTE-CONTROL-01 — las notas de venta internas emitidas.
 *
 * Lista con filtros (vigentes, anuladas, prueba; fechas; sucursal; quién la
 * emitió), la marca ANULADA, el detalle con su registro de anulación y las
 * observaciones internas.
 *
 * «Anular» aparece sólo cuando el servidor dice `can_void` — el usuario Master
 * actuando en esta empresa — y la nota sigue vigente. No es autoridad: el
 * servidor vuelve a decidirlo y responde 403 a cualquier otro.
 *
 * NADA AQUÍ REESCRIBE UNA NOTA. No hay edición de número, fecha, productos,
 * importes ni pago: el servidor no tiene ruta para eso.
 */
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { formatAdminDate } from "../../../lib/admin";
import {
  fetchSalesNoteDetail,
  fetchSalesNotes,
  type SalesNote,
  type SalesNoteDetail,
  type SalesNoteFilters,
  type SalesNoteList,
  type SalesNoteStatusFilter,
} from "../../../lib/inventory";
import { FilterBar, TableShell, internalButtonClass, internalInputClass } from "../internal-ui";
import { SalesNoteRemarks } from "./SalesNoteRemarks";
import { SalesNoteStatusBadge } from "./SalesNoteStatusBadge";
import { SalesNoteVoidDialog, dangerButtonClass } from "./SalesNoteVoidDialog";

const STATUS_OPTIONS: { value: SalesNoteStatusFilter; label: string }[] = [
  { value: "", label: "Todas" },
  { value: "vigente", label: "Vigentes" },
  { value: "anulada", label: "Anuladas" },
  { value: "prueba", label: "Prueba (anuladas como prueba)" },
];

function money(value: string): string {
  const amount = Number(value);
  return Number.isFinite(amount) ? `S/ ${amount.toFixed(2)}` : value;
}

function asId(value: string): number | null {
  const parsed = Number(value);
  return value && Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

export function SalesNotesScreen() {
  const [filters, setFilters] = useState<SalesNoteFilters>({ status: "", page: 1 });
  const [data, setData] = useState<SalesNoteList | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [voiding, setVoiding] = useState<SalesNote | null>(null);
  const [detail, setDetail] = useState<SalesNoteDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  const load = useCallback(async (wanted: SalesNoteFilters) => {
    setLoading(true);
    setError(null);
    try {
      setData(await fetchSalesNotes(wanted));
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudieron cargar las notas de venta.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(filters);
  }, [filters, load]);

  /** Cambiar un filtro vuelve a la primera página. */
  function refine(change: Partial<SalesNoteFilters>) {
    setFilters((current) => ({ ...current, ...change, page: 1 }));
  }

  async function openDetail(note: SalesNote) {
    setDetailError(null);
    try {
      setDetail(await fetchSalesNoteDetail(note.id));
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : "No se pudo cargar la nota de venta.");
    }
  }

  function replaceRow(updated: SalesNoteDetail) {
    setData((current) => current && {
      ...current,
      results: current.results.map((row) => (row.id === updated.id ? updated : row)),
    });
    setDetail((current) => (current && current.id === updated.id ? updated : current));
  }

  function dropRow(id: number) {
    setData((current) => current && {
      ...current,
      count: Math.max(0, current.count - 1),
      results: current.results.filter((row) => row.id !== id),
    });
    setDetail((current) => (current && current.id === id ? null : current));
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.count / data.page_size)) : 1;

  return (
    <div className="space-y-6">
      <FilterBar>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <label className="block text-xs font-semibold text-muted">
            Estado
            <select
              value={filters.status ?? ""}
              onChange={(event) => refine({ status: event.target.value as SalesNoteStatusFilter })}
              className={`${internalInputClass} mt-1`}
            >
              {STATUS_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
          <label className="block text-xs font-semibold text-muted">
            Desde
            <input
              type="date"
              value={filters.date_from ?? ""}
              onChange={(event) => refine({ date_from: event.target.value })}
              className={`${internalInputClass} mt-1`}
            />
          </label>
          <label className="block text-xs font-semibold text-muted">
            Hasta
            <input
              type="date"
              value={filters.date_to ?? ""}
              onChange={(event) => refine({ date_to: event.target.value })}
              className={`${internalInputClass} mt-1`}
            />
          </label>
          <label className="block text-xs font-semibold text-muted">
            Sucursal
            <select
              value={filters.branch ?? ""}
              onChange={(event) => refine({ branch: asId(event.target.value) })}
              className={`${internalInputClass} mt-1`}
            >
              <option value="">Todas mis sucursales</option>
              {(data?.branches ?? []).map((branch) => (
                <option key={branch.id} value={branch.id}>{branch.name}</option>
              ))}
            </select>
          </label>
          <label className="block text-xs font-semibold text-muted">
            Emitida por
            <select
              value={filters.user ?? ""}
              onChange={(event) => refine({ user: asId(event.target.value) })}
              className={`${internalInputClass} mt-1`}
            >
              <option value="">Cualquier usuario</option>
              {(data?.issuers ?? []).map((issuer) => (
                <option key={issuer.id} value={issuer.id}>{issuer.username}</option>
              ))}
            </select>
          </label>
        </div>
      </FilterBar>

      {error ? (
        <div role="alert" className="rounded-xl border border-danger-border bg-danger-surface px-5 py-4 text-sm text-danger">
          {error}
        </div>
      ) : null}

      {loading && !data ? (
        <div className="rounded-xl border border-bd-border bg-surface px-5 py-10 text-center text-sm text-muted">
          Cargando notas de venta…
        </div>
      ) : null}

      {data ? (
        <>
          <TableShell>
            {/* The table scrolls inside its shell; the page never does. */}
            <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-sm">
              <thead className="border-b border-bd-border text-[11px] uppercase tracking-widest text-muted">
                <tr>
                  <th className="px-4 py-3 font-semibold">Número</th>
                  <th className="px-4 py-3 font-semibold">Estado</th>
                  <th className="px-4 py-3 font-semibold">Emitida</th>
                  <th className="px-4 py-3 font-semibold">Pedido</th>
                  <th className="px-4 py-3 font-semibold">Cliente</th>
                  <th className="px-4 py-3 font-semibold">Sucursal</th>
                  <th className="px-4 py-3 font-semibold">Emitida por</th>
                  <th className="px-4 py-3 text-right font-semibold">Total</th>
                  <th className="px-4 py-3 font-semibold"><span className="sr-only">Acciones</span></th>
                </tr>
              </thead>
              <tbody>
                {data.results.length === 0 ? (
                  <tr>
                    <td colSpan={9} className="px-4 py-8 text-center text-muted">No hay notas con estos filtros.</td>
                  </tr>
                ) : data.results.map((note) => (
                  <tr key={note.id} data-testid={`sales-note-row-${note.id}`} className="border-b border-bd-border last:border-0">
                    <td className="px-4 py-3 font-mono text-foreground">{note.number}</td>
                    <td className="px-4 py-3"><SalesNoteStatusBadge note={note} /></td>
                    <td className="px-4 py-3 text-foreground/85">{note.issued_at ? formatAdminDate(note.issued_at) : "—"}</td>
                    <td className="px-4 py-3">
                      <Link href={`/admin/orders/${note.order}`} className="text-foreground underline-offset-2 hover:underline">
                        #{note.order}
                      </Link>
                    </td>
                    <td className="px-4 py-3 text-foreground/85">{note.customer_name || "—"}</td>
                    <td className="px-4 py-3 text-foreground/85">{note.branch_name || "—"}</td>
                    <td className="px-4 py-3 text-foreground/85">{note.created_by_username ?? "—"}</td>
                    <td className="px-4 py-3 text-right text-foreground">{money(note.order_total)}</td>
                    <td className="px-4 py-3">
                      <div className="flex justify-end gap-2">
                        <button type="button" onClick={() => void openDetail(note)} className={internalButtonClass}>
                          Ver
                        </button>
                        {data.can_void && !note.is_void ? (
                          <button type="button" onClick={() => setVoiding(note)} className={dangerButtonClass}>
                            Anular
                          </button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          </TableShell>

          {totalPages > 1 ? (
            <div className="flex items-center justify-between text-xs text-muted">
              <span>Página {data.page} de {totalPages} · {data.count} notas</span>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={data.page <= 1}
                  onClick={() => setFilters((current) => ({ ...current, page: data.page - 1 }))}
                  className={internalButtonClass}
                >
                  Anterior
                </button>
                <button
                  type="button"
                  disabled={data.page >= totalPages}
                  onClick={() => setFilters((current) => ({ ...current, page: data.page + 1 }))}
                  className={internalButtonClass}
                >
                  Siguiente
                </button>
              </div>
            </div>
          ) : null}
        </>
      ) : null}

      {detailError ? <p role="alert" className="text-sm text-danger">{detailError}</p> : null}
      {detail ? <SalesNoteDetailCard key={detail.id} note={detail} onClose={() => setDetail(null)} /> : null}

      {voiding ? (
        <SalesNoteVoidDialog
          note={voiding}
          onClose={() => setVoiding(null)}
          onVoided={(updated) => {
            setVoiding(null);
            if (filters.status === "vigente") {
              // It no longer matches «Vigentes»: out of the list now, and the page
              // is read again so its count and its next row are right.
              dropRow(updated.id);
              void load(filters);
            } else {
              replaceRow(updated);
            }
          }}
        />
      ) : null}
    </div>
  );
}

function SalesNoteDetailCard({ note, onClose }: { note: SalesNoteDetail; onClose: () => void }) {
  return (
    <section aria-label={`Nota ${note.number}`} className="space-y-4 rounded-xl border border-bd-border bg-surface p-5 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="font-mono text-base font-semibold text-foreground">{note.number}</h2>
          <SalesNoteStatusBadge note={note} />
        </div>
        <button type="button" onClick={onClose} className={internalButtonClass}>Cerrar</button>
      </div>
      {note.is_void ? (
        <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-xs text-muted">Anulada por</dt>
            <dd className="text-foreground">{note.voided_by_username ?? "—"}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted">Fecha de anulación</dt>
            <dd className="text-foreground">{note.voided_at ? formatAdminDate(note.voided_at) : "—"}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted">Tipo de motivo</dt>
            <dd className="text-foreground">{note.void_reason_type_label || "—"}</dd>
          </div>
          <div className="sm:col-span-2">
            <dt className="text-xs text-muted">Motivo</dt>
            <dd className="whitespace-pre-wrap text-foreground">{note.void_reason || "—"}</dd>
          </div>
        </dl>
      ) : null}
      <p className="text-xs text-muted">
        La nota conserva su número, su fecha de emisión y lo que imprimió. Anularla no cancela el pedido ni
        devuelve dinero; los importes cobrados siguen en los reportes.
      </p>
      <SalesNoteRemarks noteId={note.id} remarks={note.remarks} canRemark={note.can_remark} />
    </section>
  );
}
