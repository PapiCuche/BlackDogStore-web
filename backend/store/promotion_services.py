"""
The automatic promotion engine — Commercial Phase C1.3.

WHAT IT DOES
------------
Given a basket, works out which configured promotions it qualifies for, how many
times each applies, and how much comes off. It changes MONEY and nothing else:
the order still contains the real articles, and the same units still leave the
same shelves.

WHY IT LIVES HERE AND NOT IN THE BROWSER
----------------------------------------
The till has to show a total before charging it, so the temptation is to compute
the combo in React and let the server agree. That produces two implementations
of one rule, and the day they disagree is the day a customer is standing at the
counter watching the number change. There is one implementation; the POS asks it
through `/pos/preview/` and gets back what it will be charged.

OVERLAPPING PROMOTIONS: DETERMINISTIC, NOT OPTIMAL
--------------------------------------------------
Two promotions can want the same unit. Picking the combination that saves the
customer most is a set-packing problem — NP-hard, and worse, unstable: adding one
cable to a basket could reshuffle every discount on it, which is impossible for
a shopkeeper to explain to the person paying.

So the rule is simple and boring on purpose:

    order by priority DESC, then id ASC
    each promotion consumes the units it needs
    a unit consumed by one promotion is gone for the next

The result is always the same for the same basket, an admin controls the outcome
by setting priority, and the reason any given promotion did not apply is always
"something above it took the units".
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone

from .models import Promotion

CENT = Decimal('0.01')


def _money(value) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def live_promotions(company, branch=None, *, at=None):
    """
    The promotions in force here, right now, in evaluation order.

    Filtered in Python for the window and branch rather than in SQL: the
    conditions involve a nullable window and a scope enum whose SELECTED case
    needs a join, and the set per company is small. Clarity wins over a query
    that would have to be read twice.
    """
    at = at or timezone.now()
    rows = (
        Promotion.objects
        .filter(company=company, is_active=True)
        .prefetch_related('items__product', 'branches')
        .order_by('-priority', 'pk')
    )
    return [p for p in rows if p.is_live(at) and p.applies_to_branch(branch)]


def _applications_possible(promotion, available: dict[int, int]) -> int:
    """
    How many complete sets of this combo the remaining units can form.

    Bounded by the scarcest component: a combo needing two cables and one
    charger, against four cables and one charger, applies once — the second
    cable pair has no charger to go with it.
    """
    items = list(promotion.items.all())
    if not items:
        return 0
    possible = None
    for item in items:
        have = available.get(item.product_id, 0)
        if have < item.quantity:
            return 0
        count = have // item.quantity
        possible = count if possible is None else min(possible, count)
    return possible or 0


def _bundle_amounts(promotion, prices: dict[int, Decimal], applications: int):
    """
    What one set costs normally, and what this promotion charges instead.

    Returns `(regular, discount)` for ALL applications together.
    """
    regular_per_set = Decimal('0.00')
    for item in promotion.items.all():
        regular_per_set += prices[item.product_id] * item.quantity
    regular_per_set = _money(regular_per_set)

    if promotion.promotion_type == Promotion.BUNDLE_FIXED_PRICE:
        combo_price = _money(promotion.fixed_price or Decimal('0.00'))
        # A "combo" priced above the parts is not a discount. It is almost
        # certainly a configuration mistake, and charging MORE for buying
        # together would be indefensible — so it simply does not fire.
        discount_per_set = max(regular_per_set - combo_price, Decimal('0.00'))
    else:
        percent = Decimal(promotion.discount_percent or 0)
        discount_per_set = _money(regular_per_set * percent / Decimal('100'))

    return (
        _money(regular_per_set * applications),
        _money(discount_per_set * applications),
    )


def evaluate(company, branch, items, prices, *, at=None):
    """
    Apply every qualifying promotion to this basket.

    `items`  : normalised `[{'product': id, 'quantity': n}, ...]`
    `prices` : `{product_id: Decimal}` — the SERVER's prices, never the client's

    Returns:
        {
          'applied':  [ {promotion, applications, regular_amount,
                         discount_amount, components}, ... ],
          'discount': Decimal,   total across all of them
        }

    Units are consumed as promotions are applied, so no unit is ever discounted
    twice and a leftover article is charged at its normal price — the second
    cable in a basket of two, when the combo only needed one, costs what a cable
    costs.
    """
    available = {i['product']: i['quantity'] for i in items}
    applied = []
    total_discount = Decimal('0.00')

    for promotion in live_promotions(company, branch, at=at):
        possible = _applications_possible(promotion, available)
        if possible <= 0:
            continue
        if promotion.max_applications_per_order:
            possible = min(possible, promotion.max_applications_per_order)
        if possible <= 0:
            continue

        regular, discount = _bundle_amounts(promotion, prices, possible)
        if discount <= 0:
            # Nothing to give away — a misconfigured combo, or a percentage that
            # rounds to zero on a very cheap set. Consuming units for a discount
            # of nothing would block a promotion below it for no benefit.
            continue

        components = []
        for item in promotion.items.all():
            used = item.quantity * possible
            available[item.product_id] = available.get(item.product_id, 0) - used
            components.append({
                'product_id': item.product_id,
                'product_name': item.product.name,
                'quantity_per_application': item.quantity,
                'quantity_used': used,
                'unit_price': str(prices[item.product_id]),
            })

        applied.append({
            'promotion': promotion,
            'applications': possible,
            'regular_amount': regular,
            'discount_amount': discount,
            'components': components,
        })
        total_discount += discount

    return {'applied': applied, 'discount': _money(total_discount)}


def combo_availability(company, branch, *, at=None, limit=20):
    """
    Combos this branch could actually sell right now, for the POS shortcut.

    Each carries how many complete sets the CURRENT stock allows, because
    offering a one-tap combo that then fails at the till for want of a screen
    protector is worse than not offering it. A partial bundle is never sold.
    """
    from . import inventory_services

    out = []
    for promotion in live_promotions(company, branch, at=at)[:limit]:
        items = list(promotion.items.all())
        if not items:
            continue
        possible = None
        components = []
        for item in items:
            on_hand = inventory_services.branch_quantity(branch, item.product)
            sets = on_hand // item.quantity if item.quantity else 0
            possible = sets if possible is None else min(possible, sets)
            components.append({
                'product_id': item.product_id,
                'product_name': item.product.name,
                'quantity': item.quantity,
                'available': on_hand,
                'price': str(item.product.price),
            })
        regular = _money(sum(
            (Decimal(str(i.product.price)) * i.quantity for i in items),
            Decimal('0.00'),
        ))
        prices = {i.product_id: Decimal(str(i.product.price)) for i in items}
        _reg, discount = _bundle_amounts(promotion, prices, 1)
        out.append({
            'id': promotion.pk,
            'name': promotion.name,
            'promotion_type': promotion.promotion_type,
            'components': components,
            'regular_amount': str(regular),
            'discount_amount': str(discount),
            'combo_amount': str(_money(regular - discount)),
            'available_sets': possible or 0,
        })
    return out


def freeze(order, evaluation):
    """
    Write the snapshot rows for a completed sale.

    Called inside the sale's transaction. What it stores is deliberately
    redundant with the `Promotion` rows — the name, the type, the amounts and
    the components — because the promotion will be edited, renamed and
    eventually switched off, and this receipt must keep saying what this
    customer was charged and why.
    """
    from .models import AppliedPromotion

    rows = []
    for entry in evaluation['applied']:
        promotion = entry['promotion']
        rows.append(AppliedPromotion(
            company_id=order.company_id,
            order=order,
            promotion=promotion,
            promotion_name_snapshot=promotion.name,
            promotion_type_snapshot=promotion.promotion_type,
            applications=entry['applications'],
            regular_amount=entry['regular_amount'],
            discount_amount=entry['discount_amount'],
            metadata={'components': entry['components']},
        ))
    if rows:
        # bulk_create skips clean(); assert the tenant invariant over the set.
        AppliedPromotion.assert_all_match_company(rows)
        AppliedPromotion.objects.bulk_create(rows)
    return rows
