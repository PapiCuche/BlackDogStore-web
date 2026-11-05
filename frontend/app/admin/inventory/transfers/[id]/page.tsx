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
import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { AdminShell } from "../../../components/AdminShell";
import { AccessGuard } from "../../../components/AccessGuard";
import type { InternalAccess } from "../../../lib/internal-access";
import { PageHeader, internalButtonClass, internalPrimaryButtonClass } from "../../../components/internal-ui";
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
import { TransferDraftLines } from "../_components/TransferDraftLines";
import { buildDraftRows, draftLines, type TransferDraft } from "../_components/transfer-draft";

type Draft = TransferDraft;

/** The server's largest page; the loop below walks every page of it. */
const PRODUCT_PAGE_SIZE = 100;
/** A ceiling so a broken `count` cannot keep the screen fetching forever. */
const MAX_PRODUCT_PAGES = 50;

type Catalogue = { products: AdminProduct[]; complete: boolean };

/**
 * Every product of `company` that can travel BY QUANTITY. Serialized products
 * are left out because the server refuses them on a transfer line
 * (`set_transfer_item`): offering them only produced a save that failed for the
 * whole list. All pages are read — with only the first 100, a search would say
 * «no coincide» for a product that exists.
 *
 * - A failing page (a 429 from the shared `admin_products` throttle, a network
 *   error) keeps what was already read and answers `complete: false`, so the
 *   screen can say the list is partial instead of «no hay productos».
 * - The company is explicit: a long loop must not drift to another company
 *   chosen meanwhile in another tab.
 * - Offset pages over `-created_at` can repeat a product across pages: rows are
 *   kept once per id (a repeated id would duplicate `id="ti-N"`).
 * - `null` when the caller went away: it stops asking for pages.
 */
async function fetchTransferableProducts(company: number, isCancelled: () => boolean): Promise<Catalogue | null> {
  const byId = new Map<number, AdminProduct>();
  const result = (complete: boolean): Catalogue => ({ products: [...byId.values()], complete });
  let fetched = 0;
  for (let page = 1; page <= MAX_PRODUCT_PAGES; page += 1) {
    let data: Awaited<ReturnType<typeof fetchAdminProducts>>;
    try {
      data = await fetchAdminProducts({ company, is_serialized: "false", page_size: PRODUCT_PAGE_SIZE, page });
    } catch {
      return isCancelled() ? null : result(false);
    }
    if (isCancelled()) return null;
    for (const product of data.results) if (!byId.has(product.id)) byId.set(product.id, product);
    fetched += data.results.length;
    if (data.results.length === 0 || fetched >= data.count) return result(true);
  }
  // The ceiling was reached before the end: say so rather than pretend it is all.
  return result(false);
}

function TransferDetail({ user, access, transferId }: { user: AuthUser; access: InternalAccess; transferId: number }) {
  const [transfer, setTransfer] = useState<StockTransfer | null>(null);
  const [products, setProducts] = useState<AdminProduct[]>([]);
  const [draft, setDraft] = useState<Draft>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  /** Which load (`company:attempt`) the current `products` answer; another key means «still loading». */
  const [catalogueLoaded, setCatalogueLoaded] = useState<string | null>(null);
  const [productsIncomplete, setProductsIncomplete] = useState(false);
  const [catalogueAttempt, setCatalogueAttempt] = useState(0);

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

  // The catalogue is only needed to EDIT a draft: a read-only view or a
  // dispatched transfer does not spend the shared product-listing budget.
  const catalogueCompany = transfer?.status === "draft" && mayTransfer ? transfer.company : null;
  const catalogueKey = catalogueCompany === null ? null : `${catalogueCompany}:${catalogueAttempt}`;
  const productsLoading = catalogueKey !== null && catalogueLoaded !== catalogueKey;

  useEffect(() => {
    if (catalogueCompany === null) return;
    const key = `${catalogueCompany}:${catalogueAttempt}`;
    let cancelled = false;
    void (async () => {
      const catalogue = await fetchTransferableProducts(catalogueCompany, () => cancelled);
      if (!catalogue) return;
      setProducts(catalogue.products);
      setProductsIncomplete(!catalogue.complete);
      setCatalogueLoaded(key);
    })();
    return () => {
      cancelled = true;
    };
  }, [catalogueCompany, catalogueAttempt]);

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
    // The WHOLE draft, not the rows on screen: a line hidden by the search is still sent.
    const lines = draftLines(draft);
    await run(() => setTransferItems(transferId, lines));
  }

  const rows = useMemo(() => buildDraftRows(products, transfer?.items ?? []), [products, transfer]);

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
                <TransferDraftLines
                  rows={rows}
                  draft={draft}
                  onQuantityChange={(productId, value) => setDraft((d) => ({ ...d, [productId]: value }))}
                  onSave={() => void saveLines()}
                  busy={busy}
                  loading={productsLoading}
                  incomplete={productsIncomplete}
                  onRetry={() => setCatalogueAttempt((n) => n + 1)}
                />
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
