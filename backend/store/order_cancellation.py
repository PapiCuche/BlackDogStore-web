"""
ORDER-CANCEL-01 — cancelling a PAID web order from the admin panel.

THE OWNER'S RULES (2026-10-14)

  D1  Only an ONLINE order that is PAID and whose goods have NOT left the shop.
      Sent or delivered is a return, a later flow; a till sale and an unpaid
      order are not this flow either.
  D2  The stock comes back, once. Every sale exit of the order returns with its
      own Kardex line (`order_cancel_return`) into the same branch, with the
      order on it; a serialized device goes back to the shelf and stops being
      the device of that order.
  D3  `sales.orders.manage`, scoped like every other order view. On the
      pilot's legacy bridge, the roles that may cancel a sale — warehouse
      staff move goods, they do not cancel sales (the fulfilment rule).
  D4  A live boleta/factura is kept untouched. The cancellation records that
      its credit note or void is pending; nothing fiscal is issued from here.

CANCELLING IS NOT REFUNDING. `paid` stays true — money was taken, and the
payment pipeline reads `paid` to file any later charge as a duplicate instead
of a payment. No `PaymentTransaction` changes and no gateway is called.

ONE TRANSACTION, THE ORDER LOCKED FIRST. Every rule is re-read on the locked
row: a second cancellation, a shipment and a late payment all take the same
lock and therefore queue behind this one (or this one behind them) instead of
racing it. Inside, the module's lock order holds: order, then the shelf row,
then the units (`inventory_services.create_stock_movement`).
"""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from .models import (
    AdminAuditLog, FiscalDocument, FiscalDocumentStatus, FiscalDocumentType, Order, OrderCancellation,
    SalesChannel, SalesNote, StockMovement, StockUnit, UserProfile,
)

CAP_ORDERS_MANAGE = 'sales.orders.manage'

#: Legacy bridge only: who may cancel a sale there. Not `inventory`.
LEGACY_CANCEL_ROLES = frozenset([
    UserProfile.ROLE_SALES, UserProfile.ROLE_ADMIN, UserProfile.ROLE_SUPERADMIN,
])

#: The states in which the goods are still in the shop (D1). «Cancelado
#: operativo» is one of them: that old label moved nothing and sent nothing.
#: The CURRENT label is not enough on its own — see `ever_left`.
GOODS_IN_THE_SHOP = frozenset([
    Order.FulfillmentStatus.PENDING,
    Order.FulfillmentStatus.CONFIRMED,
    Order.FulfillmentStatus.PREPARING,
    Order.FulfillmentStatus.READY_FOR_PICKUP,
    Order.FulfillmentStatus.CANCELLED,
])

REASON_MIN, REASON_MAX = 5, 500


class OrderCancelError(Exception):
    """A cancellation that may not happen. `code` says which rule; `detail` is for the person."""

    #: A request that is wrong in itself (400); everything else is the order's
    #: state (409).
    _BAD_REQUEST = frozenset(['reason_required', 'reason_too_long'])

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail

    @property
    def http_status(self) -> int:
        return 400 if self.code in self._BAD_REQUEST else 409

    def body(self) -> dict:
        return {'detail': self.detail, 'code': self.code}


def clean_reason(reason) -> str:
    text = ' '.join(str(reason or '').split())
    if len(text) < REASON_MIN:
        raise OrderCancelError(
            'reason_required', f'Escribe el motivo de la cancelación (al menos {REASON_MIN} caracteres).')
    if len(text) > REASON_MAX:
        raise OrderCancelError(
            'reason_too_long', f'El motivo no puede pasar de {REASON_MAX} caracteres.')
    return text


def blockers(order) -> list[dict]:
    """
    Why `order` cannot be cancelled, in the order a person needs to read it.
    Empty when it can. The same answer the service gives on the locked row.
    """
    if order.status == Order.Status.CANCELLED:
        return [_blocker('already_cancelled', 'Este pedido ya está cancelado.')]
    found = []
    if not order.paid or order.status != Order.Status.PAID:
        found.append(_blocker(
            'not_paid', 'Sólo se cancela desde aquí un pedido pagado. Este pedido no figura como pagado.'))
    if order.sales_channel == SalesChannel.POS:
        found.append(_blocker(
            'pos_sale', 'Es una venta de mostrador (POS): no se cancela desde el pedido.'))
    if order.fulfillment_status not in GOODS_IN_THE_SHOP or ever_left(order):
        found.append(_blocker(
            'goods_left',
            'El pedido ya salió de la tienda (fue enviado o entregado): requiere devolución, no cancelación.'))
    return found


#: The fulfilment states in which the goods leave the shop.
LEAVING = (Order.FulfillmentStatus.SHIPPED, Order.FulfillmentStatus.DELIVERED)


def ever_left(order) -> bool:
    """
    Whether the goods of `order` have EVER left the shop, whatever its status says now.

    Moving an order back from «enviado» or «entregado» changes a label; the
    goods are still gone. Three records say it, and any of them is enough:

      * a device identified for the order was stamped as gone (`OrderUnit.left_at`);
      * the order was ever moved to sent or delivered — every such move goes
        through `change_fulfillment_status`, which audits the new status;
      * somebody wrote the date it was shipped on its delivery record.
    """
    from .models import OrderDelivery, OrderUnit

    if OrderUnit.objects.filter(order_id=order.pk, left_at__isnull=False).exists():
        return True
    if AdminAuditLog.objects.filter(
        action='order_fulfillment_status_changed', target_type='order', target_id=str(order.pk),
        metadata__new_fulfillment_status__in=list(LEAVING),
    ).exists():
        return True
    return OrderDelivery.objects.filter(order_id=order.pk, shipped_at__isnull=False).exists()


def _blocker(code, detail) -> dict:
    return {'code': code, 'detail': detail}


def may_cancel(user, company) -> bool:
    """Whether `user` holds the authority to cancel orders of `company` (D3)."""
    from .permissions import get_user_role
    from .tenancy import has_capability, uses_legacy_bridge

    if company is None:
        return False
    if uses_legacy_bridge(user, company):
        return get_user_role(user) in LEGACY_CANCEL_ROLES
    return has_capability(user, company, CAP_ORDERS_MANAGE)


def live_fiscal_document(order):
    """The order's boleta/factura that still stands (not rejected), or None."""
    return (
        FiscalDocument.objects
        .filter(order=order, document_type__in=[FiscalDocumentType.INVOICE, FiscalDocumentType.RECEIPT])
        .exclude(status=FiscalDocumentStatus.REJECTED)
        .order_by('pk').first()
    )


def fiscal_label(document) -> str:
    """The document as people read it: «Boleta de venta electrónica B001-12»."""
    if document is None:
        return ''
    return f'{document.get_document_type_display()} {document.series}-{document.number}'[:80]


def cancel_paid_order(order, user, reason, *, request=None) -> OrderCancellation:
    """
    Cancel `order` — already resolved through the caller's scoped queryset —
    and return its cancellation. Raises `OrderCancelError` when a rule says no;
    nothing is written then.
    """
    reason = clean_reason(reason)
    actor = user if getattr(user, 'is_authenticated', False) else None

    with transaction.atomic():
        locked = Order.objects.select_for_update(of=('self',)).get(pk=order.pk)
        refused = blockers(locked)
        if refused:
            raise OrderCancelError(**refused[0])
        if OrderCancellation.objects.filter(order=locked).exists():  # pragma: no cover — status says it first
            raise OrderCancelError('already_cancelled', 'Este pedido ya está cancelado.')

        previous_status, previous_fulfillment = locked.status, locked.fulfillment_status
        movements, units_returned, already_returned = _restore_stock(locked, actor, reason, request)
        note_voided = _void_sales_note(locked, actor, reason)
        document = live_fiscal_document(locked)
        label = fiscal_label(document)

        locked.status = Order.Status.CANCELLED
        locked.fulfillment_status = Order.FulfillmentStatus.CANCELLED
        locked.save(update_fields=['status', 'fulfillment_status'])

        cancellation = OrderCancellation.objects.create(
            order=locked, company=locked.company, cancelled_by=actor, reason=reason,
            previous_status=previous_status, previous_fulfillment_status=previous_fulfillment,
            stock_restored=bool(movements), fiscal_pending=document is not None,
            fiscal_document=label, sales_note_voided=note_voided,
        )
        AdminAuditLog.log(
            actor=actor, action='order_cancelled', target_type='order', target_id=locked.pk,
            metadata={
                'order_id': locked.pk,
                'branch_id': locked.fulfillment_branch_id,
                'reason': reason,
                'previous_status': previous_status,
                'new_status': locked.status,
                'previous_fulfillment_status': previous_fulfillment,
                'new_fulfillment_status': locked.fulfillment_status,
                'movements': [movement.pk for movement in movements],
                'units_returned': units_returned,
                # Devices of this sale given back before (a return): not restored twice.
                'units_already_returned': already_returned,
                'fiscal_pending': document is not None,
                'fiscal_document': label,
                'sales_note_voided': note_voided,
                # Said, so nobody reads a cancellation as a refund.
                'paid_kept': True,
            },
            request=request, company=locked.company,
        )
    return cancellation


def _restore_stock(order, actor, reason, request):
    """
    One entry per sale exit, into the branch it left from (D2). A serialized
    product comes back device by device: the units sold to this order go back
    to the shelf and stop naming it.
    """
    from . import inventory_services as inventory
    from . import unit_identification

    movements, units_returned, already_returned = [], [], []
    exits = (
        StockMovement.objects
        .filter(order=order, movement_type=StockMovement.SALE_EXIT)
        .select_related('product', 'branch').order_by('pk')
    )
    for sale_exit in exits:
        common = dict(
            branch=sale_exit.branch, product_id=sale_exit.product_id,
            movement_type=StockMovement.ORDER_CANCEL_RETURN,
            reason=f'Pedido #{order.pk} cancelado: {reason}', actor=actor, order=order,
            reference_type='order_cancellation', reference_id=order.pk,
        )
        metadata = {'order_id': order.pk, 'sale_exit_id': sale_exit.pk, 'branch_id': sale_exit.branch_id}
        if not sale_exit.product.is_serialized:
            movements.append(inventory.create_stock_movement(
                quantity=sale_exit.quantity, metadata=metadata, **common))
            continue

        # WHICH DEVICES COME BACK: every one still sold to THIS order. The goods
        # never left (`ever_left`), so whatever is out of the shelf for the order
        # is the order's: the unit the payment set aside, the one swapped in for
        # it at the hand-over, or the one handed over after a return (the
        # «second exit» of `unit_identification`). A unit sold to another order
        # does not carry this order, so it is never touched here.
        #
        # A device of this sale already given back (`stock_unit_services.return_unit`
        # writes a `return_entry` naming the order) is on the shelf: it is only
        # counted, never restored a second time.
        given_back = [
            unit_id for metadata in StockMovement.objects.filter(
                movement_type=StockMovement.RETURN_ENTRY, product_id=sale_exit.product_id,
                company_id=order.company_id, metadata__returned_from_order=order.pk,
            ).values_list('metadata', flat=True)
            for unit_id in (metadata or {}).get('unit_ids', [])
        ]
        already_returned.extend(given_back)
        unit_ids = list(
            StockUnit.objects
            .filter(order=order, product_id=sale_exit.product_id, branch=sale_exit.branch,
                    status=StockUnit.Status.SOLD)
            .exclude(pk__in=units_returned)
            .order_by('pk').values_list('pk', flat=True)
        )
        if len(unit_ids) + len(given_back) < sale_exit.quantity:
            missing = sale_exit.quantity - len(given_back)
            raise OrderCancelError(
                'stock_mismatch',
                f'No se encuentran los {missing} equipo(s) de «{sale_exit.product.name}» '
                'vendidos a este pedido. Revisa el inventario antes de cancelarlo.')
        for unit_id in unit_ids:
            # The unit is locked by the movement, after the shelf row.
            movements.append(inventory.create_stock_movement(
                quantity=1, units=[unit_id], expected_unit_status=StockUnit.Status.SOLD,
                metadata=dict(metadata), **common))
            StockUnit.objects.filter(pk=unit_id).update(
                status=StockUnit.Status.AVAILABLE, sold_at=None, order=None, updated_at=timezone.now())
            unit = StockUnit.objects.get(pk=unit_id)
            unit_identification.released_by_return(unit, order.pk, actor=actor, request=request)
            units_returned.append(unit_id)
    return movements, units_returned, already_returned


def _void_sales_note(order, actor, reason) -> bool:
    """The internal note is kept, as void. Its papers say so from now on."""
    note = SalesNote.objects.select_for_update().filter(order=order, status=SalesNote.STATUS_ISSUED).first()
    if note is None:
        return False
    note.status = SalesNote.STATUS_VOID
    note.metadata = {
        **(note.metadata or {}),
        'voided_at': timezone.now().isoformat(),
        'voided_by': getattr(actor, 'pk', None),
        'void_reason': reason,
        'voided_by_order_cancellation': True,
    }
    note.save(update_fields=['status', 'metadata'])
    return True


def payload(order) -> dict | None:
    """The cancellation as the panel shows it, or None."""
    cancellation = (
        OrderCancellation.objects.select_related('cancelled_by').filter(order=order).first()
    )
    if cancellation is None:
        return None
    by = cancellation.cancelled_by
    return {
        'cancelled_at': cancellation.created_at.isoformat(),
        'cancelled_by': (by.get_full_name() or by.username) if by else '',
        'reason': cancellation.reason,
        'previous_status': cancellation.previous_status,
        'previous_fulfillment_status': cancellation.previous_fulfillment_status,
        'stock_restored': cancellation.stock_restored,
        'fiscal_pending': cancellation.fiscal_pending,
        'fiscal_document': cancellation.fiscal_document,
        'sales_note_voided': cancellation.sales_note_voided,
    }


def is_cancelled(order) -> bool:
    return order.status == Order.Status.CANCELLED


__all__ = [
    'OrderCancelError', 'blockers', 'cancel_paid_order', 'clean_reason', 'fiscal_label', 'is_cancelled',
    'live_fiscal_document', 'may_cancel', 'payload',
]
