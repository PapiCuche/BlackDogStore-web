"""
WHICH DAYS A QUESTION COVERS (ADR §6).

    ?tax_period=YYYYMM                    the tax month (wins over the rest)
    ?period=day|week|month|year&date=     the day, ISO week (Mon–Sun), month or
                                          year containing `date` (default today)
    ?period=range&from=&to=               inclusive, at most 366 days

Days are the COMPANY's days: the boundaries come from the dashboard's own
helpers (`_company_tz`, `_day_bounds`), so a sale at 23:30 in Lima is that day's
sale here exactly as it is on the sales dashboard. They are imported, not
copied: two definitions of "today" would drift.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

MAX_DAYS = 366
KINDS = ('day', 'week', 'month', 'year', 'range', 'tax_period')


class PeriodError(ValueError):
    """The question names no valid period. A person reads the message."""


@dataclass(frozen=True)
class Period:
    kind: str
    first: date
    last: date          # inclusive
    start: datetime     # aware, the first instant
    end: datetime       # aware, exclusive
    timezone: str

    def payload(self) -> dict:
        return {
            'kind': self.kind, 'from': self.first.isoformat(), 'to': self.last.isoformat(),
            'timezone': self.timezone,
        }


def _parse_date(raw: str, name: str) -> date:
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        raise PeriodError(f'«{name}» debe ser una fecha AAAA-MM-DD válida.') from None


def _month_end(first: date) -> date:
    following = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return following - timedelta(days=1)


def resolve(params, company) -> Period:
    from store.sales_analytics_views import _company_today, _company_tz, _day_bounds

    tz = _company_tz(company)
    tax_period = (params.get('tax_period') or '').strip()
    kind = (params.get('period') or '').strip()

    if tax_period:
        match = re.fullmatch(r'(\d{4})(\d{2})', tax_period)
        if not match or not 1 <= int(match.group(2)) <= 12:
            raise PeriodError('«tax_period» debe tener la forma AAAAMM, con un mes de 01 a 12.')
        first = date(int(match.group(1)), int(match.group(2)), 1)
        last, kind = _month_end(first), 'tax_period'
    else:
        kind = kind or 'month'
        if kind not in KINDS or kind == 'tax_period':
            raise PeriodError('«period» debe ser day, week, month, year o range.')
        if kind == 'range':
            first = _parse_date(params.get('from'), 'from')
            last = _parse_date(params.get('to'), 'to')
            if last < first:
                raise PeriodError('El rango termina antes de empezar.')
        else:
            raw = params.get('date')
            anchor = _parse_date(raw, 'date') if raw else _company_today(company)
            if kind == 'day':
                first = last = anchor
            elif kind == 'week':
                first = anchor - timedelta(days=anchor.weekday())
                last = first + timedelta(days=6)
            elif kind == 'month':
                first = anchor.replace(day=1)
                last = _month_end(first)
            else:
                first, last = date(anchor.year, 1, 1), date(anchor.year, 12, 31)

    if (last - first).days + 1 > MAX_DAYS:
        raise PeriodError(f'Un período abarca como mucho {MAX_DAYS} días.')
    start, _ = _day_bounds(first, tz)
    _, end = _day_bounds(last, tz)
    return Period(kind=kind, first=first, last=last, start=start, end=end, timezone=str(tz))
