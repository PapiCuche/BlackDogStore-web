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
//
// INV-COUNT-UX-04: «Abrir recuento» opens the new count's screen at once (it used
// to reset the form and leave a draft the list could even filter out). The branch
// is always named and sent explicitly; who cannot count is told why; every row of
// the history has its action.

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { AdminShell } from "../../components/AdminShell";
import { AccessGuard } from "../../components/AccessGuard";
import type { InternalAccess } from "../../lib/internal-access";
import { PageHeader, internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "../../components/internal-ui";
import { BranchSelector, ScopeNote } from "../../components/BranchSelector";
import { useBranchScope } from "../../lib/use-branch-scope";
import {
  CountStatusBadge,
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
  type InventoryCountSummary,
  type InventoryScope,
} from "../../../lib/inventory";
import type { AuthUser } from "../../../lib/auth";

const STATUS_FILTERS: { value: "" | CountStatus; label: string }[] = [
  { value: "", label: "Todos" },
  { value: "draft", label: "Borrador" },
  { value: "counting", label: "En conteo" },
  { value: "review", label: "En revisión" },
  { value: "approved", label: "Aprobados" },
  { value: "cancelled", label: "Anulados" },
];

function CountsContent({ user, access }: { user: AuthUser; access: InternalAccess }) {
  const scope = useBranchScope({ preferAggregate: true });
  const [counts, setCounts] = useState<InventoryCountSummary[]>([]);
  const [resultScope, setResultScope] = useState<InventoryScope | null>(null);
  const [statusFilter, setStatusFilter] = useState<"" | CountStatus>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const router = useRouter();
  // The branch to count: what the person picked here, else the one chosen above, else their default.
  const [pickedBranch, setPickedBranch] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  // INV-COUNT-UX-04: list what the branch holds as pending lines, so «Pendientes» says what is left to count.
  const [listStocked, setListStocked] = useState(true);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const mayCount = access.can("inventory.adjust", ["inventory", "admin", "superadmin"]);
  const branches = scope.access?.results ?? [];
  const branch = scope.branch;
  const proposedBranch = typeof branch === "number" ? String(branch)
    : scope.access?.default_branch ? String(scope.access.default_branch.id)
      : branches.length === 1 ? String(branches[0].id) : "";
  const newBranch = pickedBranch ?? proposedBranch;

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
  }, [load, scope.ready]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (creating) return;
    if (!newBranch) {
      setCreateError("Elige la sucursal que vas a contar.");
      return;
    }
    setCreating(true);
    setCreateError(null);
    try {
      const created = await createCount({ branch: Number(newBranch), reason: reason.trim(), include_stocked: listStocked });
      if (!created || typeof created.id !== "number") {
        setCreateError("El recuento se pidió, pero no llegó su número. Búscalo en el historial antes de abrir otro.");
        setCreating(false);
        return;
      }
      // Straight to counting: the new count is a draft until its first quantity.
      router.push(`/admin/inventory/counts/${created.id}`);
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "No se pudo crear el recuento.");
      setCreating(false);
    }
  }

  const fieldClass = internalInputClass;
  const labelClass = "mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-muted";

  return (
    <AdminShell user={user}>
      <div className="space-y-8">
        <PageHeader
          eyebrow="Inventario"
          title="Recuentos físicos"
          description="Conteo de una sucursal. Las diferencias se aplican al aprobar contra el stock vigente."
          actions={
            <>
              <BranchSelector access={scope.access} value={scope.branch} onChange={(next) => { setLoading(true); setPickedBranch(null); scope.setBranch(next); }} />
              <Link href="/admin/inventory" className={internalButtonClass}>Inventario</Link>
            </>
          }
        />

        <ScopeNote scope={resultScope} />
        {scope.error ? <ErrorBox message={scope.error} /> : null}

        {!scope.loading && !scope.error && branches.length === 0 ? (
          <p role="note" className="rounded-xl border border-warning-border bg-warning-surface px-4 py-3 text-sm text-foreground">
            No tienes ninguna sucursal asignada: pide a quien administra el inventario que te asigne una para ver y hacer recuentos.
          </p>
        ) : null}

        {!mayCount && branches.length > 0 ? (
          <p role="note" className="rounded-xl border border-bd-border bg-surface px-4 py-3 text-sm text-muted">
            Puedes ver los recuentos, pero no abrirlos ni contar: hace falta el permiso «Mover inventario». Pídeselo a
            quien administra el inventario.
          </p>
        ) : null}

        {mayCount && branches.length > 0 ? (
          <Panel
            id="new-count"
            title="Nuevo recuento"
            description="Elige la sucursal y pulsa «Abrir recuento»: se abre la pantalla de conteo. El stock no cambia hasta que alguien con permiso lo apruebe."
          >
            <form onSubmit={handleCreate} className="space-y-4" noValidate>
              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  {branches.length === 1 ? (
                    // One branch: said, not a disabled control (it would leave the tab order unread).
                    <>
                      <p className={labelClass} id="c-branch-caption">Sucursal que se cuenta</p>
                      <p aria-labelledby="c-branch-caption" className="py-2 text-sm font-semibold text-foreground">{branches[0].name}</p>
                    </>
                  ) : (
                  <>
                  <label className={labelClass} htmlFor="c-branch">Sucursal que se cuenta</label>
                  <select
                    id="c-branch"
                    className={fieldClass}
                    value={newBranch}
                    onChange={(e) => { setPickedBranch(e.target.value); setCreateError(null); }}
                    disabled={creating}
                    aria-invalid={createError && !newBranch ? true : undefined}
                  >
                    <option value="">Elige la sucursal</option>
                    {branches.map((b) => (
                      <option key={b.id} value={b.id}>{b.name}</option>
                    ))}
                  </select>
                  </>
                  )}
                </div>
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

              <label className="flex items-start gap-2 text-sm text-foreground">
                <input
                  type="checkbox" className="mt-0.5 h-5 w-5" checked={listStocked} disabled={creating}
                  onChange={(e) => setListStocked(e.target.checked)}
                />
                <span>
                  Listar los productos con stock en esta sucursal
                  <span className="block text-xs text-muted">
                    Aparecen como pendientes, sin cantidad: lo que no cuentes no se toca al aprobar. Los equipos con
                    IMEI no se listan: se verifican uno por uno en «Equipos».
                  </span>
                </span>
              </label>

              {createError ? (
                <p role="alert" className="rounded-xl border border-danger-border bg-danger-surface px-4 py-3 text-sm text-danger">{createError}</p>
              ) : null}

              <button
                type="submit"
                disabled={creating}
                aria-busy={creating || undefined}
                className={internalPrimaryButtonClass}
              >
                {creating ? "Abriendo…" : "Abrir recuento"}
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
                    ? "border-foreground/25 bg-foreground/[0.06] text-foreground"
                    : "border-bd-border text-muted hover:border-foreground/20 hover:text-foreground"
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
                  <Th>Acción</Th>
                </tr>
              </thead>
              <tbody>
                {counts.map((c) => (
                  <tr key={c.id} className="border-b border-bd-border/60">
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
                    <Td><CountStatusBadge count={{ ...c, items: [] }} /></Td>
                    <Td right>{c.counted_items}</Td>
                    <Td muted>{c.created_by_username ?? "—"}</Td>
                    <Td muted>{c.approved_at ? formatDateTime(c.approved_at) : "—"}</Td>
                    <Td>
                      {["draft", "counting", "review"].includes(c.status) ? (
                        <Link href={`/admin/inventory/counts/${c.id}`} aria-label={`Continuar el recuento #${c.id}`}
                          className="font-semibold underline underline-offset-4">Continuar</Link>
                      ) : (
                        <Link href={`/admin/inventory/counts/${c.id}`} aria-label={`Ver el recuento #${c.id}`}
                          className="underline underline-offset-4">Ver</Link>
                      )}
                    </Td>
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
  return (
    <AccessGuard capability="inventory.view" legacyRoles={["inventory", "admin", "superadmin"]}>
      {(access) => <CountsContent user={access.user} access={access} />}
    </AccessGuard>
  );
}
