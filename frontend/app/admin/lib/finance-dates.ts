/**
 * Calendar arithmetic for «Pagos fijos y vencimientos».
 *
 * «HOY» IS THE COMPANY'S, NOT THE BROWSER'S. The server sends `today` with the summary (the company's own date,
 * America/Lima by default) and every function here takes it as an argument. Nothing in this file reads the
 * clock: a person in another time zone, or a laptop with a wrong date, sees the same calendar as the server.
 * All dates are ISO `YYYY-MM-DD` strings; weekday and month length are worked out in UTC, which is pure
 * arithmetic on the three numbers and cannot drift with a local zone.
 */

export const MONTH_NAMES = [
  "enero", "febrero", "marzo", "abril", "mayo", "junio",
  "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
];
export const MONTH_SHORT = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
/** Monday first, like the weekday numbers the server uses (0 = lunes). */
export const WEEKDAY_SHORT = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"];

export type YearMonth = { year: number; month: number }; // month: 1–12

const ISO = /^(\d{4})-(\d{2})-(\d{2})$/;

export function parseIso(value: string): { year: number; month: number; day: number } | null {
  const match = ISO.exec(value);
  if (!match) return null;
  return { year: Number(match[1]), month: Number(match[2]), day: Number(match[3]) };
}

export function toIso(year: number, month: number, day: number): string {
  return `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

export function daysInMonth({ year, month }: YearMonth): number {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

/** 0 = lunes … 6 = domingo. */
export function weekdayOf(year: number, month: number, day: number): number {
  return (new Date(Date.UTC(year, month - 1, day)).getUTCDay() + 6) % 7;
}

export function monthOf(iso: string): YearMonth | null {
  const parsed = parseIso(iso);
  return parsed ? { year: parsed.year, month: parsed.month } : null;
}

export function shiftMonth({ year, month }: YearMonth, delta: number): YearMonth {
  const index = year * 12 + (month - 1) + delta;
  return { year: Math.floor(index / 12), month: (index % 12) + 1 };
}

export function addDays(iso: string, days: number): string {
  const parsed = parseIso(iso);
  if (!parsed) return iso;
  const moved = new Date(Date.UTC(parsed.year, parsed.month - 1, parsed.day + days));
  return toIso(moved.getUTCFullYear(), moved.getUTCMonth() + 1, moved.getUTCDate());
}

/** Whole days from `from` to `to` (negative when `to` is earlier). */
export function daysBetween(from: string, to: string): number {
  const a = parseIso(from);
  const b = parseIso(to);
  if (!a || !b) return 0;
  return Math.round((Date.UTC(b.year, b.month - 1, b.day) - Date.UTC(a.year, a.month - 1, a.day)) / 86_400_000);
}

export type CalendarCell = { iso: string | null; day: number | null };

/** The month as weeks of seven cells (Monday first); `null` cells pad the first and last week. */
export function monthGrid(ym: YearMonth): CalendarCell[][] {
  const lead = weekdayOf(ym.year, ym.month, 1);
  const cells: CalendarCell[] = Array.from({ length: lead }, () => ({ iso: null, day: null }));
  for (let day = 1; day <= daysInMonth(ym); day += 1) {
    cells.push({ iso: toIso(ym.year, ym.month, day), day });
  }
  while (cells.length % 7 !== 0) cells.push({ iso: null, day: null });
  const weeks: CalendarCell[][] = [];
  for (let at = 0; at < cells.length; at += 7) weeks.push(cells.slice(at, at + 7));
  return weeks;
}

/** «15 nov 2026». */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "Sin fecha";
  const parsed = parseIso(iso.slice(0, 10));
  if (!parsed) return iso;
  return `${parsed.day} ${MONTH_SHORT[parsed.month - 1]} ${parsed.year}`;
}

/** «domingo 15 de noviembre de 2026». */
export function formatLongDate(iso: string): string {
  const parsed = parseIso(iso);
  if (!parsed) return iso;
  const weekday = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"][
    weekdayOf(parsed.year, parsed.month, parsed.day)];
  return `${weekday} ${parsed.day} de ${MONTH_NAMES[parsed.month - 1]} de ${parsed.year}`;
}

export function formatMonth(ym: YearMonth): string {
  return `${MONTH_NAMES[ym.month - 1]} de ${ym.year}`;
}

/**
 * An instant from the server (`2026-11-03T14:05:00-05:00`) for a person. Rendered in the browser's zone: it
 * labels WHEN SOMETHING HAPPENED, never what «today» is.
 */
export function formatInstant(value: string | null | undefined): string {
  if (!value) return "—";
  const at = new Date(value);
  if (Number.isNaN(at.getTime())) return value;
  return at.toLocaleString("es-PE", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** «vence en 3 días», «venció hace 2 días», «vence hoy», measured from the company's `today`. */
export function relativeDue(due: string | null, today: string): string {
  if (!due) return "Sin fecha de vencimiento";
  // Without the company's today (the summary failed) there is nothing honest to say about «cuándo».
  if (!parseIso(today)) return "";
  const days = daysBetween(today, due);
  if (days === 0) return "vence hoy";
  if (days === 1) return "vence mañana";
  if (days > 1) return `vence en ${days} días`;
  if (days === -1) return "venció ayer";
  return `venció hace ${-days} días`;
}
