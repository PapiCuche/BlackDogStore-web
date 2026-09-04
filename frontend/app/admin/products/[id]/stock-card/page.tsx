"use client";

// Phase 6.0 — Kardex (stock card) for a single product. Branch-scoped in 2D.
//
// `stock_before` / `stock_after` on each line are THIS BRANCH's running balance,
// not a company total, which is why the card is read one branch at a time: a
// balance interleaved from several shops does not add up in either direction.
// Selecting "todas" still lists every line, and the branch column then says
// which shelf each one moved.

import Link from "next/link";
import { use, useEffect, useState } from "react";
import { AdminShell } from "../../../components/AdminShell";
import { StaffGuard } from "../../../components/StaffGuard";
import {
  EmptyBox,
  ErrorBox,
  Panel,
  Spinner,
  StatCard,
  TableWrap,
  Td,
  Th,
  MovementBadge,
  SignedQty,
  formatDateTime,
  formatSoles,
  movementReference,
} from "../../../components/InventoryUi";
import {
  fetchStockCard,
  type BranchStockRow,
  type InventoryScope,
  type StockMovement,
} from "../../../../lib/inventory";
import { BranchSelector, ScopeNote } from "../../../components/BranchSelector";
import { useBranchScope } from "../../../lib/use-branch-scope";
import type { AuthUser } from "../../../../lib/auth";

type CardData = {
  scope: InventoryScope;
  product: {
    id: number;
    name: string;
    slug: string;
    price: string;
    is_active: boolean;
    category_name: string | null;
  };
  current_stock: number;
  stock_by_branch: BranchStockRow[];
  movements: StockMovement[];
};

function StockCardContent({ user, productId }: { user: AuthUser; productId: number }) {
  const scope = useBranchScope({ preferAggregate: true });
  const [data, setData] = useState<CardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const branch = scope.branch;

  useEffect(() => {
    if (!scope.ready) return;
    let cancelled = false;
    void (async () => {
      try {
        const result = await fetchStockCard(productId, { branch });
        if (!cancelled) {
          setData(result);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "No se pudo cargar el Kardex.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [productId, branch, scope.ready]);

  const entries = data?.movements.filter((m) => m.is_entry) ?? [];
  const exits = data?.movements.filter((m) => !m.is_entry) ?? [];
  const entryUnits = entries.reduce((sum, m) => sum + m.quantity, 0);
  const exitUnits = exits.reduce((sum, m) => sum + m.quantity, 0);

  return (
    <AdminShell user={user}>
      <div className="space-y-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold text-foreground">
              Kardex {data ? `— ${data.product.name}` : ""}
            </h1>
            <p className="mt-1 text-sm text-muted">
              Historial completo de entradas y salidas del producto.
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
              href={`/admin/products/${productId}`}
              className="rounded-lg border border-bd-border px-3.5 py-2 text-sm text-foreground/85 transition hover:border-bd-border hover:text-foreground"
            >
              Ver producto
            </Link>
            <Link
              href="/admin/inventory/movements"
              className="rounded-lg border border-bd-border px-3.5 py-2 text-sm text-foreground/85 transition hover:border-bd-border hover:text-foreground"
            >
              Movimientos
            </Link>
          </div>
        </div>

        <ScopeNote scope={data?.scope} />

        {scope.error ? <ErrorBox message={scope.error} /> : null}
        {loading && scope.ready ? <Spinner label="Cargando Kardex…" /> : null}
        {error ? <ErrorBox message={error} /> : null}

        {data ? (
          <>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <StatCard
                label="Stock actual"
                value={`${data.current_stock} u.`}
                emphasis
                hint={
                  data.scope.is_aggregate
                    ? `Suma de ${data.stock_by_branch.length} sucursal(es)`
                    : (data.scope.branch?.name ?? undefined)
                }
              />
              <StatCard label="Precio" value={formatSoles(data.product.price)} />
              <StatCard
                label="Entradas registradas"
                value={`+${entryUnits}`}
                hint={`${entries.length} movimiento(s)`}
              />
              <StatCard
                label="Salidas registradas"
                value={`−${exitUnits}`}
                hint={`${exits.length} movimiento(s)`}
              />
            </div>

            {data.stock_by_branch.length > 1 ? (
              <Panel title="Stock por sucursal" description="Dónde están las unidades">
                <TableWrap>
                  <thead>
                    <tr className="border-b border-bd-border">
                      <Th>Sucursal</Th>
                      <Th right>Stock</Th>
                      <Th right>Mínimo</Th>
                      <Th right>Objetivo</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.stock_by_branch.map((row) => (
                      <tr key={row.id} className="border-b border-bd-border">
                        <Td>{row.branch_name}</Td>
                        <Td right>{row.quantity}</Td>
                        <Td right muted>{row.minimum_stock || "—"}</Td>
                        <Td right muted>{row.target_stock || "—"}</Td>
                      </tr>
                    ))}
                  </tbody>
                </TableWrap>
              </Panel>
            ) : null}

            <Panel
              title="Movimientos"
              description={`${data.movements.length} línea(s) — más reciente primero`}
            >
              {data.movements.length === 0 ? (
                <EmptyBox message="Este producto todavía no registra movimientos de stock." />
              ) : (
                <TableWrap>
                  <thead>
                    <tr className="border-b border-bd-border">
                      <Th>Fecha</Th>
                      <Th>Sucursal</Th>
                      <Th>Tipo</Th>
                      <Th right>Cant.</Th>
                      <Th right>Antes</Th>
                      <Th right>Después</Th>
                      <Th>Motivo</Th>
                      <Th>Responsable</Th>
                      <Th>Referencia</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.movements.map((m) => (
                      <tr key={m.id} className="border-b border-bd-border">
                        <Td muted>{formatDateTime(m.created_at)}</Td>
                        <Td muted>{m.branch_name}</Td>
                        <Td><MovementBadge movement={m} /></Td>
                        <Td right><SignedQty movement={m} /></Td>
                        <Td right muted>{m.stock_before}</Td>
                        <Td right>{m.stock_after}</Td>
                        <Td muted>{m.reason || "—"}</Td>
                        <Td muted>{m.actor_username ?? "Sistema"}</Td>
                        <Td muted>{movementReference(m)}</Td>
                      </tr>
                    ))}
                  </tbody>
                </TableWrap>
              )}
            </Panel>
          </>
        ) : null}
      </div>
    </AdminShell>
  );
}

export default function StockCardPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const productId = Number(id);

  if (!Number.isFinite(productId)) {
    return (
      <StaffGuard>
        {(user) => (
          <AdminShell user={user}>
            <ErrorBox message="Identificador de producto inválido." />
          </AdminShell>
        )}
      </StaffGuard>
    );
  }

  return <StaffGuard>{(user) => <StockCardContent user={user} productId={productId} />}</StaffGuard>;
}
