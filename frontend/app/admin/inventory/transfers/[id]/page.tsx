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
import { AccessGuard } from "../../../components/AccessGuard";
import type { InternalAccess } from "../../../lib/internal-access";
import { PageHeader, internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "../../../components/internal-ui";
import {
  TransferStatusBadge,
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
import {
  cancelTransfer,
  dispatchTransfer,
  fetchTransfer,
  receiveTransfer,
  setTransferItems,
  type StockTransfer,
} from "../../../../lib/inventory";
import { fetchAdminProducts, type AdminProduct } from "../../../../lib/admin";
import type { AuthUser } from "../../../../lib/auth";

type Draft = Record<number, string>;

function TransferDetail({ user, access, transferId }: { user: AuthUser; access: InternalAccess; transferId: number }) {
  const [transfer, setTransfer] = useState<StockTransfer | null>(null);
  const [products, setProducts] = useState<AdminProduct[]>([]);
  const [draft, setDraft] = useState<Draft>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const mayTransfer = access.can("inventory.adjust", ["inventory", "admin", "superadmin"]);

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
        <PageHeader
          eyebrow="Inventario"
          title={transfer ? `Transferencia #${transfer.id}` : "Transferencia"}
          description={transfer ? `${transfer.source_branch_name} → ${transfer.destination_branch_name}` : "Movimiento entre sucursales"}
          actions={<Link href="/admin/inventory/transfers" className={internalButtonClass}>Transferencias</Link>}
        />

        {loading ? <Spinner label="Cargando transferencia…" /> : null}
        {error ? <ErrorBox message={error} /> : null}
        {actionError ? <ErrorBox message={actionError} /> : null}

        {transfer ? (
          <>
            <div className="flex items-center gap-2">
              <TransferStatusBadge transfer={transfer} />
              <span className="text-xs text-muted">
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
                  className={internalPrimaryButtonClass}
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
                  className={internalButtonClass}
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
                  className={internalButtonClass}
                >
                  Anular
                </button>
              </div>
            ) : null}

            {transfer.status === "in_transit" ? (
              <div className="rounded-lg border border-bd-border bg-surface px-4 py-3">
                <p className="text-sm text-muted">
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
                          className="min-w-0 flex-1 truncate text-sm text-muted"
                          htmlFor={`ti-${p.id}`}
                        >
                          {p.name}
                        </label>
                        <input
                          id={`ti-${p.id}`}
                          type="number"
                          min={0}
                          className={`${internalInputClass} w-20`}
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
                    className={internalPrimaryButtonClass}
                  >
                    Guardar líneas
                  </button>
                </>
              ) : transfer.items.length === 0 ? (
                <EmptyBox message="Esta transferencia no tiene productos." />
              ) : (
                <TableWrap>
                  <thead>
                    <tr className="border-b border-bd-border">
                      <Th>Producto</Th>
                      <Th right>Cantidad</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {transfer.items.map((item) => (
                      <tr key={item.id} className="border-b border-bd-border/60">
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
      <AccessGuard capability="inventory.view" legacyRoles={["inventory", "admin", "superadmin"]}>
        {(access) => (
          <AdminShell user={access.user}>
            <ErrorBox message="Identificador de transferencia inválido." />
          </AdminShell>
        )}
      </AccessGuard>
    );
  }

  return (
    <AccessGuard capability="inventory.view" legacyRoles={["inventory", "admin", "superadmin"]}>
      {(access) => <TransferDetail user={access.user} access={access} transferId={transferId} />}
    </AccessGuard>
  );
}
