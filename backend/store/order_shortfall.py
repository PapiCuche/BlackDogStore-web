"""
F1 · DELIVERY-SHORTFALL-GUARD — a paid order whose stock never left the shelf.

WHERE THE SHORTFALL COMES FROM (INV-04, `docs/adr-catalog-commercial.md` §11)

A web payment arrives after the money is captured. If the shelf cannot cover a
quantity line by then — the till sold the last unit, or another web order paid
first — the sale exit is not written (`record_sale_stock_movements`,
`strict=False`) and the order is PAID with a shortfall. That stays exactly as
it is: refusing the payment, charging again, refunding or reserving stock are
other decisions (D-B), not this one.

WHAT THIS MODULE ADDS

  * It is not handed over. «Listo para retiro», «Enviado» and «Entregado» —
    the moment the buyer is told to come, and the two in which the goods leave —
    wait until the derived shortfall is zero. The check lives in
    `change_fulfillment_status`, the one writer of a handover status, and is
    read there with the order row LOCKED: the reprocess takes the same lock, so
    a delivery and a reprocess of one order never interleave.
  * Somebody is told. When a payment leaves a shortfall, the staff who can act
    on it get ONE notice and the order gets ONE audit entry under its company —
    after the payment commits, never inside it, and never able to undo it.
  * Every refused handover is audited: who tried, which order, what was missing.

THE SHORTFALL IS NEVER STORED. It is derived from the order's lines and its
sale exits (`inventory_services.shortfall_rows`): it heals by itself once the
shelf is restocked and the exit reprocessed, and cannot drift from the Kardex.
"""
from __future__ import annotations

import logging

from django.db import transaction

from .models import AdminAuditLog, Order

logger = logging.getLogger(__name__)

CODE = 'stock_shortfall'
#: Written once, when a payment leaves an order short (actor: the system).
ACTION_FLAGGED = 'order_stock_shortfall_flagged'
#: Written on every refused handover of a short order (actor: who tried).
ACTION_BLOCKED = 'order_handover_blocked_shortfall'

#: The fulfilment states a short order does not reach: the one that tells the
#: buyer to come for the goods, and the two in which they leave the shop.
HANDOVER_STATUSES = frozenset([
    Order.FulfillmentStatus.READY_FOR_PICKUP,
    Order.FulfillmentStatus.SHIPPED,
    Order.FulfillmentStatus.DELIVERED,
])


def lines(order, *, status=None) -> list[dict]:
    """
    What is still owed by the shelf, per product — empty unless the order is PAID.

    `status` is the LOCKED status when the caller holds the lock: the one on
    `order` may have been read before somebody cancelled it.
    """
    from .inventory_services import shortfall_rows

    if (order.status if status is None else status) != Order.Status.PAID:
        return []
    return shortfall_rows(order)


def describe(rows) -> str:
    """«Funda» (faltan 2 de 2); «Cargador» (falta 1 de 3)."""
    parts = []
    for row in rows:
        verb = 'falta' if row['shortfall'] == 1 else 'faltan'
        parts.append(f'«{row["product_name"]}» ({verb} {row["shortfall"]} de {row["ordered"]})')
    return '; '.join(parts)


def _names(rows) -> str:
    return ', '.join(f'«{row["product_name"]}»' for row in rows)


def _advice(rows) -> str:
    """
    What resolves each line — said only where it is true.

      * nothing out yet, plain units: restock and «Reintentar salida de stock»;
      * nothing out yet, the device the buyer chose (DC2): other devices never
        cover it — only that device freed up again, else cancel;
      * part of the product already out (one exit per order and product), or a
        chosen device beside plain units of it: the reprocess cannot complete
        it at all — the way out is «Cancelar pedido», with the refund on record.
    """
    restock = [r for r in rows if r.get('reprocessable', True) and not r.get('chosen_unit')]
    chosen = [r for r in rows if r.get('reprocessable', True) and r.get('chosen_unit')]
    stuck = [r for r in rows if not r.get('reprocessable', True)]
    parts = []
    if restock:
        parts.append(f'Para {_names(restock)}: repón el stock en la sucursal de despacho y usa '
                     '«Reintentar salida de stock».' if (chosen or stuck) else
                     'Repón el stock en la sucursal de despacho y usa «Reintentar salida de stock».')
    if chosen:
        parts.append(
            f'El equipo elegido por el cliente ({_names(chosen)}) ya no está disponible y reponer otro equipo no lo '
            'resuelve: si ese mismo equipo vuelve a estar libre, usa «Reintentar salida de stock»; si no, '
            'cancela el pedido con «Cancelar pedido» y registra el reembolso.'
        )
    if stuck:
        parts.append(
            f'Para {_names(stuck)} ya salió una parte del stock y la salida no se completa reintentando: '
            'cancela el pedido con «Cancelar pedido» y registra el reembolso.'
        )
    return ' '.join(parts)


def reprocess_can_help(rows) -> bool:
    """Whether «Reintentar salida de stock» can complete at least one line."""
    return any(row.get('reprocessable', True) for row in rows)


def handover_detail(rows, label) -> str:
    """The refusal a person reads when a handover of a short order is asked for."""
    return (
        f'No se puede marcar «{label}»: este pedido está pagado, pero falta stock de lo vendido: '
        f'{describe(rows)}. {_advice(rows)}'
    )


def blocker_detail(rows) -> str:
    """The same reason, as the order detail lists it under the next step."""
    return f'Falta stock de lo vendido: {describe(rows)}. {_advice(rows)}'


NOT_PAID = 'not_paid'


def not_paid_detail(status, label) -> str:
    """
    F1 review M2. Only the gateway's confirmation makes a web order PAID and a
    till sale is born PAID: nothing in this code hands goods over before the
    money, so an order that is not PAID is prepared, never announced or handed over.
    """
    status_label = dict(Order.Status.choices).get(status, status)
    return (f'No se puede marcar «{label}»: este pedido no está pagado (figura «{status_label}»). '
            'Se puede preparar, pero no anunciar ni entregar hasta que el pago esté confirmado.')


def handover_hold(order, *, status=None) -> str:
    """Why `order` may not stand at a handover status now: 'not_paid', 'stock_shortfall' or ''."""
    status = order.status if status is None else status
    if status != Order.Status.PAID:
        return NOT_PAID
    return CODE if lines(order, status=status) else ''


def _audit_lines(rows) -> list[dict]:
    return [
        {key: row.get(key) for key in ('product_id', 'product_name', 'ordered', 'fulfilled', 'shortfall', 'chosen_unit',
                                     'reprocessable')}
        for row in rows
    ]


def audit_blocked(*, order, actor, company, previous_status, requested_status, rows, request=None):
    """One entry per refused handover. Its own write: the refused move rolled back."""
    AdminAuditLog.log(
        actor=actor,
        action=ACTION_BLOCKED,
        target_type='order',
        target_id=order.pk,
        company=company,
        metadata={
            'order_id': order.pk,
            'branch_id': order.fulfillment_branch_id,
            'fulfillment_status': previous_status,
            'requested_status': requested_status,
            'lines': _audit_lines(rows),
        },
        request=request,
    )


# ---------------------------------------------------------------------------
# Telling somebody — once, after the payment commits
# ---------------------------------------------------------------------------

def schedule_announcement(order) -> None:
    """
    Ask for the announcement once the current transaction commits.

    Called where a shortfall is flagged (`inventory_services._flag_stock_shortfall`),
    which is inside the payment's transaction. Nothing is decided or written
    there: after the commit the shortfall is read again, on a PAID order, and
    only then announced. A rollback announces nothing; a failure here cannot
    reach the payment (`robust`, and `announce` swallows its own errors).
    """
    if getattr(order, '_shortfall_announcement_scheduled', False):
        return
    order._shortfall_announcement_scheduled = True
    order_id = order.pk
    transaction.on_commit(lambda: announce(order_id), robust=True)


def announce(order_id):
    """Announce `order_id`'s shortfall if it has one and nobody announced it yet. Never raises."""
    try:
        return _announce(order_id)
    except Exception:  # noqa: BLE001 — a notice must never cost a sale
        logger.exception('no se pudo avisar el faltante de stock del pedido %s', order_id)
        return None


def _announce(order_id):
    from . import notification_events as events
    from .commerce_notifications import emit_order_stock_shortfall
    from .models import NotificationEvent

    with transaction.atomic():
        # Under the order's lock, so two announcers (a replay, a reprocess)
        # serialise and the second sees the first's notice.
        order = (
            Order.objects.select_for_update(of=('self',)).select_related('company')
            .filter(pk=order_id).first()
        )
        if order is None:
            return None
        rows = lines(order)
        if not rows:
            return None
        # The marker is the NOTICE itself, not the audit entry: a notice that
        # could not be written leaves nothing behind, and the next flag of this
        # order (a reprocess still short) tries again.
        key = events.event_key(events.COMMERCE_ORDER_STOCK_SHORTFALL, 'order', order.pk)
        if NotificationEvent.objects.filter(company=order.company, event_key=key).exists():
            return None
        event = emit_order_stock_shortfall(order, rows)
        if event is None:
            return None
        AdminAuditLog.log(
            actor=None,
            action=ACTION_FLAGGED,
            target_type='order',
            target_id=order.pk,
            company=order.company,
            metadata={
                'order_id': order.pk,
                'branch_id': order.fulfillment_branch_id,
                'lines': _audit_lines(rows),
                'notification_event_id': event.pk,
            },
        )
        return event
