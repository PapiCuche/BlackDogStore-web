"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AdminShell } from "../components/AdminShell";
import { PageHeader, internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "../components/internal-ui";
import {
  InternalControlGuard,
  type InternalContext,
} from "../components/InternalControlGuard";
import { fetchWithAuth } from "../../lib/auth";
import { API_BASE } from "../../lib/api";

type Capability = {
  code: string;
  module: string;
  name: string;
  description: string;
  status: "active" | "available" | "reserved";
  assignable: boolean;
};
type CapabilityCatalog = {
  capabilities: Capability[];
  held_by_me: string[];
  is_platform_admin: boolean;
};
type Role = {
  id: number;
  company: number;
  name: string;
  slug: string;
  description: string;
  capabilities: string[];
  is_active: boolean;
  assignment_count: number;
};

const MODULE_LABELS: Record<string, string> = {
  company: "Empresa",
  memberships: "Personal",
  areas: "Áreas",
  roles: "Roles y permisos",
  products: "Productos",
  inventory: "Inventario",
  sales: "Ventas",
  reports: "Reportes",
  settings: "Configuración",
  service: "Servicio técnico",
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

async function saveJson<T>(path: string, method: "POST" | "PATCH", body: unknown): Promise<T> {
  const res = await fetchWithAuth(`${API_BASE}${path}`, {
    method,
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await readDetail(res, "No se pudo guardar el rol."));
  return res.json();
}

function CapabilityMatrix({
  catalog,
  selected,
  disabled,
  onToggle,
}: {
  catalog: CapabilityCatalog;
  selected: string[];
  disabled: boolean;
  onToggle: (code: string) => void;
}) {
  const held = new Set(catalog.held_by_me);
  const grouped = useMemo(() => {
    const map = new Map<string, Capability[]>();
    for (const capability of catalog.capabilities) {
      const rows = map.get(capability.module) ?? [];
      rows.push(capability);
      map.set(capability.module, rows);
    }
    return Array.from(map.entries());
  }, [catalog.capabilities]);

  return (
    <div className="space-y-4">
      {grouped.map(([module, capabilities]) => (
        <section key={module} className="rounded-xl border border-bd-border bg-background p-4">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h3 className="text-sm font-semibold text-foreground">{MODULE_LABELS[module] ?? module}</h3>
            <span className="text-[10px] text-muted">{capabilities.length} permisos</span>
          </div>
          <div className="grid gap-2 lg:grid-cols-2">
            {capabilities.map((capability) => {
              const checked = selected.includes(capability.code);
              const canDelegate = catalog.is_platform_admin || held.has(capability.code);
              const locked = disabled || !capability.assignable || !canDelegate;
              return (
                <label
                  key={capability.code}
                  className={`flex gap-3 rounded-lg border px-3 py-3 ${checked ? "border-foreground/20 bg-foreground/[0.04]" : "border-bd-border/70"} ${locked ? "opacity-55" : "cursor-pointer"}`}
                  title={!capability.assignable ? "Todavía no puede asignarse." : !canDelegate ? "No puedes delegar un permiso que no posees." : undefined}
                >
                  <input type="checkbox" checked={checked} disabled={locked} onChange={() => onToggle(capability.code)} className="mt-0.5" />
                  <span className="min-w-0">
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium text-foreground">{capability.name}</span>
                      <span className={`rounded px-1.5 py-0.5 text-[9px] ${capability.status === "active" ? "bg-foreground/[0.06] text-muted" : capability.status === "available" ? "bg-amber-500/10 text-amber-300/70" : "bg-zinc-800 text-muted"}`}>
                        {capability.status === "active" ? "Activo" : capability.status === "available" ? "Transición" : "Reservado"}
                      </span>
                    </span>
                    <span className="mt-1 block text-xs leading-5 text-muted">{capability.description}</span>
                    <code className="mt-1.5 block text-[10px] text-muted/70">{capability.code}</code>
                  </span>
                </label>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}

function RoleCard({
  role,
  catalog,
  canManage,
  onSaved,
}: {
  role: Role;
  catalog: CapabilityCatalog;
  canManage: boolean;
  onSaved: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(role.name);
  const [description, setDescription] = useState(role.description ?? "");
  const [selected, setSelected] = useState<string[]>(role.capabilities ?? []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const held = new Set(catalog.held_by_me);
  const roleExceedsMyAuthority = !catalog.is_platform_admin && role.capabilities.some((code) => !held.has(code));
  const editable = canManage && role.is_active && !roleExceedsMyAuthority;

  function toggle(code: string) {
    setSelected((current) => current.includes(code) ? current.filter((item) => item !== code) : [...current, code].sort());
  }

  async function toggleActive() {
    // Deactivating a role is safe as of M11: the people holding it lose these
    // capabilities and, if it was their only role, hold none — they are NOT
    // handed their legacy role back. That was the bug this console refused to
    // expose until the backend closed it.
    if (!canManage || roleExceedsMyAuthority) return;
    setBusy(true);
    setError(null);
    try {
      await saveJson(`/admin/roles/${role.id}/`, "PATCH", { is_active: !role.is_active });
      await onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cambiar el estado del rol.");
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (!editable || !name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await saveJson(`/admin/roles/${role.id}/`, "PATCH", {
        name: name.trim(),
        description: description.trim(),
        capabilities: selected,
      });
      await onSaved();
      setOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo guardar el rol.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="rounded-xl border border-bd-border bg-surface">
      <button type="button" onClick={() => setOpen((value) => !value)} className="flex w-full items-center justify-between gap-4 px-5 py-4 text-left">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="font-medium text-foreground">{role.name}</h2>
            <span className={`rounded-full px-2 py-0.5 text-[10px] ${role.is_active ? "bg-foreground/[0.06] text-foreground" : "bg-red-500/10 text-red-400"}`}>{role.is_active ? "Activo" : "Inactivo"}</span>
          </div>
          <p className="mt-1 truncate text-xs text-muted">{role.description || "Sin descripción"}</p>
        </div>
        <div className="flex shrink-0 items-center gap-5 text-xs text-muted">
          <span>{role.capabilities.length} permisos</span>
          <span>{role.assignment_count} asignaciones</span>
          <span>{open ? "−" : "+"}</span>
        </div>
      </button>

      {open ? (
        <div className="border-t border-bd-border px-5 py-5">
          {roleExceedsMyAuthority ? (
            <div className="mb-4 rounded-lg border border-amber-500/20 bg-amber-500/[0.05] px-4 py-3 text-xs text-amber-200/75">Este rol contiene permisos superiores a tu autoridad. Puedes revisarlo, pero no modificarlo ni conservar esos privilegios mediante una edición indirecta.</div>
          ) : null}
          {role.assignment_count > 0 ? (
            <div className="mb-4 rounded-lg border border-bd-border bg-surface px-4 py-3 text-xs leading-5 text-muted">
              Este rol está asignado a <strong className="text-foreground">{role.assignment_count}</strong> persona(s). Desactivarlo les retira estas capacidades de inmediato; si era su único rol quedan <strong className="text-foreground">sin permisos</strong>, no con el rol heredado. Las asignaciones se conservan como historial.
            </div>
          ) : null}

          <div className="mb-5 grid gap-4 md:grid-cols-[1fr_2fr]">
            <label className="text-xs text-muted">Nombre<input value={name} onChange={(event) => setName(event.target.value)} disabled={!editable || busy} className={`mt-1.5 ${internalInputClass}`} /></label>
            <label className="text-xs text-muted">Descripción<input value={description} onChange={(event) => setDescription(event.target.value)} disabled={!editable || busy} className={`mt-1.5 ${internalInputClass}`} /></label>
          </div>

          <CapabilityMatrix catalog={catalog} selected={selected} disabled={!editable || busy} onToggle={toggle} />
          {error ? <p className="mt-4 text-sm text-red-400">{error}</p> : null}
          {canManage && !roleExceedsMyAuthority ? (
            <div className="mt-5 flex flex-wrap items-center justify-end gap-3">
              <button
                type="button"
                disabled={busy}
                onClick={() => void toggleActive()}
                className={`rounded-xl border px-4 py-2.5 text-sm font-semibold disabled:opacity-40 ${role.is_active ? "border-bd-border text-foreground hover:border-red-500/40 hover:text-red-300" : "border-bd-border text-foreground hover:border-foreground/25"}`}
              >
                {role.is_active ? "Desactivar rol" : "Reactivar rol"}
              </button>
              {editable ? (
                <button type="button" disabled={busy || !name.trim()} onClick={() => void save()} className={internalPrimaryButtonClass}>Guardar rol</button>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

function NewRole({ companyId, catalog, onCreated }: { companyId: number; catalog: CapabilityCatalog; onCreated: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function toggle(code: string) {
    setSelected((current) => current.includes(code) ? current.filter((item) => item !== code) : [...current, code].sort());
  }

  async function create() {
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const slug = name.trim().toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
      await saveJson("/admin/roles/", "POST", {
        company: companyId,
        name: name.trim(),
        slug,
        description: description.trim(),
        capabilities: selected,
        is_active: true,
      });
      setName("");
      setDescription("");
      setSelected([]);
      setOpen(false);
      await onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo crear el rol.");
    } finally {
      setBusy(false);
    }
  }

  if (!open) return <button type="button" onClick={() => setOpen(true)} className={internalPrimaryButtonClass}>Nuevo rol</button>;

  return (
    <section className="rounded-2xl border border-bd-border bg-foreground/[0.03] p-5">
      <div className="mb-5 flex items-start justify-between gap-3">
        <div><h2 className="font-semibold text-foreground">Nuevo rol empresarial</h2><p className="mt-1 text-xs text-muted">Empieza con el mínimo privilegio y agrega solo lo necesario.</p></div>
        <button type="button" onClick={() => setOpen(false)} className="text-sm text-muted hover:text-foreground">Cerrar</button>
      </div>
      <div className="mb-5 grid gap-4 md:grid-cols-2">
        <label className="text-xs text-muted">Nombre<input value={name} onChange={(event) => setName(event.target.value)} className={`mt-1.5 ${internalInputClass}`} placeholder="Ej. Recepción" /></label>
        <label className="text-xs text-muted">Descripción<input value={description} onChange={(event) => setDescription(event.target.value)} className={`mt-1.5 ${internalInputClass}`} placeholder="Responsabilidades del rol" /></label>
      </div>
      <CapabilityMatrix catalog={catalog} selected={selected} disabled={busy} onToggle={toggle} />
      {error ? <p className="mt-4 text-sm text-red-400">{error}</p> : null}
      <div className="mt-5 flex justify-end"><button type="button" onClick={() => void create()} disabled={busy || !name.trim()} className={internalPrimaryButtonClass}>Crear rol</button></div>
    </section>
  );
}

function RolesContent({ ctx }: { ctx: InternalContext }) {
  const companyId = ctx.dashboard?.company?.id ?? null;
  const access = ctx.dashboard?.access;
  const canManage = Boolean(access?.is_platform_admin || access?.capabilities.includes("roles.manage"));
  const [roles, setRoles] = useState<Role[]>([]);
  const [catalog, setCatalog] = useState<CapabilityCatalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!companyId) {
      setRoles([]);
      setCatalog(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const qs = `?company=${encodeURIComponent(String(companyId))}`;
      const [roleData, catalogData] = await Promise.all([
        getJson<{ results: Role[] }>(`/admin/roles/${qs}`),
        getJson<CapabilityCatalog>(`/admin/capabilities/${qs}`),
      ]);
      setRoles(roleData.results);
      setCatalog(catalogData);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudieron cargar los roles.");
    } finally {
      setLoading(false);
    }
  }, [companyId]);

  useEffect(() => { void load(); }, [load]);

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
        <PageHeader
          eyebrow="Administración"
          title="Roles y permisos"
          description="Configura autoridad por empresa. El nombre del rol organiza; las capacidades son las que realmente autorizan."
          actions={
            <>
              <Link href="/admin/users" className={internalButtonClass}>Personal</Link>
              {canManage && companyId && catalog ? <NewRole companyId={companyId} catalog={catalog} onCreated={load} /> : null}
            </>
          }
        />

        {!companyId ? <div className="rounded-xl border border-amber-500/20 bg-amber-500/[0.05] p-5 text-sm text-amber-200/80">Selecciona una empresa. El master de plataforma tiene autoridad global, pero debe escoger el tenant antes de editarlo.</div> : null}
        {loading ? <p className="py-12 text-center text-sm text-muted">Cargando roles…</p> : null}
        {error ? <div className="rounded-xl border border-red-500/20 bg-red-500/[0.05] p-4 text-sm text-red-400">{error}</div> : null}

        {companyId && catalog && !loading && !error ? (
          <>
            <div className="grid gap-3 md:grid-cols-3">
              <div className="rounded-xl border border-bd-border bg-surface p-4"><p className="text-xs text-muted">Roles activos</p><p className="mt-1 text-2xl font-semibold text-foreground">{roles.filter((role) => role.is_active).length}</p></div>
              <div className="rounded-xl border border-bd-border bg-surface p-4"><p className="text-xs text-muted">Capacidades asignables</p><p className="mt-1 text-2xl font-semibold text-foreground">{catalog.capabilities.filter((cap) => cap.assignable).length}</p></div>
              <div className="rounded-xl border border-bd-border bg-surface p-4"><p className="text-xs text-muted">Mi alcance delegable</p><p className="mt-1 text-2xl font-semibold text-foreground">{catalog.is_platform_admin ? catalog.capabilities.filter((cap) => cap.assignable).length : catalog.held_by_me.length}</p></div>
            </div>

            <div className="rounded-xl border border-bd-border bg-surface p-4 text-xs leading-5 text-muted"><strong className="text-foreground">Leyenda:</strong> Activo gobierna endpoints actuales; Transición todavía convive con compatibilidad legacy; Reservado describe funcionalidad futura y no puede asignarse.</div>

            <div className="space-y-3">
              {roles.map((role) => <RoleCard key={role.id} role={role} catalog={catalog} canManage={canManage} onSaved={load} />)}
              {!roles.length ? <div className="rounded-xl border border-bd-border py-12 text-center text-sm text-muted">Esta empresa todavía no tiene roles configurados.</div> : null}
            </div>
          </>
        ) : null}
      </div>
    </AdminShell>
  );
}

export default function RolesPage() {
  return <InternalControlGuard>{(ctx) => <RolesContent ctx={ctx} />}</InternalControlGuard>;
}
