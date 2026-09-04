"use client";

// Phase 2D — inter-branch transfers.
//
// Stock moves at the EDGES of the lifecycle, never on a status change:
//
//     BORRADOR ──despachar──▶ EN TRÁNSITO ──recibir──▶ RECIBIDA
//        │                    (origen −q)              (destino +q)
//        └──anular──▶ ANULADA
//
// Nothing here decides anything: every button posts to an endpoint that
// re-checks the capability AND access to BOTH branches, and refuses a dispatch
// the source cannot cover. The screen only stops the operator earlier.

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { AdminShell } from "../../components/AdminShell";
import { StaffGuard } from "../../components/StaffGuard";
import { useBranchScope } from "../../lib/use-branch-scope";
import {
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
  createTransfer,
  fetchTransfers,
  type StockTransfer,
  type TransferStatus,
} from "../../../lib/inventory";
import { canManageInventory, type AuthUser } from "../../../lib/auth";

const STATUS_FILTERS: { value: "" | TransferStatus; label: string }[] = [
  { value: "", label: "Todas" },
  { value: "draft", label: "Borrador" },
  { value: "in_transit", label: "En tránsito" },
  { value: "received", label: "Recibidas" },
  { value: "cancelled", label: "Anuladas" },
];

export function TransferStatusBadge({ transfer }: { transfer: StockTransfer }) {
  // Monochrome, matching the rest of the panel. Only IN TRANSIT is emphasised:
  // it is the one state that means somebody still has to do something.
  const emphasised = transfer.status === "in_transit";
  const muted = transfer.status === "cancelled";
  return (
    <span
      className={`inline-flex rounded-md border px-2 py-0.5 text-[11px] font-medium ${
        emphasised
          ? "border-bd-border bg-surface-2 text-foreground"
          : muted
            ? "border-bd-border text-muted"
            : "border-bd-border text-muted"
      }`}
    >
      {transfer.status_label}
    </span>
  );
}

function TransfersContent({ user }: { user: AuthUser }) {
  const scope = useBranchScope({ preferAggregate: true });
  const [transfers, setTransfers] = useState<StockTransfer[]>([]);
  const [statusFilter, setStatusFilter] = useState<"" | TransferStatus>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const [source, setSource] = useState("");
  const [destination, setDestination] = useState("");
  const [reason, setReason] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const mayTransfer = canManageInventory(user);
  const branches = scope.access?.results ?? [];

  const load = useCallback(async () => {
    return fetchTransfers({
      status: statusFilter || undefined,
      page_size: 50,
    });
  }, [statusFilter]);

  useEffect(() => {
    if (scope.loading) return;
    let cancelled = false;
    void (async () => {
      try {
        const data = await load();
        if (cancelled) return;
        setTransfers(data.results);
        setError(null);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "No se pudieron cargar las transferencias.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [load, reloadKey, scope.loading]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!source || !destination || source === destination) return;
    setCreating(true);
    setCreateError(null);
    try {
      await createTransfer({
        source_branch: Number(source),
        destination_branch: Number(destination),
        reason: reason.trim(),
      });
      setSource("");
      setDestination("");
      setReason("");
      setLoading(true);
      setReloadKey((k) => k + 1);
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "No se pudo crear la transferencia.");
    } finally {
      setCreating(false);
    }
  }

  const fieldClass =
    "w-full rounded-lg border border-bd-border bg-background/40 px-3 py-2 text-sm text-foreground outline-none transition focus:border-bd-border disabled:opacity-50";
  const labelClass = "mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-muted";

  return (
    <AdminShell user={user}>
      <div className="space-y-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold text-foreground">Transferencias</h1>
            <p className="mt-1 text-sm text-muted">
              Traslados de stock entre sucursales. El stock sale al despachar y
              entra al recibir.
            </p>
          </div>
          <Link
            href="/admin/inventory"
            className="rounded-lg border border-bd-border px-3.5 py-2 text-sm text-foreground/85 transition hover:border-bd-border hover:text-foreground"
          >
            ← Inventario
          </Link>
        </div>

        {scope.error ? <ErrorBox message={scope.error} /> : null}

        {mayTransfer && branches.length >= 2 ? (
          <Panel
            title="Nueva transferencia"
            description="Se crea en borrador; los productos se agregan en el siguiente paso."
          >
            <form onSubmit={handleCreate} className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-3">
                <div>
                  <label className={labelClass} htmlFor="t-source">Origen</label>
                  <select
                    id="t-source"
                    className={fieldClass}
                    value={source}
                    onChange={(e) => setSource(e.target.value)}
                    disabled={creating}
                  >
                    <option value="">Selecciona…</option>
                    {branches.map((b) => (
                      <option key={b.id} value={b.id}>{b.name}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className={labelClass} htmlFor="t-destination">Destino</label>
                  <select
                    id="t-destination"
                    className={fieldClass}
                    value={destination}
                    onChange={(e) => setDestination(e.target.value)}
                    disabled={creating}
                  >
                    <option value="">Selecciona…</option>
                    {branches
                      .filter((b) => String(b.id) !== source)
                      .map((b) => (
                        <option key={b.id} value={b.id}>{b.name}</option>
                      ))}
                  </select>
                </div>
                <div>
                  <label className={labelClass} htmlFor="t-reason">Motivo</label>
                  <input
                    id="t-reason"
                    type="text"
                    maxLength={500}
                    className={fieldClass}
                    placeholder="Ej. Reposición de tienda"
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    disabled={creating}
                  />
                </div>
              </div>

              {createError ? <ErrorBox message={createError} /> : null}

              <button
                type="submit"
                disabled={creating || !source || !destination || source === destination}
                className="rounded-lg bg-foreground px-4 py-2 text-sm font-semibold text-background transition hover:bg-foreground/90 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {creating ? "Creando…" : "Crear borrador"}
              </button>
            </form>
          </Panel>
        ) : null}

        {mayTransfer && branches.length < 2 ? (
          <div className="rounded-lg border border-bd-border bg-surface px-4 py-3">
            <p className="text-sm text-muted">
              Necesitas acceso a al menos dos sucursales para transferir stock.
            </p>
          </div>
        ) : null}

        <Panel title="Historial" description={`${transfers.length} transferencia(s)`}>
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
                    ? "border-bd-border bg-surface-2 text-foreground"
                    : "border-bd-border text-muted hover:border-bd-border hover:text-foreground"
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>

          {loading ? <Spinner label="Cargando transferencias…" /> : null}
          {error ? <ErrorBox message={error} /> : null}

          {!loading && !error && transfers.length === 0 ? (
            <EmptyBox message="Todavía no hay transferencias." />
          ) : null}

          {!loading && !error && transfers.length > 0 ? (
            <TableWrap>
              <thead>
                <tr className="border-b border-bd-border">
                  <Th>#</Th>
                  <Th>Fecha</Th>
                  <Th>Origen</Th>
                  <Th>Destino</Th>
                  <Th>Estado</Th>
                  <Th right>Líneas</Th>
                  <Th right>Unidades</Th>
                  <Th>Creada por</Th>
                </tr>
              </thead>
              <tbody>
                {transfers.map((t) => (
                  <tr key={t.id} className="border-b border-bd-border">
                    <Td>
                      <Link
                        href={`/admin/inventory/transfers/${t.id}`}
                        className="transition hover:text-foreground"
                      >
                        #{t.id}
                      </Link>
                    </Td>
                    <Td muted>{formatDateTime(t.created_at)}</Td>
                    <Td>{t.source_branch_name}</Td>
                    <Td>{t.destination_branch_name}</Td>
                    <Td><TransferStatusBadge transfer={t} /></Td>
                    <Td right muted>{t.items.length}</Td>
                    <Td right>{t.total_units}</Td>
                    <Td muted>{t.created_by_username ?? "—"}</Td>
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

export default function TransfersPage() {
  return <StaffGuard>{(user) => <TransfersContent user={user} />}</StaffGuard>;
}
