"use client";

// Phase 2D — replenishment suggestions.
//
// THIS SCREEN SUGGESTS AND NOTHING ELSE.
// It opens no purchase and creates no transfer. `suggested = target − current`,
// shown only for rows at or below their branch minimum, because topping up a
// well-stocked product is not replenishment, it is tying up cash.
//
// "Excedente en otras sucursales" is information, not an action: it says the
// units may already be inside the company. Moving them is a transfer, which the
// operator opens deliberately and which is permission-checked when they do.

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
  StatCard,
  TableWrap,
  Td,
  Th,
} from "../../components/InventoryUi";
import {
  fetchReplenishment,
  type InventoryScope,
  type ReplenishmentRow,
} from "../../../lib/inventory";
import type { AuthUser } from "../../../lib/auth";

function ReplenishmentContent({ user }: { user: AuthUser }) {
  const scope = useBranchScope({ preferAggregate: true });
  const [rows, setRows] = useState<ReplenishmentRow[]>([]);
  const [resultScope, setResultScope] = useState<InventoryScope | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const branch = scope.branch;

  const load = useCallback(async () => {
    return fetchReplenishment({ branch, limit: 100, with_surplus: "true" });
  }, [branch]);

  useEffect(() => {
    if (!scope.ready) return;
    let cancelled = false;
    void (async () => {
      try {
        const data = await load();
        if (cancelled) return;
        setRows(data.results);
        setResultScope(data.scope);
        setError(null);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "No se pudo cargar la reposición.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [load, scope.ready]);

  const totalSuggested = rows.reduce((sum, r) => sum + r.suggested_quantity, 0);
  const withoutTarget = rows.filter((r) => r.target === 0).length;

  return (
    <AdminShell user={user}>
      <div className="space-y-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold text-foreground">Reposición sugerida</h1>
            <p className="mt-1 text-sm text-muted">
              Productos en o por debajo de su mínimo, con la cantidad que faltaría
              para alcanzar el objetivo de esa sucursal.
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
        {loading && scope.ready ? <Spinner label="Calculando reposición…" /> : null}
        {error ? <ErrorBox message={error} /> : null}

        {!loading && !error && scope.ready ? (
          <>
            <div className="grid gap-4 sm:grid-cols-3">
              <StatCard label="Productos a reponer" value={rows.length} emphasis={rows.length > 0} />
              <StatCard label="Unidades sugeridas" value={totalSuggested} />
              <StatCard
                label="Sin objetivo configurado"
                value={withoutTarget}
                hint="No se puede sugerir cantidad"
              />
            </div>

            <Panel
              title="Sugerencias"
              description="Es una sugerencia: no genera compras ni transferencias"
            >
              {rows.length === 0 ? (
                <EmptyBox message="Ningún producto está por debajo de su mínimo. Los mínimos se configuran por sucursal." />
              ) : (
                <TableWrap>
                  <thead>
                    <tr className="border-b border-bd-border">
                      <Th>Producto</Th>
                      <Th>Sucursal</Th>
                      <Th right>Actual</Th>
                      <Th right>Mínimo</Th>
                      <Th right>Objetivo</Th>
                      <Th right>Sugerido</Th>
                      <Th>Excedente en otras sucursales</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr
                        key={`${row.branch_id}-${row.product_id}`}
                        className="border-b border-bd-border"
                      >
                        <Td>
                          <Link
                            href={`/admin/products/${row.product_id}/stock-card`}
                            className="transition hover:text-foreground"
                          >
                            {row.product_name}
                          </Link>
                        </Td>
                        <Td muted>{row.branch_name}</Td>
                        <Td right>{row.current}</Td>
                        <Td right muted>{row.minimum}</Td>
                        <Td right muted>{row.target || "—"}</Td>
                        <Td right>
                          <span className="tabular-nums font-medium text-foreground">
                            {row.suggested_quantity || "—"}
                          </span>
                        </Td>
                        <Td muted>
                          {row.surplus_branches && row.surplus_branches.length > 0
                            ? row.surplus_branches
                                .map((b) => `${b.branch_name} (+${b.surplus})`)
                                .join(" · ")
                            : "—"}
                        </Td>
                      </tr>
                    ))}
                  </tbody>
                </TableWrap>
              )}
            </Panel>

            {rows.some((r) => (r.surplus_branches?.length ?? 0) > 0) ? (
              <div className="rounded-lg border border-bd-border bg-surface px-4 py-3">
                <p className="text-sm text-muted">
                  Algunas de estas unidades ya están en otra sucursal de la empresa.
                  Para moverlas, abre una{" "}
                  <Link
                    href="/admin/inventory/transfers"
                    className="text-foreground underline underline-offset-4 transition hover:text-foreground"
                  >
                    transferencia
                  </Link>
                  . Nada se mueve automáticamente.
                </p>
              </div>
            ) : null}
          </>
        ) : null}
      </div>
    </AdminShell>
  );
}

export default function ReplenishmentPage() {
  return <StaffGuard>{(user) => <ReplenishmentContent user={user} />}</StaffGuard>;
}
