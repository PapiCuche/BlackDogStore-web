"use client";

/**
 * The month of due dates. Wide screens get the grid; below `sm` it collapses into a compact agenda (one line
 * per day that has something due), because seven columns do not fit a 390 px phone without scrolling the page.
 *
 * «HOY» IS THE SERVER'S: `today` comes from the summary (the company's own date). The browser clock is not read.
 * Only what the server returned is drawn: a day with no dot has nothing due among the loaded payments.
 */
import { useMemo } from "react";
import { STATE_LABELS, type Obligation } from "../../lib/finance-api";
import {
  WEEKDAY_SHORT,
  formatLongDate,
  formatMonth,
  monthGrid,
  parseIso,
  shiftMonth,
  MONTH_SHORT,
  type YearMonth,
} from "../../lib/finance-dates";
import { internalButtonClass } from "../../components/internal-ui";
import { STATE_DOT } from "./FinanceBits";

const STATE_ORDER = ["overdue", "due_today", "due_soon", "pending"] as const;

export type DayBucket = Partial<Record<(typeof STATE_ORDER)[number], number>> & { total: number };

/** Count of payments per due date and state, for the days of `ym` only. */
export function bucketByDay(items: Obligation[], ym: YearMonth): Record<string, DayBucket> {
  const out: Record<string, DayBucket> = {};
  for (const item of items) {
    if (!item.due_date) continue;
    const parsed = parseIso(item.due_date);
    if (!parsed || parsed.year !== ym.year || parsed.month !== ym.month) continue;
    const bucket = (out[item.due_date] ??= { total: 0 });
    const state = (STATE_ORDER as readonly string[]).includes(item.state) ? (item.state as (typeof STATE_ORDER)[number]) : "pending";
    bucket[state] = (bucket[state] ?? 0) + 1;
    bucket.total += 1;
  }
  return out;
}

function describe(iso: string, bucket: DayBucket): string {
  const parts = STATE_ORDER.flatMap((state) => (bucket[state] ? [`${bucket[state]} ${STATE_LABELS[state].toLowerCase()}`] : []));
  return `${formatLongDate(iso)}: ${parts.join(", ")}`;
}

function Dots({ bucket }: { bucket: DayBucket }) {
  return (
    <span className="flex flex-wrap items-center gap-1" aria-hidden="true">
      {STATE_ORDER.flatMap((state) =>
        bucket[state]
          ? [
              <span key={state} className="inline-flex items-center gap-0.5 text-[10px] font-semibold text-foreground">
                <span className={`inline-block h-2 w-2 rounded-full ${STATE_DOT[state]}`} />
                {bucket[state]}
              </span>,
            ]
          : [],
      )}
    </span>
  );
}

export function FinanceCalendar({
  items,
  today,
  month,
  onMonthChange,
  selectedDay,
  onSelectDay,
}: {
  items: Obligation[];
  today: string;
  month: YearMonth;
  onMonthChange: (next: YearMonth) => void;
  selectedDay: string | null;
  onSelectDay: (iso: string | null) => void;
}) {
  const buckets = useMemo(() => bucketByDay(items, month), [items, month]);
  const weeks = useMemo(() => monthGrid(month), [month]);
  const days = Object.keys(buckets).sort();
  const pick = (iso: string) => onSelectDay(selectedDay === iso ? null : iso);

  return (
    <section aria-labelledby="fin-calendar-title" className="rounded-xl border border-bd-border bg-surface p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="fin-calendar-title" className="text-sm font-semibold text-foreground first-letter:uppercase">
          Calendario · {formatMonth(month)}
        </h2>
        <div className="flex gap-2">
          <button type="button" className={internalButtonClass} aria-label="Mes anterior"
                  onClick={() => onMonthChange(shiftMonth(month, -1))}>‹</button>
          <button type="button" className={internalButtonClass}
                  onClick={() => { const parsed = parseIso(today); if (parsed) onMonthChange({ year: parsed.year, month: parsed.month }); }}>
            Mes actual
          </button>
          <button type="button" className={internalButtonClass} aria-label="Mes siguiente"
                  onClick={() => onMonthChange(shiftMonth(month, 1))}>›</button>
        </div>
      </div>

      <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted" aria-label="Leyenda del calendario">
        {STATE_ORDER.map((state) => (
          <li key={state} className="inline-flex items-center gap-1.5">
            <span className={`inline-block h-2 w-2 rounded-full ${STATE_DOT[state]}`} />{STATE_LABELS[state]}
          </li>
        ))}
      </ul>

      {/* Grid: from `sm` up. */}
      <div className="mt-3 hidden sm:block" data-testid="finance-calendar-grid">
        <table className="w-full table-fixed border-separate border-spacing-1" aria-label={`Vencimientos de ${formatMonth(month)}`}>
          <thead>
            <tr>
              {WEEKDAY_SHORT.map((d) => (
                <th key={d} scope="col" className="pb-1 text-center text-[11px] font-semibold uppercase text-muted">{d}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {weeks.map((week, w) => (
              <tr key={w}>
                {week.map((cell, c) => {
                  if (!cell.iso || cell.day === null) return <td key={c} />;
                  const bucket = buckets[cell.iso];
                  const isToday = cell.iso === today;
                  const isSelected = cell.iso === selectedDay;
                  const frame = `h-16 w-full rounded-lg border p-1.5 text-left align-top ${
                    isSelected ? "border-foreground bg-foreground/[0.06]" : isToday ? "border-foreground/50" : "border-bd-border"}`;
                  return (
                    <td key={c} className="p-0">
                      {bucket ? (
                        <button
                          type="button"
                          onClick={() => pick(cell.iso!)}
                          aria-pressed={isSelected}
                          aria-label={describe(cell.iso, bucket)}
                          className={`${frame} flex flex-col justify-between transition hover:bg-foreground/[0.04] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2`}
                        >
                          <span className={`text-xs ${isToday ? "font-extrabold text-foreground" : "text-muted"}`}>
                            {cell.day}{isToday ? " · hoy" : ""}
                          </span>
                          <Dots bucket={bucket} />
                        </button>
                      ) : (
                        <div className={`${frame} text-xs ${isToday ? "font-extrabold text-foreground" : "text-muted/70"}`}>
                          {cell.day}{isToday ? " · hoy" : ""}
                        </div>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Agenda: below `sm`. */}
      <div className="mt-3 sm:hidden" data-testid="finance-calendar-agenda">
        {days.length === 0 ? (
          <p className="text-sm text-muted">Sin vencimientos cargados en {formatMonth(month)}.</p>
        ) : (
          <ul className="space-y-1.5">
            {days.map((iso) => {
              const parsed = parseIso(iso)!;
              const isSelected = iso === selectedDay;
              return (
                <li key={iso}>
                  <button
                    type="button"
                    onClick={() => pick(iso)}
                    aria-pressed={isSelected}
                    aria-label={describe(iso, buckets[iso])}
                    className={`flex w-full items-center justify-between gap-3 rounded-lg border px-3 py-2 text-left text-sm ${
                      isSelected ? "border-foreground bg-foreground/[0.06]" : "border-bd-border"}`}
                  >
                    <span className="min-w-0 font-semibold text-foreground">
                      {parsed.day} {MONTH_SHORT[parsed.month - 1]}{iso === today ? " · hoy" : ""}
                    </span>
                    <Dots bucket={buckets[iso]} />
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
      <p className="mt-3 text-xs text-muted">
        Sólo se dibuja lo que el servidor devolvió para tus sucursales. Toca un día para ver sus pagos.
      </p>
    </section>
  );
}
