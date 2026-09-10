"use client";

/**
 * PERSONAL — la pantalla de las personas que trabajan en la empresa.
 *
 * Sustituye a `/admin/users`, que hablaba de membresías: mostraba
 * «Membresía #18» como dato principal y pedía las asignaciones de cada persona
 * por separado. Quien administra una tienda piensa en «Carlos», no en un
 * identificador de fila.
 *
 * LOS IDENTIFICADORES SIGUEN EXISTIENDO y viajan en cada petición. Lo que no
 * hacen es aparecer: se elige «Técnico» y «Servicio Técnico» por su nombre.
 *
 * EL BACKEND MANDA. Las acciones se ofrecen según lo que el servidor devuelve,
 * pero cada una vuelve a comprobarse allí: ocultar un botón nunca impidió que
 * alguien llame a la ruta.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { AdminShell } from "../components/AdminShell";
import {
  InternalControlGuard,
  type InternalContext,
} from "../components/InternalControlGuard";
import {
  createInvitation,
  fetchAreas,
  fetchBranches,
  fetchInvitations,
  fetchRoles,
  fetchStaff,
  resendInvitation,
  revokeInvitation,
  setStaffActive,
  type CompanyAreaRow,
  type NamedOption,
  type StaffFilters,
  type StaffInvitation,
  type StaffPerson,
} from "../../lib/staff";

const STATUS_LABEL: Record<string, string> = {
  pending: "Pendiente",
  accepted: "Aceptada",
  revoked: "Revocada",
  expired: "Caducada",
};

/** Cuánto falta —o cuánto hace—, en lenguaje de persona. */
function relativeDays(iso: string): string {
  const days = Math.round(
    (new Date(iso).getTime() - Date.now()) / (1000 * 60 * 60 * 24),
  );
  if (days === 0) return "hoy";
  if (days > 0) return `en ${days} día${days === 1 ? "" : "s"}`;
  return `hace ${-days} día${days === -1 ? "" : "s"}`;
}

export default function StaffPage() {
  return (
    <InternalControlGuard>{(ctx) => <StaffScreen ctx={ctx} />}</InternalControlGuard>
  );
}

function StaffScreen({ ctx }: { ctx: InternalContext }) {
  // LA EMPRESA SALE DEL DASHBOARD, no de `selectedCompanyId`.
  //
  // Aquél es el SELECTOR del master: vale `null` para quien pertenece a una
  // sola empresa, que es el caso normal. Leerlo directamente dejaba la pantalla
  // girando para siempre — sin empresa no se cargaba nada y el estado inicial
  // nunca salía de «cargando».
  const companyId = ctx.dashboard?.company?.id ?? null;

  const [people, setPeople] = useState<StaffPerson[] | null>(null);
  const [invitations, setInvitations] = useState<StaffInvitation[]>([]);
  const [areas, setAreas] = useState<CompanyAreaRow[]>([]);
  const [roles, setRoles] = useState<NamedOption[]>([]);
  const [branches, setBranches] = useState<NamedOption[]>([]);
  const [filters, setFilters] = useState<StaffFilters>({ status: "active" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);

  const load = useCallback(
    async (current: StaffFilters) => {
      if (!companyId) {
        // Sin empresa NO se deja el estado en «cargando»: una pantalla que gira
        // para siempre no dice nada de lo que pasa.
        setPeople([]);
        setError('Selecciona una empresa para ver su personal.');
        return;
      }
      try {
        const [staff, invites] = await Promise.all([
          fetchStaff(companyId, current),
          fetchInvitations(companyId),
        ]);
        setPeople(staff);
        setInvitations(invites);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : "No se pudo cargar el personal.");
        setPeople([]);
      }
    },
    [companyId],
  );

  useEffect(() => {
    if (!companyId) return;
    let cancelled = false;
    void (async () => {
      // Los catálogos se cargan una vez: alimentan los selectores del
      // formulario y de los filtros, y no cambian mientras se mira la lista.
      const [a, r, b] = await Promise.all([
        fetchAreas(companyId).catch(() => []),
        fetchRoles(companyId).catch(() => []),
        fetchBranches(companyId).catch(() => []),
      ]);
      if (cancelled) return;
      setAreas(a);
      setRoles(r);
      setBranches(b);
    })();
    return () => { cancelled = true; };
  }, [companyId]);

  useEffect(() => {
    // Se vuelve a pedir al servidor en cada cambio de filtro. Filtrar en el
    // navegador exigiría traer toda la plantilla, que es justo lo que no escala.
    let cancelled = false;
    void (async () => {
      if (!cancelled) await load(filters);
    })();
    return () => { cancelled = true; };
  }, [load, filters]);

  async function toggleAccess(person: StaffPerson) {
    if (!companyId || busy) return;
    setBusy(`toggle-${person.id}`);
    setError(null);
    try {
      await setStaffActive(companyId, person.id, !person.is_active);
      await load(filters);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cambiar el acceso.");
    } finally {
      setBusy(null);
    }
  }

  async function invitationAction(
    invitation: StaffInvitation,
    action: "resend" | "revoke",
  ) {
    if (!companyId || busy) return;
    setBusy(`${action}-${invitation.id}`);
    setError(null);
    try {
      if (action === "resend") await resendInvitation(companyId, invitation.id);
      else await revokeInvitation(companyId, invitation.id);
      await load(filters);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo completar la acción.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
      <div className="space-y-6">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="font-display text-2xl text-foreground">Personal</h1>
            <p className="mt-1 text-sm text-muted">
              Administra trabajadores, responsabilidades y acceso por sucursal.
            </p>
          </div>
          <button
            type="button"
            onClick={() => setShowForm((v) => !v)}
            className="min-h-11 rounded-lg bg-foreground px-4 text-sm font-semibold text-background transition hover:bg-foreground/90"
          >
            {showForm ? "Cancelar" : "Añadir trabajador"}
          </button>
        </header>

        {error ? (
          <div
            role="alert"
            className="rounded-lg border border-danger-border bg-danger-surface px-4 py-3"
          >
            <p className="text-sm text-danger">{error}</p>
          </div>
        ) : null}

        {showForm && companyId ? (
          <AddWorkerForm
            companyId={companyId}
            areas={areas.filter((a) => a.is_active)}
            roles={roles}
            branches={branches}
            onDone={async () => {
              setShowForm(false);
              await load(filters);
            }}
            onError={setError}
          />
        ) : null}

        <Filters
          filters={filters}
          areas={areas}
          roles={roles}
          branches={branches}
          onChange={setFilters}
        />

        {invitations.length ? (
          <PendingInvitations
            invitations={invitations}
            busy={busy}
            onAction={invitationAction}
          />
        ) : null}

        <PeopleList people={people} busy={busy} onToggle={toggleAccess} />
      </div>
    </AdminShell>
  );
}

function Filters({
  filters, areas, roles, branches, onChange,
}: {
  filters: StaffFilters;
  areas: CompanyAreaRow[];
  roles: NamedOption[];
  branches: NamedOption[];
  onChange: (f: StaffFilters) => void;
}) {
  const set = (patch: Partial<StaffFilters>) => onChange({ ...filters, ...patch });

  return (
    <section className="rounded-xl border border-bd-border bg-surface p-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div className="min-w-0">
          <label
            htmlFor="staff-search"
            className="mb-1 block text-[11px] font-semibold uppercase tracking-widest text-muted"
          >
            Buscar
          </label>
          <input
            id="staff-search"
            type="search"
            value={filters.search ?? ""}
            onChange={(e) => set({ search: e.target.value })}
            placeholder="Nombre, apellido o correo"
            className="min-h-11 w-full rounded-lg border border-bd-border bg-background px-3 text-sm text-foreground"
          />
        </div>

        <Select
          id="staff-status" label="Estado" value={filters.status ?? ""}
          onChange={(v) => set({ status: v })}
          options={[
            { value: "active", label: "Activos" },
            { value: "inactive", label: "Inactivos" },
            { value: "", label: "Todos" },
          ]}
        />
        <Select
          id="staff-area" label="Área" value={String(filters.area ?? "")}
          onChange={(v) => set({ area: v ? Number(v) : "" })}
          options={[
            { value: "", label: "Todas" },
            ...areas.map((a) => ({ value: String(a.id), label: a.name })),
          ]}
        />
        <Select
          id="staff-role" label="Rol" value={String(filters.role ?? "")}
          onChange={(v) => set({ role: v ? Number(v) : "" })}
          options={[
            { value: "", label: "Todos" },
            ...roles.map((r) => ({ value: String(r.id), label: r.name })),
          ]}
        />
        {branches.length > 1 ? (
          <Select
            id="staff-branch" label="Sucursal" value={String(filters.branch ?? "")}
            onChange={(v) => set({ branch: v ? Number(v) : "" })}
            options={[
              { value: "", label: "Todas" },
              ...branches.map((b) => ({ value: String(b.id), label: b.name })),
            ]}
          />
        ) : null}
      </div>
    </section>
  );
}

function Select({
  id, label, value, onChange, options,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <div className="min-w-0">
      <label
        htmlFor={id}
        className="mb-1 block text-[11px] font-semibold uppercase tracking-widest text-muted"
      >
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="min-h-11 w-full rounded-lg border border-bd-border bg-background px-3 text-sm text-foreground"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </div>
  );
}

function PeopleList({
  people, busy, onToggle,
}: {
  people: StaffPerson[] | null;
  busy: string | null;
  onToggle: (p: StaffPerson) => void;
}) {
  if (people === null) {
    return (
      <div className="flex items-center gap-3 text-sm text-muted">
        <div className="h-4 w-4 animate-spin rounded-full border-2 border-bd-border border-t-transparent" />
        Cargando personal…
      </div>
    );
  }

  if (!people.length) {
    return (
      <section className="rounded-xl border border-bd-border bg-surface p-8 text-center">
        <p className="text-sm text-muted">
          No hay personal que coincida con estos filtros.
        </p>
      </section>
    );
  }

  return (
    // TARJETAS EN TODAS LAS ANCHURAS, no una tabla que necesite 1400 px. Esta
    // pantalla se usa también desde el móvil del mostrador.
    <ul className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
      {people.map((person) => (
        <li
          key={person.id}
          className="min-w-0 rounded-xl border border-bd-border bg-surface p-4"
        >
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="truncate font-semibold text-foreground">
                {person.full_name}
              </p>
              {/*
                EL CORREO VA SIEMPRE. El nombre no es la identidad: dos personas
                pueden llamarse igual, y esto es lo que las distingue.
              */}
              <p className="truncate text-xs text-muted">{person.email}</p>
            </div>
            <span
              className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${
                person.is_active
                  ? "bg-success-surface text-success"
                  : "bg-surface-2 text-muted"
              }`}
            >
              {/* El punto no es la única señal: el texto lo dice también. */}
              {person.is_active ? "● Activo" : "○ Inactivo"}
            </span>
          </div>

          <dl className="mt-3 space-y-1.5 text-xs">
            <Row label="Área">
              {person.areas.length
                ? person.areas.map((a) => a.name).join(", ")
                : "Sin área"}
            </Row>
            <Row label="Roles">
              {person.roles.length
                ? person.roles.map((r) => r.name).join(", ")
                : "Sin rol asignado"}
            </Row>
            <Row label="Sucursales">{person.branch_scope_label}</Row>
          </dl>

          <button
            type="button"
            onClick={() => onToggle(person)}
            disabled={busy !== null}
            className="mt-3 min-h-11 w-full rounded-lg border border-bd-border px-3 text-sm text-foreground transition hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {busy === `toggle-${person.id}`
              ? "Guardando…"
              : person.is_active
                ? "Desactivar acceso"
                : "Reactivar acceso"}
          </button>
        </li>
      ))}
    </ul>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex min-w-0 gap-2">
      <dt className="shrink-0 text-muted">{label}:</dt>
      <dd className="min-w-0 break-words text-foreground/85">{children}</dd>
    </div>
  );
}

function PendingInvitations({
  invitations, busy, onAction,
}: {
  invitations: StaffInvitation[];
  busy: string | null;
  onAction: (i: StaffInvitation, a: "resend" | "revoke") => void;
}) {
  return (
    <section className="rounded-xl border border-bd-border bg-surface p-4">
      <h2 className="mb-1 text-sm font-semibold text-foreground">
        Invitaciones pendientes
      </h2>
      <p className="mb-3 text-xs text-muted">
        Todavía no tienen acceso: lo tendrán cuando acepten.
      </p>
      <ul className="space-y-2">
        {invitations.map((invitation) => (
          <li
            key={invitation.id}
            className="flex min-w-0 flex-wrap items-center justify-between gap-3 rounded-lg border border-bd-border px-3 py-2.5"
          >
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-foreground">
                {invitation.full_name || invitation.email}
              </p>
              <p className="truncate text-xs text-muted">{invitation.email}</p>
              <p className="mt-0.5 truncate text-xs text-muted">
                {[invitation.role_name, invitation.area_name]
                  .filter(Boolean)
                  .join(" · ") || "Sin rol"}
              </p>
              <p className="mt-0.5 text-[11px] text-muted">
                Enviada {relativeDays(invitation.created_at)} · Vence{" "}
                {relativeDays(invitation.expires_at)} ·{" "}
                {STATUS_LABEL[invitation.status] ?? invitation.status}
              </p>
            </div>
            <div className="flex shrink-0 gap-2">
              <button
                type="button"
                onClick={() => onAction(invitation, "resend")}
                disabled={busy !== null || !invitation.is_usable}
                className="min-h-11 rounded-lg border border-bd-border px-3 text-xs text-foreground transition hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {busy === `resend-${invitation.id}` ? "Enviando…" : "Reenviar"}
              </button>
              <button
                type="button"
                onClick={() => onAction(invitation, "revoke")}
                disabled={busy !== null || !invitation.is_usable}
                className="min-h-11 rounded-lg border border-bd-border px-3 text-xs text-muted transition hover:bg-surface-2 hover:text-danger disabled:cursor-not-allowed disabled:opacity-40"
              >
                {busy === `revoke-${invitation.id}` ? "Revocando…" : "Revocar"}
              </button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function AddWorkerForm({
  companyId, areas, roles, branches, onDone, onError,
}: {
  companyId: number;
  areas: CompanyAreaRow[];
  roles: NamedOption[];
  branches: NamedOption[];
  onDone: () => Promise<void>;
  onError: (m: string | null) => void;
}) {
  const [first, setFirst] = useState("");
  const [last, setLast] = useState("");
  const [email, setEmail] = useState("");
  const [roleId, setRoleId] = useState<string>("");
  const [areaId, setAreaId] = useState<string>("");
  const [scope, setScope] = useState<"all" | "selected">("all");
  const [selected, setSelected] = useState<number[]>([]);
  const [sending, setSending] = useState(false);

  const ready = useMemo(
    () => Boolean(first.trim() && email.trim() && roleId),
    [first, email, roleId],
  );

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!ready || sending) return;
    setSending(true);
    onError(null);
    try {
      await createInvitation(companyId, {
        first_name: first.trim(),
        last_name: last.trim(),
        email: email.trim(),
        role: Number(roleId),
        area: areaId ? Number(areaId) : null,
        branch_access_mode: scope,
        branch_ids: scope === "selected" ? selected : [],
      });
      setFirst(""); setLast(""); setEmail(""); setRoleId(""); setAreaId("");
      setScope("all"); setSelected([]);
      await onDone();
    } catch (err) {
      onError(err instanceof Error ? err.message : "No se pudo enviar la invitación.");
    } finally {
      setSending(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      className="space-y-5 rounded-xl border border-bd-border bg-surface p-5"
    >
      <div>
        <h2 className="text-sm font-semibold text-foreground">Añadir trabajador</h2>
        <p className="mt-1 text-xs text-muted">
          Se le enviará una invitación. No tendrá acceso hasta que la acepte.
        </p>
      </div>

      <fieldset className="space-y-3">
        <legend className="text-[11px] font-semibold uppercase tracking-widest text-muted">
          Datos del trabajador
        </legend>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field id="w-first" label="Nombres" value={first} onChange={setFirst} required />
          <Field id="w-last" label="Apellidos" value={last} onChange={setLast} />
        </div>
        <Field
          id="w-email" label="Correo de acceso" value={email} onChange={setEmail}
          type="email" required
          hint="A esta dirección se envía la invitación."
        />
      </fieldset>

      <fieldset className="space-y-3">
        <legend className="text-[11px] font-semibold uppercase tracking-widest text-muted">
          Acceso a la empresa
        </legend>
        <div className="grid gap-3 sm:grid-cols-2">
          {/*
            ÁREA Y ROL SON COSAS DISTINTAS. El área organiza; el rol autoriza.
            Alguien puede estar en Servicio Técnico con un rol que no le permita
            reparar nada.
          */}
          <Select
            id="w-area" label="Área" value={areaId} onChange={setAreaId}
            options={[
              { value: "", label: "Sin área" },
              ...areas.map((a) => ({ value: String(a.id), label: a.name })),
            ]}
          />
          <Select
            id="w-role" label="Rol" value={roleId} onChange={setRoleId}
            options={[
              { value: "", label: "Elige un rol" },
              ...roles.map((r) => ({ value: String(r.id), label: r.name })),
            ]}
          />
        </div>

        <div>
          <span className="mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-muted">
            Alcance por sucursal
          </span>
          <div className="space-y-2">
            <label className="flex items-center gap-2 text-sm text-foreground">
              <input
                type="radio" name="scope" checked={scope === "all"}
                onChange={() => setScope("all")}
                className="h-4 w-4"
              />
              Todas las sucursales
            </label>
            <label className="flex items-center gap-2 text-sm text-foreground">
              <input
                type="radio" name="scope" checked={scope === "selected"}
                onChange={() => setScope("selected")}
                className="h-4 w-4"
              />
              Sucursales seleccionadas
            </label>
            {scope === "selected" ? (
              <div className="ml-6 space-y-1.5">
                {branches.map((branch) => (
                  <label
                    key={branch.id}
                    className="flex items-center gap-2 text-sm text-foreground"
                  >
                    <input
                      type="checkbox"
                      checked={selected.includes(branch.id)}
                      onChange={(e) =>
                        setSelected((prev) =>
                          e.target.checked
                            ? [...prev, branch.id]
                            : prev.filter((id) => id !== branch.id),
                        )
                      }
                      className="h-4 w-4"
                    />
                    {branch.name}
                  </label>
                ))}
              </div>
            ) : null}
          </div>
        </div>
      </fieldset>

      <button
        type="submit"
        disabled={!ready || sending}
        className="min-h-11 w-full rounded-lg bg-foreground px-4 text-sm font-semibold text-background transition hover:bg-foreground/90 disabled:cursor-not-allowed disabled:opacity-40 sm:w-auto"
      >
        {sending ? "Enviando…" : "Enviar invitación"}
      </button>
    </form>
  );
}

function Field({
  id, label, value, onChange, type = "text", required = false, hint,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (v: string) => void;
  type?: string;
  required?: boolean;
  hint?: string;
}) {
  return (
    <div className="min-w-0">
      <label
        htmlFor={id}
        className="mb-1 block text-[11px] font-semibold uppercase tracking-widest text-muted"
      >
        {label}
      </label>
      <input
        id={id}
        type={type}
        value={value}
        required={required}
        onChange={(e) => onChange(e.target.value)}
        aria-describedby={hint ? `${id}-hint` : undefined}
        className="min-h-11 w-full rounded-lg border border-bd-border bg-background px-3 text-sm text-foreground"
      />
      {hint ? (
        <p id={`${id}-hint`} className="mt-1 text-[11px] text-muted">{hint}</p>
      ) : null}
    </div>
  );
}
