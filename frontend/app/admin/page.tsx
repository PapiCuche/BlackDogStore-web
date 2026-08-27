"use client";

/**
 * Internal control dashboard — Phase 2A.2.
 *
 * Shows the operator WHERE they are (company, branch scope), WHAT they can do
 * (roles, areas, capabilities) and WHERE they can go (quick actions, modules).
 *
 * WHAT IT DELIBERATELY DOES NOT SHOW
 * ----------------------------------
 * No sales, revenue, order counts, stock levels or best-selling products.
 * Product, Order and StockMovement have no `company` column yet, so any such
 * figure would be platform-wide, displayed inside a per-company frame. A global
 * number in a tenant dashboard is not a small inaccuracy — it reads as this
 * company's data. Those KPIs arrive with Phase 2B/2C, into the same MetricCard.
 */

import { useMemo, useState } from "react";
import { AdminShell, buildAccessContext } from "./components/AdminShell";
import { InternalControlGuard, type InternalContext } from "./components/InternalControlGuard";
import {
  AlertsPanel,
  Chip,
  EmptyState,
  MetricCard,
  ModuleCard,
  QuickActionCard,
  Section,
} from "./components/internal-ui";
import {
  IconAdministration,
  IconBranch,
  IconPeople,
  IconShield,
  IconStore,
} from "./components/icons";
import {
  MODULE_GROUPS,
  navigableModules,
  quickActions,
  roadmapByGroup,
} from "./lib/internal-modules";

function greeting(): string {
  const hour = new Date().getHours();
  if (hour < 12) return "Buenos días";
  if (hour < 20) return "Buenas tardes";
  return "Buenas noches";
}

function CompanySelectionPrompt({ onSelect, companies }: {
  onSelect: (id: number) => void;
  companies: { id: number; name: string; slug: string }[];
}) {
  return (
    <div className="mx-auto max-w-lg py-10 text-center">
      <span className="inline-flex rounded-xl border border-white/15 bg-white/[0.05] p-3 text-white">
        <IconShield />
      </span>
      <h1 className="mt-4 text-xl font-semibold text-white">
        Selecciona una empresa
      </h1>
      <p className="mt-2 text-sm text-zinc-500">
        Selecciona una empresa para abrir su control interno. Las herramientas
        globales de plataforma son una superficie distinta y están pendientes.
      </p>

      <div className="mt-6 space-y-2 text-left">
        {companies.length === 0 ? (
          <EmptyState message="No hay empresas disponibles." />
        ) : (
          companies.map((company) => (
            <button
              key={company.id}
              type="button"
              onClick={() => onSelect(company.id)}
              className="flex w-full items-center gap-3 rounded-xl border border-white/[0.06] bg-white/[0.02] p-4 text-left transition hover:border-white/20 hover:bg-white/[0.04]"
            >
              <IconStore className="h-4 w-4 shrink-0 text-zinc-500" />
              <span className="min-w-0">
                <span className="block truncate text-sm font-medium text-zinc-200">
                  {company.name}
                </span>
                <span className="block truncate font-mono text-[11px] text-zinc-600">
                  {company.slug}
                </span>
              </span>
            </button>
          ))
        )}
      </div>
    </div>
  );
}

function DashboardContent({ ctx }: { ctx: InternalContext }) {
  const { user, dashboard, selectCompany } = ctx;
  const [showRoadmap, setShowRoadmap] = useState(false);

  const access = useMemo(
    () => buildAccessContext(user, dashboard),
    [user, dashboard],
  );
  const actions = useMemo(() => quickActions(access), [access]);
  const roadmap = useMemo(() => roadmapByGroup(), []);
  const reachableIds = useMemo(
    () => new Set(navigableModules(access).map((m) => m.id)),
    [access],
  );

  const iconFor = (groupId: string) =>
    MODULE_GROUPS.find((g) => g.id === groupId)?.icon ?? IconAdministration;

  // Platform master with no tenant chosen: never guess one for them.
  if (dashboard?.requires_company_selection) {
    return (
      <CompanySelectionPrompt
        companies={dashboard.available_companies}
        onSelect={selectCompany}
      />
    );
  }

  const company = dashboard?.company ?? null;
  const branch = dashboard?.membership?.branch ?? null;
  const roles = dashboard?.access.roles ?? [];
  const areas = dashboard?.access.areas ?? [];
  const capabilities = dashboard?.access.capabilities ?? [];

  return (
    <div className="space-y-10">
      {/* ── Header ─────────────────────────────────────────────────────── */}
      <div>
        <h1 className="text-xl font-semibold text-white">
          {greeting()}, {user.first_name || user.username}
        </h1>
        <p className="mt-1 text-sm text-zinc-500">
          {company ? (
            <>
              {company.name}
              {branch ? ` · ${branch.name}` : " · alcance: empresa"}
            </>
          ) : (
            "Panel administrativo — tu cuenta todavía no pertenece a una empresa."
          )}
        </p>
      </div>

      {/* ── Organisation ───────────────────────────────────────────────── */}
      {dashboard?.organization && (
        <Section
          title="Organización"
          description="Estructura actual de la empresa."
        >
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <MetricCard
              label="Sucursales"
              value={dashboard.organization.active_branches}
              icon={IconBranch}
            />
            <MetricCard
              label="Personal activo"
              value={dashboard.organization.active_memberships}
              icon={IconPeople}
            />
            <MetricCard
              label="Áreas"
              value={dashboard.organization.active_areas}
              icon={IconAdministration}
            />
            <MetricCard
              label="Roles"
              value={dashboard.organization.active_roles}
              icon={IconShield}
            />
          </div>
        </Section>
      )}

      {/* ── My access ──────────────────────────────────────────────────── */}
      {dashboard && (
        <Section
          title="Mi acceso"
          description="Lo que tu cuenta puede hacer en esta empresa."
        >
          <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-5">
            <div className="grid gap-5 sm:grid-cols-3">
              <div>
                <p className="mb-2 text-[11px] font-semibold uppercase tracking-widest text-zinc-500">
                  Roles
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {roles.length > 0 ? (
                    roles.map((role) => (
                      <Chip key={role.id}>
                        {role.name}
                        {role.area ? ` · ${role.area}` : ""}
                      </Chip>
                    ))
                  ) : (
                    <span className="text-xs text-zinc-600">
                      Rol heredado: {dashboard.access.legacy_role ?? "—"}
                    </span>
                  )}
                </div>
              </div>

              <div>
                <p className="mb-2 text-[11px] font-semibold uppercase tracking-widest text-zinc-500">
                  Áreas
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {areas.length > 0 ? (
                    areas.map((area) => <Chip key={area.id}>{area.name}</Chip>)
                  ) : (
                    <span className="text-xs text-zinc-600">Sin área asignada</span>
                  )}
                </div>
              </div>

              <div>
                <p className="mb-2 text-[11px] font-semibold uppercase tracking-widest text-zinc-500">
                  Permisos
                </p>
                <p className="text-sm text-zinc-300">
                  {capabilities.length} habilitado
                  {capabilities.length === 1 ? "" : "s"}
                </p>
                <p className="mt-0.5 text-[11px] text-zinc-600">
                  Origen:{" "}
                  {dashboard.access.source === "custom_roles"
                    ? "roles personalizados"
                    : "rol heredado"}
                </p>
              </div>
            </div>

            <p className="mt-4 border-t border-white/[0.06] pt-3 text-[11px] leading-relaxed text-zinc-600">
              Las áreas son organizativas: no otorgan permisos por sí solas. La
              autoridad viene de las capacidades del rol.
            </p>
          </div>
        </Section>
      )}

      {/* ── Alerts ─────────────────────────────────────────────────────── */}
      {dashboard && (
        <Section
          title="Avisos"
          description="Condiciones reales de tu acceso y tu empresa."
        >
          <AlertsPanel alerts={dashboard.alerts} />
        </Section>
      )}

      {/* ── Quick actions ──────────────────────────────────────────────── */}
      <Section
        title="Accesos rápidos"
        description="Solo los módulos que existen y a los que tienes acceso."
      >
        {actions.length === 0 ? (
          <EmptyState message="Tu rol todavía no tiene módulos disponibles." />
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {actions.map((module) => (
              <QuickActionCard
                key={module.id}
                href={module.href!}
                label={module.label}
                description={module.description}
                icon={iconFor(module.group)}
              />
            ))}
          </div>
        )}
      </Section>

      {/* ── Roadmap ────────────────────────────────────────────────────── */}
      <Section
        title="Módulos de la empresa"
        description="Estado real de cada módulo del control interno."
        action={
          <button
            type="button"
            onClick={() => setShowRoadmap((v) => !v)}
            aria-expanded={showRoadmap}
            className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-zinc-400 transition hover:border-white/20 hover:text-white"
          >
            {showRoadmap ? "Ocultar" : "Ver mapa completo"}
          </button>
        }
      >
        {showRoadmap ? (
          <div className="space-y-6">
            {roadmap.map(({ group, modules }) => {
              const GroupIcon = group.icon;
              return (
                <div key={group.id}>
                  <p className="mb-2 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-widest text-zinc-500">
                    <GroupIcon className="h-3.5 w-3.5" />
                    {group.label}
                  </p>
                  <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                    {modules.map((module) => (
                      <ModuleCard
                        key={module.id}
                        label={module.label}
                        description={module.description}
                        status={module.status}
                        // Only modules the caller can actually reach get a link;
                        // everything else renders as inert metadata.
                        href={
                          reachableIds.has(module.id) ? module.href : undefined
                        }
                      />
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <EmptyState message="El mapa muestra qué módulos existen, cuáles son parciales y cuáles están pendientes." />
        )}
      </Section>
    </div>
  );
}

export default function AdminDashboardPage() {
  return (
    <InternalControlGuard>
      {(ctx) => (
        <AdminShell
          user={ctx.user}
          dashboard={ctx.dashboard}
          onSelectCompany={ctx.selectCompany}
        >
          <DashboardContent ctx={ctx} />
        </AdminShell>
      )}
    </InternalControlGuard>
  );
}
