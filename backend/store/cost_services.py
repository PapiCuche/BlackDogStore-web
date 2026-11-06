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
  THE SHELF HAS TWO PARTS (DC1 review): the units of KNOWN cost, valued at
  the average, and `BranchStock.uncosted_quantity` — units that came in
  without a cost, «costo pendiente» in every valuation. The average is of the
  first part only; when that part is empty while stock remains, the average
  is NULL. Unknown cost is never presented as known.

  entry WITH a stated cost   the line carries that cost ('entry'); the average
                             of the costed part becomes
                                 (costed × avg + qty_in × cost) / (costed + qty_in)
                             No costed units yet: the new cost is the average.
  entry without one          NULL, 'unknown'; the units join the pending part.
  return / service return    carries the current average and joins the costed
                             part; with no average, it is pending.
  back from an order's sale  any entry for an `order` with no cost stated and
                             not new stock (purchase, opening, transfer in):
                             the cost and source its sale exit left with
                             (`inventory_services._cost_the_sale_left_with`),
                             whatever the entry's type — a reference cost
                             comes back pending. Before the average above.
  exit, transfer out         the pending units leave FIRST (they are the older
                             ones: they were there before any cost was known).
                             An exit only of costed units carries the average
                             ('average'); one that takes any pending unit is
                             not of a known cost: the catalogue's reference
                             cost, marked 'reference', or NULL 'unknown'.
  empty shelf                keeps its last average for a return; nothing is
                             valued at it.
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
    #: The shelf's units of unknown cost after this movement.
    uncosted_after: int = 0


def _carried(average, reference) -> tuple[Decimal | None, str]:
    if average is not None:
        return quantize_cost(average), AVERAGE
    if reference is not None:
        return quantize_cost(reference), REFERENCE
    return None, UNKNOWN


def resolve(*, is_entry: bool, carries_average_in: bool, quantity: int, stock_before: int,
            average_before, reference_cost, stated_cost=None, stated_source: str | None = None,
            unit_costs=None, uncosted_before: int = 0) -> MovementCost:
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
        source = UNIT
        if not is_entry and reference_cost is not None and any(cost is None for cost in costs):
            # DC2 review — a device of unknown cost LEAVES at the catalogue's
            # reference cost, said so: the rule of every other exit (`_carried`),
            # and the cost its sale line freezes (`margin_services.snapshot_unit_cost`).
            costs = [reference_cost if cost is None else cost for cost in costs]
            source = REFERENCE
        elif is_entry and stated_cost is not None and any(cost is None for cost in costs):
            # DC2 second review — devices of no cost of their own ARRIVE at the
            # cost they left with (a transfer's origin line), said the same way:
            # «transfers arrive at the cost they left with».
            costs = [stated_cost if cost is None else cost for cost in costs]
            source = stated_source or ENTRY
        if costs and all(cost is not None for cost in costs):
            mean = sum((Decimal(cost) for cost in costs), Decimal('0')) / Decimal(len(costs))
            return MovementCost(quantize_cost(mean), source, average_before, 0)
        return MovementCost(None, UNKNOWN, average_before, 0)

    shelf = max(int(stock_before or 0), 0)
    pending = min(max(int(uncosted_before or 0), 0), shelf)
    costed = shelf - pending

    def done(unit_cost, source, average, pending_after):
        after = shelf + (quantity if is_entry else -quantity)
        pending_after = min(max(pending_after, 0), max(after, 0))
        if after > 0 and after - pending_after <= 0:
            average = None        # nothing left on the shelf has a known cost
        return MovementCost(unit_cost, source, average, pending_after)

    if not is_entry:
        leaving_pending = min(quantity, pending)
        if leaving_pending:
            unit_cost, source = _carried(None, reference_cost)
        else:
            unit_cost, source = _carried(average_before if costed else None, reference_cost)
        return done(unit_cost, source, average_before, pending - leaving_pending)

    if stated_cost is not None:
        source = stated_source or ENTRY
        if source in REAL_SOURCES:
            average = weighted_average(costed, average_before, quantity, stated_cost)
            return done(quantize_cost(stated_cost), source, average, pending)
        return done(quantize_cost(stated_cost), source, average_before, pending + quantity)

    if carries_average_in and average_before is not None:
        return done(quantize_cost(average_before), AVERAGE, average_before, pending)
    if carries_average_in:
        unit_cost, source = _carried(None, reference_cost)
        return done(unit_cost, source, average_before, pending + quantity)

    return done(None, UNKNOWN, average_before, pending + quantity)


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


# ---------------------------------------------------------------------------
# COST-WRITE-AUTHORITY (owner's decision C, 2026-11) — who may WRITE a cost.
# ---------------------------------------------------------------------------
#
# «Rechazar en servidor valores de costo enviados por usuarios sin autorización
# para gestionarlos.» The authority is `costs.view` («Ver costos y márgenes»):
# there is no separate capability to manage costs, and it is the one the
# reception, the unit's «Corregir costo» and the reference cost already asked.
#
# A NON-EMPTY cost from a caller without it is REFUSED, never quietly dropped
# and never stored; an empty or absent one is «no cost» and stays allowed, so
# receiving without seeing costs remains possible. One code for every door.

#: The machine-readable refusal, the reception's since INV-RECEIVE.
COSTS_FORBIDDEN = 'costs_forbidden'

#: What a request with a cost hears (the reception's own sentence).
COSTS_REQUIRED = 'Necesitas el permiso «Ver costos y márgenes» para indicar un costo.'

#: «Registrar equipos»: the same, and what to do instead.
UNIT_COSTS_REQUIRED = (COSTS_REQUIRED + ' Registra los equipos sin costo: quedará pendiente de completar.')

#: A row of a spreadsheet whose cost cell is filled, read by somebody without
#: the authority. The same sentence whatever the cell says: the value is not
#: read, so no answer can be compared with the stored cost.
COSTS_IN_FILE = ('Este archivo trae costos y tu usuario no puede registrarlos: quita la columna de costo '
                 '(o déjala vacía) o pide a un administrador el permiso «Ver costos y márgenes».')

#: A job previewed WITH costs (by somebody who could) applied by somebody who cannot.
COSTS_IN_JOB = ('Esta carga trae costos y tu usuario no puede registrarlos: pide que la aplique alguien con el '
                'permiso «Ver costos y márgenes», o vuelve a subir el archivo sin la columna de costo.')


def states_a_cost(value) -> bool:
    """Whether a sent value is a cost at all: None, '' and blank text are «no cost»; anything else is one."""
    return not (value is None or (isinstance(value, str) and not value.strip()))


def staged_rows_carry_cost(rows, keys) -> bool:
    """Whether any staged import row (its `normalized_data`) carries a cost under one of `keys`."""
    for row in rows:
        data = row.normalized_data if isinstance(row.normalized_data, dict) else {}
        if any(states_a_cost(data.get(key)) for key in keys):
            return True
    return False
