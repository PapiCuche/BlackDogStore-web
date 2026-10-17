"""
Test support for ORDER-FULFILLMENT-SAFETY-01 — not imported by application code.

Since dispatch moves exactly one step at a time, a test that needs an order
«sent» or «delivered» walks it there through the real service (`walk_to`), so
the audit trail is the one production would write. A test that needs a past
the service no longer allows (a pickup order «sent», a move back after the
goods left — production holds such rows from before this phase) writes it
with `legacy_move`, which imitates what the old free selector recorded.
"""
from __future__ import annotations

from . import fulfillment_flow
from .models import AdminAuditLog, Order, OrderDelivery


def walk_to(order, target, *, actor, company, note=''):
    """Move `order` forward one step at a time until it stands at `target`."""
    from .order_fulfillment_services import change_fulfillment_status

    for _ in range(6):
        order.refresh_from_db()
        if order.fulfillment_status == target:
            return order
        nxt = fulfillment_flow.moves(order)['forward']
        if nxt is None:
            raise AssertionError(f'{target!r} is not ahead of {order.fulfillment_status!r} on this order\'s path')
        change_fulfillment_status(order=order, new_status=nxt, actor=actor, company=company, note=note)
    raise AssertionError(f'could not reach {target!r}')


def place_at(order, status):
    """Set the status with no audit entry — a fixture, as if the order had been created there."""
    Order.objects.filter(pk=order.pk).update(fulfillment_status=status)
    order.refresh_from_db()
    return order


def carrier_details(order, *, carrier_name='Transportes de prueba', waybill_number='GR-0001', agency_name=''):
    """
    Write on the delivery record what «Enviado» asks of a carrier mode
    (`fulfillment_flow.logistics_blockers`). No shipping date: that alone
    would count as the goods having left.
    """
    record, _ = OrderDelivery.objects.get_or_create(order=order, defaults={'company': order.company})
    record.carrier_name, record.waybill_number, record.agency_name = carrier_name, waybill_number, agency_name
    record.save(update_fields=['carrier_name', 'waybill_number', 'agency_name'])
    return record


def legacy_move(order, new_status, *, actor=None, company=None, note=''):
    """
    Record a move as the pre-phase free selector did (status + audit entry),
    without the path rules. Also stamps devices as gone when it «leaves», as
    the old service did.
    """
    from . import unit_identification

    previous = Order.objects.values_list('fulfillment_status', flat=True).get(pk=order.pk)
    Order.objects.filter(pk=order.pk).update(fulfillment_status=new_status)
    order.refresh_from_db()
    if new_status in unit_identification.LEAVING:
        unit_identification.mark_left(order)
    return AdminAuditLog.log(
        actor=actor, action='order_fulfillment_status_changed', target_type='order', target_id=order.pk,
        company=company or order.company,
        metadata={'order_id': order.pk, 'customer_email': order.customer_email,
                  'old_fulfillment_status': previous, 'new_fulfillment_status': new_status,
                  'note': note[:200]},
    )
