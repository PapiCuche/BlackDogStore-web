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
  // The serializer returns the area id as well as its name; the id is what
  // decides whether a role is already held in this exact slot.
  area: number | null;
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

async function deleteJson(path: string): Promise<void> {
  // The server answers 204 with no body, so this returns nothing rather than
  // trying to parse one.
  const res = await fetchWithAuth(`${API_BASE}${path}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await readDetail(res, "No se pudo quitar el rol."));
}

function PermissionSummary({ codes }: { codes: string[] }) {
  const unique = Array.from(new Set(codes)).sort();
  if (!unique.length) return <span className="text-xs text-muted">Sin permisos efectivos</span>;
  return (
    <div className="flex flex-wrap gap-1.5">
      {unique.slice(0, 4).map((code) => (
        <code key={code} className="rounded-md border border-bd-border bg-surface-2 px-2 py-1 text-[10px] text-muted">
          {code}
        </code>
      ))}
      {unique.length > 4 ? (
        <span className="rounded-md border border-bd-border px-2 py-1 text-[10px] text-muted">
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

  // THREE STATES, NAMED ONCE, mirroring exactly what the backend resolves.
  //
  // The distinction that matters is between "never migrated" and "migrated and
  // currently holds nothing". They look identical if you only count ACTIVE
  // assignments — which is what this console used to do, so somebody stripped
  // of their last role was labelled "Legacy: Administrador", implying they
  // still had that authority. They do not: `resolve_capabilities()` returns an
  // empty set for them, and saying otherwise on screen is the UI contradicting
  // the system it is a window onto.
  //
  // `assignments` holds EVERY row including revoked ones, which is the same
  // signal `has_custom_role_history()` reads server-side.
  const accessState: "legacy" | "custom" | "custom-empty" =
    assignments.length === 0 ? "legacy" : activeAssignments.length > 0 ? "custom" : "custom-empty";

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

  async function revokeRole(assignmentId: number) {
    // DELETE is a SOFT disable server-side: the row survives as the audit trail
    // of who granted what and when. Removing one role never touches the others,
    // and — since the backend fix — never resurrects the legacy role either.
    await perform(() => deleteJson(`/admin/membership-role-assignments/${assignmentId}/`));
  }

  async function reactivateRole(assignmentId: number) {
    // PATCH, never POST. One logical assignment is ONE row that switches state;
    // posting a second one would be refused by the database anyway, and if it
    // were not, it would split this person's history across two records.
    //
    // The backend revalidates delegation on reactivation, because reactivating
    // IS granting. A 403 from there is surfaced as-is rather than worked around.
    await perform(() => patchJson(`/admin/membership-role-assignments/${assignmentId}/`, {
      is_active: true,
    }));
  }

  /**
   * The row that already represents this (role, area) slot, active or not.
   *
   * Identity is role + area because the model allows the same role in two
   * different areas — `Técnico / Taller` and `Técnico / Laboratorio` coexist.
   * What cannot coexist is the same pair twice.
   */
  function existingAssignment(roleValue: number, areaValue: number | null) {
    return assignments.find(
      (row) => row.role === roleValue && (row.area ?? null) === areaValue,
    );
  }

  async function assignRole() {
    if (!roleId) return;
    const targetArea = areaId ? Number(areaId) : null;
    const existing = existingAssignment(Number(roleId), targetArea);
    if (existing) {
      // Reuse the historical row instead of creating a second one.
      if (!existing.is_active) await reactivateRole(existing.id);
      setRoleId("");
      setAreaId("");
      return;
    }
    await perform(() => postJson("/admin/membership-role-assignments/", {
      membership: membership.id,
      role: Number(roleId),
      area: targetArea,
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
    <article className="rounded-xl border border-bd-border bg-surface">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="grid w-full gap-4 px-5 py-4 text-left lg:grid-cols-[1.1fr_1.2fr_1.5fr_auto] lg:items-center"
      >
        <div>
          <div className="flex items-center gap-2">
            <span className="font-medium text-foreground">{membership.username}</span>
            <span className={`rounded-full px-2 py-0.5 text-[10px] ${membership.is_active ? "bg-surface-2 text-foreground/85" : "bg-danger-surface text-danger"}`}>
              {membership.is_active ? "Activo" : "Inactivo"}
            </span>
          </div>
          <p className="mt-1 text-xs text-muted">Membresía #{membership.id}</p>
        </div>

        <div>
          <p className="text-[10px] font-semibold uppercase tracking-wider text-muted">Rol empresarial</p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {accessState === "custom" ? activeAssignments.map((assignment) => (
              <span key={assignment.id} className="rounded-md border border-bd-border px-2 py-1 text-xs text-foreground/85">
                {assignment.role_name}{assignment.area_name ? ` · ${assignment.area_name}` : ""}
              </span>
            )) : accessState === "legacy" ? (
              <span className="text-xs text-warning">Rol heredado: {membership.role_label}</span>
            ) : (
              <span className="text-xs text-danger">Sin roles activos</span>
            )}
          </div>
        </div>

        <div>
          <p className="text-[10px] font-semibold uppercase tracking-wider text-muted">Permisos</p>
          <div className="mt-1.5">
            {accessState === "custom" ? (
              <PermissionSummary codes={effective} />
            ) : accessState === "legacy" ? (
              <span className="text-xs text-muted">Pendiente de migrar a roles configurables.</span>
            ) : (
              <span className="text-xs text-danger">Sin permisos efectivos.</span>
            )}
          </div>
        </div>

        <div className="flex items-center justify-between gap-3 lg:justify-end">
          <span className="text-xs text-muted">
            {membership.branch_access_mode === "all" ? "Todas las sucursales" : `${membership.branch_access.length} sucursal(es)`}
          </span>
          <span className="text-muted">{open ? "−" : "+"}</span>
        </div>
      </button>

      {open ? (
        <div className="border-t border-bd-border px-5 py-5">
          {accessState === "custom-empty" ? (
            <div className="mb-5 rounded-lg border border-warning-border bg-amber-500/[0.05] px-4 py-3 text-xs leading-5 text-warning">
              <strong>Sin roles activos.</strong> Esta persona no tiene ninguna capacidad en la empresa. No vuelve al rol heredado «{membership.role_label}»: ya usa RBAC configurable, y quitarle el último rol significa exactamente eso.
            </div>
          ) : null}

          <div className="grid gap-6 xl:grid-cols-2">
            <section>
              <h3 className="text-sm font-semibold text-foreground">Roles y área</h3>
              <p className="mt-1 text-xs text-muted">El rol concede autoridad; el área solo organiza al personal.</p>

              <div className="mt-3 space-y-2">
                {assignments.length ? assignments.map((assignment) => (
                  <div key={assignment.id} className="rounded-lg border border-bd-border px-3 py-2.5">
                    <div className="flex items-center justify-between gap-3">
                      <span className={assignment.is_active ? "text-sm text-foreground" : "text-sm text-muted line-through"}>{assignment.role_name}</span>
                      <div className="flex items-center gap-2">
                        <span className="text-[10px] text-muted">{assignment.is_active ? "Activo" : "Histórico"}</span>
                        {canManage ? (
                          <button
                            type="button"
                            onClick={() => void (assignment.is_active
                              ? revokeRole(assignment.id)
                              : reactivateRole(assignment.id))}
                            disabled={busy}
                            className={`rounded-md border px-2 py-1 text-[10px] disabled:opacity-40 ${assignment.is_active
                              ? "border-bd-border text-muted hover:border-danger-border hover:text-danger"
                              : "border-success-border text-success hover:border-success-border hover:text-success"}`}
                          >
                            {assignment.is_active ? "Quitar" : "Reactivar"}
                          </button>
                        ) : null}
                      </div>
                    </div>
                    <p className="mt-1 text-[11px] text-muted">{assignment.area_name || "Sin área"} · {assignment.capabilities.length} permisos</p>
                  </div>
                )) : (
                  <div className="rounded-lg border border-warning-border bg-amber-500/[0.03] px-3 py-3 text-xs leading-5 text-warning">
                    Nunca se le asignó un rol empresarial, así que sigue rigiéndose por «{membership.role_label}» del modelo heredado. Asignarle uno lo migra al RBAC configurable — y a partir de ahí el rol heredado deja de contar para siempre.
                  </div>
                )}
              </div>

              {canManage ? (
                <div className="mt-3 grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
                  <select value={roleId} onChange={(event) => setRoleId(event.target.value)} disabled={busy} className="rounded-lg border border-bd-border bg-background/50 px-3 py-2 text-sm text-foreground/85">
                    <option value="">Elegir rol…</option>
                    {roles
                      .filter((role) => role.is_active)
                      // Se ocultan los que YA están activos en este hueco: no
                      // hay nada que hacer con ellos. Los que tienen una
                      // asignación histórica SÍ se ofrecen — elegirlos la
                      // reactiva en vez de intentar un POST que la base de
                      // datos rechazaría.
                      .filter((role) => {
                        const existing = existingAssignment(role.id, areaId ? Number(areaId) : null);
                        return !existing || !existing.is_active;
                      })
                      .map((role) => {
                        const existing = existingAssignment(role.id, areaId ? Number(areaId) : null);
                        return (
                          <option key={role.id} value={role.id}>
                            {role.name}{existing ? " · reactivar" : ""}
                          </option>
                        );
                      })}
                  </select>
                  <select value={areaId} onChange={(event) => setAreaId(event.target.value)} disabled={busy} className="rounded-lg border border-bd-border bg-background/50 px-3 py-2 text-sm text-foreground/85">
                    <option value="">Sin área</option>
                    {areas.filter((area) => area.is_active).map((area) => <option key={area.id} value={area.id}>{area.name}</option>)}
                  </select>
                  <button type="button" onClick={() => void assignRole()} disabled={busy || !roleId} className="rounded-lg bg-foreground px-4 py-2 text-sm font-semibold text-background disabled:opacity-40">
                    {roleId && existingAssignment(Number(roleId), areaId ? Number(areaId) : null) ? "Reactivar" : "Asignar"}
                  </button>
                </div>
              ) : null}
            </section>

            <section>
              <h3 className="text-sm font-semibold text-foreground">Alcance por sucursal</h3>
              <p className="mt-1 text-xs text-muted">Responde dónde puede operar; nunca agrega capacidades.</p>

              <div className="mt-3 flex gap-2">
                {(["all", "selected"] as const).map((value) => (
                  <button
                    key={value}
                    type="button"
                    disabled={!canManage || busy}
                    onClick={() => setMode(value)}
                    className={`rounded-lg border px-3 py-2 text-xs ${mode === value ? "border-bd-border bg-surface-2 text-foreground" : "border-bd-border text-muted"}`}
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
                      <label key={branch.id} className="flex items-center gap-2 rounded-lg border border-bd-border px-3 py-2 text-sm text-muted">
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
                <button type="button" onClick={() => void saveBranches()} disabled={busy} className="mt-3 rounded-lg border border-bd-border px-4 py-2 text-sm text-foreground/85 hover:text-foreground disabled:opacity-40">
                  Guardar sucursales
                </button>
              ) : null}
            </section>
          </div>

          {!canManage ? <p className="mt-4 text-xs text-muted">Modo lectura: necesitas <code>memberships.manage</code> para modificar accesos.</p> : null}
          {error ? <p className="mt-4 text-sm text-danger">{error}</p> : null}
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
    // G3 — `dashboard` and `onSelectCompany` were both missing, so the page
    // told a platform master to "selecciona una empresa" and then handed them a
    // switcher whose click did nothing: `AdminShell` calls `onSelectCompany?.()`,
    // and an undefined prop makes that a no-op. Nine other admin pages pass both.
    // Without `dashboard` the shell also refetched the dashboard on its own, so
    // the topbar's company name came from a different response than the body's.
    <AdminShell
      user={ctx.user}
      dashboard={ctx.dashboard}
      onSelectCompany={ctx.selectCompany}
    >
      <div className="space-y-7">
        <header className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.22em] text-muted">Administración</p>
            <h1 className="mt-1 text-2xl font-semibold text-foreground">Personal y accesos</h1>
            <p className="mt-2 max-w-3xl text-sm text-muted">Administra el acceso real por empresa: rol = qué puede hacer; sucursal = dónde puede hacerlo.</p>
          </div>
          {canManageRoles ? <Link href="/admin/roles" className="rounded-lg bg-foreground px-4 py-2 text-sm font-semibold text-background hover:bg-foreground/90">Roles y permisos</Link> : null}
        </header>

        {!companyId ? (
          <div className="rounded-xl border border-warning-border bg-amber-500/[0.05] p-5 text-sm text-warning">Selecciona una empresa. El master de plataforma tiene acceso global, pero debe elegir explícitamente sobre qué tenant actúa.</div>
        ) : (
          <>
            <div className="grid gap-3 sm:grid-cols-3">
              <div className="rounded-xl border border-bd-border bg-surface p-4"><p className="text-xs text-muted">Miembros</p><p className="mt-1 text-2xl font-semibold text-foreground">{memberships.length}</p></div>
              <div className="rounded-xl border border-bd-border bg-surface p-4"><p className="text-xs text-muted">Roles activos</p><p className="mt-1 text-2xl font-semibold text-foreground">{roles.filter((role) => role.is_active).length}</p></div>
              <div className="rounded-xl border border-bd-border bg-surface p-4"><p className="text-xs text-muted">Mi autoridad</p><p className="mt-1 text-sm font-medium text-foreground">{access?.is_platform_admin ? "Master de plataforma" : canManage ? "Administra accesos" : "Solo lectura"}</p></div>
            </div>

            <div className="flex items-center justify-between gap-3">
              <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Buscar usuario…" className="w-full max-w-sm rounded-lg border border-bd-border bg-background/40 px-3 py-2.5 text-sm text-foreground outline-none focus:border-bd-border" />
              <span className="hidden text-xs text-muted sm:block">{ctx.dashboard?.company?.name}</span>
            </div>

            {loading ? <p className="py-12 text-center text-sm text-muted">Cargando accesos…</p> : null}
            {error ? <div className="rounded-xl border border-danger-border bg-red-500/[0.05] p-4 text-sm text-danger">{error}</div> : null}
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
                {!filtered.length ? <div className="rounded-xl border border-bd-border py-12 text-center text-sm text-muted">No se encontraron miembros.</div> : null}
              </div>
            ) : null}

            <div className="rounded-xl border border-bd-border bg-surface p-5">
              <h2 className="text-sm font-semibold text-foreground">Jerarquía correcta</h2>
              <div className="mt-3 grid gap-3 text-xs leading-5 text-muted md:grid-cols-3">
                <p><strong className="text-foreground/85">Master de plataforma:</strong> <code>User.is_superuser</code>. Puede operar todas las empresas; no es un rol de tenant.</p>
                <p><strong className="text-foreground/85">Administrador de empresa:</strong> recibe capacidades dentro de su tenant y no puede escalar por encima de su propia autoridad.</p>
                <p><strong className="text-foreground/85">Roles operativos:</strong> ventas, inventario y técnico se limitan por capacidades y, cuando aplica, por sucursal.</p>
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
