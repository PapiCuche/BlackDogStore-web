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
          No absolute time limit; but (owner, 2026-10-16) an OLD or COMPLEX
          sale is annulled only by a superior — see `superior_conditions`.

  In the paid modes the seller's commission is closed (owner, 2026-10-16):
  an ACCRUED one is voided; a SETTLED one (already paid out) is kept as it is
  and gets ONE compensating `CommissionAdjustment` — the balance to recover
  (`compensate_commission`). A refund owed may be RECORDED as pending in the
  same transaction (`order_refunds`); never confirmed here.

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

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import (
    AdminAuditLog, FiscalDocument, FiscalDocumentStatus, FiscalDocumentType, Order, OrderCancellation,
    SalesChannel, SalesCommission, SalesNote, StockMovement, StockUnit, UserProfile,
)

CAP_ORDERS_MANAGE = 'sales.orders.manage'
#: FINANCIAL-CLOSURE (owner, 2026-10-16): the SUPERIOR authority an old or
#: complex till annulment needs — the company's own administrator (the same
#: authority that confirms a refund). Legacy bridge: admin and superadmin.
CAP_COMPANY_MANAGE = 'company.manage'
LEGACY_SUPERIOR_ROLES = frozenset([UserProfile.ROLE_ADMIN, UserProfile.ROLE_SUPERADMIN])
#: A company without a settings row: the value a new row is created with.
DEFAULT_POS_CANCEL_SIMPLE_DAYS = 7

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
    _BAD_REQUEST = frozenset([
        'reason_required', 'reason_too_long', 'goods_return_required', 'restore_stock_not_allowed',
    ])
    #: The caller may cancel orders, but not THIS one: a superior must (403).
    _FORBIDDEN = frozenset(['superior_required'])

    def __init__(self, code: str, detail: str, conditions=None):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.conditions = conditions

    @property
    def http_status(self) -> int:
        if self.code in self._FORBIDDEN:
            return 403
        return 400 if self.code in self._BAD_REQUEST else 409

    def body(self) -> dict:
        body = {'detail': self.detail, 'code': self.code}
        if self.conditions is not None:
            body['conditions'] = self.conditions
        return body


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


def blockers(order, *, for_return=False) -> list[dict]:
    """
    Why `order` cannot be cancelled, in the order a person needs to read it.
    Empty when it can. The same answer the service gives on the locked row.
    A till sale's «goods are back» is asked when it is cancelled, not here.

    `for_return`: the cancellation that closes a post-delivery return
    (`cancel_for_return`). Its goods did leave — that is the point — and they
    are back through the return, so only the order's own state is asked.
    """
    if order.status == Order.Status.CANCELLED:
        return [_blocker('already_cancelled', 'Este pedido ya está cancelado.')]
    mode = mode_of(order)
    if mode is None:
        return [_blocker(
            'not_paid',
            'Sólo se cancela un pedido pendiente de pago o pagado. '
            f'Este pedido figura como «{order.get_status_display().lower()}».')]
    if for_return:
        return [] if mode in (MODE_PAID, MODE_POS) else [_blocker(
            'not_paid', 'Sólo se cierra la devolución de un pedido pagado.')]
    # POST-DELIVERY-RETURN: while a return is open, its goods come back through
    # it — an annulment now would put them back a second time.
    if return_in_progress(order):
        return [_blocker(
            'return_in_progress',
            'Este pedido tiene una devolución en curso: ciérrala o retírala desde «Devolución».')]
    if mode == MODE_PAID and (order.fulfillment_status not in GOODS_IN_THE_SHOP or ever_left(order)):
        return [_blocker(
            'goods_left',
            'El pedido ya salió de la tienda (fue enviado o entregado): requiere devolución, no cancelación.')]
    return []


def return_in_progress(order) -> bool:
    """Whether `order` has a post-delivery return requested or received (`order_returns`)."""
    from .models import OrderReturn

    return OrderReturn.objects.filter(order_id=order.pk, status__in=OrderReturn.OPEN_STATUSES).exists()


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

    ORDER-FULFILLMENT-SAFETY-01: a move to sent or delivered that a superior
    declared accidental (`left_marking.correct`) no longer counts — that one
    entry, by its id, and nothing else. Its record stays where it was.
    """
    from .models import OrderUnit

    corrected = corrected_event_ids(order)
    # Owner, 2026-10-19: a device stamped by the very click a superior declared
    # accidental did not leave either.
    stamped = OrderUnit.objects.filter(order_id=order.pk, left_at__isnull=False).exclude(
        pk__in=excused_unit_ids(order, corrected))
    if stamped.exists():
        return True
    if leaving_events(order).exclude(pk__in=corrected).exists():
        return True
    # A shipping date counts while it is there AND after somebody blanks it:
    # the delivery audit names every field it changed (ORDER-FULFILLMENT-SAFETY-01).
    return logistics_ever_recorded(order, ('shipped_at',))


#: What on a delivery record says a carrier took the goods.
LOGISTICS_FIELDS = ('shipped_at', 'waybill_number', 'tracking_code')


def logistics_ever_recorded(order, fields=LOGISTICS_FIELDS) -> bool:
    """
    Whether the delivery record of `order` holds, or EVER held, any of `fields`.

    Now: the record itself. Before: `order_delivery_updated` lists the names of
    the fields each edit changed, and a field that changed was set at some
    point — so blanking a waybill or a shipping date does not make it unsaid.
    """
    from .models import OrderDelivery

    record = OrderDelivery.objects.filter(order_id=order.pk).first()
    if record is not None:
        if 'shipped_at' in fields and record.shipped_at is not None:
            return True
        if any(f != 'shipped_at' and (getattr(record, f) or '').strip() for f in fields):
            return True
    # A handful of rows per order; read in Python because a JSON «contains»
    # is not available on every database the project runs on (SQLite locally).
    wanted = set(fields)
    for changed in AdminAuditLog.objects.filter(
        action='order_delivery_updated', target_type='order', target_id=str(order.pk),
    ).values_list('metadata__changed_fields', flat=True):
        if isinstance(changed, list) and wanted.intersection(changed):
            return True
    return False


LEFT_MARKING_CORRECTED = 'order_left_marking_corrected'

#: How long before its audit entry a move to sent/delivered stamps its devices
#: (same transaction, a few milliseconds). Only for entries written before they
#: named the devices themselves (`stamped_units`).
STAMP_WINDOW_SECONDS = 5


def excused_unit_ids(order, corrected=None) -> set[int]:
    """
    Ids of the `OrderUnit`s of `order` whose «gone» stamp a correction explains:
    those stamped by a corrected leaving entry. Such a device never left — for
    `ever_left`, and for identifying, swapping and returning it
    (`unit_identification`). The stamp itself stays on the row.
    """
    corrected = corrected_event_ids(order) if corrected is None else corrected
    if not corrected:
        return set()
    # A device stamped again by a later move nobody corrected did leave then.
    return (units_stamped_by(order, leaving_events(order).filter(pk__in=corrected))
            - units_stamped_by(order, leaving_events(order).exclude(pk__in=corrected)))


def units_stamped_by(order, events) -> set[int]:
    """
    Ids of the `OrderUnit`s of `order` that the leaving entries `events` stamped
    as gone: the ones each entry names, or — for an entry from before it named
    them — the ones stamped in the seconds just before it was written.
    """
    from datetime import timedelta

    from .models import OrderUnit

    found = set()
    for event in events:
        named = (event.metadata or {}).get('stamped_units')
        if isinstance(named, list):
            found.update(pk for pk in named if isinstance(pk, int))
            continue
        found.update(OrderUnit.objects.filter(
            order_id=order.pk, left_at__lte=event.created_at,
            left_at__gte=event.created_at - timedelta(seconds=STAMP_WINDOW_SECONDS),
        ).values_list('pk', flat=True))
    return found


def leaving_events(order):
    """Every audited move of `order` to sent or delivered, oldest first."""
    return AdminAuditLog.objects.filter(
        action='order_fulfillment_status_changed', target_type='order', target_id=str(order.pk),
        metadata__new_fulfillment_status__in=list(LEAVING),
    ).order_by('created_at', 'pk')


def corrected_event_ids(order) -> set[int]:
    """Ids of the leaving entries of `order` a correction names. Only entries of THIS order count."""
    named = AdminAuditLog.objects.filter(
        action=LEFT_MARKING_CORRECTED, target_type='order', target_id=str(order.pk),
    ).values_list('metadata__corrected_audit_id', flat=True)
    ids = {int(v) for v in named if isinstance(v, int) or (isinstance(v, str) and v.isdigit())}
    if not ids:
        return set()
    return set(leaving_events(order).filter(pk__in=ids).values_list('pk', flat=True))


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


def may_cancel_as_superior(user, company) -> bool:
    """Whether `user` may annul an old or complex till sale of `company`: orders AND the company."""
    from .permissions import get_user_role
    from .tenancy import has_capability, uses_legacy_bridge

    if company is None or user is None or not getattr(user, 'is_authenticated', False):
        return False
    if uses_legacy_bridge(user, company):
        return get_user_role(user) in LEGACY_SUPERIOR_ROLES
    return has_capability(user, company, CAP_ORDERS_MANAGE) and has_capability(user, company, CAP_COMPANY_MANAGE)


def simple_days(company) -> int:
    """How many days a till sale may be annulled by whoever cancels orders (the company's setting)."""
    from .company_settings import get_company_settings

    row = get_company_settings(company)
    value = getattr(row, 'pos_cancel_simple_days', None)
    return DEFAULT_POS_CANCEL_SIMPLE_DAYS if value is None else int(value)


def superior_conditions(order, *, now=None, restore_stock=True) -> list[dict]:
    """
    FINANCIAL-CLOSURE (owner, 2026-10-16). Why annulling the till sale
    `order` takes a superior, in a fixed order; empty for a simple case and
    for anything that is not a till sale. «Cancelación POS sin límite temporal
    absoluto, pero restringir según estado de entrega, comprobante fiscal,
    permisos y antigüedad.» A till sale is delivered at birth: the delivery
    state is guarded by `goods_returned` and the manual-return check.

      older_than_window     sold more than the company's `pos_cancel_simple_days` ago;
      live_fiscal_document  a boleta/factura stands — it is never voided here:
                            it stays, flagged «nota de crédito pendiente»;
      commission_settled    the seller's commission was already paid out;
      several_branches      the sale took stock from more than one branch;
      no_stock_restore      annulled WITHOUT putting stock back (review: a
                            non-standard operation — the goods came back by
                            a manual return the sale does not know about).
    """
    if mode_of(order) != MODE_POS:
        return []
    conditions = []
    days = simple_days(order.company)
    sold = order.paid_at or order.created_at
    if sold is not None and (now or timezone.now()) - sold > timedelta(days=days):
        conditions.append(_blocker(
            'older_than_window',
            f'La venta tiene más de {days} día{"" if days == 1 else "s"} (el plazo de anulación simple '
            'de la empresa).'))
    document = live_fiscal_document(order)
    if document is not None:
        conditions.append(_blocker(
            'live_fiscal_document',
            f'Tiene {fiscal_label(document)} vigente: el comprobante no se anula aquí, queda pendiente '
            'de nota de crédito.'))
    if SalesCommission.objects.filter(order_id=order.pk, status=SalesCommission.STATUS_SETTLED).exists():
        conditions.append(_blocker('commission_settled', 'La comisión del vendedor ya se liquidó.'))
    branches = set(StockMovement.objects.filter(
        order_id=order.pk, movement_type=StockMovement.SALE_EXIT).values_list('branch_id', flat=True))
    if len(branches) > 1:
        conditions.append(_blocker('several_branches', 'La venta sacó stock de varias sucursales.'))
    if not restore_stock:
        conditions.append(_blocker(
            'no_stock_restore', 'Se anula sin reponer stock (la mercadería volvió por una devolución manual).'))
    return conditions


SUPERIOR_REQUIRED = 'Anular esta venta corresponde a un superior (administrador de la empresa).'


def superior_blocker(conditions) -> dict:
    """The refusal as people read it: who must do it, and every reason."""
    return _blocker('superior_required', ' '.join([SUPERIOR_REQUIRED, *(c['detail'] for c in conditions)]))


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


def cancel_order(order, user, reason, *, goods_returned=False, refund_method=None, restore_stock=True,
                 request=None) -> OrderCancellation:
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
    if refund_method is not None:
        from . import order_refunds

        order_refunds.clean_method(refund_method)

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
        # FINANCIAL-CLOSURE: an old or complex till sale is a superior's.
        conditions = superior_conditions(locked, restore_stock=restore_stock)
        if conditions and not may_cancel_as_superior(user, locked.company):
            raise OrderCancelError(**superior_blocker(conditions), conditions=conditions)
        # Review P2. Annulling without putting stock back is the cashier's
        # call for a till sale only — and the way out when goods of the sale
        # already came back by a manual return the sale does not know about.
        if not restore_stock and mode != MODE_POS:
            raise OrderCancelError(
                'restore_stock_not_allowed', 'Sólo una venta POS se anula sin reponer stock.')
        if mode == MODE_POS and restore_stock and _manual_return_after_sale(locked):
            raise OrderCancelError(
                'manual_return_after_sale',
                'Hay una devolución manual posterior de este producto: anula sin reponer stock o revisa el '
                'Kardex.')

        return _commit(
            locked, actor, reason, mode=mode, restore_stock=restore_stock, conditions=conditions,
            authority='superior' if conditions else 'simple', goods_returned=mode == MODE_POS,
            refund_method=refund_method, request=request)


def cancel_for_return(order, user, reason, *, refund_method, refund_amount, return_id, request=None):
    """
    POST-DELIVERY-RETURN — the commercial cancellation that closes a return
    (`order_returns.close_return`), through the same tail as every other.

    The goods LEFT and came back through the return, which already put them
    on the Kardex as `customer_return`: no stock is restored here. The rest is
    the cancellation as always — order and dispatch cancelled, the internal
    note voided, a live boleta/factura kept and flagged, the commission voided
    or compensated — plus ONE pending refund of `refund_amount` (none when it
    is zero), never confirmed. The caller already decided the authority (a
    superior) and holds the order's lock; it is taken again here, harmlessly.
    """
    reason = clean_reason(reason)
    actor = user if getattr(user, 'is_authenticated', False) else None
    from . import order_refunds

    order_refunds.clean_method(refund_method)
    with transaction.atomic():
        locked = Order.objects.select_for_update(of=('self',)).get(pk=order.pk)
        refused = blockers(locked, for_return=True)
        if refused:
            raise OrderCancelError(**refused[0])
        if OrderCancellation.objects.filter(order=locked).exists():  # pragma: no cover — status says it first
            raise OrderCancelError('already_cancelled', 'Este pedido ya está cancelado.')
        return _commit(
            locked, actor, reason, mode=mode_of(locked), restore_stock=False, conditions=[],
            authority='superior', goods_returned=True, refund_method=refund_method,
            refund_amount=refund_amount, request=request,
            extra_audit={'return_id': return_id, 'stock_returned_by': 'order_return'})


def _commit(locked, actor, reason, *, mode, restore_stock, conditions, authority, goods_returned,
            refund_method, request, refund_amount=None, extra_audit=None) -> OrderCancellation:
    """
    Everything a cancellation does once its rules said yes, on the locked
    order: the one tail of `cancel_order` and `cancel_for_return`.

    `refund_amount` None: a refund asked for (`refund_method`) is everything
    still refundable; otherwise exactly that amount, and none when it is zero.
    """
    previous_status, previous_fulfillment = locked.status, locked.fulfillment_status
    # DC3 before the shelf: the supply module's lock order is order, offers, shelf.
    supply_released = _release_supply(locked, actor, reason, request)
    holds_released = _release_holds(locked) if mode == MODE_UNPAID else 0
    movements, units_returned, already_returned = (
        _restore_stock(locked, actor, reason, request) if restore_stock else ([], [], []))
    note_voided = _void_sales_note(locked, actor, reason)
    # Owner, 2026-10-16: only a commission not yet settled is voided.
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
        was_paid=locked.paid, goods_returned=goods_returned, holds_released=holds_released,
    )
    adjustment = compensate_commission(locked, cancellation, actor)
    refund = None
    if refund_method:
        from . import order_refunds

        if refund_amount is not None:
            if refund_amount > 0:
                refund = order_refunds.record_pending(
                    locked, actor, amount=refund_amount, method=refund_method, request=request)
        elif order_refunds.summary(locked)['refundable'] > 0:
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
            # Review P2: annulled WITHOUT putting stock back, by the cashier's choice.
            'stock_restore_skipped': not restore_stock,
            'goods_returned': goods_returned,
            # FINANCIAL-CLOSURE: whether a superior's authority was needed,
            # and why. `actor` is who authorised it.
            'authority': authority,
            'superior_conditions': [condition['code'] for condition in conditions],
            'fiscal_pending': document is not None,
            'fiscal_document': label,
            'sales_note_voided': note_voided,
            'commission_voided': commission_voided,
            # A settled commission is not voided: it gets its compensating entry.
            'commission_adjustment_id': adjustment.pk if adjustment else None,
            # DC3: on-order lines that stopped waiting for their supplier.
            'supply_released': supply_released,
            # Said, so nobody reads a cancellation as a refund: the money
            # taken stays recorded as taken.
            'paid_kept': locked.paid,
            'refund_id': refund.pk if refund else None,
            **(extra_audit or {}),
        },
        request=request, company=locked.company,
    )
    return cancellation


def compensate_commission(order, cancellation, actor):
    """
    FINANCIAL-CLOSURE. A SETTLED commission of the cancelled `order` stays as
    it is — paid out, history — and gets one negative `CommissionAdjustment`
    for its whole amount, linked to the cancellation: the balance to recover
    from the seller. Once: an existing adjustment is returned, never a second
    (the database refuses one too). None when there is nothing settled.
    Called with the order locked.
    """
    from .models import CommissionAdjustment

    commission = SalesCommission.objects.filter(
        order_id=order.pk, status=SalesCommission.STATUS_SETTLED).first()
    if commission is None or commission.amount <= 0:
        return None
    existing = CommissionAdjustment.objects.filter(commission=commission).first()
    if existing is not None:
        return existing
    return CommissionAdjustment.objects.create(
        company_id=commission.company_id, commission=commission, cancellation=cancellation,
        seller_id=commission.seller_id, seller_name_snapshot=commission.seller_name_snapshot,
        kind=CommissionAdjustment.KIND_CANCELLATION, amount=-commission.amount, created_by=actor,
    )


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


def _manual_return_after_sale(order) -> bool:
    """
    Review P2. Whether goods of a product counted by quantity, sold by this
    sale, came back to the same branch through a `return_entry` written AFTER
    the sale exit. Such a return does not name the sale, so `_restore_stock`
    cannot tell it apart and would put the same goods back a second time. A
    serialized product is safe: only the devices still sold to the order come
    back, and a device returned by `return_unit` is counted, not restored.
    """
    exits = StockMovement.objects.filter(
        order=order, movement_type=StockMovement.SALE_EXIT, product__is_serialized=False,
    ).values_list('pk', 'product_id', 'branch_id')
    return any(
        StockMovement.objects.filter(
            movement_type=StockMovement.RETURN_ENTRY, product_id=product_id, branch_id=branch_id, pk__gt=pk,
        ).exists()
        for pk, product_id, branch_id in exits
    )


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
    from .models import OrderReturn

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
        # A post-delivery return closed it: the stock already came back at the
        # reception (`order_returns`), so «no stock to restore» would mislead.
        'by_return': OrderReturn.objects.filter(cancellation=cancellation).exists(),
    }


def is_cancelled(order) -> bool:
    return order.status == Order.Status.CANCELLED


__all__ = [
    'OrderCancelError', 'blockers', 'cancel_for_return', 'cancel_order', 'cancel_paid_order', 'clean_reason',
    'compensate_commission', 'return_in_progress',
    'fiscal_label',
    'is_cancelled', 'live_fiscal_document', 'may_cancel', 'may_cancel_as_superior', 'mode_of', 'payload',
    'superior_blocker', 'superior_conditions',
]
