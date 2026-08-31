"use client";

/**
 * Branch access per staff member — Phase 2D.
 *
 * WHAT THIS SCREEN IS AND IS NOT.
 * It edits WHERE somebody may work, not WHAT they may do. Capabilities come
 * from roles and are a separate decision on purpose: granting somebody the
 * inventory role must not also hand them every shop in the chain. Both have to
 * pass before a single unit moves, and the backend checks both on every request.
 *
 * "Todas" includes branches that open in the future. "Seleccionadas" does not —
 * that is the entire point of choosing it, and the note under the radio says so,
 * because the difference only becomes visible months later when somebody opens a
 * shop and wonders who can see it.
 *
 * Selecting nothing is a valid state: the person can operate nowhere. It is
 * spelled out rather than hidden, because the alternative design — "no branches
 * means all branches" — fails open, and somebody looking at this screen should
 * be able to see that it does not.
 */

import { useCallback, useEffect, useState } from "react";
import {
  fetchCompanyBranches,
  fetchMemberships,
  updateMembershipBranchAccess,
  type BranchRow,
  type MembershipRow,
} from "../lib/internal-api";

type Props = {
  companyId: number | null;
  /** False hides the whole panel: the caller cannot administer this company. */
  canManage: boolean;
};

function ScopeSummary({ membership }: { membership: MembershipRow }) {
  if (membership.branch_access_mode === "all") {
    return <span className="text-zinc-300">Todas las sucursales</span>;
  }
  const granted = membership.branch_access.filter((b) => b.is_active);
  if (granted.length === 0) {
    return (
      <span className="text-amber-300/80">
        Ninguna — no puede operar inventario
      </span>
    );
  }
  return <span className="text-zinc-300">{granted.map((b) => b.name).join(" · ")}</span>;
}

function MembershipRowEditor({
  membership,
  branches,
  onSaved,
}: {
  membership: MembershipRow;
  branches: BranchRow[];
  onSaved: (next: MembershipRow) => void;
}) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"all" | "selected">(membership.branch_access_mode);
  const [selected, setSelected] = useState<number[]>(
    membership.branch_access.filter((b) => b.is_active).map((b) => b.id),
  );
  const [defaultBranch, setDefaultBranch] = useState<string>(
    membership.branch === null ? "" : String(membership.branch),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function toggle(id: number) {
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const next = await updateMembershipBranchAccess(membership.id, {
        branch_access_mode: mode,
        // Grants are sent in both modes: they are kept when somebody switches to
        // "todas" for a while, so switching back does not destroy what an
        // administrator deliberately configured.
        branch_access: selected,
        branch: defaultBranch === "" ? null : Number(defaultBranch),
      });
      onSaved(next);
      setOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo guardar.");
    } finally {
      setSaving(false);
    }
  }

  // Only branches the person can actually reach may be their default.
  const defaultOptions =
    mode === "all" ? branches : branches.filter((b) => selected.includes(b.id));

  return (
    <>
      <tr className="border-b border-white/[0.03]">
        <td className="px-4 py-3 text-zinc-200">{membership.username}</td>
        <td className="px-4 py-3 text-zinc-500">{membership.role_label}</td>
        <td className="px-4 py-3 text-sm">
          <ScopeSummary membership={membership} />
        </td>
        <td className="px-4 py-3 text-zinc-500">{membership.branch_name ?? "—"}</td>
        <td className="px-4 py-3 text-right">
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-zinc-300 transition hover:border-white/20 hover:text-white"
          >
            {open ? "Cerrar" : "Editar"}
          </button>
        </td>
      </tr>

      {open && (
        <tr className="border-b border-white/[0.06] bg-white/[0.02]">
          <td colSpan={5} className="px-4 py-5">
            <fieldset className="space-y-3">
              <legend className="mb-2 text-[11px] font-semibold uppercase tracking-widest text-zinc-500">
                Alcance de sucursales
              </legend>

              <label className="flex items-start gap-2 text-sm text-zinc-300">
                <input
                  type="radio"
                  name={`mode-${membership.id}`}
                  checked={mode === "all"}
                  onChange={() => setMode("all")}
                  className="mt-1"
                />
                <span>
                  Todas
                  <span className="block text-xs text-zinc-600">
                    Incluye automáticamente las sucursales que se abran en el futuro.
                  </span>
                </span>
              </label>

              <label className="flex items-start gap-2 text-sm text-zinc-300">
                <input
                  type="radio"
                  name={`mode-${membership.id}`}
                  checked={mode === "selected"}
                  onChange={() => setMode("selected")}
                  className="mt-1"
                />
                <span>
                  Seleccionadas
                  <span className="block text-xs text-zinc-600">
                    Solo las marcadas. Una sucursal nueva NO se concede sola.
                  </span>
                </span>
              </label>
            </fieldset>

            {mode === "selected" && (
              <div className="mt-4 flex flex-wrap gap-3">
                {branches.map((branch) => (
                  <label
                    key={branch.id}
                    className="flex items-center gap-2 rounded-lg border border-white/[0.08] px-3 py-2 text-sm text-zinc-300"
                  >
                    <input
                      type="checkbox"
                      checked={selected.includes(branch.id)}
                      onChange={() => toggle(branch.id)}
                    />
                    {branch.name}
                  </label>
                ))}
              </div>
            )}

            {mode === "selected" && selected.length === 0 && (
              <p className="mt-3 text-xs text-amber-300/80">
                Sin ninguna sucursal marcada, esta persona no podrá ver ni mover
                inventario en ningún sitio. Es un estado válido, no un error.
              </p>
            )}

            <div className="mt-4 max-w-xs">
              <label
                className="mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-zinc-500"
                htmlFor={`default-${membership.id}`}
              >
                Sucursal predeterminada
              </label>
              <select
                id={`default-${membership.id}`}
                value={defaultBranch}
                onChange={(e) => setDefaultBranch(e.target.value)}
                className="w-full rounded-lg border border-white/[0.08] bg-black/40 px-3 py-2 text-sm text-zinc-200 outline-none transition focus:border-white/25"
              >
                <option value="">Sin preferencia</option>
                {defaultOptions.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </select>
              <p className="mt-1.5 text-[11px] text-zinc-600">
                Por cuál abre el Control Interno. No concede acceso por sí sola.
              </p>
            </div>

            {error && (
              <p className="mt-3 rounded-lg border border-red-500/20 bg-red-500/5 px-3 py-2 text-sm text-red-400">
                {error}
              </p>
            )}

            <button
              type="button"
              onClick={() => void save()}
              disabled={saving}
              className="mt-4 rounded-lg bg-white px-4 py-2 text-sm font-semibold text-black transition hover:bg-zinc-200 disabled:opacity-40"
            >
              {saving ? "Guardando…" : "Guardar acceso"}
            </button>
          </td>
        </tr>
      )}
    </>
  );
}

export function BranchAccessPanel({ companyId, canManage }: Props) {
  const [memberships, setMemberships] = useState<MembershipRow[]>([]);
  const [branches, setBranches] = useState<BranchRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [people, locations] = await Promise.all([
      fetchMemberships(companyId),
      fetchCompanyBranches(companyId),
    ]);
    return { people: people.results, locations: locations.results };
  }, [companyId]);

  useEffect(() => {
    if (!canManage) return;
    let cancelled = false;
    void (async () => {
      try {
        const { people, locations } = await load();
        if (cancelled) return;
        setMemberships(people);
        setBranches(locations.filter((b) => b.is_active));
        setError(null);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "No se pudo cargar el personal.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [load, canManage]);

  if (!canManage) return null;

  return (
    <section className="space-y-4">
      <div>
        <h2 className="text-base font-semibold text-white">Acceso por sucursal</h2>
        <p className="mt-1 text-sm text-zinc-500">
          Dónde puede trabajar cada persona. Es una decisión distinta de qué puede
          hacer: los permisos vienen de sus roles, y ambos deben permitirlo.
        </p>
      </div>

      {loading && <div className="py-8 text-center text-zinc-600">Cargando personal…</div>}
      {error && !loading && (
        <div className="rounded-xl border border-red-500/20 bg-red-500/5 px-5 py-4 text-sm text-red-400">
          {error}
        </div>
      )}

      {!loading && !error && memberships.length === 0 && (
        <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] py-10 text-center text-zinc-500">
          Esta empresa todavía no tiene personal interno.
        </div>
      )}

      {!loading && !error && memberships.length > 0 && (
        <div className="overflow-x-auto rounded-xl border border-white/[0.06]">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/[0.06] bg-white/[0.02] text-left text-xs font-semibold uppercase tracking-wider text-zinc-500">
                <th className="px-4 py-3">Persona</th>
                <th className="px-4 py-3">Rol</th>
                <th className="px-4 py-3">Sucursales</th>
                <th className="px-4 py-3">Predeterminada</th>
                <th className="px-4 py-3 text-right">Acción</th>
              </tr>
            </thead>
            <tbody>
              {memberships.map((membership) => (
                <MembershipRowEditor
                  key={membership.id}
                  membership={membership}
                  branches={branches}
                  onSaved={(next) =>
                    setMemberships((prev) =>
                      prev.map((m) => (m.id === next.id ? next : m)),
                    )
                  }
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
