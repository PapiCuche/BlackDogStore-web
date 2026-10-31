"use client";

/**
 * What is owed, by how urgent it is. Amounts are the server's, PER CURRENCY (soles and dollars are never added
 * together) and counts are the server's too. Each card opens the list it summarises.
 */
import type { Summary } from "../../lib/finance-api";
import { PerCurrencyLines } from "./FinanceBits";

export type SummaryTarget = "overdue" | "today" | "upcoming" | "partial";

const CARDS: {
  id: SummaryTarget | "total";
  label: string;
  tone: string;
  pick: (s: Summary) => { amounts: Summary["open"]; count: number };
}[] = [
  { id: "overdue", label: "Vencidos", tone: "border-danger-border bg-danger-surface",
    pick: (s) => ({ amounts: s.overdue, count: s.counts.overdue }) },
  { id: "today", label: "Vencen hoy", tone: "border-warning-border bg-warning-surface",
    pick: (s) => ({ amounts: s.due_today, count: s.counts.due_today }) },
  { id: "upcoming", label: "Por vencer (7 días)", tone: "border-info-border bg-info-surface",
    pick: (s) => ({ amounts: s.due_soon, count: s.counts.due_soon }) },
  { id: "total", label: "Pendiente total", tone: "border-bd-border bg-surface",
    pick: (s) => ({ amounts: s.open, count: s.counts.open }) },
];

export function SummaryCards({
  summary,
  partialCount,
  onOpen,
}: {
  summary: Summary;
  /** How many of the loaded open payments have a partial payment. A count of rows, not an amount. */
  partialCount: number | null;
  onOpen: (target: SummaryTarget) => void;
}) {
  return (
    <section aria-labelledby="fin-summary-title">
      <h2 id="fin-summary-title" className="sr-only">Resumen de pagos pendientes</h2>
      <ul className="grid grid-cols-1 gap-3 min-[360px]:grid-cols-2 lg:grid-cols-4">
        {CARDS.map((card) => {
          const { amounts, count } = card.pick(summary);
          const body = (
            <>
              <span className="block text-[11px] font-semibold uppercase tracking-wide text-muted">{card.label}</span>
              <span className="mt-2 block break-words font-display text-xl font-bold text-foreground sm:text-2xl">
                <PerCurrencyLines amounts={amounts} empty="Nada" />
              </span>
              <span className="mt-1 block text-xs text-muted">{count === 1 ? "1 pago" : `${count} pagos`}</span>
            </>
          );
          const frame = `h-full w-full rounded-xl border p-4 text-left ${card.tone}`;
          return (
            <li key={card.id} className="min-w-0">
              {card.id === "total" ? (
                <div data-summary="total" className={frame}>
                  {body}
                  {partialCount !== null && partialCount > 0 ? (
                    <button type="button" onClick={() => onOpen("partial")}
                            className="mt-2 text-xs font-semibold text-foreground underline">
                      {partialCount} con pago parcial · ver
                    </button>
                  ) : null}
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => onOpen(card.id as SummaryTarget)}
                  data-summary={card.id}
                  className={`${frame} transition hover:border-foreground/30 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2`}
                >
                  {body}
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
