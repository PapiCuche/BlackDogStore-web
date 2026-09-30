"use client";

import Link from "next/link";
import type { IconComponent } from "./icons";
import { IconAlert } from "./icons";
import { STATUS_LABELS, type ModuleStatus } from "../lib/internal-modules";

type ButtonTone = "default" | "primary" | "danger";

export function Button({
  children,
  onClick,
  disabled,
  tone = "default",
  type = "button",
  className = "",
}: {
  children: React.ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  tone?: ButtonTone;
  type?: "button" | "submit";
  className?: string;
}) {
  const tones: Record<ButtonTone, string> = {
    default:
      "border-bd-border bg-surface text-muted-foreground hover:border-foreground/25 hover:bg-surface-elevated hover:text-foreground",
    primary:
      "border-primary bg-primary text-background hover:opacity-90",
    danger:
      "border-red-500/30 bg-red-500/[0.07] text-red-300 hover:border-red-400/50 hover:bg-red-500/12",
  };

  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex min-h-9 items-center justify-center rounded-lg border px-3 py-2 text-xs font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-40 ${tones[tone]} ${className}`}
    >
      {children}
    </button>
  );
}

export function Field({
  label,
  value,
  onChange,
  placeholder,
  textarea,
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  textarea?: boolean;
  type?: string;
}) {
  const control =
    "mt-1.5 w-full rounded-lg border border-bd-border bg-background px-3 py-2.5 text-sm text-foreground placeholder:text-muted-foreground focus:border-foreground/30 focus:outline-none focus:ring-2 focus:ring-accent/20";

  return (
    <label className="block text-xs font-medium text-muted-foreground">
      {label}
      {textarea ? (
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          rows={3}
          className={control}
        />
      ) : (
        <input
          type={type}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          className={control}
        />
      )}
    </label>
  );
}

export function Panel({
  title,
  description,
  action,
  children,
  padded = true,
}: {
  title?: string;
  description?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
  padded?: boolean;
}) {
  return (
    <section className="overflow-hidden rounded-xl border border-bd-border bg-surface">
      {title || description || action ? (
        <header className="flex flex-wrap items-start justify-between gap-3 border-b border-bd-border px-5 py-4">
          <div className="min-w-0">
            {title ? <h2 className="text-sm font-semibold text-foreground">{title}</h2> : null}
            {description ? <p className="mt-1 text-xs leading-5 text-muted-foreground">{description}</p> : null}
          </div>
          {action}
        </header>
      ) : null}
      <div className={padded ? "p-5" : ""}>{children}</div>
    </section>
  );
}

export function StatCard({
  label,
  value,
  hint,
  icon: Icon,
  emphasis = false,
}: {
  label: string;
  value: string | number;
  hint?: string;
  icon?: IconComponent;
  emphasis?: boolean;
}) {
  return (
    <div className={`group rounded-xl border p-5 transition ${
      emphasis
        ? "border-foreground/20 bg-surface-elevated"
        : "border-bd-border bg-surface hover:border-foreground/15"
    }`}>
      <div className="flex items-start justify-between gap-3">
        <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
          {label}
        </p>
        {Icon ? (
          <span className="rounded-lg border border-bd-border bg-background p-1.5 text-muted-foreground transition group-hover:text-foreground">
            <Icon className="h-4 w-4" />
          </span>
        ) : null}
      </div>
      <p className="mt-3 text-2xl font-bold tabular-nums leading-none text-foreground">{value}</p>
      {hint ? <p className="mt-2 text-xs leading-5 text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

/** Backwards-compatible name used by the original dashboard registry. */
export const MetricCard = StatCard;

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
      className="group flex items-start gap-3 rounded-xl border border-bd-border bg-surface p-5 transition hover:border-foreground/15 hover:bg-surface-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
    >
      <span className="mt-0.5 rounded-lg border border-bd-border bg-background p-2 text-muted-foreground transition group-hover:text-foreground">
        <Icon />
      </span>
      <span className="min-w-0">
        <span className="block text-sm font-semibold text-foreground">{label}</span>
        <span className="mt-1 block text-xs leading-5 text-muted-foreground">{description}</span>
      </span>
    </Link>
  );
}

export function StatusBadge({ status }: { status: ModuleStatus }) {
  const tone =
    status === "implemented"
      ? "border-foreground/20 bg-foreground/[0.06] text-foreground"
      : status === "partial"
        ? "border-bd-border bg-background text-muted-foreground"
        : "border-bd-border bg-transparent text-muted-foreground";

  return (
    <span className={`shrink-0 rounded-md border px-2 py-1 text-[9px] font-semibold uppercase tracking-[0.12em] ${tone}`}>
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
      <p className="mt-1 text-xs leading-5 text-muted-foreground">{description}</p>
    </>
  );

  if (!href) {
    return (
      <div className="rounded-lg border border-bd-border bg-background/30 p-4 opacity-65">
        {body}
      </div>
    );
  }

  return (
    <Link
      href={href}
      className="block rounded-lg border border-bd-border bg-surface p-4 transition hover:border-foreground/15 hover:bg-surface-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
    >
      {body}
    </Link>
  );
}

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
          <h2 className="text-base font-semibold text-foreground">{title}</h2>
          {description ? <p className="mt-1 text-xs leading-5 text-muted-foreground">{description}</p> : null}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

export function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-block rounded-lg border border-bd-border bg-background px-2.5 py-1 text-xs text-muted-foreground">
      {children}
    </span>
  );
}

export function Pill({
  label,
  tone = "neutral",
}: {
  label: string;
  tone?: "neutral" | "good" | "warn" | "bad";
}) {
  const tones = {
    neutral: "border-bd-border bg-background text-muted-foreground",
    good: "border-emerald-400/30 bg-emerald-400/[0.07] text-emerald-300",
    warn: "border-amber-400/30 bg-amber-400/[0.07] text-amber-300",
    bad: "border-red-500/30 bg-red-500/[0.07] text-red-300",
  } as const;
  return (
    <span className={`inline-flex rounded-full border px-2.5 py-1 text-[10px] font-medium ${tones[tone]}`}>
      {label}
    </span>
  );
}

export function AlertsPanel({
  alerts,
}: {
  alerts: { level: string; code: string; title: string; detail: string }[];
}) {
  if (alerts.length === 0) {
    return <EmptyState message="Sin avisos pendientes." />;
  }

  return (
    <div className="space-y-2">
      {alerts.map((alert) => {
        const tone =
          alert.level === "critical"
            ? "border-red-500/25 bg-red-500/[0.07]"
            : alert.level === "warning"
              ? "border-amber-400/25 bg-amber-400/[0.06]"
              : "border-bd-border bg-surface";
        return (
          <div key={alert.code + alert.title} className={`flex gap-3 rounded-xl border p-4 ${tone}`}>
            <IconAlert className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
            <div className="min-w-0">
              <p className="text-sm font-medium text-foreground">{alert.title}</p>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">{alert.detail}</p>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function Spinner({ label = "Cargando…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-3 py-8 text-sm text-muted-foreground" aria-live="polite">
      <div className="h-4 w-4 animate-spin rounded-full border-2 border-muted-foreground border-t-transparent" />
      {label}
    </div>
  );
}

export function ErrorBox({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-red-500/25 bg-red-500/[0.07] px-4 py-3 text-sm text-red-300">
      {message}
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  return <ErrorBox message={error instanceof Error ? error.message : String(error)} />;
}

export function EmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-xl border border-dashed border-bd-border px-4 py-8 text-center">
      <p className="text-sm text-muted-foreground">{message}</p>
    </div>
  );
}

export function DashboardSkeleton() {
  return (
    <div className="space-y-8" aria-busy="true" aria-label="Cargando dashboard">
      <div className="h-28 animate-pulse rounded-xl bg-surface" />
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

export function Th({ children, right = false }: { children: React.ReactNode; right?: boolean }) {
  return (
    <th className={`whitespace-nowrap px-3 py-2.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground ${right ? "text-right" : "text-left"}`}>
      {children}
    </th>
  );
}

export function Td({
  children,
  right = false,
  muted = false,
}: {
  children: React.ReactNode;
  right?: boolean;
  muted?: boolean;
}) {
  return (
    <td className={`px-3 py-2.5 text-sm ${right ? "text-right" : "text-left"} ${muted ? "text-muted-foreground" : "text-foreground"}`}>
      {children}
    </td>
  );
}

export function TableWrap({ children }: { children: React.ReactNode }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-bd-border">
      <table className="w-full min-w-[640px] border-collapse">{children}</table>
    </div>
  );
}
