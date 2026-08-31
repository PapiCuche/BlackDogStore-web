"use client";

// Phase 2D — one physical count: enter quantities, review, approve.
//
// The table shows all three numbers on purpose. `Teórico inicial` is the
// evidence of what the counter was looking at; `Teórico al aprobar` is what the
// correction was actually computed against. They differ exactly when the shop
// kept trading during the count, which is the normal case and the reason the
// difference is not computed from the first one.

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
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
  formatDateTime,
} from "../../../components/InventoryUi";
import { CountStatusBadge } from "../page";
import {
  approveCount,
  cancelCount,
  fetchCount,
  setCountItems,
  type InventoryCount,
} from "../../../../lib/inventory";
import { fetchAdminProducts, type AdminProduct } from "../../../../lib/admin";
import { canManageInventory, type AuthUser } from "../../../../lib/auth";

type Draft = Record<number, string>;

function CountDetail({ user, countId }: { user: AuthUser; countId: number }) {
  const [count, setCount] = useState<InventoryCount | null>(null);
  const [products, setProducts] = useState<AdminProduct[]>([]);
  const [draft, setDraft] = useState<Draft>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const mayCount = canManageInventory(user);

  const load = useCallback(async () => {
    const data = await fetchCount(countId);
    setCount(data);
    const next: Draft = {};
    for (const item of data.items) {
      next[item.product] = item.physical_quantity === null ? "" : String(item.physical_quantity);
    }
    setDraft(next);
    return data;
  }, [countId]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        await load();
        if (!cancelled) setError(null);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "No se pudo cargar el recuento.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const data = await fetchAdminProducts({ page_size: 100 });
        if (!cancelled) setProducts(data.results);
      } catch {
        /* the count still reads without the product list */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

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

  async function saveQuantities() {
    // Only products with something typed are sent. A blank field means NOT
    // COUNTED, and sending it as zero would write off stock nobody looked at.
    const lines = Object.entries(draft)
      .filter(([, value]) => value.trim() !== "")
      .map(([product, value]) => ({
        product: Number(product),
        physical_quantity: Math.max(0, parseInt(value, 10) || 0),
      }));
    if (lines.length === 0) return;
    await run(() => setCountItems(countId, lines));
  }

  const editable =
    count !== null && ["draft", "counting", "review"].includes(count.status);
  const approvable = count !== null && ["counting", "review"].includes(count.status);
  const counted = count?.items.filter((i) => i.is_counted) ?? [];
  const withDifference = count?.items.filter((i) => (i.difference ?? 0) !== 0) ?? [];

  return (
    <AdminShell user={user}>
      <div className="space-y-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold text-white">
              Recuento {count ? `#${count.id}` : ""}
            </h1>
            {count ? (
              <p className="mt-1 flex items-center gap-2 text-sm text-zinc-500">
                {count.branch_name} <CountStatusBadge count={count} />
              </p>
            ) : null}
          </div>
          <Link
            href="/admin/inventory/counts"
            className="rounded-lg border border-white/10 px-3.5 py-2 text-sm text-zinc-300 transition hover:border-white/20 hover:text-white"
          >
            ← Recuentos
          </Link>
        </div>

        {loading ? <Spinner label="Cargando recuento…" /> : null}
        {error ? <ErrorBox message={error} /> : null}
        {actionError ? <ErrorBox message={actionError} /> : null}

        {count ? (
          <>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <StatCard label="Productos contados" value={counted.length} />
              <StatCard
                label="Con diferencia"
                value={withDifference.length}
                emphasis={withDifference.length > 0}
                hint={count.status === "approved" ? "Ajustadas al aprobar" : "Se calcula al aprobar"}
              />
              <StatCard
                label="Aprobado"
                value={count.approved_at ? formatDateTime(count.approved_at) : "—"}
              />
              <StatCard label="Creado por" value={count.created_by_username ?? "—"} />
            </div>

            {mayCount && approvable ? (
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  disabled={busy || counted.length === 0}
                  onClick={() => {
                    if (
                      window.confirm(
                        `¿Aprobar el recuento #${count.id}?\n\n` +
                          "Se compararán las cantidades físicas contra el stock ACTUAL " +
                          "de la sucursal y se generarán los movimientos de corrección. " +
                          "Los productos sin contar se omiten.",
                      )
                    ) {
                      void run(() => approveCount(count.id));
                    }
                  }}
                  className="rounded-lg bg-white px-4 py-2 text-sm font-semibold text-black transition hover:bg-zinc-200 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  Aprobar y ajustar
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    if (window.confirm(`¿Anular el recuento #${count.id}?`)) {
                      void run(() => cancelCount(count.id));
                    }
                  }}
                  className="rounded-lg border border-white/10 px-4 py-2 text-sm text-zinc-400 transition hover:border-white/20 hover:text-white disabled:opacity-40"
                >
                  Anular
                </button>
              </div>
            ) : null}

            {count.status === "approved" ? (
              <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-4 py-3">
                <p className="text-sm text-zinc-400">
                  Este recuento ya generó movimientos de corrección y no puede
                  anularse. Para corregirlo, abre un recuento nuevo.
                </p>
              </div>
            ) : null}

            {mayCount && editable ? (
              <Panel
                title="Registrar cantidades físicas"
                description="Deja en blanco lo que no se contó — no es lo mismo que contar cero."
              >
                <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {products.map((p) => (
                    <div key={p.id} className="flex items-center gap-2">
                      <label
                        className="min-w-0 flex-1 truncate text-sm text-zinc-400"
                        htmlFor={`ci-${p.id}`}
                      >
                        {p.name}
                      </label>
                      <input
                        id={`ci-${p.id}`}
                        type="number"
                        min={0}
                        placeholder="—"
                        className="w-20 rounded-lg border border-white/[0.08] bg-black/40 px-2 py-1.5 text-sm text-zinc-200 outline-none transition focus:border-white/25"
                        value={draft[p.id] ?? ""}
                        onChange={(e) => setDraft((d) => ({ ...d, [p.id]: e.target.value }))}
                      />
                    </div>
                  ))}
                </div>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void saveQuantities()}
                  className="rounded-lg bg-white px-4 py-2 text-sm font-semibold text-black transition hover:bg-zinc-200 disabled:opacity-40"
                >
                  Guardar cantidades
                </button>
              </Panel>
            ) : null}

            <Panel title="Líneas del recuento" description={`${count.items.length} producto(s)`}>
              {count.items.length === 0 ? (
                <EmptyBox message="Todavía no se registró ninguna cantidad." />
              ) : (
                <TableWrap>
                  <thead>
                    <tr className="border-b border-white/[0.06]">
                      <Th>Producto</Th>
                      <Th right>Teórico inicial</Th>
                      <Th right>Físico</Th>
                      <Th right>Teórico al aprobar</Th>
                      <Th right>Diferencia</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {count.items.map((item) => (
                      <tr key={item.id} className="border-b border-white/[0.03]">
                        <Td>{item.product_name}</Td>
                        <Td right muted>{item.theoretical_at_start}</Td>
                        <Td right>
                          {item.physical_quantity === null ? (
                            <span className="text-zinc-600">Sin contar</span>
                          ) : (
                            item.physical_quantity
                          )}
                        </Td>
                        <Td right muted>{item.theoretical_at_approval ?? "—"}</Td>
                        <Td right>
                          {item.difference === null ? (
                            <span className="text-zinc-600">—</span>
                          ) : (
                            <span
                              className={
                                item.difference === 0
                                  ? "text-zinc-500"
                                  : "font-medium text-white"
                              }
                            >
                              {item.difference > 0 ? `+${item.difference}` : item.difference}
                            </span>
                          )}
                        </Td>
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

export default function CountDetailPage() {
  const { id } = useParams<{ id: string }>();
  const countId = Number(id);

  if (!Number.isFinite(countId)) {
    return (
      <StaffGuard>
        {(user) => (
          <AdminShell user={user}>
            <ErrorBox message="Identificador de recuento inválido." />
          </AdminShell>
        )}
      </StaffGuard>
    );
  }

  return <StaffGuard>{(user) => <CountDetail user={user} countId={countId} />}</StaffGuard>;
}
