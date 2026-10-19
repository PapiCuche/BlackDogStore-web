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

from . import fulfillment_flow, order_risk
from .commerce_notifications import emit_fulfillment_changed
from .models import AdminAuditLog, Order, OrderItem, OrderUnit, UserProfile
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
    #: DC3 — a line sold on order whose goods have not been received.
    SUPPLY_PENDING = 'supply_pending'
    #: ORDER-CANCEL-01: a cancelled order's fulfilment does not move.
    ORDER_CANCELLED = 'order_cancelled'
    #: ORDER-FULFILLMENT-SAFETY-01 (`fulfillment_flow`): not the next step of
    #: this order's path, or back after the goods left; «Enviado» without the
    #: carrier's data; a step back without a reason (the only 400).
    TRANSITION_NOT_ALLOWED = 'transition_not_allowed'
    LOGISTICS_MISSING = 'logistics_missing'
    REASON_REQUIRED = 'reason_required'

    def __init__(self, detail: str, *, pending: dict, code: str = UNITS_PENDING):
        super().__init__(detail)
        self.detail = detail
        self.pending = pending
        self.code = code

    @property
    def http_status(self) -> int:
        return 400 if self.code == self.REASON_REQUIRED else 409


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
        # ORDER-FULFILLMENT-SAFETY-01. Exactly the next step of this order's
        # path, or exactly one back with a reason while the goods never left.
        # Asked of the locked status, not of `order`.
        record = fulfillment_flow.delivery_record(order)
        allowed_moves = fulfillment_flow.moves(order, current=previous, record=record)
        going_back = new_status == allowed_moves['back']
        if new_status not in (allowed_moves['forward'], allowed_moves['back']):
            raise FulfillmentBlocked(
                transition_refusal(order, previous, new_status, record),
                pending={}, code=FulfillmentBlocked.TRANSITION_NOT_ALLOWED,
            )
        reason = fulfillment_flow.clean_reason(note)
        if going_back and not reason:
            raise FulfillmentBlocked(
                'Volver al paso anterior exige un motivo (al menos '
                f'{fulfillment_flow.REASON_MIN} caracteres).',
                pending={}, code=FulfillmentBlocked.REASON_REQUIRED,
            )
        if new_status == Order.FulfillmentStatus.SHIPPED:
            missing = fulfillment_flow.logistics_blockers(order, record)
            if missing:
                raise FulfillmentBlocked(
                    missing[0]['detail'], pending={}, code=FulfillmentBlocked.LOGISTICS_MISSING,
                )
        # CLIENT-SEC-01. Only where the company asked for it: an order waiting
        # for its review does not leave — nor is its buyer told to come for it —
        # until somebody has decided. Asked with the order locked, like the
        # devices below.
        if new_status in HELD_WHILE_REVIEW_PENDING and order_risk.review_holds_dispatch(order, company):
            raise FulfillmentBlocked(
                order_risk.REVIEW_PENDING_DETAIL, pending={}, code=FulfillmentBlocked.REVIEW_PENDING,
            )
        # DC3. Goods sold «bajo pedido» that have not arrived are not handed
        # over, sent or announced as ready: nothing fictitious leaves the shop.
        if new_status in HELD_WHILE_REVIEW_PENDING and order.items.filter(
            supply_origin=OrderItem.SUPPLY_SUPPLIER,
        ).exclude(supply_status=OrderItem.SUPPLY_RECEIVED).exists():
            raise FulfillmentBlocked(
                'Este pedido tiene productos bajo pedido que aún no se recibieron del proveedor. '
                'Recíbelos (o resuelve el abastecimiento) antes de marcarlo listo, enviado o entregado.',
                pending={}, code=FulfillmentBlocked.SUPPLY_PENDING,
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
        stamped_units = []
        if leaving:
            stamped_units = list(OrderUnit.objects.filter(order=order, left_at__isnull=True).values_list('pk', flat=True))
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
                # A step back keeps its whole reason; any other note, as before.
                'note': (reason if going_back else (note[:200] if note else '')),
                'direction': 'back' if going_back else 'forward',
                'delivery_mode': fulfillment_flow.delivery_mode(order, record),
                # WHETHER (never what) the delivery record held the carrier's
                # data when this was written — evidence a later edit cannot blank.
                # Which devices THIS move stamped as gone: a correction of it
                # explains those stamps and no others.
                **({'stamped_units': stamped_units} if leaving else {}),
                **({'logistics': {
                    'carrier': bool(record and (record.carrier_id or record.carrier_name.strip())),
                    'waybill': bool(record and record.waybill_number.strip()),
                    'tracking': bool(record and record.tracking_code.strip()),
                    'shipped_at': bool(record and record.shipped_at),
                }} if leaving else {}),
            },
            request=request,
        )

        # M12B — inside the transaction, so the notice is as durable as the
        # status change and disappears with it on a rollback. Emitted from the
        # ONE place that actually moves a fulfillment status, so a future
        # second caller gets the notification for free instead of forgetting it.
        emit_fulfillment_changed(order, new_status)

    return order


def transition_refusal(order, current, new_status, record=None) -> str:
    """What to tell the person whose step is not the next one (or not allowed back)."""
    FS = Order.FulfillmentStatus
    labels = dict(FS.choices)
    if new_status == FS.CANCELLED:
        return ('«Cancelado operativo» no se elige a mano: para cancelar la venta usa «Cancelar pedido», '
                'que repone el stock y deja el reembolso registrado.')
    if new_status == current:
        return f'El pedido ya está en «{labels.get(current, current)}».'
    if new_status == FS.SHIPPED and fulfillment_flow.kind(order, record) == fulfillment_flow.PICKUP:
        return 'Es un pedido de recojo en tienda: no se envía, se marca «Listo para retiro» y luego «Entregado».'
    if new_status == FS.READY_FOR_PICKUP and fulfillment_flow.kind(order, record) == fulfillment_flow.SHIPPING:
        return 'Es un pedido con envío: no pasa por «Listo para retiro», se marca «Enviado».'
    allowed = fulfillment_flow.moves(order, current=current, record=record)
    path = fulfillment_flow.steps(order, record)
    here = path.index(FS.PENDING if current == FS.CONFIRMED else current) if (
        current in path or current == FS.CONFIRMED) else None
    backwards = current in (FS.SHIPPED, FS.DELIVERED, FS.CANCELLED) or (
        here is not None and new_status in path and path.index(new_status) < here)
    if backwards:
        if allowed['back'] is None and new_status in path:
            return ('El producto ya salió de la tienda (o así quedó registrado): el despacho no vuelve atrás. '
                    'Si fue un marcado por error, un superior puede corregirlo; si salió, corresponde una devolución.')
        if allowed['back']:
            return f'Sólo se puede volver un paso, a «{labels.get(allowed["back"], allowed["back"])}», con un motivo.'
    nxt = allowed['forward']
    if nxt:
        return f'El siguiente paso de este pedido es «{labels.get(nxt, nxt)}»: los pasos no se saltan.'
    return 'Este pedido no tiene más pasos de despacho.'


def available_moves(order, user, company) -> list[str]:
    """
    The statuses THIS user may set on THIS order now, in path order: the next
    step and the one back, each only if the user may set it. Empty for a
    cancelled order or for someone who cannot manage orders.
    """
    from . import order_cancellation as cancelling

    allowed = set(allowed_fulfillment_statuses(user, company))
    if not allowed or cancelling.is_cancelled(order):
        return []
    found = fulfillment_flow.moves(order)
    return [s for s in (found['back'], found['forward']) if s and s in allowed]


HISTORY_ACTIONS = (
    'order_fulfillment_status_changed', 'order_left_marking_corrected', 'order_delivery_updated', 'order_cancelled',
)


def history(order) -> list[dict]:
    """
    The order's dispatch record, oldest first, AS IT WAS WRITTEN — read-only,
    from the audit log. An incoherent past (a pickup order «sent») is shown, not
    tidied; an entry a superior declared accidental says so and stays.
    """
    from . import left_marking
    from . import order_cancellation as cancelling

    corrected = cancelling.corrected_event_ids(order)
    labels = dict(Order.FulfillmentStatus.choices)
    rows = []
    for row in AdminAuditLog.objects.filter(
        action__in=HISTORY_ACTIONS, target_type='order', target_id=str(order.pk),
    ).select_related('actor').order_by('created_at', 'pk'):
        meta = row.metadata or {}
        item = {
            'id': row.pk, 'at': row.created_at.isoformat(), 'action': row.action,
            'actor': left_marking.actor_label(row.actor),
            'old_status': '', 'new_status': '', 'old_label': '', 'new_label': '',
            'note': '', 'direction': '', 'corrected': False, 'corrects_id': None,
        }
        if row.action == 'order_fulfillment_status_changed':
            old, new = meta.get('old_fulfillment_status', ''), meta.get('new_fulfillment_status', '')
            item.update(old_status=old, new_status=new, old_label=labels.get(old, old),
                        new_label=labels.get(new, new), note=meta.get('note', '') or '',
                        direction=meta.get('direction', ''), corrected=row.pk in corrected)
        elif row.action == 'order_left_marking_corrected':
            restored = meta.get('status_restored_to', '') or ''
            item.update(note=meta.get('reason', '') or '', corrects_id=meta.get('corrected_audit_id'),
                        new_status=restored, new_label=labels.get(restored, restored))
        elif row.action == 'order_cancelled':
            item.update(note=meta.get('reason', '') or '')
        rows.append(item)
    return rows


def dispatch_view(order, user, company) -> dict:
    """Everything the order detail shows about dispatch, for THIS user. Read-only."""
    from . import left_marking
    from . import order_cancellation as cancelling
    from . import unit_identification

    FS = Order.FulfillmentStatus
    labels = dict(FS.choices)
    record = fulfillment_flow.delivery_record(order)
    path = fulfillment_flow.steps(order, record)
    entries = history(order)
    reached = {}
    for item in entries:
        if item['action'] == 'order_fulfillment_status_changed' and item['new_status'] and not item['corrected']:
            reached[item['new_status']] = item
    current = order.fulfillment_status
    moves = available_moves(order, user, company)
    found = fulfillment_flow.moves(order, record=record)
    forward = found['forward'] if found['forward'] in moves else None
    back = found['back'] if found['back'] in moves else None
    blockers = []
    if forward in (FS.READY_FOR_PICKUP, FS.SHIPPED, FS.DELIVERED):
        if order_risk.review_holds_dispatch(order, company):
            blockers.append({'code': FulfillmentBlocked.REVIEW_PENDING, 'detail': order_risk.REVIEW_PENDING_DETAIL})
        if order.items.filter(supply_origin=OrderItem.SUPPLY_SUPPLIER).exclude(
                supply_status=OrderItem.SUPPLY_RECEIVED).exists():
            blockers.append({'code': FulfillmentBlocked.SUPPLY_PENDING,
                             'detail': 'Hay productos bajo pedido que aún no se recibieron del proveedor.'})
    if forward in (FS.SHIPPED, FS.DELIVERED):
        missing = sum(unit_identification.pending_units(order).values())
        if missing:
            blockers.append({'code': FulfillmentBlocked.UNITS_PENDING,
                             'detail': f'Falta identificar {missing} equipo(s) (IMEI o serie).'})
    if forward == FS.SHIPPED:
        blockers.extend(fulfillment_flow.logistics_blockers(order, record))
    here = path.index(FS.PENDING if current == FS.CONFIRMED else current) if (
        current in path or current == FS.CONFIRMED) else None
    steps = []
    for index, status_code in enumerate(path):
        hit = reached.get(status_code)
        at = hit['at'] if hit else None
        if at is None and index == 0 and order.created_at:
            at = order.created_at.isoformat()     # nobody moves an order into «Pendiente»: it is born there
        steps.append({
            'status': status_code, 'label': labels[status_code],
            'state': ('current' if index == here else 'done' if here is not None and index < here else 'todo'),
            'at': at, 'actor': hit['actor'] if hit else '',
        })
    may_manage = bool(allowed_fulfillment_statuses(user, company))
    if cancelling.is_cancelled(order):
        notice = 'Pedido cancelado: su despacho ya no cambia.'
    elif current == FS.CANCELLED:
        notice = ('Despacho detenido con el selector anterior («Cancelado operativo»), pero la venta sigue vigente. '
                  'Para cancelarla usa «Cancelar pedido».')
    elif here is None:
        notice = f'Estado «{labels.get(current, current)}», que no corresponde a esta modalidad (registro anterior).'
    else:
        notice = ''
    return {
        'kind': fulfillment_flow.kind(order, record),
        'mode': fulfillment_flow.delivery_mode(order, record),
        'current': current, 'current_label': labels.get(current, current),
        'steps': steps,
        'forward': forward, 'forward_label': labels.get(forward, '') if forward else '',
        'back': back, 'back_label': labels.get(back, '') if back else '',
        'reason_min': fulfillment_flow.REASON_MIN,
        'correction_reason_min': left_marking.REASON_MIN,
        'blockers': blockers,
        'may_manage': may_manage,
        'notice': notice,
        'ever_left': cancelling.ever_left(order),
        'history': entries,
        'left_markings': left_marking.markings(order, user, company),
        'may_correct': left_marking.may_correct(user, company),
    }
