"""
The event catalogue — M12B.

WHY A REGISTRY AND NOT STRING LITERALS. An event type appears in at least four
places: where it is emitted, where its recipients are resolved, where its text
is written, and in the tests. Four spellings of `service.quote.available` is
three bugs waiting, and the fourth one is silent — a typo emits an event nobody
resolves recipients for, so nothing happens and nothing complains.

EVERY EVENT HERE IS BACKED BY A REAL DOMAIN TRANSITION. Nothing was invented to
round out the list: if the lifecycle has no `waiting_parts`, there is no
`SERVICE_WAITING_PARTS`. Events for modules that do not exist yet — wallet, for
instance — are absent, not stubbed.
"""

from __future__ import annotations

# --- servicio técnico ------------------------------------------------------

SERVICE_ORDER_CREATED = 'service.order.created'
SERVICE_ASSIGNMENT_CREATED = 'service.assignment.created'
SERVICE_STATUS_CHANGED = 'service.status.changed'
SERVICE_QUOTE_AVAILABLE = 'service.quote.available'
SERVICE_QUOTE_APPROVED = 'service.quote.approved'
SERVICE_QUOTE_REJECTED = 'service.quote.rejected'
SERVICE_READY_FOR_PICKUP = 'service.ready_for_pickup'
SERVICE_DELIVERED = 'service.delivered'
# M12B.1 — the payment ledger did not exist when this registry was written.
#
# `recorded` reaches the customer; `reversed` deliberately does NOT. A reversal
# means "this row was written in error" — a till keyed 500 instead of 50 — and
# the code that performs it says so in as many words. Whether cash went back
# over a counter is between the shop and the person standing at it, so there is
# no sentence a customer could be sent that is both informative and true.
# "Tu pago fue reembolsado" would be the lie; a vaguer wording would alarm
# without informing. The balance they can already read stays authoritative.
SERVICE_PAYMENT_RECORDED = 'service.payment.recorded'
SERVICE_PAYMENT_REVERSED = 'service.payment.reversed'

# --- comercio --------------------------------------------------------------

COMMERCE_PAYMENT_CONFIRMED = 'commerce.payment.confirmed'
COMMERCE_FULFILLMENT_READY = 'commerce.fulfillment.ready'
COMMERCE_FULFILLMENT_SHIPPED = 'commerce.fulfillment.shipped'
COMMERCE_FULFILLMENT_DELIVERED = 'commerce.fulfillment.delivered'
COMMERCE_ORDER_CANCELLED = 'commerce.order.cancelled'

# M12C. The only event a PERSON causes directly rather than a business change.
# It lives in the same catalogue as the rest because the inbox is the same
# inbox: what differs is the origin, and `Notification.source` records that.
COMMUNICATIONS_ANNOUNCEMENT_PUBLISHED = 'communications.announcement.published'

ALL_EVENT_TYPES = frozenset({
    SERVICE_ORDER_CREATED,
    SERVICE_ASSIGNMENT_CREATED,
    SERVICE_STATUS_CHANGED,
    SERVICE_QUOTE_AVAILABLE,
    SERVICE_QUOTE_APPROVED,
    SERVICE_QUOTE_REJECTED,
    SERVICE_READY_FOR_PICKUP,
    SERVICE_DELIVERED,
    SERVICE_PAYMENT_RECORDED,
    SERVICE_PAYMENT_REVERSED,
    COMMERCE_PAYMENT_CONFIRMED,
    COMMERCE_FULFILLMENT_READY,
    COMMERCE_FULFILLMENT_SHIPPED,
    COMMERCE_FULFILLMENT_DELIVERED,
    COMMERCE_ORDER_CANCELLED,
    COMMUNICATIONS_ANNOUNCEMENT_PUBLISHED,
})

# WHICH EVENTS EARN AN E-MAIL.
#
# Not all of them, deliberately. In-app is cheap and granular — a status change
# belongs there. E-mail interrupts somebody, so it is reserved for the events a
# person actually needs to act on or would want to be told about away from the
# screen. A customer who gets a mail for every internal transition stops
# reading them, which is worse than sending none.
EMAIL_WORTHY_EVENTS = frozenset({
    SERVICE_QUOTE_AVAILABLE,
    SERVICE_READY_FOR_PICKUP,
    COMMERCE_FULFILLMENT_READY,
    COMMERCE_FULFILLMENT_SHIPPED,
})


def event_key(event_type: str, target_type: str, target_id, discriminator=None) -> str:
    """
    The idempotency key, derived from the ENTITY and never from the request.

    A webhook replayed ten times is ten requests about one payment, so the key
    must describe the payment. `discriminator` exists for the case where the
    same entity legitimately produces the event twice — a quote published,
    revised, and published again — and there the revision IS part of what
    happened.
    """
    parts = [event_type, target_type, str(target_id)]
    if discriminator is not None:
        parts.append(str(discriminator))
    return ':'.join(parts)
