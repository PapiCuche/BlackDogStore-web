"""
What a till is TOLD, shaped once for every surface that asks.

WHY THIS MODULE EXISTS
----------------------
`pos_services` already owns the decisions — what a basket costs, who may be
credited, whether the cash adds up. It was extracted long before this file, and
nothing here re-decides any of it.

What was NOT extracted was the SHAPING. Every response the Web till reads was
assembled inline in `pos_views`, which was fine while there was one caller and
became a liability the moment there were two: an internal v1 surface that built
its own payload would be one edit away from telling a phone a different total
than it tells a browser. Not because the arithmetic differed — the service
guarantees that — but because a key got renamed on one side and not the other.

So the shaping lives here, once, and both surfaces call it. A parity test can
then assert the two responses are EQUAL rather than merely similar, which is
the only version of that claim worth having.

PURE FUNCTIONS, NO REQUEST. Nothing here reads `request`, resolves a capability
or touches authority. Callers decide what the caller may do and pass the answers
in as plain booleans — the same way `pos_services.create_pos_sale` takes
`may_assign_seller` rather than working it out for itself. A payload builder
that could grant something would be a permission system nobody reviewed.
"""

from __future__ import annotations

from . import inventory_services, pos_services
from .models import PaymentMethod


def product_payload(product, branch, *, barcode=None) -> dict:
    """
    One article, as a till needs to see it: what it is, what it costs, how many
    are on THIS shelf.

    `available` is read per branch on purpose. A national figure would tell a
    cashier they can sell something that is three cities away.
    """
    primary = (
        barcode
        or product.barcodes.filter(is_active=True, is_primary=True).first()
        or product.barcodes.filter(is_active=True).first()
    )
    return {
        'id': product.pk,
        'name': product.name,
        'price': str(product.price),
        'available': inventory_services.branch_quantity(branch, product),
        'barcode': primary.code if primary else '',
    }


def context_payload(
    company, branches, *, default_branch, actor,
    can_manage_customers: bool, can_assign_seller: bool,
    can_apply_discount: bool, can_view_commissions: bool, sellers,
) -> dict:
    """
    What this till may do, before it opens.

    The branches are the ones the caller can ACTUALLY sell from, so no surface
    ever offers one the backend would then refuse. The capability answers are
    asked once, at open, rather than guessed per action: a control that appears
    and then 403s is worse than one that was never offered.

    `sellers` is passed in already filtered. A list of colleagues is staffing
    information, and somebody who cannot reassign a sale has no reason to hold
    one — so the caller decides whether to gather it at all.
    """
    return {
        'company': {'id': company.pk, 'name': company.name},
        'branches': [{'id': b.pk, 'name': b.name} for b in branches],
        'default_branch': default_branch,
        'payment_methods': [
            {'value': v, 'label': l}
            for v, l in PaymentMethod.choices
            # The gateway method belongs to the online channel; a counter
            # cannot pick it.
            if v != PaymentMethod.ONLINE
        ],
        'can_manage_customers': can_manage_customers,
        'can_assign_seller': can_assign_seller,
        'can_apply_discount': can_apply_discount,
        'can_view_commissions': can_view_commissions,
        'seller': {
            'id': actor.pk,
            'username': actor.get_username(),
            'name': pos_services.seller_display_name(actor),
        },
        'sellers': sellers,
    }


def default_branch_for(company, branches):
    """
    The branch a till should open on, or None to make it ask.

    The configured default, but ONLY if the caller may actually sell from it;
    otherwise the single branch they have, if there is exactly one. With several
    and no authorised default it returns None and the surface asks.

    IT NEVER PICKS ONE. Selling from the wrong shop moves real units off a real
    shelf, and "the first branch in the list" is not a decision anybody made.
    """
    configured = company.default_inventory_branch
    if configured is not None and any(b.pk == configured.pk for b in branches):
        return configured.pk
    if len(branches) == 1:
        return branches[0].pk
    return None


def preview_payload(priced, *, may_see_commission: bool) -> dict:
    """
    What this basket costs, before anybody is charged.

    Built from `pos_services.build_pos_sale`, which runs the same resolution and
    the same arithmetic the sale will — so the number an operator reads aloud is
    the number the server will take.
    """
    seller = priced['seller']
    return {
        'subtotal': str(priced['subtotal']),
        'discount': str(priced['discount_amount']),
        'discount_source': priced['discount']['source'],
        'coupon_code': priced['discount']['coupon_code'],
        # What fired on its own, so a till can NAME it on screen rather than
        # showing an unexplained reduction.
        'promotions': [
            {
                'id': a['promotion'].pk,
                'name': a['promotion'].name,
                'applications': a['applications'],
                'regular_amount': str(a['regular_amount']),
                'discount_amount': str(a['discount_amount']),
            }
            for a in priced['promotions']['applied']
        ],
        'total': str(priced['total']),
        'seller': {
            'id': seller.pk if seller else None,
            'name': pos_services.seller_display_name(seller),
        },
        'customer': (
            {'id': priced['customer'].pk, 'name': priced['customer'].display_name}
            if priced['customer'] else None
        ),
        'commission': (
            {
                'rate_percent': str(priced['commission']['rate']),
                'base_amount': str(priced['commission']['base']),
                'amount': str(priced['commission']['amount']),
            }
            if may_see_commission else None
        ),
        'lines': [
            {
                'product': p.pk, 'name': p.name,
                'quantity': q, 'price': str(u),
            }
            for p, q, u in priced['lines']
        ],
    }


def sale_payload(
    order, branch, *, created: bool, may_see_commission: bool,
    available_elsewhere=None,
) -> dict:
    """
    A completed counter sale.

    Every monetary field is a STRING. They are `Decimal` in the database and
    they stay decimal all the way out: handing a client a JSON number invites it
    to do arithmetic that disagrees with the till.

    `commission` is None for anybody not allowed to see earnings. A cashier does
    not need to know what the sale paid a colleague.
    """
    commission = getattr(order, 'sales_commission', None)
    payload = {
        'order_id': order.pk,
        'created': created,
        'subtotal': str(order.total + order.discount_amount),
        'discount': str(order.discount_amount),
        'discount_source': order.discount_source,
        'discount_reason': order.discount_reason,
        'total': str(order.total),
        'paid_at': order.paid_at,
        'payment_method': order.payment_method,
        'amount_received': (
            str(order.amount_received) if order.amount_received is not None else None
        ),
        'change_amount': (
            str(order.change_amount) if order.change_amount is not None else None
        ),
        'payment_reference': order.payment_reference,
        'branch': {'id': branch.pk, 'name': branch.name},
        'seller': order.seller_name_snapshot,
        'customer': order.customer_name,
        'commission': (
            str(commission.amount) if commission and may_see_commission else None
        ),
        'items': [
            {
                'product': i.product_id,
                'name': i.product.name,
                'quantity': i.quantity,
                'price': str(i.price),
            }
            for i in order.items.select_related('product').all()
        ],
    }
    if available_elsewhere:
        payload['available_elsewhere'] = available_elsewhere
    return payload


def other_branches_with_stock(branches, product_ids, *, limit: int = 10) -> list[dict]:
    """
    Where the units actually are.

    This INFORMS; it moves nothing. Taking stock from another shop to cover a
    sale here is a transfer, and a transfer is a decision with paperwork.

    The caller decides who may see it — it is inventory information, and a till
    without `inventory.view` is told nothing.
    """
    if not product_ids:
        return []
    rows = (
        inventory_services.branch_stock_queryset(branches)
        .filter(product_id__in=product_ids, quantity__gt=0)
        .select_related('branch', 'product')[:limit]
    )
    return [
        {
            'branch': r.branch.name,
            'product': r.product.name,
            'quantity': r.quantity,
        }
        for r in rows
    ]
