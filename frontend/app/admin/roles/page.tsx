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
        <section key={module} className="rounded-xl border border-white/[0.06] bg-black/20 p-4">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h3 className="text-sm font-semibold text-zinc-200">{MODULE_LABELS[module] ?? module}</h3>
            <span className="text-[10px] text-zinc-600">{capabilities.length} permisos</span>
          </div>
          <div className="grid gap-2 lg:grid-cols-2">
            {capabilities.map((capability) => {
              const checked = selected.includes(capability.code);
              const canDelegate = catalog.is_platform_admin || held.has(capability.code);
              const locked = disabled || !capability.assignable || !canDelegate;
              return (
                <label
                  key={capability.code}
                  className={`flex gap-3 rounded-lg border px-3 py-3 ${checked ? "border-white/20 bg-white/[0.04]" : "border-white/[0.05]"} ${locked ? "opacity-55" : "cursor-pointer"}`}
                  title={!capability.assignable ? "Todavía no puede asignarse." : !canDelegate ? "No puedes delegar un permiso que no posees." : undefined}
                >
                  <input type="checkbox" checked={checked} disabled={locked} onChange={() => onToggle(capability.code)} className="mt-0.5" />
                  <span className="min-w-0">
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium text-zinc-200">{capability.name}</span>
                      <span className={`rounded px-1.5 py-0.5 text-[9px] ${capability.status === "active" ? "bg-white/[0.06] text-zinc-400" : capability.status === "available" ? "bg-amber-500/10 text-amber-300/70" : "bg-zinc-800 text-zinc-600"}`}>
                        {capability.status === "active" ? "Activo" : capability.status === "available" ? "Transición" : "Reservado"}
                      </span>
                    </span>
                    <span className="mt-1 block text-xs leading-5 text-zinc-600">{capability.description}</span>
                    <code className="mt-1.5 block text-[10px] text-zinc-700">{capability.code}</code>
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
    <article className="rounded-xl border border-white/[0.07] bg-white/[0.02]">
      <button type="button" onClick={() => setOpen((value) => !value)} className="flex w-full items-center justify-between gap-4 px-5 py-4 text-left">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="font-medium text-zinc-100">{role.name}</h2>
            <span className={`rounded-full px-2 py-0.5 text-[10px] ${role.is_active ? "bg-white/[0.06] text-zinc-300" : "bg-red-500/10 text-red-400"}`}>{role.is_active ? "Activo" : "Inactivo"}</span>
          </div>
          <p className="mt-1 truncate text-xs text-zinc-600">{role.description || "Sin descripción"}</p>
        </div>
        <div className="flex shrink-0 items-center gap-5 text-xs text-zinc-500">
          <span>{role.capabilities.length} permisos</span>
          <span>{role.assignment_count} asignaciones</span>
          <span>{open ? "−" : "+"}</span>
        </div>
      </button>

      {open ? (
        <div className="border-t border-white/[0.06] px-5 py-5">
          {roleExceedsMyAuthority ? (
            <div className="mb-4 rounded-lg border border-amber-500/20 bg-amber-500/[0.05] px-4 py-3 text-xs text-amber-200/75">Este rol contiene permisos superiores a tu autoridad. Puedes revisarlo, pero no modificarlo ni conservar esos privilegios mediante una edición indirecta.</div>
          ) : null}
          {role.assignment_count > 0 ? (
            <div className="mb-4 rounded-lg border border-white/[0.07] bg-white/[0.02] px-4 py-3 text-xs text-zinc-500">La desactivación de roles en uso no se expone en esta consola hasta cerrar el fallback legacy detectado en la auditoría. Editar sus capacidades sí mantiene el control en el modelo personalizado.</div>
          ) : null}

          <div className="mb-5 grid gap-4 md:grid-cols-[1fr_2fr]">
            <label className="text-xs text-zinc-500">Nombre<input value={name} onChange={(event) => setName(event.target.value)} disabled={!editable || busy} className="mt-1.5 w-full rounded-lg border border-white/[0.08] bg-black/40 px-3 py-2.5 text-sm text-zinc-200" /></label>
            <label className="text-xs text-zinc-500">Descripción<input value={description} onChange={(event) => setDescription(event.target.value)} disabled={!editable || busy} className="mt-1.5 w-full rounded-lg border border-white/[0.08] bg-black/40 px-3 py-2.5 text-sm text-zinc-200" /></label>
          </div>

          <CapabilityMatrix catalog={catalog} selected={selected} disabled={!editable || busy} onToggle={toggle} />
          {error ? <p className="mt-4 text-sm text-red-400">{error}</p> : null}
          {editable ? <div className="mt-5 flex justify-end"><button type="button" disabled={busy || !name.trim()} onClick={() => void save()} className="rounded-lg bg-white px-5 py-2.5 text-sm font-semibold text-black hover:bg-zinc-200 disabled:opacity-40">Guardar rol</button></div> : null}
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

  if (!open) return <button type="button" onClick={() => setOpen(true)} className="rounded-lg bg-white px-4 py-2 text-sm font-semibold text-black hover:bg-zinc-200">Nuevo rol</button>;

  return (
    <section className="rounded-xl border border-white/10 bg-white/[0.03] p-5">
      <div className="mb-5 flex items-start justify-between gap-3">
        <div><h2 className="font-semibold text-white">Nuevo rol empresarial</h2><p className="mt-1 text-xs text-zinc-600">Empieza con el mínimo privilegio y agrega solo lo necesario.</p></div>
        <button type="button" onClick={() => setOpen(false)} className="text-sm text-zinc-500 hover:text-white">Cerrar</button>
      </div>
      <div className="mb-5 grid gap-4 md:grid-cols-2">
        <label className="text-xs text-zinc-500">Nombre<input value={name} onChange={(event) => setName(event.target.value)} className="mt-1.5 w-full rounded-lg border border-white/[0.08] bg-black/40 px-3 py-2.5 text-sm text-zinc-200" placeholder="Ej. Recepción" /></label>
        <label className="text-xs text-zinc-500">Descripción<input value={description} onChange={(event) => setDescription(event.target.value)} className="mt-1.5 w-full rounded-lg border border-white/[0.08] bg-black/40 px-3 py-2.5 text-sm text-zinc-200" placeholder="Responsabilidades del rol" /></label>
      </div>
      <CapabilityMatrix catalog={catalog} selected={selected} disabled={busy} onToggle={toggle} />
      {error ? <p className="mt-4 text-sm text-red-400">{error}</p> : null}
      <div className="mt-5 flex justify-end"><button type="button" onClick={() => void create()} disabled={busy || !name.trim()} className="rounded-lg bg-white px-5 py-2.5 text-sm font-semibold text-black disabled:opacity-40">Crear rol</button></div>
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
    <AdminShell user={ctx.user}>
      <div className="space-y-7">
        <header className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.22em] text-zinc-600">Administración</p>
            <h1 className="mt-1 text-2xl font-semibold text-white">Roles y permisos</h1>
            <p className="mt-2 max-w-3xl text-sm text-zinc-500">Configura autoridad por empresa. El nombre del rol organiza; las capacidades son las que realmente autorizan.</p>
          </div>
          <div className="flex gap-2">
            <Link href="/admin/users" className="rounded-lg border border-white/10 px-4 py-2 text-sm text-zinc-300 hover:text-white">Personal</Link>
            {canManage && companyId && catalog ? <NewRole companyId={companyId} catalog={catalog} onCreated={load} /> : null}
          </div>
        </header>

        {!companyId ? <div className="rounded-xl border border-amber-500/20 bg-amber-500/[0.05] p-5 text-sm text-amber-200/80">Selecciona una empresa. El master de plataforma tiene autoridad global, pero debe escoger el tenant antes de editarlo.</div> : null}
        {loading ? <p className="py-12 text-center text-sm text-zinc-600">Cargando roles…</p> : null}
        {error ? <div className="rounded-xl border border-red-500/20 bg-red-500/[0.05] p-4 text-sm text-red-400">{error}</div> : null}

        {companyId && catalog && !loading && !error ? (
          <>
            <div className="grid gap-3 md:grid-cols-3">
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4"><p className="text-xs text-zinc-600">Roles activos</p><p className="mt-1 text-2xl font-semibold text-white">{roles.filter((role) => role.is_active).length}</p></div>
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4"><p className="text-xs text-zinc-600">Capacidades asignables</p><p className="mt-1 text-2xl font-semibold text-white">{catalog.capabilities.filter((cap) => cap.assignable).length}</p></div>
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4"><p className="text-xs text-zinc-600">Mi alcance delegable</p><p className="mt-1 text-2xl font-semibold text-white">{catalog.is_platform_admin ? catalog.capabilities.filter((cap) => cap.assignable).length : catalog.held_by_me.length}</p></div>
            </div>

            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4 text-xs leading-5 text-zinc-500"><strong className="text-zinc-300">Leyenda:</strong> Activo gobierna endpoints actuales; Transición todavía convive con compatibilidad legacy; Reservado describe funcionalidad futura y no puede asignarse.</div>

            <div className="space-y-3">
              {roles.map((role) => <RoleCard key={role.id} role={role} catalog={catalog} canManage={canManage} onSaved={load} />)}
              {!roles.length ? <div className="rounded-xl border border-white/[0.06] py-12 text-center text-sm text-zinc-500">Esta empresa todavía no tiene roles configurados.</div> : null}
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
