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

WHAT THIS DELIBERATELY DOES NOT DO

No email. No stock movement. No sales note. The legacy view does none of those
either, and this extraction is not the place to add behaviour: changing what a
status change *means* would be a business decision, not a refactor.
"""
from django.db import transaction

from .commerce_notifications import emit_fulfillment_changed
from .models import AdminAuditLog, Order, UserProfile
from .permissions import get_user_role

# Warehouse staff move goods; they do not cancel sales.
#
# Preserved EXACTLY from `admin_views._INVENTORY_ALLOWED_FULFILLMENT`. The
# restriction is keyed on the legacy `UserProfile.role` rather than on a
# capability, which is not how the rest of the system reasons any more — but it
# is the rule in force today, and quietly widening what an inventory user may do
# is not something a refactor gets to decide.
INVENTORY_ALLOWED_FULFILLMENT = frozenset([
    Order.FulfillmentStatus.PREPARING,
    Order.FulfillmentStatus.READY_FOR_PICKUP,
    Order.FulfillmentStatus.SHIPPED,
    Order.FulfillmentStatus.DELIVERED,
])

ALL_FULFILLMENT_STATUSES = tuple(choice[0] for choice in Order.FulfillmentStatus.choices)


class FulfillmentNotAllowed(Exception):
    """This actor may not set this status. Carries the message the caller shows."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def allowed_fulfillment_statuses(user) -> tuple[str, ...]:
    """
    Every status `user` may set, in the model's own order.

    Returned to the client so a native app does not have to carry a second copy
    of this table. A UI that computes its own allowed transitions is a UI that
    drifts from the server the first time the rule changes — and the drift shows
    up as a button that fails, which reads as a broken app rather than a rule.

    This is presentation input, not authorisation: `change_fulfillment_status`
    re-checks regardless of what the client did with it.
    """
    if get_user_role(user) == UserProfile.ROLE_INVENTORY:
        return tuple(s for s in ALL_FULFILLMENT_STATUSES if s in INVENTORY_ALLOWED_FULFILLMENT)
    return ALL_FULFILLMENT_STATUSES


def change_fulfillment_status(
    *, order: Order, new_status: str, actor, company, note: str = '', request=None,
) -> Order:
    """
    Move one order's fulfilment state and record who did it.

    The caller has already established that `actor` may manage orders in
    `company` and that `order` belongs to it. What is decided HERE is the
    narrower question of whether this particular actor may set this particular
    status.

    The write and the audit entry share a transaction: a status change nobody
    can account for is worse than a status change that did not happen.
    """
    allowed = allowed_fulfillment_statuses(actor)
    if new_status not in allowed:
        raise FulfillmentNotAllowed(
            f'El rol de inventario no puede establecer el estado "{new_status}". '
            f'Estados permitidos: {", ".join(sorted(allowed))}.'
        )

    previous = order.fulfillment_status
    with transaction.atomic():
        order.fulfillment_status = new_status
        order.save(update_fields=['fulfillment_status'])
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
