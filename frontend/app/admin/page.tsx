"use client";

/**
 * Internal control dashboard — Phase 2B.1 (visual analytics).
 *
 * WHAT THE CHARTS ARE BUILT ON
 * ----------------------------
 * Only data the backend already computes with an explicit `company=` filter:
 * catalogue composition and organisational structure. Every series arrives from
 * /api/me/internal-dashboard/, gated by the same capability as the counters it
 * sits beside.
 *
 * WHAT IS STILL ABSENT, AND WHY
 * -----------------------------
 * No sales, revenue, average ticket, payment methods, orders by status, stock by
 * branch, best sellers, cash, purchases or repairs. Order and StockMovement have
 * no company column yet, so any of those would be a PLATFORM-WIDE number drawn
 * inside a tenant's dashboard — which reads as that tenant's data. They arrive
 * with Phase 2C/2D, into these same card components.
 *
 * Nothing here decides authorisation. A section renders because the backend
 * returned its data; the backend returned it because the caller's capability
 * allowed it.
 */

import Link from "next/link";
import { useMemo, useState } from "react";
import { AdminShell, buildAccessContext } from "./components/AdminShell";
import { InternalControlGuard, type InternalContext } from "./components/InternalControlGuard";
import {
  DonutChart,
  HorizontalBarChart,
  StackedBar,
  VerticalBarChart,
} from "./components/charts";
import {
  AlertsPanel,
  ChartCard,
  Chip,
  DashboardHeader,
  DashboardSection,
  EmptyState,
  SummaryStatCard,
  formatSoles,
} from "./components/dashboard-ui";
import { ModuleCard } from "./components/internal-ui";
import {
  IconAdministration,
  IconBranch,
  IconCash,
  IconPeople,
  IconProducts,
  IconSales,
  IconShield,
  IconStore,
} from "./components/icons";
import {
  INTERNAL_MODULES,
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

function CompanySelectionPrompt({
  onSelect,
  companies,
}: {
  onSelect: (id: number) => void;
  companies: { id: number; name: string; slug: string }[];
}) {
  return (
    <div className="mx-auto max-w-lg py-10 text-center">
      <span className="inline-flex rounded-xl border border-white/15 bg-white/[0.05] p-3 text-white">
        <IconShield />
      </span>
      <h1 className="mt-4 font-display text-xl font-bold text-white">
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
              className="flex w-full items-center gap-3 rounded-xl border border-white/[0.07] bg-[#111111] p-4 text-left transition hover:border-white/20"
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

  const access = useMemo(() => buildAccessContext(user, dashboard), [user, dashboard]);
  const actions = useMemo(() => quickActions(access), [access]);
  const roadmap = useMemo(() => roadmapByGroup(), []);
  const reachableIds = useMemo(
    () => new Set(navigableModules(access).map((m) => m.id)),
    [access],
  );

  // System coverage: a state-of-the-system figure, not a business KPI.
  const coverage = useMemo(() => {
    // `module` is a reserved global in the Next.js bundler context — hence `entry`.
    const counts = { implemented: 0, partial: 0, pending: 0, proposed: 0 };
    for (const entry of INTERNAL_MODULES) counts[entry.status] += 1;
    return [
      { label: "Disponibles", value: counts.implemented },
      { label: "Parciales", value: counts.partial },
      { label: "Pendientes", value: counts.pending },
      { label: "Propuestas", value: counts.proposed },
    ];
  }, []);

  const iconFor = (groupId: string) =>
    MODULE_GROUPS.find((g) => g.id === groupId)?.icon ?? IconAdministration;

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
  const organization = dashboard?.organization ?? null;
  const catalog = dashboard?.catalog ?? null;
  const sales = dashboard?.sales ?? null;
  const roles = dashboard?.access.roles ?? [];
  const areas = dashboard?.access.areas ?? [];
  const capabilities = dashboard?.access.capabilities ?? [];

  const scope = branch ? `Sucursal: ${branch.name}` : "Alcance: toda la empresa";

  return (
    <div className="space-y-10">
      <DashboardHeader
        greeting={greeting()}
        name={user.first_name || user.username}
        companyName={company?.name ?? null}
        scope={company ? scope : "Sin empresa asignada"}
        isPlatformAdmin={Boolean(dashboard?.access.is_platform_admin)}
      />

      {/* ── Commercial KPIs (Phase 2C) ─────────────────────────────────── */}
      {sales && (
        <DashboardSection
          title="Ventas"
          description="Pedidos pagados de esta empresa. No incluye pendientes ni cancelados."
        >
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
            <SummaryStatCard
              label="Ventas hoy"
              value={formatSoles(sales.today_revenue)}
              hint={`${sales.today_orders} pedido${sales.today_orders === 1 ? "" : "s"}`}
              icon={IconCash}
            />
            <SummaryStatCard
              label="Ticket promedio"
              value={formatSoles(sales.average_ticket)}
              hint="Sobre pedidos pagados"
              icon={IconSales}
            />
            <SummaryStatCard
              label="Pedidos pagados"
              value={sales.total_paid_orders}
              icon={IconSales}
            />
            <SummaryStatCard
              label="Ingresos totales"
              value={formatSoles(sales.total_revenue)}
              icon={IconCash}
            />
            <SummaryStatCard
              label="Pendientes de pago"
              value={sales.pending_payment}
              hint="Checkout iniciado sin pagar"
              icon={IconSales}
            />
            <SummaryStatCard
              label="Por despachar"
              value={sales.awaiting_fulfillment}
              hint="Pagados, aún no enviados"
              icon={IconSales}
            />
          </div>

          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <ChartCard
              title="Ventas últimos 7 días"
              description="Importe pagado por día, fechado por la fecha de pago."
              footnote="No se muestra utilidad: el sistema no tiene un modelo de costos, así que cualquier margen sería una cifra inventada."
            >
              <VerticalBarChart
                series={sales.revenue_trend}
                unit="soles"
                formatValue={formatSoles}
                emptyMessage="Todavía no hay ventas pagadas en el período."
              />
            </ChartCard>

            <ChartCard
              title="Pedidos por estado"
              description="Distribución de todos los pedidos de la empresa."
            >
              <HorizontalBarChart
                series={sales.orders_by_status}
                unit="pedidos"
                emptyMessage="Todavía no hay pedidos."
              />
            </ChartCard>
          </div>
        </DashboardSection>
      )}

      {/* ── KPI row ────────────────────────────────────────────────────── */}
      {(organization || catalog) && (
        <DashboardSection
          title="Resumen"
          description="Estructura y catálogo de esta empresa."
        >
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
            {organization && (
              <>
                <SummaryStatCard
                  label="Sucursales"
                  value={organization.active_branches}
                  icon={IconBranch}
                />
                <SummaryStatCard
                  label="Personal"
                  value={organization.active_memberships}
                  hint="Membresías activas"
                  icon={IconPeople}
                />
                <SummaryStatCard
                  label="Áreas"
                  value={organization.active_areas}
                  icon={IconAdministration}
                />
                <SummaryStatCard
                  label="Roles"
                  value={organization.active_roles}
                  icon={IconShield}
                />
              </>
            )}
            {catalog && (
              <>
                <SummaryStatCard
                  label="Productos"
                  value={catalog.products}
                  hint={`${catalog.active_products} publicados`}
                  icon={IconProducts}
                />
                <SummaryStatCard
                  label="Categorías"
                  value={catalog.categories}
                  icon={IconProducts}
                />
              </>
            )}
          </div>
        </DashboardSection>
      )}

      {/* ── Charts ─────────────────────────────────────────────────────── */}
      {(catalog || organization) && (
        <DashboardSection
          title="Análisis"
          description="Todo calculado exclusivamente sobre los datos de esta empresa."
        >
          <div className="grid gap-4 lg:grid-cols-2">
            {catalog && (
              <ChartCard
                title="Estado del catálogo"
                description="Productos publicados frente a ocultos."
                footnote="Aún no se muestran ventas ni stock: esos modelos todavía no pertenecen a una empresa, y una cifra global aquí se leería como propia."
              >
                <DonutChart
                  series={[
                    { label: "Publicados", value: catalog.active_products },
                    { label: "Ocultos", value: catalog.inactive_products },
                  ]}
                  centerLabel="productos"
                  centerValue={catalog.products}
                  unit="productos"
                  emptyMessage="Todavía no hay productos en el catálogo."
                />
              </ChartCard>
            )}

            {catalog && (
              <ChartCard
                title="Productos por categoría"
                description="Composición del catálogo."
              >
                <HorizontalBarChart
                  series={catalog.products_per_category}
                  unit="productos"
                  emptyMessage="Todavía no hay categorías con productos."
                />
              </ChartCard>
            )}

            {organization && (
              <ChartCard
                title="Personal por área"
                description="Asignaciones activas en cada área."
                footnote="Las áreas son organizativas: no otorgan permisos por sí solas."
              >
                <HorizontalBarChart
                  series={organization.assignments_per_area}
                  unit="personas"
                  emptyMessage="Todavía no hay personal asignado a áreas."
                />
              </ChartCard>
            )}

            {organization && (
              <ChartCard
                title="Personal por rol"
                description="Cuántas personas tiene cada rol interno."
              >
                <HorizontalBarChart
                  series={organization.assignments_per_role}
                  unit="personas"
                  emptyMessage="Todavía no hay roles asignados."
                />
              </ChartCard>
            )}
          </div>
        </DashboardSection>
      )}

      {/* ── My access ──────────────────────────────────────────────────── */}
      {dashboard && (
        <DashboardSection
          title="Mi acceso"
          description="Lo que tu cuenta puede hacer en esta empresa."
        >
          <div className="rounded-xl border border-white/[0.07] bg-[#111111] p-5 sm:p-6">
            <div className="grid gap-6 sm:grid-cols-3">
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
                <p className="font-display text-2xl font-bold tabular-nums text-white">
                  {capabilities.length}
                </p>
                <p className="mt-0.5 text-[11px] text-zinc-600">
                  habilitado{capabilities.length === 1 ? "" : "s"} · origen:{" "}
                  {dashboard.access.is_platform_admin
                    ? "plataforma"
                    : dashboard.access.source === "custom_roles"
                      ? "roles personalizados"
                      : "rol heredado"}
                </p>
              </div>
            </div>
          </div>
        </DashboardSection>
      )}

      {/* ── Alerts ─────────────────────────────────────────────────────── */}
      {dashboard && (
        <DashboardSection
          title="Avisos"
          description="Condiciones reales de tu acceso y tu empresa."
        >
          <AlertsPanel alerts={dashboard.alerts} />
        </DashboardSection>
      )}

      {/* ── Quick actions ──────────────────────────────────────────────── */}
      <DashboardSection
        title="Accesos rápidos"
        description="Solo los módulos que existen y a los que tienes acceso."
      >
        {actions.length === 0 ? (
          <EmptyState message="Tu rol todavía no tiene módulos disponibles." />
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {actions.map((module) => {
              const Icon = iconFor(module.group);
              return (
                <Link
                  key={module.id}
                  href={module.href!}
                  className="group flex items-start gap-3.5 rounded-xl border border-white/[0.07] bg-[#111111] p-5 transition hover:border-white/20"
                >
                  <span className="mt-0.5 rounded-lg border border-white/[0.06] bg-white/[0.03] p-2 text-zinc-400 transition group-hover:text-white">
                    <Icon />
                  </span>
                  <span className="min-w-0">
                    <span className="block text-sm font-semibold text-white">
                      {module.label}
                    </span>
                    <span className="mt-0.5 block text-xs leading-relaxed text-zinc-500">
                      {module.description}
                    </span>
                  </span>
                </Link>
              );
            })}
          </div>
        )}
      </DashboardSection>

      {/* ── System coverage + roadmap ──────────────────────────────────── */}
      <DashboardSection
        title="Estado del sistema"
        description="Qué módulos del control interno existen hoy."
        action={
          <button
            type="button"
            onClick={() => setShowRoadmap((v) => !v)}
            aria-expanded={showRoadmap}
            className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-zinc-400 transition hover:border-white/20 hover:text-white"
          >
            {showRoadmap ? "Ocultar mapa" : "Ver mapa completo"}
          </button>
        }
      >
        <ChartCard
          title="Cobertura de módulos"
          description="Estado de implementación del control interno, no un indicador de negocio."
        >
          <StackedBar series={coverage} unit="módulos" />
        </ChartCard>

        {showRoadmap && (
          <div className="mt-4 space-y-6">
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
                        href={reachableIds.has(module.id) ? module.href : undefined}
                      />
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </DashboardSection>
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
