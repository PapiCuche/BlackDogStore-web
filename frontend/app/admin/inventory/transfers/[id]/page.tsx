"use client";

// Phase 2D — one transfer: lines, dispatch, receipt.
//
// Lines are editable only in BORRADOR. After dispatch the document describes
// units already on a van, and editing it would make the Kardex disagree with
// the paperwork somebody is holding.
//
// Cancelling is offered only for a draft. A dispatched transfer cannot be
// reversed with a status change — its stock has physically left the source — and
// the backend refuses with that explanation rather than silently returning units
// the shop does not have. Compensating movements are not implemented in V1.

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
import { TransferStatusBadge } from "../page";
import {
  cancelTransfer,
  dispatchTransfer,
  fetchTransfer,
  receiveTransfer,
  setTransferItems,
  type StockTransfer,
} from "../../../../lib/inventory";
import { fetchAdminProducts, type AdminProduct } from "../../../../lib/admin";
import { canManageInventory, type AuthUser } from "../../../../lib/auth";

type Draft = Record<number, string>;

function TransferDetail({ user, transferId }: { user: AuthUser; transferId: number }) {
  const [transfer, setTransfer] = useState<StockTransfer | null>(null);
  const [products, setProducts] = useState<AdminProduct[]>([]);
  const [draft, setDraft] = useState<Draft>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const mayTransfer = canManageInventory(user);

  const load = useCallback(async () => {
    const data = await fetchTransfer(transferId);
    setTransfer(data);
    const next: Draft = {};
    for (const item of data.items) next[item.product] = String(item.quantity);
    setDraft(next);
    return data;
  }, [transferId]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        await load();
        if (!cancelled) setError(null);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "No se pudo cargar la transferencia.");
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
        /* the transfer still reads without the product list */
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

  async function saveLines() {
    const lines = Object.entries(draft)
      .map(([product, quantity]) => ({
        product: Number(product),
        quantity: Math.max(0, parseInt(quantity, 10) || 0),
      }))
      .filter((line) => line.quantity > 0);
    await run(() => setTransferItems(transferId, lines));
  }

  const editable = transfer?.status === "draft";
  const canDispatch = editable && (transfer?.items.length ?? 0) > 0;
  const canReceive = transfer?.status === "in_transit";

  return (
    <AdminShell user={user}>
      <div className="space-y-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold text-white">
              Transferencia {transfer ? `#${transfer.id}` : ""}
            </h1>
            {transfer ? (
              <p className="mt-1 text-sm text-zinc-500">
                {transfer.source_branch_name} → {transfer.destination_branch_name}
              </p>
            ) : null}
          </div>
          <Link
            href="/admin/inventory/transfers"
            className="rounded-lg border border-white/10 px-3.5 py-2 text-sm text-zinc-300 transition hover:border-white/20 hover:text-white"
          >
            ← Transferencias
          </Link>
        </div>

        {loading ? <Spinner label="Cargando transferencia…" /> : null}
        {error ? <ErrorBox message={error} /> : null}
        {actionError ? <ErrorBox message={actionError} /> : null}

        {transfer ? (
          <>
            <div className="flex items-center gap-2">
              <TransferStatusBadge transfer={transfer} />
              <span className="text-xs text-zinc-600">
                {transfer.items.length} línea(s) · creada por{" "}
                {transfer.created_by_username ?? "—"}
              </span>
            </div>

            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <StatCard label="Estado" value={transfer.status_label} />
              <StatCard label="Unidades" value={transfer.total_units} />
              <StatCard
                label="Despachada"
                value={transfer.dispatched_at ? formatDateTime(transfer.dispatched_at) : "—"}
              />
              <StatCard
                label="Recibida"
                value={transfer.received_at ? formatDateTime(transfer.received_at) : "—"}
              />
            </div>

            {mayTransfer ? (
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  disabled={busy || !canDispatch}
                  onClick={() => {
                    if (
                      window.confirm(
                        `¿Despachar la transferencia #${transfer.id}?\n\n` +
                          `Se descontará el stock de ${transfer.source_branch_name}. ` +
                          "Una vez despachada no podrá editarse ni anularse.",
                      )
                    ) {
                      void run(() => dispatchTransfer(transfer.id));
                    }
                  }}
                  className="rounded-lg bg-white px-4 py-2 text-sm font-semibold text-black transition hover:bg-zinc-200 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  Despachar
                </button>
                <button
                  type="button"
                  disabled={busy || !canReceive}
                  onClick={() => {
                    if (
                      window.confirm(
                        `¿Recibir la transferencia #${transfer.id}?\n\n` +
                          `Se sumará el stock a ${transfer.destination_branch_name}.`,
                      )
                    ) {
                      void run(() => receiveTransfer(transfer.id));
                    }
                  }}
                  className="rounded-lg border border-white/15 px-4 py-2 text-sm font-medium text-zinc-200 transition hover:border-white/30 hover:text-white disabled:cursor-not-allowed disabled:opacity-40"
                >
                  Recibir
                </button>
                <button
                  type="button"
                  disabled={busy || !editable}
                  onClick={() => {
                    if (window.confirm(`¿Anular la transferencia #${transfer.id}?`)) {
                      void run(() => cancelTransfer(transfer.id));
                    }
                  }}
                  className="rounded-lg border border-white/10 px-4 py-2 text-sm text-zinc-400 transition hover:border-white/20 hover:text-white disabled:cursor-not-allowed disabled:opacity-40"
                >
                  Anular
                </button>
              </div>
            ) : null}

            {transfer.status === "in_transit" ? (
              <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-4 py-3">
                <p className="text-sm text-zinc-400">
                  El stock ya salió de {transfer.source_branch_name} y todavía no
                  entró en {transfer.destination_branch_name}. Una transferencia
                  despachada no se anula: debe recibirse.
                </p>
              </div>
            ) : null}

            <Panel
              title="Productos"
              description={
                editable
                  ? "Edita las cantidades y guarda. Una cantidad de 0 elimina la línea."
                  : "Las líneas quedaron fijas al despachar."
              }
            >
              {editable && mayTransfer ? (
                <>
                  <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                    {products.map((p) => (
                      <div key={p.id} className="flex items-center gap-2">
                        <label
                          className="min-w-0 flex-1 truncate text-sm text-zinc-400"
                          htmlFor={`ti-${p.id}`}
                        >
                          {p.name}
                        </label>
                        <input
                          id={`ti-${p.id}`}
                          type="number"
                          min={0}
                          className="w-20 rounded-lg border border-white/[0.08] bg-black/40 px-2 py-1.5 text-sm text-zinc-200 outline-none transition focus:border-white/25"
                          value={draft[p.id] ?? ""}
                          onChange={(e) =>
                            setDraft((d) => ({ ...d, [p.id]: e.target.value }))
                          }
                        />
                      </div>
                    ))}
                  </div>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void saveLines()}
                    className="rounded-lg bg-white px-4 py-2 text-sm font-semibold text-black transition hover:bg-zinc-200 disabled:opacity-40"
                  >
                    Guardar líneas
                  </button>
                </>
              ) : transfer.items.length === 0 ? (
                <EmptyBox message="Esta transferencia no tiene productos." />
              ) : (
                <TableWrap>
                  <thead>
                    <tr className="border-b border-white/[0.06]">
                      <Th>Producto</Th>
                      <Th right>Cantidad</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {transfer.items.map((item) => (
                      <tr key={item.id} className="border-b border-white/[0.03]">
                        <Td>{item.product_name}</Td>
                        <Td right>{item.quantity}</Td>
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

export default function TransferDetailPage() {
  const { id } = useParams<{ id: string }>();
  const transferId = Number(id);

  if (!Number.isFinite(transferId)) {
    return (
      <StaffGuard>
        {(user) => (
          <AdminShell user={user}>
            <ErrorBox message="Identificador de transferencia inválido." />
          </AdminShell>
        )}
      </StaffGuard>
    );
  }

  return <StaffGuard>{(user) => <TransferDetail user={user} transferId={transferId} />}</StaffGuard>;
}
