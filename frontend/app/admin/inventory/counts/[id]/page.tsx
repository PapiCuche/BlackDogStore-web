"use client";

// Phase 2D — one physical count. MIGRATION-D — the counting itself lives in
// `CountWorkspace` (scanner, total/increment, pending, recount).
//
// APPROVAL IS NOT THIS SCREEN'S JOB for a migration count: it belongs to
// MIGRATION-F's review screen, so here a migration count only reads «Pendiente
// de aprobación». An ORDINARY count keeps today's «Aprobar y ajustar» until F
// exists: removing it now would leave nobody able to apply a count. On a server
// that implements MIGRATION-C the button asks for `inventory.counts.approve`;
// on today's server, for `inventory.adjust`, as it always did.

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { AdminShell } from "../../../components/AdminShell";
import { AccessGuard } from "../../../components/AccessGuard";
import type { InternalAccess } from "../../../lib/internal-access";
import { PageHeader, internalButtonClass, internalPrimaryButtonClass } from "../../../components/internal-ui";
import { CountStatusBadge, ErrorBox, Spinner, StatCard, formatDateTime } from "../../../components/InventoryUi";
import { approveCount, cancelCount } from "../../../../lib/inventory";
import { fetchCountDetail, supportsCountContract, type CountDetail } from "../../../lib/counts-api";
import type { AuthUser } from "../../../../lib/auth";
import { CountWorkspace } from "../_components/CountWorkspace";
import { pendingLines } from "../_components/count-lines";

const ADJUST_ROLES = ["inventory", "admin", "superadmin"];

function CountDetailView({ user, access, countId }: { user: AuthUser; access: InternalAccess; countId: number }) {
  const [count, setCount] = useState<CountDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const mayCount = access.can("inventory.adjust", ADJUST_ROLES);
  const canSeeCosts = access.can("costs.view", ["admin", "superadmin"]);

  const load = useCallback(async () => {
    const data = await fetchCountDetail(countId);
    setCount(data);
    return data;
  }, [countId]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const data = await fetchCountDetail(countId);
        if (!cancelled) {
          setCount(data);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "No se pudo cargar el recuento.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [countId]);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setActionError(null);
    try {
      await action();
      await load();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "No se pudo completar la operación.");
    } finally {
      setBusy(false);
    }
  }

  const contract = supportsCountContract(count);
  const isMigration = count?.kind === "migration";
  const approvable = count !== null && ["counting", "review"].includes(count.status);
  const mayApprove = contract
    ? access.can("inventory.counts.approve", ADJUST_ROLES)
    : mayCount;
  const counted = count?.items.filter((i) => i.physical_quantity !== null) ?? [];
  const pending = count ? pendingLines(count) : [];

  return (
    <AdminShell user={user}>
      <div className="space-y-8">
        <PageHeader
          eyebrow="Inventario"
          title={count ? `Recuento #${count.id}` : "Recuento"}
          description={count ? `${count.branch_name}${isMigration ? " · migración" : ""}` : "Conteo físico"}
          actions={
            <>
              {count ? <CountStatusBadge count={{ ...count, items: [] }} /> : null}
              <Link href="/admin/inventory/counts" className={internalButtonClass}>Recuentos</Link>
            </>
          }
        />

        {loading ? <Spinner label="Cargando recuento…" /> : null}
        {error ? <ErrorBox message={error} /> : null}
        {actionError ? <ErrorBox message={actionError} /> : null}

        {count ? (
          <>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <StatCard label="Productos contados" value={counted.length} />
              <StatCard label="Pendientes" value={pending.length} emphasis={pending.length > 0} />
              <StatCard
                label="Aprobado"
                value={count.approved_at ? formatDateTime(count.approved_at) : "—"}
              />
              <StatCard label="Creado por" value={count.created_by_username ?? "—"} />
            </div>

            {mayCount && approvable ? (
              <div className="flex flex-wrap items-center gap-2">
                {!isMigration && mayApprove ? (
                  <button
                    type="button"
                    disabled={busy || counted.length === 0}
                    onClick={() => {
                      if (
                        window.confirm(
                          `¿Aprobar el recuento #${count.id}?\n\n` +
                            "Se compararán las cantidades físicas contra el stock de la sucursal " +
                            "y se generarán los movimientos de corrección. " +
                            "Los productos sin contar se omiten.",
                        )
                      ) {
                        void run(() => approveCount(count.id));
                      }
                    }}
                    className={internalPrimaryButtonClass}
                  >
                    Aprobar y ajustar
                  </button>
                ) : null}
                {isMigration ? (
                  <p className="text-sm text-muted">
                    La aprobación de un recuento de migración se hace en su pantalla de revisión, con el resumen de
                    movimientos previstos. Desde aquí no se mueve stock.
                  </p>
                ) : null}
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    if (window.confirm(`¿Anular el recuento #${count.id}?`)) {
                      void run(() => cancelCount(count.id));
                    }
                  }}
                  className="rounded-lg border border-bd-border px-4 py-2 text-sm text-muted transition hover:border-foreground/20 hover:text-foreground disabled:opacity-40"
                >
                  Anular
                </button>
              </div>
            ) : null}

            {count.status === "approved" ? (
              <div className="rounded-lg border border-bd-border bg-surface px-4 py-3">
                <p className="text-sm text-muted">
                  Este recuento ya generó movimientos de corrección y no puede
                  anularse. Para corregirlo, abre un recuento nuevo.
                </p>
              </div>
            ) : null}

            <CountWorkspace
              count={count}
              onCountChange={setCount}
              canCount={mayCount}
              canSeeCosts={canSeeCosts}
            />
          </>
        ) : null}
      </div>
    </AdminShell>
  );
}

export default function CountDetailPage() {
  const { id } = useParams<{ id: string }>();
  const countId = Number(id);

  if (!Number.isFinite(countId)) {
    return (
      <AccessGuard capability="inventory.view" legacyRoles={["inventory", "admin", "superadmin"]}>
        {(access) => (
          <AdminShell user={access.user}>
            <ErrorBox message="Identificador de recuento inválido." />
          </AdminShell>
        )}
      </AccessGuard>
    );
  }

  return (
    <AccessGuard capability="inventory.view" legacyRoles={["inventory", "admin", "superadmin"]}>
      {(access) => <CountDetailView user={access.user} access={access} countId={countId} />}
    </AccessGuard>
  );
}
