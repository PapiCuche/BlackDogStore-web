"use client";

/**
 * Administración → Sucursales — closes the Phase 2D debt.
 *
 * Branches could be created and listed but never edited, so a shop that moved
 * had no way to correct its own address — the one that then prints on every
 * pickup receipt.
 *
 * WHAT THIS SCREEN IS NOT: stock management. Deactivating a branch does not
 * move, release or delete its units; they stay on that shelf and stop being
 * sellable, which is what "this shop is closed" means. Moving them is a
 * transfer, and it lives in Inventario.
 *
 * The FULFILLMENT BRANCH selector is here rather than in Configuración because
 * it is a choice among these rows. Changing it affects FUTURE orders only:
 * existing ones carry their own `fulfillment_branch`, decided when they were
 * placed.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { AdminShell } from "../components/AdminShell";
import { InternalControlGuard, type InternalContext } from "../components/InternalControlGuard";
import {
  createBranch,
  fetchCompanyBranches,
  fetchCompanyConfiguration,
  updateBranch,
  updateFulfillmentBranch,
  type BranchRow,
  type CompanyConfiguration,
} from "../lib/internal-api";

type Draft = { name: string; address: string; phone: string; email: string };

function BranchEditor({
  branch,
  disabled,
  onSaved,
}: {
  branch: BranchRow & { address?: string; phone?: string; email?: string };
  disabled: boolean;
  onSaved: (next: BranchRow) => void;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Draft>({
    name: branch.name ?? "",
    address: branch.address ?? "",
    phone: branch.phone ?? "",
    email: branch.email ?? "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(data: Parameters<typeof updateBranch>[1]) {
    setBusy(true);
    setError(null);
    try {
      onSaved(await updateBranch(branch.id, data));
      setOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo guardar.");
    } finally {
      setBusy(false);
    }
  }

  const field =
    "w-full rounded-lg border border-white/[0.08] bg-black/40 px-3 py-2 text-sm text-zinc-200 outline-none transition focus:border-white/25 disabled:opacity-50";

  return (
    <>
      <tr className="border-b border-white/[0.03]">
        <td className="px-4 py-3 text-zinc-200">{branch.name}</td>
        <td className="px-4 py-3 text-zinc-500">{branch.address || "—"}</td>
        <td className="px-4 py-3 text-zinc-500">{branch.phone || "—"}</td>
        <td className="px-4 py-3">
          <span
            className={`inline-flex rounded-md border px-2 py-0.5 text-[11px] ${
              branch.is_active
                ? "border-white/20 text-zinc-300"
                : "border-white/[0.06] text-zinc-600"
            }`}
          >
            {branch.is_active ? "Activa" : "Inactiva"}
          </span>
        </td>
        <td className="px-4 py-3 text-right">
          <button
            type="button"
            disabled={disabled}
            onClick={() => setOpen((v) => !v)}
            className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-zinc-300 transition hover:border-white/20 hover:text-white disabled:opacity-40"
          >
            {open ? "Cerrar" : "Editar"}
          </button>
        </td>
      </tr>

      {open ? (
        <tr className="border-b border-white/[0.06] bg-white/[0.02]">
          <td colSpan={5} className="px-4 py-5">
            <div className="grid gap-4 sm:grid-cols-2">
              {(
                [
                  ["Nombre", "name"],
                  ["Dirección", "address"],
                  ["Teléfono", "phone"],
                  ["Email", "email"],
                ] as [string, keyof Draft][]
              ).map(([label, key]) => (
                <div key={key}>
                  <label
                    className="mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-zinc-500"
                    htmlFor={`br-${branch.id}-${key}`}
                  >
                    {label}
                  </label>
                  <input
                    id={`br-${branch.id}-${key}`}
                    className={field}
                    value={draft[key]}
                    disabled={busy}
                    onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
                  />
                </div>
              ))}
            </div>

            {error ? <p className="mt-3 text-sm text-red-400">{error}</p> : null}

            <div className="mt-4 flex flex-wrap gap-2">
              <button
                type="button"
                disabled={busy}
                onClick={() => void run(draft)}
                className="rounded-lg bg-white px-4 py-2 text-sm font-semibold text-black transition hover:bg-zinc-200 disabled:opacity-40"
              >
                Guardar
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  const next = !branch.is_active;
                  const warning = next
                    ? `¿Reactivar «${branch.name}»?`
                    : `¿Desactivar «${branch.name}»?\n\nSu stock NO se mueve ni se borra: ` +
                      `deja de poder venderse desde ahí. Si es la sucursal de despacho, ` +
                      `la tienda dejará de poder cerrar pedidos hasta que elijas otra.`;
                  if (window.confirm(warning)) void run({ is_active: next });
                }}
                className="rounded-lg border border-white/10 px-4 py-2 text-sm text-zinc-400 transition hover:border-white/20 hover:text-white disabled:opacity-40"
              >
                {branch.is_active ? "Desactivar" : "Reactivar"}
              </button>
            </div>
          </td>
        </tr>
      ) : null}
    </>
  );
}

function BranchesContent({ user, ctx }: { user: InternalContext["user"]; ctx: InternalContext }) {
  const companyId = ctx.dashboard?.company?.id ?? null;
  const [branches, setBranches] = useState<BranchRow[]>([]);
  const [config, setConfig] = useState<CompanyConfiguration | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [newName, setNewName] = useState("");
  const [newAddress, setNewAddress] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [list, configuration] = await Promise.all([
      fetchCompanyBranches(companyId),
      fetchCompanyConfiguration(companyId),
    ]);
    setBranches(list.results);
    setConfig(configuration);
  }, [companyId]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        await load();
        if (!cancelled) setError(null);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "No se pudieron cargar las sucursales.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [load]);

  const canManage = config?.can_manage ?? false;

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!companyId || !newName.trim()) return;
    setBusy(true);
    setCreateError(null);
    try {
      await createBranch({
        company: companyId,
        name: newName.trim(),
        address: newAddress.trim(),
      });
      setNewName("");
      setNewAddress("");
      await load();
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "No se pudo crear la sucursal.");
    } finally {
      setBusy(false);
    }
  }

  async function handleFulfillment(value: string) {
    if (!companyId) return;
    setBusy(true);
    setError(null);
    try {
      await updateFulfillmentBranch(companyId, value === "" ? null : Number(value));
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cambiar la sucursal.");
    } finally {
      setBusy(false);
    }
  }

  const field =
    "w-full rounded-lg border border-white/[0.08] bg-black/40 px-3 py-2 text-sm text-zinc-200 outline-none transition focus:border-white/25 disabled:opacity-50";

  return (
    <AdminShell user={user}>
      <div className="space-y-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold text-white">Sucursales</h1>
            <p className="mt-1 text-sm text-zinc-500">
              Ubicaciones de la empresa. Sus datos aparecen como punto de retiro en
              los documentos de los pedidos que despachan.
            </p>
          </div>
          <Link
            href="/admin/settings"
            className="rounded-lg border border-white/10 px-3.5 py-2 text-sm text-zinc-300 transition hover:border-white/20 hover:text-white"
          >
            ← Configuración
          </Link>
        </div>

        {loading ? <p className="py-10 text-center text-zinc-600">Cargando…</p> : null}
        {error ? (
          <div className="rounded-xl border border-red-500/20 bg-red-500/5 px-5 py-4 text-sm text-red-400">
            {error}
          </div>
        ) : null}

        {config ? (
          <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-5">
            <label
              className="mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-zinc-500"
              htmlFor="fulfillment-branch"
            >
              Sucursal de despacho de la tienda online
            </label>
            <select
              id="fulfillment-branch"
              className={`${field} max-w-sm`}
              value={config.fulfillment_branch?.id ?? ""}
              disabled={busy || !canManage}
              onChange={(e) => void handleFulfillment(e.target.value)}
            >
              <option value="">Sin configurar</option>
              {branches
                .filter((b) => b.is_active)
                .map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
            </select>
            <p className="mt-2 text-[11px] text-zinc-600">
              De aquí sale el stock de las ventas online, y su dirección es el punto
              de retiro. Cambiarla afecta a los pedidos FUTUROS: los ya existentes
              conservan la sucursal con la que se vendieron.
              {!config.fulfillment_branch
                ? " Sin una sucursal elegida, la tienda no puede cerrar pedidos."
                : ""}
            </p>
          </div>
        ) : null}

        {canManage ? (
          <form
            onSubmit={handleCreate}
            className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-5"
          >
            <p className="mb-4 text-sm font-medium text-zinc-300">Nueva sucursal</p>
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label
                  className="mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-zinc-500"
                  htmlFor="new-branch-name"
                >
                  Nombre
                </label>
                <input
                  id="new-branch-name"
                  className={field}
                  value={newName}
                  disabled={busy}
                  onChange={(e) => setNewName(e.target.value)}
                />
              </div>
              <div>
                <label
                  className="mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-zinc-500"
                  htmlFor="new-branch-address"
                >
                  Dirección
                </label>
                <input
                  id="new-branch-address"
                  className={field}
                  value={newAddress}
                  disabled={busy}
                  onChange={(e) => setNewAddress(e.target.value)}
                />
              </div>
            </div>
            {createError ? (
              <p className="mt-3 text-sm text-red-400">{createError}</p>
            ) : null}
            <button
              type="submit"
              disabled={busy || !newName.trim()}
              className="mt-4 rounded-lg bg-white px-4 py-2 text-sm font-semibold text-black transition hover:bg-zinc-200 disabled:opacity-40"
            >
              Crear sucursal
            </button>
          </form>
        ) : null}

        {!loading && branches.length === 0 ? (
          <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] py-10 text-center text-zinc-500">
            Esta empresa todavía no tiene sucursales.
          </div>
        ) : null}

        {branches.length > 0 ? (
          <div className="overflow-x-auto rounded-xl border border-white/[0.06]">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/[0.06] bg-white/[0.02] text-left text-xs font-semibold uppercase tracking-wider text-zinc-500">
                  <th className="px-4 py-3">Sucursal</th>
                  <th className="px-4 py-3">Dirección</th>
                  <th className="px-4 py-3">Teléfono</th>
                  <th className="px-4 py-3">Estado</th>
                  <th className="px-4 py-3 text-right">Acción</th>
                </tr>
              </thead>
              <tbody>
                {branches.map((branch) => (
                  <BranchEditor
                    key={branch.id}
                    branch={branch}
                    disabled={!canManage}
                    onSaved={(next) => {
                      setBranches((prev) =>
                        prev.map((b) => (b.id === next.id ? next : b)),
                      );
                      void load();
                    }}
                  />
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>
    </AdminShell>
  );
}

export default function BranchesPage() {
  return (
    <InternalControlGuard>
      {(ctx) => <BranchesContent user={ctx.user} ctx={ctx} />}
    </InternalControlGuard>
  );
}
