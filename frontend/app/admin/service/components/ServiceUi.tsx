"use client";

/** Shared chrome for the service console. Presentation only — no rules here. */

import { useState } from "react";

export function Panel({
  title, subtitle, children, actions,
}: {
  title?: string;
  subtitle?: string;
  children: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-bd-border bg-surface p-6">
      {title ? (
        <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold uppercase tracking-wide text-muted">
              {title}
            </h2>
            {subtitle ? (
              <p className="mt-1 text-xs text-muted">{subtitle}</p>
            ) : null}
          </div>
          {actions}
        </header>
      ) : null}
      {children}
    </section>
  );
}

export function Field({
  label, value, onChange, placeholder, textarea, type = "text",
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  textarea?: boolean;
  type?: string;
}) {
  const cls =
    "mt-1 w-full rounded-lg border border-bd-border bg-background/40 px-3 py-2 text-sm text-foreground placeholder:text-muted";
  return (
    <label className="block text-xs text-muted">
      {label}
      {textarea ? (
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          rows={3}
          className={cls}
        />
      ) : (
        <input
          type={type}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          className={cls}
        />
      )}
    </label>
  );
}

export function Button({
  children, onClick, disabled, tone = "default",
}: {
  children: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
  tone?: "default" | "primary" | "danger";
}) {
  const tones = {
    default: "border-bd-border text-foreground/85 hover:border-bd-border",
    primary: "border-emerald-400/40 bg-emerald-400/10 text-emerald-200 hover:border-emerald-400/70",
    danger: "border-rose-400/40 text-rose-200 hover:border-rose-400/70",
  } as const;
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`rounded-lg border px-3 py-1.5 text-xs transition disabled:opacity-30 ${tones[tone]}`}
    >
      {children}
    </button>
  );
}

export function Pill({ label, tone = "neutral" }: { label: string; tone?: "neutral" | "good" | "warn" | "bad" }) {
  const tones = {
    neutral: "border-bd-border text-muted",
    good: "border-emerald-400/40 text-emerald-200",
    warn: "border-amber-400/40 text-amber-200",
    bad: "border-rose-400/40 text-rose-200",
  } as const;
  return (
    <span className={`rounded-full border px-2.5 py-1 text-[11px] ${tones[tone]}`}>
      {label}
    </span>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <p className="mt-3 rounded-lg border border-rose-400/30 bg-rose-400/5 px-3 py-2 text-xs text-rose-200">
      {error instanceof Error ? error.message : String(error)}
    </p>
  );
}

/**
 * A destructive or physical action, behind one deliberate click.
 *
 * Consuming a part and passing a quality check both change something outside
 * this screen — a shelf, a customer's expectation — so neither happens on a
 * stray click.
 */
export function Confirm({
  label, question, onConfirm, disabled, tone = "default",
}: {
  label: string;
  question: string;
  onConfirm: () => void;
  disabled?: boolean;
  tone?: "default" | "primary" | "danger";
}) {
  const [asking, setAsking] = useState(false);
  if (!asking) {
    return <Button onClick={() => setAsking(true)} disabled={disabled} tone={tone}>{label}</Button>;
  }
  return (
    <span className="inline-flex items-center gap-2 rounded-lg border border-bd-border px-2 py-1">
      <span className="text-[11px] text-muted">{question}</span>
      <Button onClick={() => { setAsking(false); onConfirm(); }} tone={tone}>Sí</Button>
      <Button onClick={() => setAsking(false)}>No</Button>
    </span>
  );
}

export function dateTime(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString("es-PE", { dateStyle: "medium", timeStyle: "short" });
}
