"use client";

// Phase 2D — one physical count. MIGRATION-D — the counting itself lives in
// `CountWorkspace` (scanner, total/increment, pending, recount).
//
// APPROVAL IS NOT THIS SCREEN'S JOB for a migration count: it belongs to
// MIGRATION-F's review screen, so here a migration count only reads «Pendiente
// de aprobación». An ORDINARY count keeps «Aprobar y ajustar» until F exists:
// removing it now would leave nobody able to apply a count. With MIGRATION-C
// (#175) the button asks for `inventory.counts.approve` — which an approver may
// hold without `inventory.adjust` — and on a server without it, for
// `inventory.adjust`, as it always did. A refused approval lists its reason per
// line, and the count is re-read after any failed action, so the lines the
// server just found ambiguous or overlapping are marked.
//
// COUNT-REVIEW-FREEZE (owner decision B). In review the count is the
// approver's: the counting panel is closed for every kind, and only the
// approver sees «Volver a edición» (asks for a reason, audited), «Aprobar y
// ajustar» (sends the `version` on screen: a count that moved answers 409
// `count_stale` and nothing is applied) and «Anular recuento».

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { AdminShell } from "../../../components/AdminShell";
import { AccessGuard } from "../../../components/AccessGuard";
import type { InternalAccess } from "../../../lib/internal-access";
import { PageHeader, internalButtonClass, internalPrimaryButtonClass } from "../../../components/internal-ui";
import { CountStatusBadge, ErrorBox, Spinner, StatCard, formatDateTime } from "../../../components/InventoryUi";
import { cancelCount } from "../../../../lib/inventory";
import {
  CountApiError,
  fetchCountDetail,
  reopenCount,
  supportsCountContract,
  type CountApprovalProblem,
  type CountDetail,
} from "../../../lib/counts-api";
import type { AuthUser } from "../../../../lib/auth";
import { CountWorkspace } from "../_components/CountWorkspace";
import { lineState, pendingLines } from "../_components/count-lines";
import { approveOrdinaryCount } from "../_components/ordinary-approval";

const ADJUST_ROLES = ["inventory", "admin", "superadmin"];

function CountDetailView({ user, access, countId }: { user: AuthUser; access: InternalAccess; countId: number }) {
  const [count, setCount] = useState<CountDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [problems, setProblems] = useState<CountApprovalProblem[]>([]);
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
    setProblems([]);
    try {
      await action();
      await load();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "No se pudo completar la operación.");
      if (err instanceof CountApiError && err.problems?.length) setProblems(err.problems);
      // What the server just found (an ambiguous or overlapping line) is marked
      // on the lines only once they are read again.
      try {
        await load();
      } catch {
        /* the refusal is shown; the old list stays */
      }
    } finally {
      setBusy(false);
    }
  }

  const contract = supportsCountContract(count);
  const isMigration = count?.kind === "migration";
  const inReview = count?.status === "review";
  // Review #240 P2: a count that went to review and came back is approved only from review again.
  const sentBack = count?.status === "counting" && Boolean(count.was_submitted);
  const approvable = count !== null && (count.status === "review" || (count.status === "counting" && !sentBack));
  const mayApprove = contract
    ? access.can("inventory.counts.approve", ADJUST_ROLES)
    : mayCount;
  // INV-COUNT-UX-04: a draft (opened, nothing counted yet) can be cancelled too.
  // COUNT-REVIEW-FREEZE (B2): in review only the approver cancels.
  const cancellable =
    count !== null && ["draft", "counting", "review"].includes(count.status) && (!inReview || mayApprove);
  const reopenable = contract && inReview && mayApprove;
  const counted = count?.items.filter((i) => i.physical_quantity !== null) ?? [];
  const pending = count ? pendingLines(count) : [];
  const unresolved = count?.items.filter((i) => ["ambiguous", "overlapping"].includes(lineState(i))) ?? [];

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
        {actionError && problems.length === 0 ? <ErrorBox message={actionError} /> : null}
        {problems.length > 0 ? (
          <section
            role="alert"
            aria-labelledby="count-approval-problems"
            className="rounded-xl border border-danger-border bg-danger-surface px-4 py-3 text-sm text-danger"
          >
            <h2 id="count-approval-problems" className="font-semibold">No se pudo aprobar</h2>
            <p className="mt-1">{actionError}</p>
            <ul className="mt-2 list-disc space-y-1 pl-5">
              {problems.map((problem) => (
                <li key={`${problem.product}-${problem.reason}`}>
                  <strong>{problem.product_name}</strong>: {problem.detail}
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        {count ? (
          <>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <StatCard label="Productos contados" value={counted.length} />
              <StatCard label={cancellable ? "Pendientes" : "No contados"} value={pending.length} emphasis={cancellable && pending.length > 0} />
              <StatCard
                label="Aprobado"
                value={count.approved_at ? formatDateTime(count.approved_at) : "—"}
              />
              <StatCard label="Creado por" value={count.created_by_username ?? "—"} />
            </div>

            {sentBack && mayApprove && !isMigration ? (
              <p className="text-sm text-muted">
                Volvió a edición: se aprueba cuando se envíe de nuevo a revisión.
              </p>
            ) : null}

            {(approvable && (mayCount || (mayApprove && !isMigration))) || (cancellable && mayCount) || reopenable ? (
              <div className="flex flex-wrap items-center gap-2">
                {reopenable ? (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      const reason = window.prompt(
                        `¿Por qué vuelve a edición el recuento #${count.id}?\n\n` +
                          "Quien cuenta podrá cambiar cantidades otra vez y tendrá que volver a enviarlo a revisión. " +
                          "El motivo queda registrado con tu nombre.",
                      );
                      if (reason === null) return;
                      if (!reason.trim()) {
                        setProblems([]);
                        setActionError("Indica el motivo para volver a edición: queda registrado con tu nombre.");
                        return;
                      }
                      void run(() => reopenCount(count.id, reason.trim()));
                    }}
                    className={internalButtonClass}
                  >
                    Volver a edición
                  </button>
                ) : null}
                {approvable && !isMigration && mayApprove ? (
                  <button
                    type="button"
                    disabled={busy || counted.length === 0 || unresolved.length > 0}
                    onClick={() => {
                      if (
                        window.confirm(
                          `¿Aprobar el recuento #${count.id}?\n\n` +
                            "Se compararán las cantidades físicas contra el stock de la sucursal " +
                            "y se generarán los movimientos de corrección. " +
                            "Los productos sin contar se omiten. Se aplica exactamente lo que ves en esta " +
                            "pantalla: si el recuento cambió, no se aprueba nada.",
                        )
                      ) {
                        // The version on screen; a server without the contract has none and gets no body.
                        void run(() => approveOrdinaryCount(count.id, count.version));
                      }
                    }}
                    className={internalPrimaryButtonClass}
                  >
                    Aprobar y ajustar
                  </button>
                ) : null}
                {approvable && !isMigration && mayApprove && unresolved.length > 0 ? (
                  <p className="text-sm text-muted">
                    Recuenta las líneas marcadas antes de aprobar ({unresolved.length}).
                  </p>
                ) : null}
                {approvable && isMigration ? (
                  <p className="text-sm text-muted">
                    La aprobación de un recuento de migración se hace en su pantalla de revisión, con el resumen de
                    movimientos previstos. Desde aquí no se mueve stock.
                  </p>
                ) : null}
                {cancellable && mayCount ? (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      if (inReview) {
                        // Review #240 P3: cancelling a count in review is the approver's decision, with a reason.
                        const reason = window.prompt(
                          `¿Por qué se anula el recuento #${count.id}?\n\n` +
                            "Está en revisión: el motivo queda registrado con tu nombre.",
                        );
                        if (reason === null) return;
                        if (!reason.trim()) {
                          setProblems([]);
                          setActionError("Indica por qué se anula el recuento: queda registrado con tu nombre.");
                          return;
                        }
                        void run(() => cancelCount(count.id, reason.trim()));
                        return;
                      }
                      if (window.confirm(`¿Anular el recuento #${count.id}?`)) {
                        void run(() => cancelCount(count.id));
                      }
                    }}
                    className="rounded-lg border border-bd-border px-4 py-2 text-sm text-muted transition hover:border-foreground/20 hover:text-foreground disabled:opacity-40"
                  >
                    Anular recuento
                  </button>
                ) : null}
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
              onRecounted={() => {
                setProblems([]);
                setActionError(null);
              }}
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
