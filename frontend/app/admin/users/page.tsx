"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AdminShell } from "../components/AdminShell";
import {
  InternalControlGuard,
  type InternalContext,
} from "../components/InternalControlGuard";
import { fetchWithAuth } from "../../lib/auth";
import { API_BASE } from "../../lib/api";

type Branch = { id: number; name: string; is_active: boolean };
type Membership = {
  id: number;
  username: string;
  company: number;
  company_name: string;
  role_label: string;
  branch_access_mode: "all" | "selected";
  branch_access: Branch[];
  is_active: boolean;
};
type Role = {
  id: number;
  name: string;
  capabilities: string[];
  is_active: boolean;
};
type Area = { id: number; name: string; is_active: boolean };
type Assignment = {
  id: number;
  membership: number;
  role: number;
  role_name: string;
  area_name: string | null;
  capabilities: string[];
  is_active: boolean;
};

async function readDetail(res: Response, fallback: string) {
  const body = await res.json().catch(() => null);
  return body?.detail ? String(body.detail) : fallback;
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetchWithAuth(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(await readDetail(res, "No se pudo cargar la información."));
  return res.json();
}

async function patchJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetchWithAuth(`${API_BASE}${path}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await readDetail(res, "No se pudo guardar el acceso."));
  return res.json();
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetchWithAuth(`${API_BASE}${path}`, {
    method: "POST",
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await readDetail(res, "No se pudo asignar el rol."));
  return res.json();
}

function PermissionSummary({ codes }: { codes: string[] }) {
  const unique = Array.from(new Set(codes)).sort();
  if (!unique.length) return <span className="text-xs text-zinc-600">Sin permisos efectivos</span>;
  return (
    <div className="flex flex-wrap gap-1.5">
      {unique.slice(0, 4).map((code) => (
        <code key={code} className="rounded-md border border-white/[0.07] bg-white/[0.025] px-2 py-1 text-[10px] text-zinc-500">
          {code}
        </code>
      ))}
      {unique.length > 4 ? (
        <span className="rounded-md border border-white/[0.07] px-2 py-1 text-[10px] text-zinc-600">
          +{unique.length - 4}
        </span>
      ) : null}
    </div>
  );
}

function MemberCard({
  membership,
  assignments,
  roles,
  areas,
  branches,
  canManage,
  onChanged,
}: {
  membership: Membership;
  assignments: Assignment[];
  roles: Role[];
  areas: Area[];
  branches: Branch[];
  canManage: boolean;
  onChanged: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [roleId, setRoleId] = useState("");
  const [areaId, setAreaId] = useState("");
  const [mode, setMode] = useState<"all" | "selected">(membership.branch_access_mode);
  const [selectedBranches, setSelectedBranches] = useState<number[]>(membership.branch_access.map((row) => row.id));

  useEffect(() => {
    setMode(membership.branch_access_mode);
    setSelectedBranches(membership.branch_access.map((row) => row.id));
  }, [membership.branch_access, membership.branch_access_mode]);

  const activeAssignments = assignments.filter((row) => row.is_active);
  const effective = activeAssignments.flatMap((row) => row.capabilities);

  async function perform(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      await onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo guardar.");
    } finally {
      setBusy(false);
    }
  }

  async function assignRole() {
    if (!roleId) return;
    await perform(() => postJson("/admin/membership-role-assignments/", {
      membership: membership.id,
      role: Number(roleId),
      area: areaId ? Number(areaId) : null,
    }));
    setRoleId("");
    setAreaId("");
  }

  async function saveBranches() {
    await perform(() => patchJson(`/admin/memberships/${membership.id}/`, {
      branch_access_mode: mode,
      ...(mode === "selected" ? { branch_access: selectedBranches } : {}),
    }));
  }

  return (
    <article className="rounded-xl border border-white/[0.07] bg-white/[0.02]">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="grid w-full gap-4 px-5 py-4 text-left lg:grid-cols-[1.1fr_1.2fr_1.5fr_auto] lg:items-center"
      >
        <div>
          <div className="flex items-center gap-2">
            <span className="font-medium text-zinc-100">{membership.username}</span>
            <span className={`rounded-full px-2 py-0.5 text-[10px] ${membership.is_active ? "bg-white/[0.06] text-zinc-300" : "bg-red-500/10 text-red-400"}`}>
              {membership.is_active ? "Activo" : "Inactivo"}
            </span>
          </div>
          <p className="mt-1 text-xs text-zinc-600">Membresía #{membership.id}</p>
        </div>

        <div>
          <p className="text-[10px] font-semibold uppercase tracking-wider text-zinc-600">Rol empresarial</p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {activeAssignments.length ? activeAssignments.map((assignment) => (
              <span key={assignment.id} className="rounded-md border border-white/10 px-2 py-1 text-xs text-zinc-300">
                {assignment.role_name}{assignment.area_name ? ` · ${assignment.area_name}` : ""}
              </span>
            )) : (
              <span className="text-xs text-amber-300/80">Legacy: {membership.role_label}</span>
            )}
          </div>
        </div>

        <div>
          <p className="text-[10px] font-semibold uppercase tracking-wider text-zinc-600">Permisos</p>
          <div className="mt-1.5">
            {activeAssignments.length ? <PermissionSummary codes={effective} /> : <span className="text-xs text-zinc-500">Compatibilidad heredada pendiente de migrar.</span>}
          </div>
        </div>

        <div className="flex items-center justify-between gap-3 lg:justify-end">
          <span className="text-xs text-zinc-500">
            {membership.branch_access_mode === "all" ? "Todas las sucursales" : `${membership.branch_access.length} sucursal(es)`}
          </span>
          <span className="text-zinc-600">{open ? "−" : "+"}</span>
        </div>
      </button>

      {open ? (
        <div className="border-t border-white/[0.06] px-5 py-5">
          <div className="mb-5 rounded-lg border border-amber-500/15 bg-amber-500/[0.04] px-4 py-3 text-xs leading-5 text-amber-200/70">
            Por seguridad, esta consola permite <strong>asignar</strong> roles pero todavía no retirar el último rol personalizado. La revocación total queda bloqueada en UI hasta endurecer el fallback legacy del backend.
          </div>

          <div className="grid gap-6 xl:grid-cols-2">
            <section>
              <h3 className="text-sm font-semibold text-zinc-200">Roles y área</h3>
              <p className="mt-1 text-xs text-zinc-600">El rol concede autoridad; el área solo organiza al personal.</p>

              <div className="mt-3 space-y-2">
                {assignments.length ? assignments.map((assignment) => (
                  <div key={assignment.id} className="rounded-lg border border-white/[0.06] px-3 py-2.5">
                    <div className="flex items-center justify-between gap-3">
                      <span className={assignment.is_active ? "text-sm text-zinc-200" : "text-sm text-zinc-600 line-through"}>{assignment.role_name}</span>
                      <span className="text-[10px] text-zinc-600">{assignment.is_active ? "Activo" : "Histórico"}</span>
                    </div>
                    <p className="mt-1 text-[11px] text-zinc-600">{assignment.area_name || "Sin área"} · {assignment.capabilities.length} permisos</p>
                  </div>
                )) : (
                  <div className="rounded-lg border border-amber-500/15 bg-amber-500/[0.03] px-3 py-3 text-xs text-amber-200/70">
                    Aún usa «{membership.role_label}» del modelo heredado. Asigna un rol empresarial para migrarlo al RBAC configurable.
                  </div>
                )}
              </div>

              {canManage ? (
                <div className="mt-3 grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
                  <select value={roleId} onChange={(event) => setRoleId(event.target.value)} disabled={busy} className="rounded-lg border border-white/[0.08] bg-black/50 px-3 py-2 text-sm text-zinc-300">
                    <option value="">Elegir rol…</option>
                    {roles.filter((role) => role.is_active).map((role) => <option key={role.id} value={role.id}>{role.name}</option>)}
                  </select>
                  <select value={areaId} onChange={(event) => setAreaId(event.target.value)} disabled={busy} className="rounded-lg border border-white/[0.08] bg-black/50 px-3 py-2 text-sm text-zinc-300">
                    <option value="">Sin área</option>
                    {areas.filter((area) => area.is_active).map((area) => <option key={area.id} value={area.id}>{area.name}</option>)}
                  </select>
                  <button type="button" onClick={() => void assignRole()} disabled={busy || !roleId} className="rounded-lg bg-white px-4 py-2 text-sm font-semibold text-black disabled:opacity-40">
                    Asignar
                  </button>
                </div>
              ) : null}
            </section>

            <section>
              <h3 className="text-sm font-semibold text-zinc-200">Alcance por sucursal</h3>
              <p className="mt-1 text-xs text-zinc-600">Responde dónde puede operar; nunca agrega capacidades.</p>

              <div className="mt-3 flex gap-2">
                {(["all", "selected"] as const).map((value) => (
                  <button
                    key={value}
                    type="button"
                    disabled={!canManage || busy}
                    onClick={() => setMode(value)}
                    className={`rounded-lg border px-3 py-2 text-xs ${mode === value ? "border-white/30 bg-white/[0.07] text-white" : "border-white/[0.07] text-zinc-500"}`}
                  >
                    {value === "all" ? "Todas" : "Seleccionadas"}
                  </button>
                ))}
              </div>

              {mode === "selected" ? (
                <div className="mt-3 grid gap-2 sm:grid-cols-2">
                  {branches.filter((branch) => branch.is_active).map((branch) => {
                    const checked = selectedBranches.includes(branch.id);
                    return (
                      <label key={branch.id} className="flex items-center gap-2 rounded-lg border border-white/[0.06] px-3 py-2 text-sm text-zinc-400">
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={!canManage || busy}
                          onChange={() => setSelectedBranches((current) => checked ? current.filter((id) => id !== branch.id) : [...current, branch.id])}
                        />
                        {branch.name}
                      </label>
                    );
                  })}
                </div>
              ) : null}

              {canManage ? (
                <button type="button" onClick={() => void saveBranches()} disabled={busy} className="mt-3 rounded-lg border border-white/10 px-4 py-2 text-sm text-zinc-300 hover:text-white disabled:opacity-40">
                  Guardar sucursales
                </button>
              ) : null}
            </section>
          </div>

          {!canManage ? <p className="mt-4 text-xs text-zinc-600">Modo lectura: necesitas <code>memberships.manage</code> para modificar accesos.</p> : null}
          {error ? <p className="mt-4 text-sm text-red-400">{error}</p> : null}
        </div>
      ) : null}
    </article>
  );
}

function StaffAccess({ ctx }: { ctx: InternalContext }) {
  const companyId = ctx.dashboard?.company?.id ?? null;
  const access = ctx.dashboard?.access;
  const caps = new Set(access?.capabilities ?? []);
  const canManage = Boolean(access?.is_platform_admin || caps.has("memberships.manage"));
  const canManageRoles = Boolean(access?.is_platform_admin || caps.has("roles.manage"));

  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [areas, setAreas] = useState<Area[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [assignments, setAssignments] = useState<Record<number, Assignment[]>>({});
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!companyId) {
      setMemberships([]);
      setAssignments({});
      setLoading(false);
      return;
    }

    setLoading(true);
    try {
      const qs = `?company=${encodeURIComponent(String(companyId))}`;
      const [memberData, roleData, areaData, branchData] = await Promise.all([
        getJson<{ results: Membership[] }>(`/admin/memberships/${qs}`),
        getJson<{ results: Role[] }>(`/admin/roles/${qs}`),
        getJson<{ results: Area[] }>(`/admin/areas/${qs}`),
        getJson<{ results: Branch[] }>(`/admin/branches/${qs}`),
      ]);
      const pairs = await Promise.all(memberData.results.map(async (membership) => {
        const data = await getJson<{ results: Assignment[] }>(`/admin/membership-role-assignments/?membership=${membership.id}`);
        return [membership.id, data.results] as const;
      }));
      setMemberships(memberData.results);
      setRoles(roleData.results);
      setAreas(areaData.results);
      setBranches(branchData.results);
      setAssignments(Object.fromEntries(pairs));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cargar el personal.");
    } finally {
      setLoading(false);
    }
  }, [companyId]);

  useEffect(() => { void load(); }, [load]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return q ? memberships.filter((row) => row.username.toLowerCase().includes(q)) : memberships;
  }, [memberships, search]);

  return (
    <AdminShell user={ctx.user}>
      <div className="space-y-7">
        <header className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.22em] text-zinc-600">Administración</p>
            <h1 className="mt-1 text-2xl font-semibold text-white">Personal y accesos</h1>
            <p className="mt-2 max-w-3xl text-sm text-zinc-500">Administra el acceso real por empresa: rol = qué puede hacer; sucursal = dónde puede hacerlo.</p>
          </div>
          {canManageRoles ? <Link href="/admin/roles" className="rounded-lg bg-white px-4 py-2 text-sm font-semibold text-black hover:bg-zinc-200">Roles y permisos</Link> : null}
        </header>

        {!companyId ? (
          <div className="rounded-xl border border-amber-500/20 bg-amber-500/[0.05] p-5 text-sm text-amber-200/80">Selecciona una empresa. El master de plataforma tiene acceso global, pero debe elegir explícitamente sobre qué tenant actúa.</div>
        ) : (
          <>
            <div className="grid gap-3 sm:grid-cols-3">
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4"><p className="text-xs text-zinc-600">Miembros</p><p className="mt-1 text-2xl font-semibold text-white">{memberships.length}</p></div>
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4"><p className="text-xs text-zinc-600">Roles activos</p><p className="mt-1 text-2xl font-semibold text-white">{roles.filter((role) => role.is_active).length}</p></div>
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4"><p className="text-xs text-zinc-600">Mi autoridad</p><p className="mt-1 text-sm font-medium text-zinc-200">{access?.is_platform_admin ? "Master de plataforma" : canManage ? "Administra accesos" : "Solo lectura"}</p></div>
            </div>

            <div className="flex items-center justify-between gap-3">
              <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Buscar usuario…" className="w-full max-w-sm rounded-lg border border-white/[0.08] bg-black/40 px-3 py-2.5 text-sm text-zinc-200 outline-none focus:border-white/25" />
              <span className="hidden text-xs text-zinc-600 sm:block">{ctx.dashboard?.company?.name}</span>
            </div>

            {loading ? <p className="py-12 text-center text-sm text-zinc-600">Cargando accesos…</p> : null}
            {error ? <div className="rounded-xl border border-red-500/20 bg-red-500/[0.05] p-4 text-sm text-red-400">{error}</div> : null}
            {!loading && !error ? (
              <div className="space-y-3">
                {filtered.map((membership) => (
                  <MemberCard
                    key={membership.id}
                    membership={membership}
                    assignments={assignments[membership.id] ?? []}
                    roles={roles}
                    areas={areas}
                    branches={branches}
                    canManage={canManage}
                    onChanged={load}
                  />
                ))}
                {!filtered.length ? <div className="rounded-xl border border-white/[0.06] py-12 text-center text-sm text-zinc-500">No se encontraron miembros.</div> : null}
              </div>
            ) : null}

            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-5">
              <h2 className="text-sm font-semibold text-zinc-200">Jerarquía correcta</h2>
              <div className="mt-3 grid gap-3 text-xs leading-5 text-zinc-500 md:grid-cols-3">
                <p><strong className="text-zinc-300">Master de plataforma:</strong> <code>User.is_superuser</code>. Puede operar todas las empresas; no es un rol de tenant.</p>
                <p><strong className="text-zinc-300">Administrador de empresa:</strong> recibe capacidades dentro de su tenant y no puede escalar por encima de su propia autoridad.</p>
                <p><strong className="text-zinc-300">Roles operativos:</strong> ventas, inventario y técnico se limitan por capacidades y, cuando aplica, por sucursal.</p>
              </div>
            </div>
          </>
        )}
      </div>
    </AdminShell>
  );
}

export default function UsersPage() {
  return <InternalControlGuard>{(ctx) => <StaffAccess ctx={ctx} />}</InternalControlGuard>;
}
