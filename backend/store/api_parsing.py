"""
Parsing of request values that Django would otherwise accept and regret.

WHY THIS EXISTS
---------------
`promotion.starts_at = request.data['starts_at']` looks like it validates. It
does not. A Django `DateTimeField` accepts whatever you assign to it in Python;
the value is only converted when it reaches the database, and by then the request
is deep inside a transaction. So a string like `"mañana"` did not produce a 400 —
it produced a 500 from the database layer, and `"2026-01-01 10:00"` (no timezone)
produced a row saved with a `NaiveDateTimeWarning` and an hour that means
whatever the server's clock happened to mean that day.

A promotion window is not cosmetic: `starts_at` and `ends_at` decide whether a
discount fires at a till. Being five hours out because a string was stored naive
is a real price on a real receipt.

So dates coming from a request get parsed HERE, before anything is assigned,
and a bad one is a 400 with a message — never a 500, never a silent naive
datetime.
"""

from __future__ import annotations

from datetime import date, datetime

from django.utils import timezone


class DateTimeParseError(ValueError):
    """A supplied value is not a datetime this API will accept."""


def parse_optional_datetime(value, *, label: str):
    """
    Turn a request value into an aware `datetime`, `None`, or an error.

    Accepts:
        None / '' / whitespace   → None   (an absent bound is a real answer:
                                           "no start" means "already started")
        an ISO-8601 datetime     → aware datetime
        an ISO-8601 date         → aware midnight, local time

    Raises `DateTimeParseError` for anything else.

    NAIVE INPUT IS INTERPRETED, NOT REJECTED
    ----------------------------------------
    A browser `datetime-local` input has no timezone, and rejecting it would
    make the obvious HTML control unusable. A naive value is therefore attached
    to the CURRENT timezone — `America/Lima` for this deployment — which is what
    the person typing it into an admin screen in Arequipa means by "8pm".
    `is_dst`-ambiguous instants are resolved by `make_aware`'s default rather
    than guessed at here.
    """
    if value is None:
        return None
    if isinstance(value, bool) or isinstance(value, (int, float)):
        # A JSON number is not a date. `20260301` would parse as an ISO basic
        # date, but a bare number in an API body is far more often an epoch
        # timestamp — and picking one of those two readings is precisely the
        # guess this module exists to avoid making.
        raise DateTimeParseError(
            f'{label}: fecha inválida. Usa el formato ISO-8601, '
            f'por ejemplo 2026-03-01T20:00.'
        )
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day)
    else:
        text = str(value).strip()
        if not text:
            return None
        # `fromisoformat` in 3.11+ handles the 'Z' suffix and offsets; earlier
        # versions do not, so normalise it first rather than depend on the
        # interpreter version.
        candidate = text[:-1] + '+00:00' if text.endswith(('Z', 'z')) else text
        try:
            parsed = datetime.fromisoformat(candidate)
        except (TypeError, ValueError):
            try:
                parsed = datetime.combine(date.fromisoformat(text), datetime.min.time())
            except (TypeError, ValueError):
                raise DateTimeParseError(
                    f'{label}: fecha inválida. Usa el formato ISO-8601, '
                    f'por ejemplo 2026-03-01T20:00.'
                ) from None

    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def parse_window(data, *, errors, current_start=None, current_end=None,
                 start_field='starts_at', end_field='ends_at',
                 start_label='Inicio', end_label='Fin'):
    """
    Parse an optional `[start, end)` window from request data.

    Handles the PATCH case that a naive implementation gets wrong: when only ONE
    bound is supplied, the ordering has to be checked against the bound ALREADY
    STORED, not against `None`. Sending just `ends_at`, moving it before an
    existing `starts_at`, would otherwise produce a window that can never be
    open — a promotion that silently never fires and gives no reason.

    Returns `(start, end, touched)` where `touched` is the set of fields the
    request actually addressed. Errors are accumulated into `errors` so the
    caller reports every problem at once instead of one per round trip.
    """
    start, end = current_start, current_end
    touched = set()

    for field, label, setter in (
        (start_field, start_label, 'start'),
        (end_field, end_label, 'end'),
    ):
        if field not in data:
            continue
        touched.add(field)
        try:
            parsed = parse_optional_datetime(data.get(field), label=label)
        except DateTimeParseError as exc:
            errors[field] = [str(exc)]
            continue
        if setter == 'start':
            start = parsed
        else:
            end = parsed

    if not errors and start and end and start >= end:
        errors[end_field] = [
            f'{end_label} debe ser posterior a {start_label.lower()}.'
        ]

    return start, end, touched
