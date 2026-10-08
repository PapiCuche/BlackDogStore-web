"""Money and dates the way the template prints them. The template computes nothing."""
from decimal import Decimal

from django.utils import timezone


def money(amount) -> str:
    """`Decimal('4299')` → `S/ 4,299.00`. A negative amount carries its sign first."""
    value = Decimal(str(amount))
    sign = '– ' if value < 0 else ''
    return f'{sign}S/ {abs(value):,.2f}'


def date(moment) -> str:
    """DD/MM/AAAA, in the shop's own time zone."""
    if moment is None:
        return ''
    if hasattr(moment, 'hour') and timezone.is_aware(moment):
        moment = timezone.localtime(moment)
    return moment.strftime('%d/%m/%Y')


def short_date(moment) -> str:
    """DD/MM, for a line of a progress list."""
    return date(moment)[:5]


def year() -> str:
    return str(timezone.localdate().year)
