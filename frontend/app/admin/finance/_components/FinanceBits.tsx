"use client";

import { STATE_LABELS, money, type Obligation, type PerCurrency } from "../../lib/finance-api";

/** Colour per state. The word is always there too: colour alone says nothing to everyone. */
export const STATE_TONE: Record<string, string> = {
  overdue: "border-danger-border bg-danger-surface text-danger",
  due_today: "border-warning-border bg-warning-surface text-warning",
  due_soon: "border-info-border bg-info-surface text-info",
  pending: "border-bd-border bg-surface-2 text-muted",
  paid: "border-success-border bg-success-surface text-success",
  voided: "border-bd-border bg-surface-2 text-muted",
  paused: "border-bd-border bg-surface-2 text-muted",
};

/** The dot of the calendar. */
export const STATE_DOT: Record<string, string> = {
  overdue: "bg-danger-solid",
  due_today: "bg-warning-solid",
  due_soon: "bg-info-solid",
  pending: "bg-muted",
  paid: "bg-success-solid",
};

/**
 * A letter per state, drawn next to the colour in the calendar: colour alone says nothing to everybody.
 * V = vencido, H = vence hoy, P = por vencer, L = pendiente más adelante.
 */
export const STATE_GLYPH: Record<string, string> = { overdue: "V", due_today: "H", due_soon: "P", pending: "L" };

export function StateBadge({ state }: { state: string }) {
  return (
    <span
      className={`inline-block whitespace-nowrap rounded-full border px-2.5 py-0.5 text-xs font-semibold ${
        STATE_TONE[state] ?? STATE_TONE.pending}`}
    >
      {STATE_LABELS[state] ?? state}
    </span>
  );
}

/** «S/ 1,250.00» for each currency, never added across currencies. */
export function PerCurrencyLines({ amounts, empty = "—" }: { amounts: PerCurrency; empty?: string }) {
  const entries = Object.entries(amounts);
  if (entries.length === 0) return <span className="text-muted">{empty}</span>;
  return (
    <span className="flex flex-col">
      {entries.map(([currency, value]) => (
        <span key={currency} data-currency={currency} className="tabular-nums">{money(value, currency)}</span>
      ))}
    </span>
  );
}

export function Balance({ item }: { item: Pick<Obligation, "balance" | "effective" | "paid" | "currency" | "partially_paid"> }) {
  return (
    <div className="text-right">
      <p className="text-base font-bold tabular-nums text-foreground">{money(item.balance, item.currency)}</p>
      {item.partially_paid ? (
        <p className="text-xs text-muted">
          Pagado {money(item.paid, item.currency)} de {money(item.effective, item.currency)}
        </p>
      ) : (
        <p className="text-xs text-muted">Saldo pendiente</p>
      )}
    </div>
  );
}

export const labelClass = "block text-sm font-semibold text-foreground";
export const hintClass = "mt-1 text-xs text-muted";
export const linkButtonClass =
  "rounded-lg border border-bd-border bg-background px-3 py-1.5 text-xs font-semibold text-foreground transition hover:border-foreground/25 hover:bg-foreground/[0.03] disabled:cursor-not-allowed disabled:opacity-40";
