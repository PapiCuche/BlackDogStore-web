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
    <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-5">
      <div className="flex items-start justify-between gap-3">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">
          {label}
        </p>
        {Icon ? <Icon className="h-4 w-4 shrink-0 text-zinc-600" /> : null}
      </div>
      <p className="mt-2 text-2xl font-semibold tabular-nums text-white">{value}</p>
      {hint ? <p className="mt-1 text-xs text-zinc-500">{hint}</p> : null}
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
      className="group flex items-start gap-3 rounded-xl border border-white/[0.06] bg-white/[0.02] p-5 transition hover:border-white/15 hover:bg-white/[0.04]"
    >
      <span className="mt-0.5 rounded-lg border border-white/[0.08] bg-black/40 p-2 text-zinc-400 transition group-hover:text-white">
        <Icon />
      </span>
      <span className="min-w-0">
        <span className="block text-sm font-semibold text-white">{label}</span>
        <span className="mt-0.5 block text-xs leading-relaxed text-zinc-500">
          {description}
        </span>
      </span>
    </Link>
  );
}

export function StatusBadge({ status }: { status: ModuleStatus }) {
  const tone =
    status === "implemented"
      ? "border-white/25 bg-white/[0.08] text-white"
      : status === "partial"
        ? "border-white/15 bg-white/[0.04] text-zinc-300"
        : "border-white/[0.08] bg-transparent text-zinc-500";
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
        <p className="text-sm font-medium text-zinc-200">{label}</p>
        <StatusBadge status={status} />
      </div>
      <p className="mt-1 text-xs leading-relaxed text-zinc-500">{description}</p>
    </>
  );

  // No href → no link. A module that does not exist never becomes a dead click.
  if (!href) {
    return (
      <div className="rounded-lg border border-white/[0.05] bg-white/[0.01] p-4 opacity-70">
        {body}
      </div>
    );
  }

  return (
    <Link
      href={href}
      className="block rounded-lg border border-white/[0.06] bg-white/[0.02] p-4 transition hover:border-white/15 hover:bg-white/[0.04]"
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
          <h2 className="text-sm font-semibold text-white">{title}</h2>
          {description ? (
            <p className="mt-0.5 text-xs text-zinc-500">{description}</p>
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
    <span className="inline-block rounded-lg border border-white/[0.08] bg-white/[0.03] px-2.5 py-1 text-xs text-zinc-300">
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
      <div className="rounded-xl border border-dashed border-white/10 px-4 py-6 text-center">
        <p className="text-sm text-zinc-500">Sin avisos pendientes.</p>
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
              ? "border-white/20 bg-white/[0.05]"
              : "border-white/[0.06] bg-white/[0.02]";
        return (
          <div
            key={alert.code + alert.title}
            className={`flex gap-3 rounded-lg border p-4 ${tone}`}
          >
            <IconAlert className="mt-0.5 h-4 w-4 shrink-0 text-zinc-400" />
            <div className="min-w-0">
              <p className="text-sm font-medium text-zinc-200">{alert.title}</p>
              <p className="mt-0.5 text-xs leading-relaxed text-zinc-500">
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
      <div className="h-16 w-full max-w-md animate-pulse rounded-xl bg-white/[0.03]" />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="h-28 animate-pulse rounded-xl bg-white/[0.03]" />
        ))}
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <div key={i} className="h-24 animate-pulse rounded-xl bg-white/[0.03]" />
        ))}
      </div>
    </div>
  );
}

export function EmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-xl border border-dashed border-white/10 px-4 py-8 text-center">
      <p className="text-sm text-zinc-500">{message}</p>
    </div>
  );
}
