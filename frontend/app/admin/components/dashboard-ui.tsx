"use client";

/**
 * Dashboard presentation layer — Phase 2B.1.
 *
 * Visual refinements only. Nothing here decides authorisation: a card renders
 * because the backend already returned its data, and the backend returned it
 * because the caller's capability allowed it.
 *
 * Palette: strictly the brand tokens from globals.css — #080808 background,
 * #111111 surfaces, white at low opacity for elevation, zinc for text. No new
 * hues. Emphasis comes from contrast and spacing, not colour.
 */

import type { IconComponent } from "./icons";
import { IconAlert } from "./icons";

// ---------------------------------------------------------------------------
// Layout
// ---------------------------------------------------------------------------

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
    <header className="relative overflow-hidden rounded-2xl border border-bd-border bg-surface px-6 py-7 sm:px-8">
      {/* Brand texture, already part of the visual language (globals.css) */}
      <div
        aria-hidden="true"
        className="dot-grid pointer-events-none absolute -right-6 -top-6 h-32 w-32 opacity-[0.35]"
      />
      <div className="relative">
        <p className="text-[11px] font-semibold uppercase tracking-[0.25em] text-muted">
          Control interno
        </p>
        <h1 className="mt-1.5 font-display text-2xl font-bold text-foreground sm:text-3xl">
          {greeting}, {name}
        </h1>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {companyName ? (
            <span className="rounded-lg border border-bd-border bg-surface px-2.5 py-1 text-xs text-foreground/85">
              {companyName}
            </span>
          ) : null}
          <span className="rounded-lg border border-bd-border px-2.5 py-1 text-xs text-muted">
            {scope}
          </span>
          {isPlatformAdmin ? (
            <span className="rounded-lg border border-bd-border bg-surface-2 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-widest text-foreground">
              Master
            </span>
          ) : null}
        </div>
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
    <section className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="font-display text-base font-semibold tracking-wide text-foreground">
            {title}
          </h2>
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

// ---------------------------------------------------------------------------
// Cards
// ---------------------------------------------------------------------------

export function SummaryStatCard({
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
    <div className="group rounded-xl border border-bd-border bg-surface p-5 transition hover:border-bd-border">
      <div className="flex items-start justify-between gap-3">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-muted">
          {label}
        </p>
        {Icon ? (
          <span className="rounded-lg border border-bd-border bg-surface p-1.5 text-muted transition group-hover:text-foreground/85">
            <Icon className="h-4 w-4" />
          </span>
        ) : null}
      </div>
      <p className="mt-3 font-display text-3xl font-bold tabular-nums leading-none text-foreground">
        {value}
      </p>
      {hint ? <p className="mt-2 text-xs text-muted">{hint}</p> : null}
    </div>
  );
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
        {description ? (
          <p className="mt-0.5 text-xs text-muted">{description}</p>
        ) : null}
      </div>
      <div className="flex-1">{children}</div>
      {footnote ? (
        <p className="mt-5 border-t border-bd-border pt-3 text-[11px] leading-relaxed text-muted">
          {footnote}
        </p>
      ) : null}
    </div>
  );
}

export function AlertsPanel({
  alerts,
}: {
  alerts: { level: string; code: string; title: string; detail: string }[];
}) {
  if (alerts.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-bd-border px-4 py-7 text-center">
        <p className="text-sm text-muted">Sin avisos pendientes.</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {alerts.map((alert) => {
        const tone =
          alert.level === "critical"
            ? "border-danger-border bg-red-500/[0.07]"
            : alert.level === "warning"
              ? "border-warning-border bg-amber-400/[0.05]"
              : "border-bd-border bg-surface";
        return (
          <div
            key={alert.code + alert.title}
            className={`flex gap-3 rounded-xl border p-4 ${tone}`}
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

export function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-block rounded-lg border border-bd-border bg-surface px-2.5 py-1 text-xs text-foreground/85">
      {children}
    </span>
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

export function EmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-xl border border-dashed border-bd-border px-4 py-8 text-center">
      <p className="text-sm text-muted">{message}</p>
    </div>
  );
}

export function DashboardSkeleton() {
  return (
    <div className="space-y-8" aria-busy="true" aria-label="Cargando dashboard">
      <div className="h-32 animate-pulse rounded-2xl bg-surface" />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <div key={i} className="h-28 animate-pulse rounded-xl bg-surface" />
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        {[0, 1].map((i) => (
          <div key={i} className="h-72 animate-pulse rounded-xl bg-surface" />
        ))}
      </div>
    </div>
  );
}
