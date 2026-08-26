"use client";

// Phase 6.0 — Kardex (stock card) for a single product.

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
import { fetchStockCard, type InventoryProduct, type StockMovement } from "../../../../lib/inventory";
import type { AuthUser } from "../../../../lib/auth";

type CardData = {
  product: InventoryProduct;
  current_stock: number;
  movements: StockMovement[];
};

function StockCardContent({ user, productId }: { user: AuthUser; productId: number }) {
  const [data, setData] = useState<CardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const result = await fetchStockCard(productId);
        if (!cancelled) setData(result);
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
  }, [productId]);

  const entries = data?.movements.filter((m) => m.is_entry) ?? [];
  const exits = data?.movements.filter((m) => !m.is_entry) ?? [];
  const entryUnits = entries.reduce((sum, m) => sum + m.quantity, 0);
  const exitUnits = exits.reduce((sum, m) => sum + m.quantity, 0);

  return (
    <AdminShell user={user}>
      <div className="space-y-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold text-white">
              Kardex {data ? `— ${data.product.name}` : ""}
            </h1>
            <p className="mt-1 text-sm text-zinc-500">
              Historial completo de entradas y salidas del producto.
            </p>
          </div>
          <div className="flex gap-2">
            <Link
              href={`/admin/products/${productId}`}
              className="rounded-lg border border-white/10 px-3.5 py-2 text-sm text-zinc-300 transition hover:border-white/20 hover:text-white"
            >
              Ver producto
            </Link>
            <Link
              href="/admin/inventory/movements"
              className="rounded-lg border border-white/10 px-3.5 py-2 text-sm text-zinc-300 transition hover:border-white/20 hover:text-white"
            >
              Movimientos
            </Link>
          </div>
        </div>

        {loading ? <Spinner label="Cargando Kardex…" /> : null}
        {error ? <ErrorBox message={error} /> : null}

        {data ? (
          <>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <StatCard label="Stock actual" value={`${data.current_stock} u.`} emphasis />
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

            <Panel
              title="Movimientos"
              description={`${data.movements.length} línea(s) — más reciente primero`}
            >
              {data.movements.length === 0 ? (
                <EmptyBox message="Este producto todavía no registra movimientos de stock." />
              ) : (
                <TableWrap>
                  <thead>
                    <tr className="border-b border-white/[0.06]">
                      <Th>Fecha</Th>
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
                      <tr key={m.id} className="border-b border-white/[0.03]">
                        <Td muted>{formatDateTime(m.created_at)}</Td>
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
