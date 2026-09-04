"use client";

// Phase 2D — physical inventory counts.
//
// A count is not instantaneous: somebody walks the shelves for an hour while the
// shop keeps selling. That is why each line keeps THREE numbers — the theoretical
// stock when counting began, what was physically found, and the theoretical stock
// re-read under lock at approval — and why the correction applied is
// `físico − teórico al aprobar`, never `físico − teórico al iniciar`. Using the
// older figure would silently un-sell everything sold during the count.
//
// A product whose physical quantity was never entered is SKIPPED at approval.
// "Nobody counted this" is not "there are none of these".

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { AdminShell } from "../../components/AdminShell";
import { StaffGuard } from "../../components/StaffGuard";
import { BranchSelector, ScopeNote } from "../../components/BranchSelector";
import { useBranchScope } from "../../lib/use-branch-scope";
import {
  EmptyBox,
  ErrorBox,
  Panel,
  Spinner,
  TableWrap,
  Td,
  Th,
  formatDateTime,
} from "../../components/InventoryUi";
import {
  createCount,
  fetchCounts,
  type CountStatus,
  type InventoryCount,
  type InventoryScope,
} from "../../../lib/inventory";
import { canManageInventory, type AuthUser } from "../../../lib/auth";

const STATUS_FILTERS: { value: "" | CountStatus; label: string }[] = [
  { value: "", label: "Todos" },
  { value: "counting", label: "En conteo" },
  { value: "review", label: "En revisión" },
  { value: "approved", label: "Aprobados" },
  { value: "cancelled", label: "Anulados" },
];

export function CountStatusBadge({ count }: { count: InventoryCount }) {
  const emphasised = count.status === "counting" || count.status === "review";
  const muted = count.status === "cancelled";
  return (
    <span
      className={`inline-flex rounded-md border px-2 py-0.5 text-[11px] font-medium ${
        emphasised
          ? "border-bd-border bg-surface-2 text-foreground"
          : muted
            ? "border-bd-border text-muted"
            : "border-bd-border text-muted"
      }`}
    >
      {count.status_label}
    </span>
  );
}

function CountsContent({ user }: { user: AuthUser }) {
  const scope = useBranchScope({ preferAggregate: true });
  const [counts, setCounts] = useState<InventoryCount[]>([]);
  const [resultScope, setResultScope] = useState<InventoryScope | null>(null);
  const [statusFilter, setStatusFilter] = useState<"" | CountStatus>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const [newBranch, setNewBranch] = useState("");
  const [reason, setReason] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const mayCount = canManageInventory(user);
  const branches = scope.access?.results ?? [];
  const branch = scope.branch;

  const load = useCallback(async () => {
    return fetchCounts({ branch, status: statusFilter || undefined, page_size: 50 });
  }, [branch, statusFilter]);

  useEffect(() => {
    if (!scope.ready) return;
    let cancelled = false;
    void (async () => {
      try {
        const data = await load();
        if (cancelled) return;
        setCounts(data.results);
        setResultScope(data.scope);
        setError(null);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "No se pudieron cargar los recuentos.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [load, reloadKey, scope.ready]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    setCreateError(null);
    try {
      await createCount({
        branch: newBranch ? Number(newBranch) : undefined,
        reason: reason.trim(),
      });
      setReason("");
      setLoading(true);
      setReloadKey((k) => k + 1);
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "No se pudo crear el recuento.");
    } finally {
      setCreating(false);
    }
  }

  const fieldClass =
    "w-full rounded-lg border border-bd-border bg-background/40 px-3 py-2 text-sm text-foreground outline-none transition focus:border-bd-border disabled:opacity-50";
  const labelClass = "mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-muted";

  return (
    <AdminShell user={user}>
      <div className="space-y-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold text-foreground">Recuentos físicos</h1>
            <p className="mt-1 text-sm text-muted">
              Conteo de una sucursal. Las diferencias se aplican como corrección
              al aprobar, contra el stock del momento de la aprobación.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <BranchSelector
              access={scope.access}
              value={scope.branch}
              onChange={(next) => {
                setLoading(true);
                scope.setBranch(next);
              }}
            />
            <Link
              href="/admin/inventory"
              className="rounded-lg border border-bd-border px-3.5 py-2 text-sm text-foreground/85 transition hover:border-bd-border hover:text-foreground"
            >
              ← Inventario
            </Link>
          </div>
        </div>

        <ScopeNote scope={resultScope} />
        {scope.error ? <ErrorBox message={scope.error} /> : null}

        {mayCount && branches.length > 0 ? (
          <Panel
            title="Nuevo recuento"
            description="Se abre en borrador; las cantidades se registran en el siguiente paso."
          >
            <form onSubmit={handleCreate} className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-2">
                {branches.length > 1 ? (
                  <div>
                    <label className={labelClass} htmlFor="c-branch">Sucursal</label>
                    <select
                      id="c-branch"
                      className={fieldClass}
                      value={newBranch}
                      onChange={(e) => setNewBranch(e.target.value)}
                      disabled={creating}
                    >
                      <option value="">Mi sucursal por defecto</option>
                      {branches.map((b) => (
                        <option key={b.id} value={b.id}>{b.name}</option>
                      ))}
                    </select>
                  </div>
                ) : null}
                <div>
                  <label className={labelClass} htmlFor="c-reason">Motivo</label>
                  <input
                    id="c-reason"
                    type="text"
                    maxLength={500}
                    className={fieldClass}
                    placeholder="Ej. Inventario mensual"
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    disabled={creating}
                  />
                </div>
              </div>

              {createError ? <ErrorBox message={createError} /> : null}

              <button
                type="submit"
                disabled={creating}
                className="rounded-lg bg-foreground px-4 py-2 text-sm font-semibold text-background transition hover:bg-foreground/90 disabled:opacity-40"
              >
                {creating ? "Creando…" : "Abrir recuento"}
              </button>
            </form>
          </Panel>
        ) : null}

        <Panel title="Historial" description={`${counts.length} recuento(s)`}>
          <div className="mb-5 flex flex-wrap gap-2">
            {STATUS_FILTERS.map((option) => (
              <button
                key={option.value || "all"}
                type="button"
                onClick={() => {
                  setLoading(true);
                  setStatusFilter(option.value);
                }}
                className={`rounded-lg border px-3 py-1.5 text-sm transition ${
                  statusFilter === option.value
                    ? "border-bd-border bg-surface-2 text-foreground"
                    : "border-bd-border text-muted hover:border-bd-border hover:text-foreground"
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>

          {loading && scope.ready ? <Spinner label="Cargando recuentos…" /> : null}
          {error ? <ErrorBox message={error} /> : null}

          {!loading && !error && counts.length === 0 ? (
            <EmptyBox message="Todavía no hay recuentos." />
          ) : null}

          {!loading && !error && counts.length > 0 ? (
            <TableWrap>
              <thead>
                <tr className="border-b border-bd-border">
                  <Th>#</Th>
                  <Th>Fecha</Th>
                  <Th>Sucursal</Th>
                  <Th>Estado</Th>
                  <Th right>Contados</Th>
                  <Th>Creado por</Th>
                  <Th>Aprobado</Th>
                </tr>
              </thead>
              <tbody>
                {counts.map((c) => (
                  <tr key={c.id} className="border-b border-bd-border">
                    <Td>
                      <Link
                        href={`/admin/inventory/counts/${c.id}`}
                        className="transition hover:text-foreground"
                      >
                        #{c.id}
                      </Link>
                    </Td>
                    <Td muted>{formatDateTime(c.created_at)}</Td>
                    <Td>{c.branch_name}</Td>
                    <Td><CountStatusBadge count={c} /></Td>
                    <Td right>{c.counted_items}</Td>
                    <Td muted>{c.created_by_username ?? "—"}</Td>
                    <Td muted>{c.approved_at ? formatDateTime(c.approved_at) : "—"}</Td>
                  </tr>
                ))}
              </tbody>
            </TableWrap>
          ) : null}
        </Panel>
      </div>
    </AdminShell>
  );
}

export default function CountsPage() {
  return <StaffGuard>{(user) => <CountsContent user={user} />}</StaffGuard>;
}
