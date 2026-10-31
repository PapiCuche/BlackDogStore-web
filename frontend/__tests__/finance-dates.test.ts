export {};
/**
 * FIN-REMIND-01 — la aritmética del calendario. Ninguna función lee el reloj del navegador:
 * «hoy» llega del servidor y todo lo demás es cálculo sobre cadenas ISO.
 */
/* eslint-disable @typescript-eslint/no-require-imports */
const dates = require('@/app/admin/lib/finance-dates');
/* eslint-enable @typescript-eslint/no-require-imports */

describe('month grid', () => {
  it('starts the month on the right weekday (Monday first)', () => {
    // 1 nov 2026 is a Sunday: six empty cells before it.
    const weeks = dates.monthGrid({ year: 2026, month: 11 });
    expect(weeks[0].filter((c: { iso: string | null }) => c.iso === null)).toHaveLength(6);
    expect(weeks[0][6]).toEqual({ iso: '2026-11-01', day: 1 });
    expect(weeks.every((w: unknown[]) => w.length === 7)).toBe(true);
  });

  it('knows leap years and month lengths', () => {
    expect(dates.daysInMonth({ year: 2028, month: 2 })).toBe(29);
    expect(dates.daysInMonth({ year: 2026, month: 2 })).toBe(28);
    const days = dates.monthGrid({ year: 2026, month: 10 }).flat().filter((c: { iso: string | null }) => c.iso);
    expect(days).toHaveLength(31);
  });

  it('moves between months across the year boundary', () => {
    expect(dates.shiftMonth({ year: 2026, month: 12 }, 1)).toEqual({ year: 2027, month: 1 });
    expect(dates.shiftMonth({ year: 2026, month: 1 }, -1)).toEqual({ year: 2025, month: 12 });
  });
});

describe('day arithmetic', () => {
  it('adds days over month ends and measures distances from the given today', () => {
    expect(dates.addDays('2026-10-31', 1)).toBe('2026-11-01');
    expect(dates.addDays('2026-10-31', -90)).toBe('2026-08-02');
    expect(dates.daysBetween('2026-10-31', '2026-11-03')).toBe(3);
    expect(dates.daysBetween('2026-10-31', '2026-10-28')).toBe(-3);
  });

  it('says when something is due relative to the company today', () => {
    expect(dates.relativeDue('2026-10-31', '2026-10-31')).toBe('vence hoy');
    expect(dates.relativeDue('2026-11-01', '2026-10-31')).toBe('vence mañana');
    expect(dates.relativeDue('2026-11-05', '2026-10-31')).toBe('vence en 5 días');
    expect(dates.relativeDue('2026-10-30', '2026-10-31')).toBe('venció ayer');
    expect(dates.relativeDue('2026-10-20', '2026-10-31')).toBe('venció hace 11 días');
    expect(dates.relativeDue(null, '2026-10-31')).toBe('Sin fecha de vencimiento');
  });

  it('formats dates in Spanish', () => {
    expect(dates.formatDate('2026-11-05')).toBe('5 nov 2026');
    expect(dates.formatLongDate('2026-11-01')).toBe('domingo 1 de noviembre de 2026');
    expect(dates.formatMonth({ year: 2026, month: 11 })).toBe('noviembre de 2026');
  });

  it('does not read the clock: the same inputs give the same answers whatever the browser date', () => {
    jest.useFakeTimers().setSystemTime(new Date('2030-01-01T00:00:00Z'));
    try {
      expect(dates.relativeDue('2026-11-05', '2026-10-31')).toBe('vence en 5 días');
      expect(dates.addDays('2026-10-31', 1)).toBe('2026-11-01');
    } finally {
      jest.useRealTimers();
    }
  });
});

describe('without the company today', () => {
  it('says nothing about when, rather than «vence hoy»', () => {
    expect(dates.relativeDue('2026-11-05', '')).toBe('');
    expect(dates.relativeDue('2026-11-05', 'no-es-fecha')).toBe('');
    expect(dates.relativeDue(null, '')).toBe('Sin fecha de vencimiento');
  });
});
