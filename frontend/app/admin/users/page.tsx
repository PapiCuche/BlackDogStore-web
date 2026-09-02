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
  user: number;
  username: string;
  company: number;
  company_name: string;
  role: string;
  role_label: string;
  branch: number | null;
  branch_name: string | null;
  branch_access_mode: "all" | "selected";
  branch_access: Branch[];
  is_active: boolean;
};
type Role = {
  id: number;
  name: string;
  slug: string;
  description: string;
  capabilities: string[];
  is_active: boolean;
};
type Area = { id: number; name: string; is_active: boolean };
type Assignment = {
  id: number;
  membership: number;
  role: number;
  role_name: string;
  role_slug: string;
  area: number | null;
  area_name: string | null;
  capabilities: string[];
  is_active: boolean;
};

async function detail(res: Response, fallback: string) {
  const payload = await res.json().catch(() => null);
  return payload?.detail ? String(payload.detail) : fallback;
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetchWithAuth(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(await detail(res, "No se pudo cargar la información."));
  return res.json();
}

async function writeJson<T>(path: string, method: "POST" | "PATCH", body: unknown): Promise<T> {
  const res = await fetchWithAuth(`${API_BASE}${path}`, {
    method,
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await detail(res, "No se pudo guardar el acceso."));
  return res.json();
}

function CapabilityPills({ codes }: { codes: string[] }) {
  if (!codes.length) return <span className="text-xs text-zinc-600">Sin permisos efectivos</span>;
  const visible = codes.slice(0, 4);
  return (
    <div className="flex flex-wrap gap-1.5">
      {visible.map((code) => (
        <span key={code} className="rounded-md border border-white/[0.08] bg-white/[0.03] px-2 py-1 text-[10px] text-zinc-400">
          {code}
        </span>
      ))}
      {codes.length > visible.length ? (
        <span className="rounded-md border border-white/[0.08] px-2 py-1 text-[10px] text-zinc-500">
          +{codes.length - visible.length}
        </span>
      ) : null}
    </div>
  );
}

function MemberAccessEditor({
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
  const [selectedBranches, setSelectedBranches] = useState<number[]>(
    membership.branch_access.map((branch) => branch.id),
  );

  const activeAssignments = assignments.filter((row) => row.is_active);
  const effective = Array.from(
    new Set(activeAssignments.flatMap((row) => row.capabilities)),
  ).sort();

  async function run(action: () => Promise<unknown>) {
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

  async function addRole() {
    if (!roleId) return;
    await run(() =>
      writeJson("/admin/membership-role-assignments/", "POST", {
        membership: membership.id,
        role: Number(roleId),
        area: areaId ? Number(areaId) : null,
      }),
    );
    setRoleId("");
    setAreaId("");
  }

  async function toggleAssignment(assignment: Assignment) {
    await run(() =>
      writeJson(
        `/admin/membership-role-assignments/${assignment.id}/`,
        "PATCH",
        { is_active: !assignment.is_active },
      ),
    );
  }

  async function saveBranches() {
    await run(() =>
      writeJson(`/admin/memberships/${membership.id}/`, "PATCH", {
        branch_access_mode: mode,
        branch_access: mode === "selected" ? selectedBranches : membership.branch_access.map((b) => b.id),
      }),
    );
  }

  return (
    <div className="rounded-xl border border-white/[0.07] bg-white/[0.02]">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="grid w-full gap-4 px-5 py-4 text-left lg:grid-cols-[1.1fr_1fr_1.4fr_auto] lg:items-center"
      >
        <div>
          <div className="flex items-center gap-2">
            <p className="font-medium text-zinc-100">{membership.username}</p>
            <span className={`rounded-full px-2 py-0.5 text-[10px] ${membership.is_active ? "bg-white/[0.06] text-zinc-300" : "bg-red-500/10 text-red-400"}`}>
              {membership.is_active ? "Activo" : "Inactivo"}
            </span>
          </div>
          <p className="mt-1 text-xs text-zinc-600">Membresía #{membership.id}</p>
        </div>

        <div>
          <p className="text-[10px] font-semibold uppercase tracking-wider text-zinc-600">Roles empresariales</p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {activeAssignments.length ? activeAssignments.map((row) => (
              <span key={row.id} className="rounded-md border border-white/10 px-2 py-1 text-xs text-zinc-300">
                {row.role_name}{row.area_name ? ` · ${row.area_name}` : ""}
              </span>
            )) : (
              <span className="text-xs text-amber-300/80">Compatibilidad: {membership.role_label}</span>
            )}
          </div>
        </div>

        <div>
          <p className="text-[10px] font-semibold uppercase tracking-wider text-zinc-600">Permisos efectivos</p>
          <div className="mt-1.5">
            {activeAssignments.length ? <CapabilityPills codes={effective} /> : (
              <p className="text-xs text-zinc-500">Derivados temporalmente del rol heredado.</p>
            )}
          </div>
        </div>

        <div className="flex items-center justify-between gap-3 lg:justify-end">
          <span className="text-xs text-zinc-500">
            {membership.branch_access_mode === "all"
              ? "Todas las sucursales"
              : `${membership.branch_access.length} sucursal(es)`}
          </span>
          <span className="text-zinc-500">{open ? "−" : "+"}</span>
        </div>
      </button>

      {open ? (
        <div className="border-t border-white/[0.06] px-5 py-5">
          {!canManage ? (
            <div className="rounded-lg border border-white/[0.06] bg-black/20 px-4 py-3 text-sm text-zinc-500">
              Puedes revisar este acceso, pero necesitas <code className="text-zinc-300">memberships.manage</code> para modificarlo.
            </div>
          ) : null}

          <div className="grid gap-6 xl:grid-cols-2">
            <section>
              <div className="mb-3 flex items-center justify-between">
                <div>
                  <h3 className="text-sm font-semibold text-zinc-200">Roles y área</h3>
                  <p className="mt-1 text-xs text-zinc-600">El rol define qué puede hacer. El área solo organiza al equipo.</p>
                </div>
              </div>

              <div className="space-y-2">
                {assignments.length ? assignments.map((assignment) => (
                  <div key={assignment.id} className="flex items-center justify-between gap-3 rounded-lg border border-white/[0.06] px-3 py-2.5">
                    <div>
                      <p className={assignment.is_active ? "text-sm text-zinc-200" : "text-sm text-zinc-600 line-through"}>
                        {assignment.role_name}
                      </p>
                      <p className="mt-0.5 text-[11px] text-zinc-600">{assignment.area_name || "Sin área"} · {assignment.capabilities.length} permisos</p>
                    </div>
                    {canManage ? (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => void toggleAssignment(assignment)}
                        className="rounded-md border border-white/10 px-2.5 py-1.5 text-xs text-zinc-400 hover:text-white disabled:opacity-40"
                      >
                        {assignment.is_active ? "Desactivar" : "Reactivar"}
                      </button>
                    ) : null}
                  </div>
                )) : (
                  <p className="rounded-lg border border-amber-500/15 bg-amber-500/[0.04] px-3 py-3 text-xs text-amber-200/70">
                    Aún usa el rol heredado «{membership.role_label}». Asigna un rol empresarial para pasar al modelo SaaS configurable.
                  </p>
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
                  <button type="button" onClick={() => void addRole()} disabled={busy || !roleId} className="rounded-lg bg-white px-4 py-2 text-sm font-semibold text-black disabled:opacity-40">
                    Asignar
                  </button>
                </div>
              ) : null}
            </section>

            <section>
              <h3 className="text-sm font-semibold text-zinc-200">Sucursales</h3>
              <p className="mt-1 text-xs text-zinc-600">Esto define dónde puede operar; no agrega permisos funcionales.</p>

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
                      <label key={branch.id} className="flex cursor-pointer items-center gap-2 rounded-lg border border-white/[0.06] px-3 py-2 text-sm text-zinc-400">
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
                <button type="button" disabled={busy} onClick={() => void saveBranches()} className="mt-3 rounded-lg border border-white/10 px-4 py-2 text-sm text-zinc-300 hover:text-white disabled:opacity-40">
                  Guardar alcance de sucursales
                </button>
              ) : null}
            </section>
          </div>

          {error ? <p className="mt-4 text-sm text-red-400">{error}</p> : null}
        </div>
      ) : null}
    </div>
  );
}

function StaffAccessContent({ ctx }: { ctx: InternalContext }) {
  const companyId = ctx.dashboard?.company?.id ?? null;
  const access = ctx.dashboard?.access;
  const capabilities = new Set(access?.capabilities ?? []);
  const canManage = Boolean(access?.is_platform_admin || capabilities.has("memberships.manage"));
  const canManageRoles = Boolean(access?.is_platform_admin || capabilities.has("roles.manage"));

  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [areas, setAreas] = useState<Area[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [assignments, setAssignments] = useState<Record<number, Assignment[]>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");

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

      const membershipRows = memberData.results;
      const assignmentPairs = await Promise.all(
        membershipRows.map(async (membership) => {
          const data = await getJson<{ results: Assignment[] }>(
            `/admin/membership-role-assignments/?membership=${membership.id}`,
          );
          return [membership.id, data.results] as const;
        }),
      );

      setMemberships(membershipRows);
      setRoles(roleData.results);
      setAreas(areaData.results);
      setBranches(branchData.results);
      setAssignments(Object.fromEntries(assignmentPairs));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cargar el personal.");
    } finally {
      setLoading(false);
    }
  }, [companyId]);

  useEffect(() => {
    void load();
  }, [load]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return memberships;
    return memberships.filter((row) => row.username.toLowerCase().includes(q));
  }, [memberships, search]);

  return (
    <AdminShell user={ctx.user}>
      <div className="space-y-7">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.22em] text-zinc-600">Administración</p>
            <h1 className="mt-1 text-2xl font-semibold text-white">Personal y accesos</h1>
            <p className="mt-2 max-w-3xl text-sm text-zinc-500">
              Los permisos pertenecen a la empresa. Un rol define <strong className="font-medium text-zinc-300">qué</strong> puede hacer una persona y el alcance de sucursal define <strong className="font-medium text-zinc-300">dónde</strong> puede hacerlo.
            </p>
          </div>
          {canManageRoles ? (
            <Link href="/admin/roles" className="rounded-lg bg-white px-4 py-2 text-sm font-semibold text-black transition hover:bg-zinc-200">
              Gestionar roles y permisos
            </Link>
          ) : null}
        </div>

        {!companyId ? (
          <div className="rounded-xl border border-amber-500/20 bg-amber-500/[0.05] p-5 text-sm text-amber-200/80">
            Selecciona una empresa en el encabezado para administrar su personal. El master de plataforma nunca opera sobre un tenant implícito.
          </div>
        ) : (
          <>
            <div className="grid gap-3 sm:grid-cols-3">
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                <p className="text-xs text-zinc-600">Miembros</p>
                <p className="mt-1 text-2xl font-semibold text-white">{memberships.length}</p>
              </div>
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                <p className="text-xs text-zinc-600">Roles configurados</p>
                <p className="mt-1 text-2xl font-semibold text-white">{roles.filter((role) => role.is_active).length}</p>
              </div>
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                <p className="text-xs text-zinc-600">Mi autoridad</p>
                <p className="mt-1 text-sm font-medium text-zinc-200">{access?.is_platform_admin ? "Master de plataforma" : canManage ? "Administrador de accesos" : "Solo lectura"}</p>
              </div>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3">
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Buscar por usuario…"
                className="w-full max-w-sm rounded-lg border border-white/[0.08] bg-black/40 px-3 py-2.5 text-sm text-zinc-200 outline-none focus:border-white/25"
              />
              <p className="text-xs text-zinc-600">{ctx.dashboard?.company?.name}</p>
            </div>

            {loading ? <p className="py-12 text-center text-sm text-zinc-600">Cargando accesos…</p> : null}
            {error ? <div className="rounded-xl border border-red-500/20 bg-red-500/[0.05] p-4 text-sm text-red-400">{error}</div> : null}

            {!loading && !error ? (
              <div className="space-y-3">
                {filtered.map((membership) => (
                  <MemberAccessEditor
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
                {!filtered.length ? (
                  <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] py-12 text-center text-sm text-zinc-500">No se encontraron miembros.</div>
                ) : null}
              </div>
            ) : null}

            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-5">
              <h2 className="text-sm font-semibold text-zinc-200">Modelo de seguridad</h2>
              <div className="mt-3 grid gap-3 text-xs text-zinc-500 md:grid-cols-3">
                <p><strong className="text-zinc-300">Master:</strong> es autoridad de plataforma y puede actuar en cualquier empresa, siempre seleccionándola explícitamente.</p>
                <p><strong className="text-zinc-300">Rol empresarial:</strong> concede capacidades solo dentro de esta empresa. El nombre del rol no es una autorización por sí solo.</p>
                <p><strong className="text-zinc-300">Sucursal:</strong> limita el lugar operativo. Nunca amplía los permisos funcionales del rol.</p>
              </div>
            </div>
          </>
        )}
      </div>
    </AdminShell>
  );
}

export default function UsersPage() {
  return (
    <InternalControlGuard>
      {(ctx) => <StaffAccessContent ctx={ctx} />}
    </InternalControlGuard>
  );
}
