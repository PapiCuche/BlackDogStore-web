"""
FIN-B1 — the calendar is COMPUTED from the anchor and the index, never accumulated
(docs/adr-finance-obligations.md §4.1).
"""
from datetime import date

from django.test import SimpleTestCase

from store.finance import calendar


def dues(frequency, anchor, due_day, count, start=0):
    return [calendar.period(frequency, anchor, due_day, n).due for n in range(start, start + count)]


class MonthlyCalendarTest(SimpleTestCase):
    def test_the_31st_falls_on_the_last_day_of_short_months_and_comes_back(self):
        self.assertEqual(dues('monthly', date(2026, 1, 31), 31, 4),
                         [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)])

    def test_a_leap_february_is_the_29th(self):
        self.assertEqual(dues('monthly', date(2028, 1, 31), 31, 2), [date(2028, 1, 31), date(2028, 2, 29)])

    def test_period_zero_is_the_first_due_on_or_after_the_anchor(self):
        # Anchored on the 15th, due on the 5th: January's 5th is before the rule existed.
        self.assertEqual(calendar.period('monthly', date(2026, 1, 15), 5, 0).due, date(2026, 2, 5))
        self.assertEqual(calendar.period('monthly', date(2026, 1, 5), 5, 0).due, date(2026, 1, 5))

    def test_a_period_starts_on_the_first_of_its_month(self):
        self.assertEqual(calendar.period('monthly', date(2026, 1, 1), 5, 2).start, date(2026, 3, 1))

    def test_the_index_of_a_day_is_its_period(self):
        anchor = date(2026, 1, 1)
        self.assertEqual(calendar.index_on('monthly', anchor, 5, date(2026, 1, 20)), 0)
        self.assertEqual(calendar.index_on('monthly', anchor, 5, date(2026, 10, 25)), 9)
        self.assertEqual(calendar.index_on('monthly', anchor, 5, date(2025, 6, 1)), 0)


class OtherFrequenciesTest(SimpleTestCase):
    def test_quarterly_is_three_months_apart(self):
        self.assertEqual(dues('quarterly', date(2026, 1, 1), 31, 3),
                         [date(2026, 1, 31), date(2026, 4, 30), date(2026, 7, 31)])

    def test_yearly_on_february_29_falls_on_the_28th_in_common_years(self):
        self.assertEqual(dues('yearly', date(2028, 2, 1), 29, 3),
                         [date(2028, 2, 29), date(2029, 2, 28), date(2030, 2, 28)])

    def test_weekly_is_due_on_its_weekday(self):
        # 2026-01-07 is a Wednesday; due on Friday (4).
        got = dues('weekly', date(2026, 1, 7), 4, 3)
        self.assertEqual(got, [date(2026, 1, 9), date(2026, 1, 16), date(2026, 1, 23)])
        self.assertTrue(all(day.weekday() == 4 for day in got))

    def test_weekly_period_zero_skips_a_weekday_before_the_anchor(self):
        # Anchored Wednesday, due Monday (0): this week's Monday is already gone.
        self.assertEqual(calendar.period('weekly', date(2026, 1, 7), 0, 0).due, date(2026, 1, 12))


class EndDateAndHorizonTest(SimpleTestCase):
    def test_no_period_is_due_after_the_end_date(self):
        got = [p.due for p in calendar.periods_until(
            'monthly', date(2026, 1, 1), 5, start_index=0, horizon=date(2026, 12, 31), end_date=date(2026, 3, 4))]
        self.assertEqual(got, [date(2026, 1, 5), date(2026, 2, 5)])

    def test_the_horizon_bounds_generation(self):
        got = [p.index for p in calendar.periods_until(
            'monthly', date(2026, 1, 1), 5, start_index=2, horizon=date(2026, 5, 5))]
        self.assertEqual(got, [2, 3, 4])

    def test_a_negative_index_does_not_exist(self):
        with self.assertRaises(ValueError):
            calendar.period('monthly', date(2026, 1, 1), 5, -1)
