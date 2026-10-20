"""
SUPPLIER-DEVICE-FLOW-02 · B — «Alertas operativas»: what of the orders already
paid is waiting for a person.

    GET /api/admin/orders/alerts/      sales.orders.view

Counts, never a customer. Scoped like every other order view: the caller's
company and, for somebody limited to some branches, only the orders those
branches fulfil (`tenancy.visible_orders`). Each alert links to the orders
list filtered by it (`?alert=<kind>`, `filter_orders`), so the count and the
list are the same question.

THE ALERTS (paid, current orders only — a cancelled or refunded one has its own
follow-up):
  * supplier_pending   a line ordered from the supplier and not received yet;
  * promise_due        of those, the promised date (day of payment + the
                       line's lead time, in the shop's days) is within
                       `DUE_SOON_DAYS` or already passed (`overdue` says how
                       many passed);
  * offer_expired      a line still waiting whose offer has ended (or was
                       deactivated): the supplier may not honour it;
  * to_confirm         a line «abastecimiento por confirmar»;
  * units_pending      a device nobody has identified yet (SERIAL-PICK-01):
                       the order cannot leave until somebody does;
  * ready_to_deliver   everything on order arrived and every device is named,
                       and the order has not been delivered.

Nothing here writes, and nothing decides what an order may do: the order's own
flow still says that.
"""
from __future__ import annotations

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Order, OrderItem

#: «The promised date passes within two days» (the owner's words).
DUE_SOON_DAYS = 2

#: Kind → (label, level). The order is the one the panel shows.
KINDS = {
    'to_confirm': ('Abastecimiento por confirmar', 'critical'),
    'promise_due': ('Fecha prometida vencida o por vencer', 'critical'),
    'offer_expired': ('Esperando a un proveedor con la oferta vencida', 'warning'),
    'supplier_pending': ('Esperando al proveedor', 'info'),
    'units_pending': ('Equipos por identificar', 'warning'),
    'ready_to_deliver': ('Recibidos, por entregar', 'info'),
}

#: Already handed over, or never will be: nothing about it is waiting.
_DONE = (Order.FulfillmentStatus.DELIVERED, Order.FulfillmentStatus.CANCELLED)
_LEFT = (Order.FulfillmentStatus.SHIPPED,) + _DONE


def _paid(orders):
    return orders.filter(status=Order.Status.PAID)


def _waiting_lines(orders):
    return OrderItem.objects.filter(
        order__in=_paid(orders), supply_origin=OrderItem.SUPPLY_SUPPLIER, supply_status=OrderItem.SUPPLY_PENDING,
    )


def promises(orders, today) -> tuple[set, set]:
    """(orders due within DUE_SOON_DAYS or past, orders past) — by each waiting line's promised day."""
    due, overdue = set(), set()
    tz = timezone.get_current_timezone()
    for order_id, paid_at, created_at, lead in _waiting_lines(orders).filter(lead_time_days__isnull=False) \
            .values_list('order_id', 'order__paid_at', 'order__created_at', 'lead_time_days'):
        moment = paid_at or created_at
        if moment is None:
            continue
        promised = timezone.localtime(moment, tz).date() + timedelta(days=lead)
        if promised <= today + timedelta(days=DUE_SOON_DAYS):
            due.add(order_id)
        if promised < today:
            overdue.add(order_id)
    return due, overdue


def _missing_devices(orders) -> set:
    """Paid orders still in the shop with a device nobody identified (SERIAL-PICK-01)."""
    from .unit_identification import pending_units

    candidates = (_paid(orders).exclude(fulfillment_status__in=_LEFT)
                  .filter(items__product__is_serialized=True).distinct())
    return {order.pk for order in candidates if pending_units(order)}


def _ready(orders) -> set:
    """Paid, not delivered, every on-order line received and every device named."""
    from .unit_identification import pending_units

    not_in = OrderItem.objects.filter(supply_origin=OrderItem.SUPPLY_SUPPLIER).exclude(
        supply_status=OrderItem.SUPPLY_RECEIVED).values('order_id')
    candidates = (_paid(orders).exclude(fulfillment_status__in=_DONE)
                  .filter(items__supply_origin=OrderItem.SUPPLY_SUPPLIER).exclude(pk__in=not_in).distinct())
    return {order.pk for order in candidates if not pending_units(order)}


def _ids(orders, kind, today) -> set:
    if kind == 'supplier_pending':
        return set(_waiting_lines(orders).values_list('order_id', flat=True))
    if kind == 'promise_due':
        return promises(orders, today)[0]
    if kind == 'offer_expired':
        return set(_waiting_lines(orders).filter(
            Q(supplier_offer__valid_until__lt=today) | Q(supplier_offer__is_active=False),
        ).values_list('order_id', flat=True))
    if kind == 'to_confirm':
        return set(OrderItem.objects.filter(
            order__in=_paid(orders), supply_origin=OrderItem.SUPPLY_SUPPLIER,
            supply_status=OrderItem.SUPPLY_TO_CONFIRM,
        ).values_list('order_id', flat=True))
    if kind == 'units_pending':
        return _missing_devices(orders)
    if kind == 'ready_to_deliver':
        return _ready(orders)
    raise KeyError(kind)


def filter_orders(orders, kind):
    """The orders list's `?alert=<kind>`: those of `orders` the alert counts. An unknown kind narrows nothing."""
    if kind not in KINDS:
        return orders
    return orders.filter(pk__in=_ids(orders, kind, timezone.localdate()))


def summary(orders) -> list[dict]:
    """One row per alert, in `KINDS` order, with its count and the list it opens."""
    today = timezone.localdate()
    rows = []
    for kind, (label, level) in KINDS.items():
        if kind == 'promise_due':
            due, overdue = promises(orders, today)
            row = {'count': len(due), 'overdue': len(overdue)}
        else:
            row = {'count': len(_ids(orders, kind, today))}
        rows.append({'kind': kind, 'label': label, 'level': level, 'href': f'/admin/orders?alert={kind}', **row})
    return rows


class AdminOrderAlertsView(APIView):
    """GET /api/admin/orders/alerts/ — the «Alertas operativas» of the dashboard."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from .admin_views import CAP_ORDERS_VIEW, _LEGACY_VIEW_ORDERS_ROLES, _company_context
        from .tenancy import visible_orders

        company, error = _company_context(request, CAP_ORDERS_VIEW, _LEGACY_VIEW_ORDERS_ROLES)
        if error:
            return error
        rows = summary(visible_orders(request.user, company))
        return Response({'alerts': rows, 'due_soon_days': DUE_SOON_DAYS})

