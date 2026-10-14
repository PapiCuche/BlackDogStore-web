"""
DEVICE-COMMERCE-01 · DC1 — what a sale line was worth and what it cost.

TWO SNAPSHOTS, TAKEN ONCE, NEVER RECOMPUTED
-------------------------------------------
When a sale leaves the shelf (`inventory_services.record_sale_stock_movements`:
the web payment confirmation, or the POS sale), each `OrderItem` keeps:

  * ITS SHARE OF THE ORDER'S TAX BASE. `net_amount` is the line's part of
    `Order.taxable_amount` (the frozen breakdown of `tax_services`), and the
    lines ADD UP TO IT EXACTLY: the base is shared by the lines' gross weight
    (price × quantity) with the largest-remainder rule, so the cent that
    rounding leaves over goes to a definite line — the largest fraction, ties
    to the lowest line id. `discount_net` is its share of the order discount
    without tax (subtotal without tax − base), shared the same way, and
    `unit_price_net` its price without tax before the discount.

    Why share the base instead of dividing each line by 1.18: dividing each
    line rounds each line, and n roundings do not add up to the one rounding
    the order's breakdown made. A margin report whose lines do not add up to
    the invoice is a report nobody can reconcile.

  * ITS COST, from the Kardex line its exit wrote: the branch average
    ('average'), the units' specific cost ('unit'), the catalogue's reference
    ('reference') or nothing ('unknown' — «costo pendiente»).

Both are written only while empty, so a replayed notification or a reprocessed
exit cannot rewrite them; and nothing reads the catalogue to fill them later.
Lines from before DC1 keep NULL / 'unknown'.

THE WORD «MARGEN», NEVER «UTILIDAD». What this computes is the commercial
margin of the goods sold: net sales minus their cost. It is not a profit —
there are no expenses, commissions, payment fees or taxes in it.
"""
from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

CENT = Decimal('0.01')
COST_PLACES = Decimal('0.0001')


def _money(value) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def allocate(amount, weighted) -> dict:
    """
    Share `amount` (two decimals, >= 0) among `weighted` = [(key, weight), …].

    Proportional to the weights, in cents, by the largest remainder: every key
    first gets its share rounded DOWN to the cent; the cents left over go one
    each to the largest fractions, ties to the lowest key. The result adds up
    to `amount` exactly and does not depend on the order of `weighted`.
    With no weight at all, the whole amount goes to the lowest key.
    """
    amount = _money(amount)
    rows = sorted(weighted, key=lambda row: row[0])
    if not rows:
        return {}
    total_cents = int((amount / CENT).to_integral_value())
    weight_sum = sum((Decimal(w) for _k, w in rows), Decimal('0'))
    if weight_sum <= 0:
        out = {key: Decimal('0.00') for key, _w in rows}
        out[rows[0][0]] = amount
        return out

    shares, fractions, given = {}, [], 0
    for key, weight in rows:
        exact = Decimal(total_cents) * Decimal(weight) / weight_sum
        floor = int(exact.to_integral_value(rounding=ROUND_DOWN))
        shares[key] = floor
        fractions.append((-(exact - floor), key))
        given += floor
    for _fraction, key in sorted(fractions)[: total_cents - given]:
        shares[key] += 1
    return {key: (Decimal(cents) * CENT).quantize(CENT) for key, cents in shares.items()}


def line_nets(order, items) -> dict:
    """
    {item.pk: (unit_price_net, discount_net, net_amount)} for `items` of `order`.

    Read from the order's FROZEN breakdown (`tax_services.breakdown_for_order`),
    never recomputed from today's rate.
    """
    from .tax_services import breakdown_for_order

    breakdown = breakdown_for_order(order)
    weights = [(item.pk, Decimal(item.price) * item.quantity) for item in items]
    gross = sum((weight for _k, weight in weights), Decimal('0'))
    base = breakdown.taxable_amount
    rate = Decimal(breakdown.tax_rate or 0)
    before_discount = _money(gross / (Decimal('1') + rate)) if rate else _money(gross)
    discount = max(before_discount - base, Decimal('0.00'))

    nets = allocate(base, weights)
    discounts = allocate(discount, weights)
    out = {}
    for item in items:
        line_before = nets[item.pk] + discounts[item.pk]
        unit = (line_before / item.quantity).quantize(COST_PLACES, rounding=ROUND_HALF_UP) \
            if item.quantity else Decimal('0.0000')
        out[item.pk] = (unit, discounts[item.pk], nets[item.pk])
    return out


def snapshot_nets(order) -> None:
    """
    Freeze each line's share of the order's base and discount. Once.

    All the lines or none: the shares only add up as a set. Lines that already
    carry a share (a replay) are left exactly as they are.
    """
    from .models import OrderItem

    items = list(OrderItem.objects.filter(order=order).order_by('pk'))
    if not items or any(item.net_amount is not None for item in items):
        return
    for pk, (unit, discount, net) in line_nets(order, items).items():
        OrderItem.objects.filter(pk=pk, net_amount__isnull=True).update(
            unit_price_net=unit, discount_net=discount, net_amount=net,
        )


def snapshot_cost(item, movement) -> None:
    """Freeze the cost this line left the shelf at, from its Kardex line. Once."""
    from .models import OrderItem

    source = movement.cost_source or OrderItem.COST_UNKNOWN
    OrderItem.objects.filter(
        pk=item.pk, unit_cost__isnull=True, cost_source=OrderItem.COST_UNKNOWN,
    ).update(
        unit_cost=movement.unit_cost if source != OrderItem.COST_UNKNOWN else None,
        cost_source=source if source in OrderItem.SNAPSHOT_SOURCES else OrderItem.COST_UNKNOWN,
    )
