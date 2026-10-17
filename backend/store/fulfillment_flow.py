"""
ORDER-FULFILLMENT-SAFETY-01 — which dispatch step an order may take next.

Until this phase any status could follow any other: a pickup order could be
«sent», «pending» could jump to «delivered», «sent» could go back to
«pending», and a paid order could be set to «Cancelado operativo» without
being cancelled. Each of those happened in production. This module is the one
place that says what may follow what; `change_fulfillment_status` asks it on
the locked row, and the order detail shows the same answer.

THE PATH DEPENDS ON HOW THE ORDER IS DELIVERED, never on the company:

  pickup    Pendiente → En preparación → Listo para retiro → Entregado
  shipping  Pendiente → En preparación → Enviado → Entregado

How it is delivered is the delivery record's `mode` (what staff confirmed),
else the order's own `delivery_method` (what the buyer chose). A sale at the
till has neither and is collected in the shop.

FORWARD is exactly the next step: no skipping. BACK is exactly one step, with
a reason, and only while the goods have never left (`ever_left`). Once they
have, nothing goes back: what follows is a return or a correction of an
accidental marking (`left_marking`), never a label change.

«Cancelado operativo» is no longer a step anyone sets: only a commercial
cancellation (`order_cancellation`) writes it. An order left there by the old
free selector (paid, not cancelled) may go back to «Pendiente» with a reason,
under the same «never left» rule.

«Confirmado» is legacy: an order in it stands where «Pendiente» stands.
"""
from __future__ import annotations

from .models import Order, OrderDelivery

FS = Order.FulfillmentStatus

PICKUP = 'pickup'
SHIPPING = 'shipping'

PICKUP_STEPS = (FS.PENDING, FS.PREPARING, FS.READY_FOR_PICKUP, FS.DELIVERED)
SHIPPING_STEPS = (FS.PENDING, FS.PREPARING, FS.SHIPPED, FS.DELIVERED)

#: Delivery-record modes in which a carrier takes the goods: «Enviado» needs
#: the carrier and the waybill or tracking code it gave. Own delivery
#: («Delivery local») needs neither — nothing is invented for it.
CARRIER_MODES = frozenset([OrderDelivery.Mode.NATIONAL_ADDRESS, OrderDelivery.Mode.AGENCY_PICKUP])

REASON_MIN, REASON_MAX = 5, 500

_SHIPPING_METHODS = frozenset([Order.DeliveryMethod.DELIVERY_AREQUIPA, Order.DeliveryMethod.NATIONAL_SHIPPING])


def delivery_record(order):
    return OrderDelivery.objects.filter(order_id=order.pk).first()


def delivery_mode(order, record=None) -> str:
    """The delivery-record mode, else the one the order's own method implies ('' for a till sale)."""
    record = delivery_record(order) if record is None else record
    if record is not None and record.mode:
        return record.mode
    return {
        Order.DeliveryMethod.PICKUP_STORE: OrderDelivery.Mode.PICKUP_STORE,
        Order.DeliveryMethod.DELIVERY_AREQUIPA: OrderDelivery.Mode.LOCAL_DELIVERY,
        Order.DeliveryMethod.NATIONAL_SHIPPING: OrderDelivery.Mode.NATIONAL_ADDRESS,
    }.get(order.delivery_method or '', '')


def kind(order, record=None) -> str:
    mode = delivery_mode(order, record)
    if mode in (OrderDelivery.Mode.LOCAL_DELIVERY, OrderDelivery.Mode.NATIONAL_ADDRESS,
                OrderDelivery.Mode.AGENCY_PICKUP):
        return SHIPPING
    return PICKUP


def steps(order, record=None) -> tuple:
    return SHIPPING_STEPS if kind(order, record) == SHIPPING else PICKUP_STEPS


def _position(status, path) -> int | None:
    if status == FS.CONFIRMED:
        status = FS.PENDING
    return path.index(status) if status in path else None


def moves(order, current=None, record=None) -> dict:
    """
    {'forward': status | None, 'back': status | None} from `current` (the
    order's status unless given — the caller passes the locked one).
    """
    from .order_cancellation import ever_left

    current = order.fulfillment_status if current is None else current
    path = steps(order, record)
    forward = back = None
    if current == FS.CANCELLED:
        # The old free selector's leftover: a paid order stopped, not cancelled.
        back = FS.PENDING
    else:
        at = _position(current, path)
        if at is None:
            # A status this path does not have, from before this phase or from
            # a delivery mode changed afterwards. «Enviado» on a pickup order:
            # the goods left, the one way on is to hand them over. «Listo para
            # retiro» on a shipping order: it stands where «En preparación»
            # does — on to «Enviado», or back to «En preparación».
            if current == FS.SHIPPED:
                forward = FS.DELIVERED
            elif current == FS.READY_FOR_PICKUP:
                forward, back = FS.SHIPPED, FS.PREPARING
        else:
            if at + 1 < len(path):
                forward = path[at + 1]
            if at > 0 and path[at - 1] not in (FS.SHIPPED, FS.DELIVERED) and current not in (FS.SHIPPED, FS.DELIVERED):
                back = path[at - 1]
    if back is not None and ever_left(order):
        back = None
    return {'forward': forward, 'back': back}


def logistics_blockers(order, record=None) -> list[dict]:
    """What «Enviado» still needs from the delivery record, for this mode. Empty when nothing."""
    record = delivery_record(order) if record is None else record
    mode = delivery_mode(order, record)
    if mode not in CARRIER_MODES:
        return []
    missing = []
    if record is None:
        return [{'code': 'delivery_record_missing',
                 'detail': 'Registra la entrega (transportista y guía) antes de marcarlo como enviado.'}]
    if not (record.carrier_id or record.carrier_name.strip()):
        missing.append('el transportista')
    if mode == OrderDelivery.Mode.AGENCY_PICKUP and not record.agency_name.strip():
        missing.append('la agencia de destino')
    if not (record.waybill_number.strip() or record.tracking_code.strip()):
        missing.append('el número de guía o de seguimiento')
    if not missing:
        return []
    return [{'code': 'logistics_missing',
             'detail': 'Para marcarlo como enviado falta ' + ', '.join(missing) + ' en la tarjeta «Entrega».'}]


def clean_reason(note) -> str:
    text = ' '.join((note or '').split())
    if len(text) < REASON_MIN:
        return ''
    return text[:REASON_MAX]
