"""
Who may move an order's fulfilment state, and what happens when they do.

ONE STATE MACHINE, TWO SURFACES.

  WEB ADMIN   `PATCH /api/admin/orders/<pk>/fulfillment-status/`
  INTERNAL V1 `PATCH /api/v1/internal/<slug>/orders/<pk>/fulfillment/`

The two differ in how they authenticate and how they name the tenant. They must
NOT differ in which statuses a warehouse user is allowed to set, or in what gets
written to the audit log — a rule enforced in one place and forgotten in the
other is how an operation becomes possible from a phone that is refused on a
desk.

WHO DECIDES — TWO AUTHORITY PATHS, NEVER MIXED (H4.1.2, RBAC-LEGACY-01)

  SaaS — a Membership, or a platform master naming the company. The company
  capability `sales.orders.manage` decides, and nothing else. `UserProfile.role`
  is a global label that predates companies: inside a company it neither narrows
  nor widens what that company granted. Someone whose profile still says
  `inventory` and whose company gave them `sales.orders.manage` may set every
  state, because the company said so; someone whose profile says `admin` and
  whose company gave them only `sales.orders.view` may set none.

  LEGACY BRIDGE — a pre-SaaS operator on the pilot, with no Membership. On that
  path the legacy role IS the authority, so the historical rule stands exactly
  as it was: warehouse staff move goods, they do not cancel sales.

WHAT THIS DELIBERATELY DOES NOT DO

No email. No sales note. The legacy view does none of those either, and this
extraction is not the place to add behaviour: changing what a status change
*means* would be a business decision, not a refactor.

THE ONE THING A STATUS CHANGE DOES MEAN (SERIAL-PICK-01, an owner's decision)

«Sent» and «delivered» are the moments a device leaves the shop. An order that
sells serialized devices does not reach them until somebody has identified each
one, and from then on those devices are not renamed. Both surfaces get the same
refusal and the same way to identify the device (`unit_identification`).

AND ONE A COMPANY MAY ADD (CLIENT-SEC-01). An order whose assessment asks for a
person to look (`order_risk`) can be sent and handed over all the same: the
review is advice. A company that turns `hold_orders_pending_review` on makes
those two moments wait until somebody has decided — either way. Off, which is
how every company starts, nothing here changes.
"""
from django.db import transaction

from . import order_risk
from .commerce_notifications import emit_fulfillment_changed
from .models import AdminAuditLog, Order, UserProfile
from .permissions import get_user_role
from .tenancy import has_capability, uses_legacy_bridge

CAP_ORDERS_MANAGE = 'sales.orders.manage'

# LEGACY BRIDGE ONLY. Warehouse staff move goods; they do not cancel sales.
#
# Keyed on the legacy `UserProfile.role`, which is right on the one path where
# that role is still the authority and wrong everywhere else — which is why
# allowed_fulfillment_statuses() never reads it for a Membership.
INVENTORY_ALLOWED_FULFILLMENT = frozenset([
    Order.FulfillmentStatus.PREPARING,
    Order.FulfillmentStatus.READY_FOR_PICKUP,
    Order.FulfillmentStatus.SHIPPED,
    Order.FulfillmentStatus.DELIVERED,
])

ALL_FULFILLMENT_STATUSES = tuple(choice[0] for choice in Order.FulfillmentStatus.choices)

#: The states a company's hold keeps an order out of while its review is
#: pending (CLIENT-SEC-01): the two in which the goods leave, and the one that
#: tells the buyer to come and collect them.
HELD_WHILE_REVIEW_PENDING = frozenset([
    Order.FulfillmentStatus.READY_FOR_PICKUP,
    Order.FulfillmentStatus.SHIPPED,
    Order.FulfillmentStatus.DELIVERED,
])


class FulfillmentNotAllowed(Exception):
    """This actor may not set this status. Carries the message the caller shows."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class FulfillmentBlocked(Exception):
    """
    The actor may set this status, and the ORDER is not ready for it.

    SERIAL-PICK-01: an order with serialized devices does not leave the shop
    — shipped or delivered — until somebody has identified each one.

    CLIENT-SEC-01: in a company that holds orders until they are reviewed, an
    order waiting for its review does not leave either. `code` says which of
    the two it was (`units_pending`, `review_pending`); `pending` is the
    devices still to identify, and empty for a review.
    """

    UNITS_PENDING = 'units_pending'
    REVIEW_PENDING = 'review_pending'
    #: ORDER-CANCEL-01: a cancelled order's fulfilment does not move.
    ORDER_CANCELLED = 'order_cancelled'

    def __init__(self, detail: str, *, pending: dict, code: str = UNITS_PENDING):
        super().__init__(detail)
        self.detail = detail
        self.pending = pending
        self.code = code


def allowed_fulfillment_statuses(user, company) -> tuple[str, ...]:
    """
    Every status `user` may set on an order of `company`, in the model's own order.

      Legacy bridge → the historical role rule: `inventory` gets the four
                      goods-moving states; sales, admin and superadmin get all.
      Anyone else   → `sales.orders.manage` in THIS company gets all of them, and
                      without it, none. The global role is not consulted.

    Empty is an honest answer: someone who may read an order but not manage it
    has no transition to offer, and a row of buttons that all fail says the
    opposite.

    Returned to the clients so neither the app nor the web panel carries a second
    copy of this table. A UI that computes its own allowed transitions drifts
    from the server the first time the rule changes — and the drift shows up as
    a button that fails, which reads as a broken app rather than a rule. The
    server still re-checks: change_fulfillment_status() asks this same function.
    """
    if company is None:
        return ()
    if uses_legacy_bridge(user, company):
        if get_user_role(user) == UserProfile.ROLE_INVENTORY:
            return tuple(s for s in ALL_FULFILLMENT_STATUSES if s in INVENTORY_ALLOWED_FULFILLMENT)
        return ALL_FULFILLMENT_STATUSES
    if has_capability(user, company, CAP_ORDERS_MANAGE):
        return ALL_FULFILLMENT_STATUSES
    return ()


def change_fulfillment_status(
    *, order: Order, new_status: str, actor, company, note: str = '', request=None,
) -> Order:
    """
    Move one order's fulfilment state and record who did it.

    The caller has already established that `actor` may manage orders in
    `company` and resolved `order` from visible_orders(actor, company), so it is
    an order this actor may see. What is decided HERE is the narrower question
    of whether this particular actor may set this particular status.

    The write and the audit entry share a transaction: a status change nobody
    can account for is worse than a status change that did not happen.
    """
    allowed = allowed_fulfillment_statuses(actor, company)
    if new_status not in allowed:
        if allowed:
            # Only the legacy inventory rule yields a partial list.
            detail = (
                f'El rol de inventario no puede establecer el estado "{new_status}". '
                f'Estados permitidos: {", ".join(sorted(allowed))}.'
            )
        else:
            detail = 'No tienes permiso para cambiar el estado de despacho en esta empresa.'
        raise FulfillmentNotAllowed(detail)

    from . import unit_identification

    leaving = new_status in unit_identification.LEAVING
    with transaction.atomic():
        # SERIAL-PICK-01. As at the till: a device does not leave without
        # somebody saying which one it is. Getting an order ready is free;
        # sending it or handing it over is not. Decided with the order LOCKED —
        # the same lock identifying or taking back a device holds — so nobody
        # un-names a device between this check and the write below.
        #
        # ORDER-CANCEL-01: EVERY change takes the lock now, because a
        # cancellation takes it too and a cancelled order does not move. Read
        # under the lock, not from `order`, which may have been read before
        # somebody cancelled it.
        current = (
            Order.objects.select_for_update(of=('self',)).filter(pk=order.pk)
            .values_list('status', 'fulfillment_status').first()
        )
        if current is not None and current[0] == Order.Status.CANCELLED:
            raise FulfillmentBlocked(
                'Este pedido está cancelado: su estado de despacho ya no cambia.',
                pending={}, code=FulfillmentBlocked.ORDER_CANCELLED,
            )
        previous = current[1] if current is not None else order.fulfillment_status
        # CLIENT-SEC-01. Only where the company asked for it: an order waiting
        # for its review does not leave — nor is its buyer told to come for it —
        # until somebody has decided. Asked with the order locked, like the
        # devices below.
        if new_status in HELD_WHILE_REVIEW_PENDING and order_risk.review_holds_dispatch(order, company):
            raise FulfillmentBlocked(
                order_risk.REVIEW_PENDING_DETAIL, pending={}, code=FulfillmentBlocked.REVIEW_PENDING,
            )
        if leaving:
            pending = unit_identification.pending_units(order)
            if pending:
                missing = sum(pending.values())
                raise FulfillmentBlocked(
                    f'Falta identificar {missing} equipo(s) de este pedido. Escanea o escribe su IMEI o serie '
                    f'antes de marcarlo como enviado o entregado.',
                    pending=pending,
                )
        order.fulfillment_status = new_status
        order.save(update_fields=['fulfillment_status'])
        if leaving:
            unit_identification.mark_left(order)
        AdminAuditLog.log(
            actor=actor,
            action='order_fulfillment_status_changed',
            target_type='order',
            target_id=order.pk,
            company=company,
            metadata={
                'order_id': order.pk,
                'customer_email': order.customer_email,
                'old_fulfillment_status': previous,
                'new_fulfillment_status': new_status,
                'note': note[:200] if note else '',
            },
            request=request,
        )

        # M12B — inside the transaction, so the notice is as durable as the
        # status change and disappears with it on a rollback. Emitted from the
        # ONE place that actually moves a fulfillment status, so a future
        # second caller gets the notification for free instead of forgetting it.
        emit_fulfillment_changed(order, new_status)

    return order
