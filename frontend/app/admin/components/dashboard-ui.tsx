"use client";

/**
 * Dashboard presentation layer.
 *
 * The canonical primitives live in internal-ui.tsx. This file keeps the public
 * API used by existing dashboard pages so the visual consolidation does not
 * force a risky functional rewrite.
 */

import type { IconComponent } from "./icons";
import { Section, StatCard } from "./internal-ui";

export { AlertsPanel, Chip, DashboardSkeleton, EmptyState } from "./internal-ui";

export function DashboardHeader({
  greeting,
  name,
  companyName,
  scope,
  isPlatformAdmin,
}: {
  greeting: string;
  name: string;
  companyName: string | null;
  scope: string;
  isPlatformAdmin: boolean;
}) {
  return (
    <header className="border-b border-bd-border pb-7">
      <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
        Control interno
      </p>
      <div className="mt-2 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          <h1 className="font-display text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
            {greeting}, {name}
          </h1>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {companyName ? (
              <span className="rounded-lg border border-bd-border bg-surface px-2.5 py-1 text-xs text-foreground">
                {companyName}
              </span>
            ) : null}
            <span className="rounded-lg border border-bd-border bg-background px-2.5 py-1 text-xs text-muted-foreground">
              {scope}
            </span>
            {isPlatformAdmin ? (
              <span className="rounded-lg border border-foreground/20 bg-foreground/[0.06] px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.12em] text-foreground">
                Master
              </span>
            ) : null}
          </div>
        </div>
        <span className="hidden h-px w-20 bg-accent lg:block" aria-hidden="true" />
      </div>
    </header>
  );
}

export function DashboardSection({
  title,
  description,
  action,
  children,
}: {
  title: string;
  description?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <Section title={title} description={description} action={action}>
      {children}
    </Section>
  );
}

export function SummaryStatCard({
  label,
  value,
  hint,
  icon,
}: {
  label: string;
  value: string | number;
  hint?: string;
  icon?: IconComponent;
}) {
  return <StatCard label={label} value={value} hint={hint} icon={icon} />;
}

export function ChartCard({
  title,
  description,
  footnote,
  children,
}: {
  title: string;
  description?: string;
  footnote?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col rounded-xl border border-bd-border bg-surface p-5 sm:p-6">
      <div className="mb-5">
        <h3 className="text-sm font-semibold text-foreground">{title}</h3>
        {description ? <p className="mt-1 text-xs leading-5 text-muted-foreground">{description}</p> : null}
      </div>
      <div className="flex-1">{children}</div>
      {footnote ? (
        <p className="mt-5 border-t border-bd-border pt-3 text-[11px] leading-relaxed text-muted-foreground">
          {footnote}
        </p>
      ) : null}
    </div>
  );
}

/** Peruvian soles, the currency the store already prices in. */
export function formatSoles(value: string | number): string {
  const n = typeof value === "string" ? parseFloat(value) : value;
  if (!Number.isFinite(n)) return "S/ 0.00";
  return `S/ ${n.toLocaleString("es-PE", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}
