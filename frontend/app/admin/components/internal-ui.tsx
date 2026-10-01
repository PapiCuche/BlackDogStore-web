"use client";

/**
 * Shared presentational pieces for the internal control surface — Phase 2A.2.
 *
 * MetricCard is deliberately generic so later phases can drop tenantised KPIs
 * (sales today, average ticket, open repairs…) into the same visual language.
 * Today it is used ONLY for organisational counters, which are safely
 * company-scoped. It is not wired to any global commercial figure.
 */

import Link from "next/link";
import type { IconComponent } from "./icons";
import { IconAlert } from "./icons";
import { STATUS_LABELS, type ModuleStatus } from "../lib/internal-modules";


export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: React.ReactNode;
}) {
  return (
    <header className="flex flex-col gap-4 border-b border-bd-border pb-6 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        {eyebrow ? (
          <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-muted">{eyebrow}</p>
        ) : null}
        <h1 className="mt-1 font-display text-2xl font-extrabold tracking-[-0.025em] text-foreground sm:text-3xl">
          {title}
        </h1>
        {description ? (
          <p className="mt-2 max-w-3xl text-sm leading-6 text-muted">{description}</p>
        ) : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  );
}

export function FilterBar({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-2xl border border-bd-border bg-surface p-4 sm:p-5">
      {children}
    </div>
  );
}

export function TableShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="overflow-x-auto rounded-2xl border border-bd-border bg-surface">
      {children}
    </div>
  );
}

export const internalInputClass =
  "w-full rounded-xl border border-bd-border bg-background px-3 py-2.5 text-sm text-foreground placeholder:text-muted/60 outline-none transition focus:border-foreground/25";

export const internalButtonClass =
  "rounded-xl border border-bd-border px-3.5 py-2.5 text-sm font-semibold text-foreground transition hover:border-foreground/25 disabled:cursor-not-allowed disabled:opacity-40";

export const internalPrimaryButtonClass =
  "rounded-xl bg-primary px-4 py-2.5 text-sm font-bold text-background transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40";

// ---------------------------------------------------------------------------
// Metrics
// ---------------------------------------------------------------------------

export function MetricCard({
  label,
  value,
  hint,
  icon: Icon,
}: {
  label: string;
  value: string | number;
  hint?: string;
  icon?: IconComponent;
}) {
  return (
    <div className="rounded-xl border border-bd-border bg-surface p-5">
      <div className="flex items-start justify-between gap-3">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-muted">
          {label}
        </p>
        {Icon ? <Icon className="h-4 w-4 shrink-0 text-muted" /> : null}
      </div>
      <p className="mt-2 text-2xl font-semibold tabular-nums text-foreground">{value}</p>
      {hint ? <p className="mt-1 text-xs text-muted">{hint}</p> : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Cards
// ---------------------------------------------------------------------------

export function QuickActionCard({
  href,
  label,
  description,
  icon: Icon,
}: {
  href: string;
  label: string;
  description: string;
  icon: IconComponent;
}) {
  return (
    <Link
      href={href}
      className="group flex items-start gap-3 rounded-xl border border-bd-border bg-surface p-5 transition hover:border-foreground/20 hover:bg-foreground/[0.04]"
    >
      <span className="mt-0.5 rounded-lg border border-bd-border bg-background p-2 text-muted transition group-hover:text-foreground">
        <Icon />
      </span>
      <span className="min-w-0">
        <span className="block text-sm font-semibold text-foreground">{label}</span>
        <span className="mt-0.5 block text-xs leading-relaxed text-muted">
          {description}
        </span>
      </span>
    </Link>
  );
}

export function StatusBadge({ status }: { status: ModuleStatus }) {
  const tone =
    status === "implemented"
      ? "border-foreground/25 bg-foreground/[0.08] text-foreground"
      : status === "partial"
        ? "border-foreground/20 bg-foreground/[0.04] text-foreground"
        : "border-white/[0.08] bg-transparent text-muted";
  return (
    <span
      className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider ${tone}`}
    >
      {STATUS_LABELS[status]}
    </span>
  );
}

export function ModuleCard({
  label,
  description,
  status,
  href,
}: {
  label: string;
  description: string;
  status: ModuleStatus;
  href?: string;
}) {
  const body = (
    <>
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm font-medium text-foreground">{label}</p>
        <StatusBadge status={status} />
      </div>
      <p className="mt-1 text-xs leading-relaxed text-muted">{description}</p>
    </>
  );

  // No href → no link. A module that does not exist never becomes a dead click.
  if (!href) {
    return (
      <div className="rounded-lg border border-bd-border bg-background p-4 opacity-70">
        {body}
      </div>
    );
  }

  return (
    <Link
      href={href}
      className="block rounded-lg border border-bd-border bg-surface p-4 transition hover:border-foreground/20 hover:bg-foreground/[0.04]"
    >
      {body}
    </Link>
  );
}

// ---------------------------------------------------------------------------
// Sections and states
// ---------------------------------------------------------------------------

export function Section({
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
    <section className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-foreground">{title}</h2>
          {description ? (
            <p className="mt-0.5 text-xs text-muted">{description}</p>
          ) : null}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

export function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-block rounded-lg border border-bd-border bg-foreground/[0.03] px-2.5 py-1 text-xs text-foreground">
      {children}
    </span>
  );
}

export function AlertsPanel({
  alerts,
}: {
  alerts: { level: string; code: string; title: string; detail: string }[];
}) {
  if (alerts.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-bd-border px-4 py-6 text-center">
        <p className="text-sm text-muted">Sin avisos pendientes.</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {alerts.map((alert) => {
        const tone =
          alert.level === "critical"
            ? "border-red-500/25 bg-red-500/[0.07]"
            : alert.level === "warning"
              ? "border-white/20 bg-foreground/[0.05]"
              : "border-bd-border bg-surface";
        return (
          <div
            key={alert.code + alert.title}
            className={`flex gap-3 rounded-lg border p-4 ${tone}`}
          >
            <IconAlert className="mt-0.5 h-4 w-4 shrink-0 text-muted" />
            <div className="min-w-0">
              <p className="text-sm font-medium text-foreground">{alert.title}</p>
              <p className="mt-0.5 text-xs leading-relaxed text-muted">
                {alert.detail}
              </p>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function DashboardSkeleton() {
  return (
    <div className="space-y-8" aria-busy="true" aria-label="Cargando dashboard">
      <div className="h-16 w-full max-w-md animate-pulse rounded-xl bg-foreground/[0.03]" />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="h-28 animate-pulse rounded-xl bg-foreground/[0.03]" />
        ))}
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <div key={i} className="h-24 animate-pulse rounded-xl bg-foreground/[0.03]" />
        ))}
      </div>
    </div>
  );
}

export function EmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-xl border border-dashed border-bd-border px-4 py-8 text-center">
      <p className="text-sm text-muted">{message}</p>
    </div>
  );
}
