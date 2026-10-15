"""
ORDER-CANCEL-01 — cancelling a PAID web order from the admin panel.
COMMERCE-ADMIN-UX-01 (owner, 2026-10-15) — also an UNPAID web order and a TILL
sale. Three modes (`mode_of`), one service (`cancel_order`), one lock:

  unpaid  an online order still waiting for its payment. Nothing was sold:
          no stock comes back, no income is counted. The devices its buyer
          held (DC2) go back to the shelf now. A payment that arrives later
          is filed as money to give back (`views._confirm`), never a sale.
  paid    the rules below (D1–D4), unchanged.
  pos     a till sale, born paid and handed over. Annulled only when the
          cashier confirms the goods are back in the shop (`goods_returned`);
          then exactly like a paid order: stock back once at the sale's cost,
          the internal note voided, a live boleta/factura kept and flagged.
          No time limit: the owner set none, and none is invented here.

  In the paid modes the seller's accrued commission is voided (the model's
  own rule: «it may be voided when the goods come back»), and a refund owed
  may be RECORDED as pending in the same transaction (`order_refunds`).

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
    SalesChannel, SalesCommission, SalesNote, StockMovement, StockUnit, UserProfile,
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
    _BAD_REQUEST = frozenset(['reason_required', 'reason_too_long', 'goods_return_required'])

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


MODE_UNPAID, MODE_PAID, MODE_POS = 'unpaid', 'paid', 'pos'


def mode_of(order) -> str | None:
    """Which cancellation `order` is open to, by its channel and payment; None when none is."""
    if order.sales_channel == SalesChannel.POS:
        return MODE_POS if order.paid and order.status == Order.Status.PAID else None
    if order.status == Order.Status.PENDING_PAYMENT and not order.paid:
        return MODE_UNPAID
    if order.status == Order.Status.PAID and order.paid:
        return MODE_PAID
    return None


def blockers(order) -> list[dict]:
    """
    Why `order` cannot be cancelled, in the order a person needs to read it.
    Empty when it can. The same answer the service gives on the locked row.
    A till sale's «goods are back» is asked when it is cancelled, not here.
    """
    if order.status == Order.Status.CANCELLED:
        return [_blocker('already_cancelled', 'Este pedido ya está cancelado.')]
    mode = mode_of(order)
    if mode is None:
        return [_blocker(
            'not_paid',
            'Sólo se cancela un pedido pendiente de pago o pagado. '
            f'Este pedido figura como «{order.get_status_display().lower()}».')]
    if mode == MODE_PAID and (order.fulfillment_status not in GOODS_IN_THE_SHOP or ever_left(order)):
        return [_blocker(
            'goods_left',
            'El pedido ya salió de la tienda (fue enviado o entregado): requiere devolución, no cancelación.')]
    return []


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


def cancel_order(order, user, reason, *, goods_returned=False, refund_method=None, request=None) -> OrderCancellation:
    """
    Cancel `order` — already resolved through the caller's scoped queryset —
    and return its cancellation. Raises `OrderCancelError` (or, for the
    refund asked with it, `order_refunds.RefundError`) when a rule says no;
    nothing is written then.

    `goods_returned`: the cashier's word that a till sale's goods are back.
    `refund_method`: record, with the cancellation, a PENDING refund of
    everything collected, to be given back that way. Paid modes only.
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
        mode = mode_of(locked)
        if mode == MODE_POS and goods_returned is not True:
            raise OrderCancelError(
                'goods_return_required',
                'Confirma que la mercadería volvió al local antes de anular la venta.')

        previous_status, previous_fulfillment = locked.status, locked.fulfillment_status
        # DC3 before the shelf: the supply module's lock order is order, offers, shelf.
        supply_released = _release_supply(locked, actor, reason, request)
        holds_released = _release_holds(locked) if mode == MODE_UNPAID else 0
        movements, units_returned, already_returned = _restore_stock(locked, actor, reason, request)
        note_voided = _void_sales_note(locked, actor, reason)
        commission_voided = bool(SalesCommission.objects.filter(
            order=locked, status=SalesCommission.STATUS_ACCRUED,
        ).update(status=SalesCommission.STATUS_VOIDED, updated_at=timezone.now()))
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
            was_paid=locked.paid, goods_returned=mode == MODE_POS, holds_released=holds_released,
        )
        refund = None
        if refund_method:
            from . import order_refunds

            if order_refunds.summary(locked)['refundable'] > 0:
                refund = order_refunds.record_pending(locked, actor, method=refund_method, request=request)
        AdminAuditLog.log(
            actor=actor, action='order_cancelled', target_type='order', target_id=locked.pk,
            metadata={
                'order_id': locked.pk,
                'branch_id': locked.fulfillment_branch_id,
                'mode': mode,
                'channel': locked.sales_channel,
                'reason': reason,
                'previous_status': previous_status,
                'new_status': locked.status,
                'previous_fulfillment_status': previous_fulfillment,
                'new_fulfillment_status': locked.fulfillment_status,
                'movements': [movement.pk for movement in movements],
                'units_returned': units_returned,
                # Devices of this sale given back before (a return): not restored twice.
                'units_already_returned': already_returned,
                'holds_released': holds_released,
                'goods_returned': mode == MODE_POS,
                'fiscal_pending': document is not None,
                'fiscal_document': label,
                'sales_note_voided': note_voided,
                'commission_voided': commission_voided,
                # DC3: on-order lines that stopped waiting for their supplier.
                'supply_released': supply_released,
                # Said, so nobody reads a cancellation as a refund: the money
                # taken stays recorded as taken.
                'paid_kept': locked.paid,
                'refund_id': refund.pk if refund else None,
            },
            request=request, company=locked.company,
        )
    return cancellation


#: ORDER-CANCEL-01's name for it. Every mode goes through the same rules now.
cancel_paid_order = cancel_order

HOLD_RELEASE_REASON = 'Apartado web liberado: pedido cancelado'


def _release_holds(order) -> int:
    """
    DC2. The devices a buyer's unpaid order held go back to the shelf now,
    each with its Kardex line — not when their clock runs out. Locked like
    every release: the shelf, then the device; a device that moved meanwhile
    (sold, released by a sweep) is left to whoever moved it.
    """
    from . import inventory_services as inventory
    from . import unit_hold_services as holds
    from .stock_unit_services import StockUnitError

    released = 0
    held = (
        StockUnit.objects.filter(hold_order=order, status=StockUnit.Status.RESERVED)
        .exclude(hold_key='').select_related('branch', 'product').order_by('pk')
    )
    for unit in held:
        if unit.branch_id is None:
            continue
        inventory._locked_branch_stocks(unit.branch, [unit.product])
        current = StockUnit.objects.select_for_update().filter(pk=unit.pk).first()
        if (current is None or current.status != StockUnit.Status.RESERVED or not current.hold_key
                or current.hold_order_id != order.pk):
            continue
        try:
            with transaction.atomic():
                holds._release(current, reason=HOLD_RELEASE_REASON)
        except StockUnitError:
            continue
        released += 1
    return released


def _release_supply(order, actor, reason, request) -> list[dict]:
    """
    DEVICE-COMMERCE-01 · DC3. Every on-order line still waiting for its
    supplier stops waiting: «No se pudo abastecer», with the cancellation's
    reason. A line committed against an offer gives the offer back what it
    committed — never more than the offer holds — exactly as the staff's
    `supply_services.fail_line` does, audited the same way. A line «por
    confirmar» committed nothing. A RECEIVED line is goods on the shelf: its
    sale exit comes back in `_restore_stock`. A line not yet paid ('')
    committed nothing either.

    Called with the order locked; offers are locked next (order, offers, shelf).
    """
    from django.db.models import F

    from .models import OrderItem, SupplierOffer
    from .supply_services import _audit

    released = []
    lines = (
        OrderItem.objects.select_for_update()
        .filter(order=order, supply_origin=OrderItem.SUPPLY_SUPPLIER,
                supply_status__in=(OrderItem.SUPPLY_PENDING, OrderItem.SUPPLY_TO_CONFIRM))
        .order_by('supplier_offer_id', 'pk')
    )
    for line in lines:
        quantity = 0
        if line.supply_status == OrderItem.SUPPLY_PENDING and line.supplier_offer_id:
            offer = SupplierOffer.objects.select_for_update().filter(pk=line.supplier_offer_id).first()
            if offer is not None:
                quantity = min(offer.committed_quantity, line.quantity)
                if quantity:
                    SupplierOffer.objects.filter(pk=offer.pk).update(
                        committed_quantity=F('committed_quantity') - quantity, updated_at=timezone.now())
        OrderItem.objects.filter(pk=line.pk).update(
            supply_status=OrderItem.SUPPLY_FAILED, supply_note=f'Pedido cancelado: {reason}'[:300])
        _audit('order_supply_failed', company=order.company, actor=actor, target_type='order',
               target_id=order.pk, request=request, line_id=line.pk, reason=reason, released=quantity,
               order_status=Order.Status.CANCELLED, source='order_cancellation')
        released.append({'line_id': line.pk, 'offer_id': line.supplier_offer_id, 'quantity': quantity,
                         'previous_status': line.supply_status})
    return released


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
    from .sales_note_control import REASON_ORDER_CANCELLED, mark_void

    note = SalesNote.objects.select_for_update().filter(order=order, status=SalesNote.STATUS_ISSUED).first()
    if note is None:
        return False
    # SALES-NOTE-CONTROL-01: the same void record the master's void writes,
    # with its own reason type. The metadata #119 kept stays as it was.
    mark_void(note, actor=actor, reason_type=REASON_ORDER_CANCELLED, reason=reason, extra_metadata={
        'voided_at': timezone.now().isoformat(),
        'voided_by': getattr(actor, 'pk', None),
        'void_reason': reason,
        'voided_by_order_cancellation': True,
    })
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
        'was_paid': cancellation.was_paid,
        'goods_returned': cancellation.goods_returned,
        'holds_released': cancellation.holds_released,
    }


def is_cancelled(order) -> bool:
    return order.status == Order.Status.CANCELLED


__all__ = [
    'OrderCancelError', 'blockers', 'cancel_order', 'cancel_paid_order', 'clean_reason', 'fiscal_label',
    'is_cancelled', 'live_fiscal_document', 'may_cancel', 'mode_of', 'payload',
]
