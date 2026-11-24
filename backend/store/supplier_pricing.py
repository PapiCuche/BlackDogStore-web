"""
SUPPLY-MARGIN-01 — the SUGGESTED price of a supplier offer: its cost plus the company's margin.

Owner's rule: «Precio sugerido: costo más margen configurable por empresa, presentado sólo como propuesta
revisable. No cambiar automáticamente los precios publicados.»

THE ARITHMETIC (`suggested_price`)

    cost × (1 + margin / 100), then rounded UP:
      * to the cent when the company names no step;
      * to the next MULTIPLE of the step N (`supplier_price_round_to`, whole currency units) when it does — a
        price that already is a multiple stays. N = 1 gives whole units, 10 «…0», 50 «…00/…50».

    UP, never to the nearest: the suggestion is never below the margin the company asked for.

    «ENDING IN 9» IS NOT A STEP. N = 9 means multiples of 9 (99, 108, 117…), not prices ending in 9; a «…99» rule
    would be a second, different setting, and nobody has asked for it yet.

    Nothing (None) without a cost (or a cost of 0 or less), without a margin, or when the result would not fit an
    offer's price (`SupplierOffer.commercial_price`, 10 digits with 2 decimals).

WHERE IT TRAVELS. Only to `costs.view` (cost + margin is the cost), only in payloads (offer list and detail, the
weekly panel, the import preview — Excel and the text assistant's dry run), and only as the key
`suggested_price`. Without `costs.view` the key is ABSENT, whatever the company configured.

WHAT IT NEVER DOES. It writes nothing: no offer's price, no variant's price, no import row. A person reads it and,
if they want it, types it (or presses «Usar sugerido», which only fills the field of the form) and saves —
through the same rules (price-change decision, below-cost confirmation) as any other price.
"""
from __future__ import annotations

from decimal import ROUND_CEILING, Decimal, InvalidOperation

CENT = Decimal('0.01')
HUNDRED = Decimal('100')
#: The largest price an offer can hold (`SupplierOffer.commercial_price`: max_digits=10, decimal_places=2).
MAX_PRICE = Decimal('99999999.99')


def _decimal(value) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def suggested_price(cost, settings) -> Decimal | None:
    """
    The suggested price for `cost` under `settings` (anything with `supplier_margin_percent` and
    `supplier_price_round_to`, normally the company's `CompanySettings`; None = no settings). Decimal with two
    places, or None. See the module docstring.
    """
    if settings is None:
        return None
    cost = _decimal(cost)
    margin = _decimal(getattr(settings, 'supplier_margin_percent', None))
    if cost is None or cost <= 0 or margin is None or margin < 0:
        return None
    price = cost * (HUNDRED + margin) / HUNDRED
    step = getattr(settings, 'supplier_price_round_to', None)
    if step:
        step = Decimal(int(step))
        price = (price / step).to_integral_value(rounding=ROUND_CEILING) * step
    price = price.quantize(CENT, rounding=ROUND_CEILING)
    return price if price <= MAX_PRICE else None


def company_rules(company_id):
    """
    The company's two settings, read in ONE query (None: no settings row). Load it once per payload and pass it
    to `suggest` for every row.
    """
    from .models import CompanySettings

    return (CompanySettings.objects.filter(company_id=company_id)
            .only('pk', 'supplier_margin_percent', 'supplier_price_round_to').first())


def suggest(cost, settings) -> str | None:
    """`suggested_price` as a payload writes it: a string with two decimals, or None."""
    price = suggested_price(cost, settings)
    return None if price is None else str(price)
