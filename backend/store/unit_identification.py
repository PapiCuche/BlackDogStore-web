"""
SERIAL-PICK-01 · saying WHICH device.

A serialized product is not sold as «one of them». Somebody identifies the
device that leaves — at the till when it is sold, for a web order when it is
handed over — and that is what every paper names afterwards (`OrderUnit`).

TWO WAYS TO IDENTIFY, AND THEY ARE RECORDED APART:

  scanner   the code was read off the device or its box.
  manual    somebody typed it because the scanner would not read. A typed
            IMEI checks itself (15 digits and their Luhn digit), so for a
            product that carries an IMEI the IMEI is what is typed: a serial
            typed by hand is one wrong character away from another device.
            It is written down that it was typed, and by whom.

A CODE IS LOOKED UP INSIDE ONE COMPANY, always. Another tenant's device is not
«refused»: it is not there.
"""
import re

from django.db import transaction
from django.db.models import Q

from . import device_identity
from .models import AdminAuditLog, Order, OrderItem, OrderUnit, StockMovement, StockUnit

SCANNER, MANUAL = OrderUnit.Method.SCANNER.value, OrderUnit.Method.MANUAL.value
METHODS = (SCANNER, MANUAL)

#: The moments a device leaves the shop for a web order.
LEAVING = (Order.FulfillmentStatus.SHIPPED, Order.FulfillmentStatus.DELIVERED)
#: An order that is still to be handed over: its box is set aside, not delivered.
WAITING = (
    Order.FulfillmentStatus.PENDING, Order.FulfillmentStatus.CONFIRMED,
    Order.FulfillmentStatus.PREPARING, Order.FulfillmentStatus.READY_FOR_PICKUP,
)

_ONLY_DIGITS = re.compile(r'^[0-9][0-9\s.\-/]*$')


class UnitIdentificationError(Exception):
    """A device could not be identified. `code` says which rule; `detail` is for the person."""

    #: What the code typed is NOT (400), what does not exist (404); everything
    #: else is a device that exists and cannot be used for this (409).
    _STATUS = {'invalid_code': 400, 'invalid_method': 400, 'manual_needs_imei': 400, 'not_found': 404}

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail

    @property
    def http_status(self) -> int:
        return self._STATUS.get(self.code, 409)

    def body(self) -> dict:
        return {'detail': self.detail, 'code': self.code}


def clean_method(value) -> str:
    if value not in METHODS:
        raise UnitIdentificationError('invalid_method', 'Indica si el equipo se leyó con el lector o se escribió a mano.')
    return value


def describe(unit) -> dict:
    """
    What is shown so a person can confirm «yes, this one» before it is sold.

    The product's name is the model and the colour: this catalogue writes them
    there. No cost: this is read at the till.
    """
    return {
        'unit_id': unit.pk,
        'product_id': unit.product_id,
        'product_name': unit.product.name,
        'serial_number': unit.serial_number or '',
        'imei': unit.imei or '',
        'imei2': unit.imei2 or '',
        'condition': unit.condition,
        'condition_label': unit.get_condition_display(),
        'requires_imei': unit.product.requires_imei,
    }


def find_unit(company, code):
    """
    The device of THIS company that `code` names: an IMEI (either of its two) or a serial.

    Fifteen digits are an IMEI and are checked as one; when no device carries
    that IMEI they may still be somebody's serial (a product without IMEI can
    have a numeric one). Anything else is a serial. A serial is unique inside a
    product, not across them: when two products share one it is not guessed —
    the IMEI tells them apart.
    """
    text = str(code or '').strip()
    if not text:
        raise UnitIdentificationError('invalid_code', 'Escanea o escribe el IMEI o la serie del equipo.')
    base = StockUnit.objects.filter(company=company).select_related('product', 'branch')

    digits = re.sub(r'[\s.\-/]', '', text)
    looks_like_imei = bool(_ONLY_DIGITS.match(text)) and len(digits) == 15
    found, not_an_imei = [], None
    if looks_like_imei:
        try:
            imei = device_identity.clean_imei(text)
        except device_identity.DeviceIdentityError as exc:
            not_an_imei = str(exc)
        else:
            found = list(base.filter(Q(imei=imei) | Q(imei2=imei))[:2])
    if not found:
        try:
            serial = device_identity.clean_serial(text)
        except device_identity.DeviceIdentityError as exc:
            if not looks_like_imei:
                raise UnitIdentificationError('invalid_code', str(exc)) from None
        else:
            found = list(base.filter(serial_number=serial)[:2])
    if not found and not_an_imei:
        # Fifteen digits that are nobody's serial: what is wrong with them as an IMEI.
        raise UnitIdentificationError('invalid_code', not_an_imei)

    if not found:
        raise UnitIdentificationError('not_found', 'Ese IMEI o serie no es de ningún equipo registrado.')
    if len(found) > 1:
        raise UnitIdentificationError(
            'ambiguous', 'Hay más de un equipo con esa serie. Identifícalo por su IMEI.',
        )
    return found[0]


def _typed_by_hand_is_an_imei(unit, code, method):
    """Typed by hand, a device that has an IMEI is identified by it."""
    if method != MANUAL or not unit.product.requires_imei:
        return
    digits = re.sub(r'[\s.\-/]', '', str(code or ''))
    if digits not in {unit.imei, unit.imei2}:
        raise UnitIdentificationError(
            'manual_needs_imei',
            'A mano, este equipo se identifica por su IMEI (15 dígitos), no por la serie. '
            'Si lo leíste con el lector, vuelve a pasarlo.',
        )


def sellable_unit(*, company, branch, code, method):
    """The device `code` names, if it can be sold from `branch` right now."""
    method = clean_method(method)
    unit = find_unit(company, code)
    _typed_by_hand_is_an_imei(unit, code, method)
    if unit.branch_id != branch.pk:
        # Not WHERE it is: a till is told nothing about other branches.
        raise UnitIdentificationError('wrong_branch', 'Ese equipo no está en esta sucursal.')
    if unit.status != StockUnit.Status.AVAILABLE:
        raise UnitIdentificationError(
            'not_available', f'Ese equipo está {unit.get_status_display().lower()}: no se puede vender.',
        )
    if not unit.product.is_active:
        raise UnitIdentificationError('not_available', 'El producto de ese equipo está desactivado.')
    return unit


def record(*, order, unit, method, actor, request=None, left=False, **how) -> OrderUnit:
    """
    Write down that `unit` is the device of `order`, how it was identified and by whom.

    `left`: the device walks out with this very act (a till sale). `how` is
    what else happened to make it so — `swapped_from`, `exchanged_with_order`,
    `second_exit` — and goes to the audit line.
    """
    row = OrderUnit.objects.create(
        company_id=order.company_id, order=order, product_id=unit.product_id, unit=unit,
        method=method, identified_by=actor, left_at=_now() if left else None,
    )
    metadata = {'order_id': order.pk, 'unit_id': unit.pk, 'product_id': unit.product_id, 'method': method}
    metadata.update({key: value for key, value in how.items() if value is not None})
    # Not the IMEI: the unit is the record, and a log line is read by more people.
    AdminAuditLog.log(
        actor=actor, action='order_unit_identified', target_type='order', target_id=order.pk,
        metadata=metadata, request=request, company=order.company,
    )
    return row


# ---------------------------------------------------------------------------
# A web order, when it is handed over
# ---------------------------------------------------------------------------

def _wanted(order) -> dict:
    """
    product id -> how many devices this order owes somebody's identification.

    A product that left this order's stock as a plain COUNT had no serials when
    it was sold. If it got them afterwards, there is no device this order could
    name: it owes none.
    """
    wanted: dict[int, int] = {}
    for item in order.items.select_related('product'):
        if item.supply_origin == OrderItem.SUPPLY_SUPPLIER and item.supply_status != OrderItem.SUPPLY_RECEIVED:
            continue      # DC3 — on order: no device exists yet; receiving it names it.
        if item.product.is_serialized:
            wanted[item.product_id] = wanted.get(item.product_id, 0) + item.quantity
    if wanted:
        for movement in StockMovement.objects.filter(
            order=order, movement_type=StockMovement.SALE_EXIT, product_id__in=list(wanted),
        ):
            if not (movement.metadata or {}).get('unit_ids'):
                wanted.pop(movement.product_id, None)
    return wanted


def _identified(order) -> dict:
    """
    product id -> how many of its devices are identified AND accounted for.

    A row counts when the device left with the order, or is still out of the
    shelf for it. One that went back to the shelf before anybody handed it over
    names nothing any more.
    """
    done: dict[int, int] = {}
    for product_id, left_at, unit_order, unit_status in OrderUnit.objects.filter(order=order).values_list(
        'product_id', 'left_at', 'unit__order_id', 'unit__status',
    ):
        if left_at is None and not (unit_order == order.pk and unit_status == StockUnit.Status.SOLD):
            continue
        done[product_id] = done.get(product_id, 0) + 1
    return done


def pending_units(order) -> dict:
    """product id -> how many of its devices nobody has identified yet. Empty when none is missing."""
    wanted = _wanted(order)
    if not wanted:
        return {}
    done = _identified(order)
    return {
        product_id: quantity - done.get(product_id, 0)
        for product_id, quantity in wanted.items() if quantity > done.get(product_id, 0)
    }


def left_the_shop(order) -> bool:
    return order.fulfillment_status in LEAVING


def mark_left(order) -> None:
    """
    The order is being sent or handed over: its devices have LEFT, for good.

    Stamped once and never cleared. Moving the order back to «en preparación»
    afterwards is a status, not a time machine — the device that left is the
    one that left, and no paper is rewritten.
    """
    OrderUnit.objects.filter(order=order, left_at__isnull=True).update(left_at=_now())


def released_by_return(unit, order_id, *, actor=None, request=None) -> None:
    """
    `unit` went back to the shelf (a return). If it had been identified for an
    order and had NOT left with it, it is no longer the device of that order.
    What did leave keeps its row: that sale's paper named it.
    """
    if not order_id:
        return
    for row in OrderUnit.objects.filter(order_id=order_id, unit=unit, left_at__isnull=True).select_related('order__company'):
        metadata = {'order_id': order_id, 'unit_id': unit.pk, 'product_id': row.product_id,
                    'method': row.method, 'because': 'returned_before_it_left'}
        company = row.order.company
        row.delete()
        AdminAuditLog.log(
            actor=actor, action='order_unit_forgotten', target_type='order', target_id=order_id,
            metadata=metadata, request=request, company=company,
        )


MINE, SHELF, THEIRS = 'mine', 'shelf', 'theirs'


def _standing(order, unit) -> str:
    """
    Where `unit` stands for `order`:

      MINE     out of the shelf for this very order (the one the payment set aside);
      SHELF    available on the shelf;
      THEIRS   set aside for ANOTHER order that is also waiting, and that nobody
               has identified there. Set aside is not identified: which box the
               payment picked for which order is nobody's business, so whoever
               hands one over may take either.

    Anything else cannot be handed over with this order.
    """
    if unit.status == StockUnit.Status.AVAILABLE:
        return SHELF
    if unit.status == StockUnit.Status.SOLD and unit.order_id == order.pk:
        return MINE
    if unit.status == StockUnit.Status.SOLD and unit.order_id:
        other = Order.objects.filter(pk=unit.order_id, company_id=order.company_id).first()
        free = (
            other is not None and other.status == Order.Status.PAID
            and other.fulfillment_status in WAITING
            and not OrderUnit.objects.filter(order_id=other.pk, unit=unit).exists()
        )
        if free:
            return THEIRS
        raise UnitIdentificationError('other_order', 'Ese equipo ya salió con otra venta: no se puede entregar con este pedido.')
    raise UnitIdentificationError(
        'not_available', f'Ese equipo está {unit.get_status_display().lower()}: no se puede entregar con este pedido.',
    )


def candidate_for_order(*, order, code, method):
    """
    The device `code` names, if this order is waiting for it. Writes nothing.

    What the screen shows to confirm — «iPhone 16, nuevo, IMEI …» — before a
    typed code is accepted, and the first half of `identify_for_order`.
    """
    method = clean_method(method)
    if order.status != Order.Status.PAID:
        raise UnitIdentificationError('not_paid', 'Este pedido no está pagado: todavía no se entrega ningún equipo.')
    unit = find_unit(order.company, code)
    _typed_by_hand_is_an_imei(unit, code, method)

    # The branch before anything about the device: what is elsewhere is not described.
    branch = order.fulfillment_branch
    if branch is None or unit.branch_id != branch.pk:
        raise UnitIdentificationError('wrong_branch', 'Ese equipo no está en la sucursal que entrega este pedido.')
    if unit.product_id not in _wanted(order):
        raise UnitIdentificationError('wrong_product', f'Este pedido no lleva «{unit.product.name}».')
    if OrderUnit.objects.filter(order=order, unit=unit).exists():
        raise UnitIdentificationError('already_identified', 'Ese equipo ya está identificado en este pedido.')
    if unit.product_id not in pending_units(order):
        raise UnitIdentificationError('complete', f'Los equipos de «{unit.product.name}» de este pedido ya están identificados.')
    _standing(order, unit)
    return unit


def _set_aside(order, product_id):
    """The unit out of the shelf for this order that nobody has named yet (oldest first), or None."""
    named = OrderUnit.objects.filter(order=order).values_list('unit_id', flat=True)
    return (
        StockUnit.objects
        .filter(order=order, product_id=product_id, status=StockUnit.Status.SOLD)
        .exclude(pk__in=list(named)).order_by('received_at', 'pk').first()
    )


@transaction.atomic
def identify_for_order(*, order, code, method, actor, request=None) -> OrderUnit:
    """
    `code` is the device being handed over for `order`.

    The payment set a unit aside (the oldest). The device in hand is:

      that one                      it is simply named;
      another one, on the shelf     THEY ARE SWAPPED: the one set aside goes back
                                    and this one leaves — two Kardex lines, one
                                    each way, so the count does not change and
                                    the history says what happened;
      the one set aside for         the two orders EXCHANGE boxes. Both stay out
      another waiting order         of the shelf: no Kardex line;
      one on the shelf, and the     the return put one back and this hand-over
      one set aside was returned    takes one out: one Kardex line.

    LOCKS, in the module's one order: the order, then the shelf row, then the
    devices (by id). Everything is decided again once the shelf is held — every
    writer of a device's status holds it too.
    """
    from . import inventory_services

    order = (
        Order.objects.select_for_update(of=('self',))
        .select_related('company', 'fulfillment_branch').get(pk=order.pk)
    )
    unit = candidate_for_order(order=order, code=code, method=method)      # a typo is refused before any lock
    branch = order.fulfillment_branch
    inventory_services._locked_branch_stocks(branch, [unit.product])
    unit = candidate_for_order(order=order, code=code, method=method)      # and again, now that nothing moves
    standing = _standing(order, unit)
    aside = _set_aside(order, unit.product_id)
    # The rows about to be written, locked by id. Only themselves: not their product or branch.
    list(StockUnit.objects.select_for_update().filter(pk__in=[unit.pk, *([aside.pk] if aside else [])]).order_by('pk'))

    how = {}
    if standing == MINE:
        pass
    elif standing == THEIRS:
        if aside is None:
            raise UnitIdentificationError('other_order', 'Ese equipo está apartado para otro pedido.')
        other_id = unit.order_id
        StockUnit.objects.filter(pk=unit.pk).update(order=order, updated_at=_now())
        StockUnit.objects.filter(pk=aside.pk).update(order_id=other_id, updated_at=_now())
        how['exchanged_with_order'] = other_id
    else:
        common = dict(branch=branch, product_id=unit.product_id, quantity=1, actor=actor,
                      reference_type='order', reference_id=order.pk)
        if aside is not None:
            reason = f'Cambio de equipo al entregar — orden #{order.pk}'
            inventory_services.create_stock_movement(
                movement_type=StockMovement.MANUAL_ENTRY, units=[aside], expected_unit_status=StockUnit.Status.SOLD,
                reason=reason, metadata={'swap_for_order': order.pk, 'swapped_with': unit.pk}, **common)
            StockUnit.objects.filter(pk=aside.pk).update(
                status=StockUnit.Status.AVAILABLE, order=None, sold_at=None, updated_at=_now())
            inventory_services.create_stock_movement(
                movement_type=StockMovement.MANUAL_EXIT, units=[unit], expected_unit_status=StockUnit.Status.AVAILABLE,
                reason=reason, metadata={'swap_for_order': order.pk, 'swapped_with': aside.pk}, **common)
            how['swapped_from'] = aside.pk
        elif _sold_with_devices(order, unit.product_id):
            # Its stock did leave, device by device, and one came back (a return
            # before the hand-over). This one goes out in its place.
            inventory_services.create_stock_movement(
                movement_type=StockMovement.MANUAL_EXIT, units=[unit], expected_unit_status=StockUnit.Status.AVAILABLE,
                reason=f'Entrega tras una devolución — orden #{order.pk}',
                metadata={'second_exit_for_order': order.pk}, **common)
            how['second_exit'] = True
        else:
            raise UnitIdentificationError(
                'needs_stock_exit',
                'Este pedido todavía no tiene registrada su salida de stock. Reprocésala antes de identificar el equipo.',
            )
        StockUnit.objects.filter(pk=unit.pk).update(
            status=StockUnit.Status.SOLD, order=order, sold_at=_now(), updated_at=_now())
    return record(order=order, unit=unit, method=method, actor=actor, request=request, **how)


def _sold_with_devices(order, product_id) -> bool:
    """Whether this order's stock exit for that product happened, and happened device by device."""
    return any(
        (movement.metadata or {}).get('unit_ids')
        for movement in StockMovement.objects.filter(
            order=order, movement_type=StockMovement.SALE_EXIT, product_id=product_id,
        )
    )


@transaction.atomic
def forget(*, order, order_unit_id, actor, request=None) -> None:
    """
    Take back an identification made by mistake — while the device is still in the shop.

    The unit stays set aside for the order; it is only no longer NAMED. What
    already left is not renamed, whatever the order's status says today: that
    would change what a paper says.
    """
    order = Order.objects.select_for_update(of=('self',)).get(pk=order.pk)
    row = OrderUnit.objects.filter(order=order, pk=order_unit_id).first()
    if row is None:
        raise UnitIdentificationError('not_found', 'Ese equipo no está identificado en este pedido.')
    if row.left_at is not None or left_the_shop(order):
        raise UnitIdentificationError('already_left', 'El pedido ya salió de la tienda: el equipo que se entregó no se cambia aquí.')
    if row.method == OrderUnit.Method.BUYER:
        # DC2: the buyer chose and paid for THIS device. Handing over another
        # one is a change the buyer agrees to, not a correction of the panel.
        raise UnitIdentificationError('chosen_by_buyer', 'El comprador eligió este equipo: no se cambia desde aquí.')
    metadata = {'order_id': order.pk, 'unit_id': row.unit_id, 'product_id': row.product_id, 'method': row.method}
    row.delete()
    AdminAuditLog.log(
        actor=actor, action='order_unit_forgotten', target_type='order', target_id=order.pk,
        metadata=metadata, request=request, company=order.company,
    )


def order_view(order) -> dict:
    """
    What a screen shows of an order's devices — the same for the panel and the app.

    `records`: product id -> the devices somebody identified. Whole serial and
    IMEI: read by staff with the device in hand. `id` is the record's, to take
    it back; `can_remove` says whether it still can be.
    """
    pending = pending_units(order)
    left = left_the_shop(order)
    records: dict[int, list[dict]] = {}
    for row in (
        OrderUnit.objects.filter(order=order, company_id=order.company_id)
        .select_related('unit', 'identified_by').order_by('identified_at', 'pk')
    ):
        records.setdefault(row.product_id, []).append({
            'id': row.pk,
            'product_id': row.product_id,
            'serial_number': row.unit.serial_number or '',
            'imei': row.unit.imei or '',
            'imei2': row.unit.imei2 or '',
            'method': row.method,
            'method_label': row.get_method_display(),
            'identified_at': row.identified_at.isoformat(),
            'identified_by': row.identified_by.username if row.identified_by_id else '',
            'can_remove': row.left_at is None and not left,
        })
    return {'pending': pending, 'records': records, 'left': left}


def _now():
    from django.utils import timezone

    return timezone.now()
