"""
The calendar of a recurring rule, COMPUTED from its anchor and the period index.

Never accumulated: period `n` is derived from `anchor_date` and `n`, not by
adding a month to period `n - 1`. Accumulating is how a rule due on the 31st
drifts to the 28th forever after its first February; computing it puts March
back on the 31st (docs/adr-finance-obligations.md §4.1).

Monthly, quarterly and yearly periods are calendar months `step` apart (1, 3,
12); the period starts on the 1st and is due on `due_day`, or on the last day of
a shorter month. Weekly periods start on a Monday and are due `due_day` days
later (0 = Monday … 6 = Sunday). Period 0 is the first one due ON or AFTER the
anchor: a rule anchored on the 15th and due on the 5th starts next month.

A period exists only while `due <= end_date`. Pure functions, no database.
"""
from __future__ import annotations

import calendar as _cal
from dataclasses import dataclass
from datetime import date, timedelta

WEEKLY = 'weekly'
STEPS = {'monthly': 1, 'quarterly': 3, 'yearly': 12}


@dataclass(frozen=True)
class Period:
    index: int
    start: date
    due: date


def _month_date(month_number: int, day: int) -> date:
    year, month = divmod(month_number, 12)
    last = _cal.monthrange(year, month + 1)[1]
    return date(year, month + 1, min(day, last))


def _first_month(anchor: date, due_day: int) -> int:
    month_number = anchor.year * 12 + anchor.month - 1
    if _month_date(month_number, due_day) < anchor:
        month_number += 1
    return month_number


def _first_week(anchor: date, due_day: int) -> date:
    monday = anchor - timedelta(days=anchor.weekday())
    if monday + timedelta(days=due_day) < anchor:
        monday += timedelta(days=7)
    return monday


def period(frequency: str, anchor: date, due_day: int, index: int) -> Period:
    if index < 0:
        raise ValueError('El índice de un período no puede ser negativo.')
    if frequency == WEEKLY:
        start = _first_week(anchor, due_day) + timedelta(days=7 * index)
        return Period(index, start, start + timedelta(days=due_day))
    step = STEPS[frequency]
    month_number = _first_month(anchor, due_day) + step * index
    return Period(index, _month_date(month_number, 1), _month_date(month_number, due_day))


def index_on(frequency: str, anchor: date, due_day: int, day: date) -> int:
    """The index of the period whose window contains `day` (0 before the first)."""
    if frequency == WEEKLY:
        days = (day - _first_week(anchor, due_day)).days
        return max(0, days // 7)
    step = STEPS[frequency]
    months = (day.year * 12 + day.month - 1) - _first_month(anchor, due_day)
    return max(0, months // step)


def periods_until(frequency: str, anchor: date, due_day: int, *, start_index: int, horizon: date,
                  end_date: date | None = None):
    """Every period from `start_index` whose due date is on or before `horizon` (and `end_date`)."""
    index = start_index
    while True:
        current = period(frequency, anchor, due_day, index)
        if current.due > horizon or (end_date is not None and current.due > end_date):
            return
        yield current
        index += 1


def exists(frequency: str, anchor: date, due_day: int, index: int, end_date: date | None) -> bool:
    return end_date is None or period(frequency, anchor, due_day, index).due <= end_date
