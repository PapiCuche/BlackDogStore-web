"""
E-commerce lifecycle → notification events — M12B.

WHAT THIS MODULE IS ALLOWED TO DO: observe decisions that have already been
made and record that they happened.

WHAT IT MUST NOT DO: influence them. Nothing here validates a payment, moves
stock, clears a cart or decides a status. The payment path has its own
signature check, its own amount and currency validation and its own
idempotency, and M12B hooks in AFTER all of that has concluded — never in the
middle of it, and never as a condition of it.

WHY THE CONFIRMATION E-MAIL IS NOT SENT FROM HERE. `email_services` already
sends it, with the receipt PDF, the tenant's identity and its own
`confirmation_email_sent_at` idempotency flag. Emitting a second one through
the notification channel would double every order confirmation on the platform.
So `commerce.payment.confirmed` is deliberately NOT email-worthy: it
contributes the durable in-app record, and the existing mail keeps being the
mail.
"""

from __future__ import annotations

import logging

from . import notification_events as ev
from . import notification_services as notif
from .models import Notification

logger = logging.getLogger(__name__)


def _safely(fn, *args, **kwargs):
    """A notification failure must never undo a sale."""
    try:
        return fn(*args, **kwargs)
    except Exception:  # noqa: BLE001
        logger.exception('no se pudo emitir la notificación de comercio')
        return None


def _customer_of(order):
    """
    The customer row that owns this order, or None.

    None is a normal answer: guest checkout is a supported contract, and a
    buyer without an account has nowhere to receive an in-app notice. The
    e-mail path still reaches them. Inventing a `User` so the inbox has
    somewhere to write would create an account nobody asked for.
    """
    return getattr(order, 'customer', None)


def emit_payment_confirmed(order):
    return _safely(_emit_payment_confirmed, order)


def _emit_payment_confirmed(order):
    customer = _customer_of(order)
    if customer is None:
        return None
    return notif.emit(
        company=order.company,
        event_type=ev.COMMERCE_PAYMENT_CONFIRMED,
        # Keyed on the ORDER, not the notification that announced it: ten
        # replayed IPNs are ten messages about one payment.
        event_key=ev.event_key(ev.COMMERCE_PAYMENT_CONFIRMED, 'order', order.pk),
        title='Pago confirmado',
        body=f'Tu pedido #{order.pk} fue pagado correctamente.',
        target_type='order', target_id=order.pk,
        customers=[customer],
        priority=Notification.Priority.INFO,
    )


#: Fulfillment states that are worth telling the buyer about, and what to say.
#:
#: Read from `Order.FulfillmentStatus` — no invented states. `pending`,
#: `confirmed` and `preparing` are real and deliberately silent: a shop moving
#: an order through its own internal steps is not news for the person waiting.
_FULFILLMENT_MESSAGES = {
    'ready_for_pickup': (
        ev.COMMERCE_FULFILLMENT_READY,
        'Tu pedido está listo para recoger',
        'Puedes pasar a retirarlo.',
    ),
    'shipped': (
        ev.COMMERCE_FULFILLMENT_SHIPPED,
        'Tu pedido fue enviado',
        # NO tracking number, courier or URL: this project has no such fields,
        # and a notification promising a link that does not exist is worse than
        # one that does not promise it.
        'Va en camino.',
    ),
    'delivered': (
        ev.COMMERCE_FULFILLMENT_DELIVERED,
        'Tu pedido fue entregado',
        'Gracias por tu compra.',
    ),
    'cancelled': (
        ev.COMMERCE_ORDER_CANCELLED,
        'Tu pedido fue cancelado',
        'Si tienes dudas, contáctanos.',
    ),
}


def emit_fulfillment_changed(order, fulfillment_status):
    return _safely(_emit_fulfillment_changed, order, fulfillment_status)


def _emit_fulfillment_changed(order, fulfillment_status):
    entry = _FULFILLMENT_MESSAGES.get(fulfillment_status)
    customer = _customer_of(order)
    if entry is None or customer is None:
        return None
    event_type, title, body = entry
    return notif.emit(
        company=order.company,
        event_type=event_type,
        # (order, status): an order that returns to a state does not re-announce
        # it, because from the buyer's side it is the same sentence.
        event_key=ev.event_key(event_type, 'order', order.pk, fulfillment_status),
        title=title,
        body=f'{body} Pedido #{order.pk}.',
        target_type='order', target_id=order.pk,
        customers=[customer],
        priority=Notification.Priority.INFO,
    )
