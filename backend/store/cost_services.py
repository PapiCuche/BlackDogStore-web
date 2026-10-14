"""
DEVICE-COMMERCE-01 · DC1 — the cost a stock movement carries.

THE OWNER'S DECISIONS (2026-10-14)
----------------------------------
  * Products counted by quantity (accessories): WEIGHTED AVERAGE COST, PER
    BRANCH. `BranchStock.average_cost`.
  * Serialized devices: the SPECIFIC cost of each unit. `StockUnit.cost`.
  * The history is never recalculated from the catalogue.

This module only DECIDES. The one writer of stock
(`inventory_services.create_stock_movement`) calls `resolve()` while it holds
the `BranchStock` row lock, writes the answer on the Kardex line and, when the
answer says so, the new average on the stock row. Nothing else writes either.

THE RULES, IN ONE PLACE
-----------------------
  entry WITH a stated cost   the line carries that cost ('entry'); the branch
                             average becomes
                                 (qty_before × avg + qty_in × cost) / (qty_before + qty_in)
                             An empty shelf, or a shelf whose units are of
                             unknown cost, simply takes the new cost: there is
                             nothing known to weigh it against.
  entry without one          NULL, 'unknown'; the average is untouched.
  return / service return    carries the current average; the average is
                             untouched (adding units at the average does not
                             move it).
  exit, transfer out         carries the current average ('average'). With no
                             average yet, the catalogue's reference cost,
                             marked 'reference'. With neither, NULL 'unknown'.
  transfer in                the cost and the source the units LEFT with. It
                             feeds the destination's average only when that
                             cost was a real one (entry, average or unit): a
                             reference cost is an estimate, never a valuation.
  serialized product         the mean of the moved units' specific costs
                             ('unit'), or NULL 'unknown' when any is missing.
                             The branch keeps no average for it.

Four decimals, half up: a unit cost is often a fraction of a cent once
averaged, and rounding it to cents at every entry would drift.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

COST_PLACES = Decimal('0.0001')

ENTRY = 'entry'
AVERAGE = 'average'
UNIT = 'unit'
REFERENCE = 'reference'
UNKNOWN = 'unknown'

#: A cost somebody actually paid, as opposed to an estimate.
REAL_SOURCES = frozenset([ENTRY, AVERAGE, UNIT])


def quantize_cost(value) -> Decimal:
    return Decimal(value).quantize(COST_PLACES, rounding=ROUND_HALF_UP)


def clean_cost(value) -> Decimal | None:
    """A stated cost as a Decimal, None when absent. Raises ValueError when it is not one."""
    if value is None or value == '':
        return None
    if isinstance(value, float):
        raise ValueError('Un costo no se expresa como float.')
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('El costo no es un importe válido.') from None
    if not amount.is_finite() or amount < 0 or amount >= Decimal('10000000000'):
        raise ValueError('El costo no es un importe válido.')
    return quantize_cost(amount)


def weighted_average(qty_before: int, average_before, qty_in: int, unit_cost) -> Decimal:
    """
    The branch average after `qty_in` units arrive at `unit_cost`.

    `qty_before` is the shelf BEFORE the entry. When it is zero, or its cost is
    unknown (`average_before` None), the new cost is the whole answer.
    """
    if qty_in <= 0:
        raise ValueError('Una entrada mueve al menos una unidad.')
    unit_cost = Decimal(unit_cost)
    if unit_cost < 0:
        raise ValueError('Un costo no puede ser negativo.')
    if qty_before <= 0 or average_before is None:
        return quantize_cost(unit_cost)
    total = Decimal(qty_before) * Decimal(average_before) + Decimal(qty_in) * unit_cost
    return quantize_cost(total / Decimal(qty_before + qty_in))


@dataclass(frozen=True)
class MovementCost:
    unit_cost: Decimal | None
    source: str
    #: The branch average after this movement. Equal to the one before unless
    #: the movement is an entry with a real cost.
    average_after: Decimal | None


def _carried(average, reference) -> tuple[Decimal | None, str]:
    if average is not None:
        return quantize_cost(average), AVERAGE
    if reference is not None:
        return quantize_cost(reference), REFERENCE
    return None, UNKNOWN


def resolve(*, is_entry: bool, carries_average_in: bool, quantity: int, stock_before: int,
            average_before, reference_cost, stated_cost=None, stated_source: str | None = None,
            unit_costs=None) -> MovementCost:
    """
    The cost of one movement, and the branch average it leaves.

    `carries_average_in`: an entry that brings units BACK (a return), valued at
    the current average rather than at a cost of its own.
    `stated_cost` / `stated_source`: the cost the caller knows (a purchase, a
    transfer's origin). `unit_costs`: the specific costs of the moved units of
    a serialized product — its presence is what makes the product serialized.
    """
    if unit_costs is not None:
        costs = list(unit_costs)
        if costs and all(cost is not None for cost in costs):
            mean = sum((Decimal(cost) for cost in costs), Decimal('0')) / Decimal(len(costs))
            return MovementCost(quantize_cost(mean), UNIT, average_before)
        return MovementCost(None, UNKNOWN, average_before)

    if not is_entry:
        unit_cost, source = _carried(average_before, reference_cost)
        return MovementCost(unit_cost, source, average_before)

    if stated_cost is not None:
        source = stated_source or ENTRY
        if source in REAL_SOURCES:
            average = weighted_average(stock_before, average_before, quantity, stated_cost)
            return MovementCost(quantize_cost(stated_cost), source, average)
        return MovementCost(quantize_cost(stated_cost), source, average_before)

    if carries_average_in:
        unit_cost, source = _carried(average_before, reference_cost)
        return MovementCost(unit_cost, source, average_before)

    return MovementCost(None, UNKNOWN, average_before)


def user_may_view_costs(user, company) -> bool:
    """
    DC1 — whether `user` may see (or set) what things cost in `company`.

    THE one answer for code that holds a user and a company (a service, the
    margin view); `inventory_views.may_view_costs` is the same answer for a
    request. The legacy bridge counts like everywhere else: a pre-SaaS admin
    of the pilot tenant has always seen costs.
    """
    from .models import UserProfile
    from .permissions import get_user_role
    from .tenancy import has_capability, uses_legacy_bridge

    if company is None or user is None or not getattr(user, 'is_authenticated', False):
        return False
    if uses_legacy_bridge(user, company):
        return get_user_role(user) in (UserProfile.ROLE_ADMIN, UserProfile.ROLE_SUPERADMIN)
    return has_capability(user, company, 'costs.view')
