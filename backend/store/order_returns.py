"""
POST-DELIVERY-RETURN — phase 1 (owner, 2026-10-19; `docs/diseno-devolucion-post-entrega.md`).

The goods of a PAID order LEFT the shop — sent or delivered online (`ever_left`),
or handed over at the till (a till sale is delivered at birth) — and the
customer brings them back. Phase 1 is the WHOLE order (D1). Cancelling stays
what it was: the flow for goods that never left (`order_cancellation`).

THREE STEPS, BY THREE AUTHORITIES (D6), EACH ONCE

  request  `sales.orders.manage` (legacy bridge: as `may_cancel`). Kind and
           reason. Later than the company's `return_simple_days` after the
           delivery, only a superior (D5). Nothing moves.
  receive  `inventory.adjust` in the company, into a branch the person
           operates. Every device sold is named by its IMEI or serial and must
           be EXACTLY one that left with this order; every quantity comes back
           whole. One `customer_return` Kardex line per line, AT THE COST ITS
           SALE EXIT LEFT WITH (`inventory_services.create_stock_movement`'s
           own rule; a device at its own cost). Then its state (D3): sellable
           → on the shelf; review → set aside («Apartado», with the reason)
           until somebody releases it; damaged → entered and written off. A
           quantity is only sellable or damaged (no place to set one aside).
  close    a superior (`may_cancel_as_superior`). The amount to give back is
           what was paid minus an optional deduction with its reason, never
           more than paid (D4) — the server computes it and refuses another.
           Then the commercial cancellation through the existing tail
           (`order_cancellation.cancel_for_return`): order and dispatch
           cancelled, internal note voided, a live boleta/factura kept and
           flagged, commission voided or compensated, and ONE pending refund.
           No stock is restored again: the reception did it.

  withdraw before reception, with a reason: the requester or a superior.

Nothing here calls a gateway, touches a `PaymentTransaction` or confirms a
refund: confirming stays the evidence flow of `order_refunds`.

ONE TRANSACTION, THE ORDER LOCKED FIRST, then the return row, then — for the
stock — the module's lock order (shelf row, then units). Every rule is re-read
under that lock, so a second reception or a close and a withdrawal at once
queue instead of racing. Audited as `order_return_*`, with ids and field names
only: never an IMEI, a serial or a customer's data.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.utils import timezone

from . import order_cancellation as cancelling
from .models import (
    AdminAuditLog, Order, OrderRefund, OrderReturn, OrderReturnLine, StockMovement, StockUnit, UserProfile,
)

CAP_INVENTORY_ADJUST = 'inventory.adjust'
#: Legacy bridge: who receives goods — the inventory operator's set
#: (`admin_views._LEGACY_ADJUST_INVENTORY_ROLES`).
LEGACY_RECEIVE_ROLES = frozenset([
    UserProfile.ROLE_INVENTORY, UserProfile.ROLE_ADMIN, UserProfile.ROLE_SUPERADMIN,
])
#: A company without a settings row: the value a new row is created with.
DEFAULT_RETURN_SIMPLE_DAYS = 7
REASON_MIN, REASON_MAX = 5, 500
DEDUCTION_REASON_MIN, DEDUCTION_REASON_MAX = 5, 300
MAX_LINES = 200
ZERO = Decimal('0.00')
CENT = Decimal('0.01')

KINDS = frozenset(code for code, _label in OrderReturn.KIND_CHOICES)
DEVICE_CONDITIONS = frozenset(code for code, _label in OrderReturnLine.CONDITION_CHOICES)
QUANTITY_CONDITIONS = frozenset([OrderReturnLine.CONDITION_SELLABLE, OrderReturnLine.CONDITION_DAMAGED])

ACTION_REQUESTED = 'order_return_requested'
ACTION_RECEIVED = 'order_return_received'
ACTION_CLOSED = 'order_return_closed'
ACTION_WITHDRAWN = 'order_return_withdrawn'


class OrderReturnError(Exception):
    """A step of a return that may not happen. `code` says which rule; `detail` is for the person."""

    #: A request that is wrong in itself (400); everything else is the order's state (409).
    _BAD_REQUEST = frozenset([
        'reason_required', 'reason_too_long', 'kind_invalid', 'lines_required', 'lines_invalid',
        'lines_incomplete', 'condition_invalid', 'branch_invalid', 'unit_code_required', 'unit_ambiguous',
        'amount_invalid', 'amount_mismatch', 'deduction_reason_required', 'deduction_reason_too_long',
        'deduction_exceeds_paid', 'method_invalid',
    ])
    _FORBIDDEN = frozenset(['superior_required', 'not_allowed', 'not_the_requester'])

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


def _blocker(code, detail) -> dict:
    return {'code': code, 'detail': detail}


# -- who -----------------------------------------------------------------------

def may_request(user, company) -> bool:
    """Registering a return is a judgement about a sale: who may cancel orders (D6)."""
    return cancelling.may_cancel(user, company)


def may_receive(user, company) -> bool:
    """Taking the goods back into stock: who adjusts inventory in `company` (D6)."""
    from .permissions import get_user_role
    from .tenancy import has_capability, uses_legacy_bridge

    if company is None or user is None or not getattr(user, 'is_authenticated', False):
        return False
    if uses_legacy_bridge(user, company):
        return get_user_role(user) in LEGACY_RECEIVE_ROLES
    return has_capability(user, company, CAP_INVENTORY_ADJUST)


def may_close(user, company) -> bool:
    """Closing — the money and the cancellation — is a superior's (D6, D4)."""
    return cancelling.may_cancel_as_superior(user, company)


def may_withdraw(user, company, order_return) -> bool:
    """The person who registered it (still managing orders) or a superior."""
    if order_return is None:
        return False
    if may_close(user, company):
        return True
    return may_request(user, company) and order_return.requested_by_id == getattr(user, 'pk', None)


def receiving_branches(user, company):
    """The branches `user` may take goods back into: the ones they operate."""
    from .tenancy import visible_branches

    return visible_branches(user, company).order_by('name', 'pk')


# -- the window (D5) -------------------------------------------------------------

def window_days(company) -> int:
    from .company_settings import get_company_settings

    row = get_company_settings(company)
    value = getattr(row, 'return_simple_days', None)
    return DEFAULT_RETURN_SIMPLE_DAYS if value is None else int(value)


def delivered_at(order):
    """
    When the goods of `order` reached the customer, as the records say: the
    latest move to sent or delivered a superior did not declare accidental;
    without one (a till sale, a device stamped before moves were audited),
    when it was paid.
    """
    corrected = cancelling.corrected_event_ids(order)
    last = cancelling.leaving_events(order).exclude(pk__in=corrected).last()
    if last is not None:
        return last.created_at
    return order.paid_at or order.created_at


def window_conditions(order, *, now=None) -> list[dict]:
    """Why registering a return of `order` now takes a superior; empty inside the window."""
    days = window_days(order.company)
    since = delivered_at(order)
    if since is None or (now or timezone.now()) - since <= timedelta(days=days):
        return []
    return [_blocker(
        'older_than_window',
        f'La entrega fue hace más de {days} día{"" if days == 1 else "s"} (el plazo de devolución simple de la '
        'empresa).')]


SUPERIOR_REQUIRED = 'Registrar esta devolución corresponde a un superior (administrador de la empresa).'


def superior_blocker(conditions) -> dict:
    return _blocker('superior_required', ' '.join([SUPERIOR_REQUIRED, *(c['detail'] for c in conditions)]))


# -- which orders ------------------------------------------------------------------

def current(order):
    """The return of `order` that is not withdrawn (open or closed), or None. At most one (database)."""
    return OrderReturn.objects.filter(order_id=order.pk).exclude(status=OrderReturn.STATUS_WITHDRAWN).first()


def order_blockers(order) -> list[dict]:
    """
    Why the ORDER is not open to a return, whatever its returns say. Empty
    when it is: paid, not cancelled, and its goods left the shop (D1, D8).
    """
    if order.status == Order.Status.CANCELLED:
        return [_blocker('already_cancelled', 'Este pedido ya está cancelado.')]
    if not (order.status == Order.Status.PAID and order.paid):
        return [_blocker(
            'not_paid',
            'Sólo se devuelve un pedido pagado. '
            f'Este pedido figura como «{order.get_status_display().lower()}».')]
    if cancelling.mode_of(order) == cancelling.MODE_POS:
        return []                                    # a till sale is delivered at birth
    if not cancelling.ever_left(order):
        return [_blocker(
            'goods_not_left',
            'La mercadería no salió de la tienda: cancela el pedido («Cancelar pedido»); no hace falta una '
            'devolución.')]
    return []


def request_blockers(order) -> list[dict]:
    """Why a return of `order` cannot be registered now. The same answer the service gives under the lock."""
    refused = order_blockers(order)
    if refused:
        return refused
    if current(order) is not None:
        return [_blocker('return_exists', 'Este pedido ya tiene una devolución registrada.')]
    return []


# -- cleaning --------------------------------------------------------------------------

def _text(value) -> str:
    return ' '.join(str(value or '').split()) if isinstance(value, (str, type(None))) else ''


def clean_reason(value, *, what='de la devolución') -> str:
    text = _text(value)
    if len(text) < REASON_MIN:
        raise OrderReturnError('reason_required', f'Escribe el motivo {what} (al menos {REASON_MIN} caracteres).')
    if len(text) > REASON_MAX:
        raise OrderReturnError('reason_too_long', f'El motivo no puede pasar de {REASON_MAX} caracteres.')
    return text


def clean_kind(value) -> str:
    if not isinstance(value, str) or value not in KINDS:
        raise OrderReturnError(
            'kind_invalid', 'Elige el tipo de devolución: desistimiento, producto defectuoso, error de envío u otro.')
    return value


def _money(value, *, field) -> Decimal:
    if isinstance(value, float):
        value = str(value)
    try:
        amount = Decimal(str(value).strip().replace(',', '.'))
    except (InvalidOperation, ValueError, TypeError):
        raise OrderReturnError('amount_invalid', f'Escribe {field}.') from None
    if not amount.is_finite() or amount < ZERO or amount != amount.quantize(CENT):
        raise OrderReturnError('amount_invalid', f'{field.capitalize()} no puede ser negativo ni tener más de dos decimales.')
    return amount.quantize(CENT)


def _clean_branch(user, company, branch):
    pk = getattr(branch, 'pk', branch)
    if isinstance(pk, str) and pk.strip().isdigit():
        pk = int(pk.strip())
    if not isinstance(pk, int) or isinstance(pk, bool):
        raise OrderReturnError('branch_invalid', 'Elige la sucursal que recibe la mercadería.')
    found = receiving_branches(user, company).filter(pk=pk).first()
    if found is None:
        # Another company's, inactive or out of reach: all the same answer.
        raise OrderReturnError('branch_invalid', 'Elige una sucursal en la que trabajes.')
    return found


def _clean_lines(lines) -> list[dict]:
    if not isinstance(lines, list) or not lines:
        raise OrderReturnError('lines_required', 'Indica lo que vuelve: cada equipo por su IMEI o serie, y las cantidades.')
    if len(lines) > MAX_LINES:
        raise OrderReturnError('lines_invalid', f'Se reciben hasta {MAX_LINES} líneas por vez.')
    rows = []
    for raw in lines:
        if not isinstance(raw, dict):
            raise OrderReturnError('lines_invalid', 'Cada línea indica la línea del pedido, la cantidad y el estado.')
        item = raw.get('order_item')
        quantity = raw.get('quantity', 1)
        if isinstance(item, bool) or not isinstance(item, int) or isinstance(quantity, bool) \
                or not isinstance(quantity, int) or quantity <= 0:
            raise OrderReturnError('lines_invalid', 'Cada línea indica la línea del pedido y una cantidad mayor que cero.')
        condition = raw.get('condition')
        if not isinstance(condition, str) or condition not in DEVICE_CONDITIONS:
            raise OrderReturnError('condition_invalid', 'Indica el estado en que llega: vendible, a revisión o dañado.')
        code = raw.get('code')
        rows.append({'order_item': item, 'quantity': quantity, 'condition': condition,
                     'code': code if isinstance(code, str) else ''})
    return rows


# -- 1. request ----------------------------------------------------------------------

def request_return(order, user, kind, reason, *, request=None) -> OrderReturn:
    """
    Register that the goods of `order` — already resolved through the
    caller's scoped queryset — are coming back. Nothing moves. Raises
    `OrderReturnError`; nothing is written then.
    """
    kind = clean_kind(kind)
    reason = clean_reason(reason)
    actor = user if getattr(user, 'is_authenticated', False) else None
    if not may_request(user, order.company):
        raise OrderReturnError('not_allowed', 'Registrar una devolución corresponde a quien gestiona los pedidos.')

    with transaction.atomic():
        locked = Order.objects.select_for_update(of=('self',)).get(pk=order.pk)
        refused = request_blockers(locked)
        if refused:
            raise OrderReturnError(**refused[0])
        conditions = window_conditions(locked)
        if conditions and not may_close(user, locked.company):
            raise OrderReturnError(**superior_blocker(conditions), conditions=conditions)
        try:
            with transaction.atomic():
                created = OrderReturn.objects.create(
                    company_id=locked.company_id, order=locked, kind=kind, reason=reason, requested_by=actor)
        except IntegrityError:  # pragma: no cover — the order's lock decides first
            raise OrderReturnError('return_exists', 'Este pedido ya tiene una devolución registrada.') from None
        AdminAuditLog.log(
            actor=actor, action=ACTION_REQUESTED, target_type='order', target_id=locked.pk,
            metadata={
                'order_id': locked.pk, 'return_id': created.pk, 'kind': kind, 'reason': reason,
                'channel': locked.sales_channel, 'fulfillment_status': locked.fulfillment_status,
                'authority': 'superior' if conditions else 'simple',
                'superior_conditions': [c['code'] for c in conditions],
            },
            request=request, company=locked.company,
        )
    return created


# -- 2. receive ------------------------------------------------------------------------

def _locked_open_return(locked_order):
    return (OrderReturn.objects.select_for_update()
            .filter(order_id=locked_order.pk).exclude(status=OrderReturn.STATUS_WITHDRAWN).first())


def _find_device(order, code):
    from . import unit_identification

    if not code.strip():
        raise OrderReturnError('unit_code_required', 'Escanea o escribe el IMEI o la serie de cada equipo que vuelve.')
    try:
        return unit_identification.find_unit(order.company, code)
    except unit_identification.UnitIdentificationError as exc:
        if exc.code == 'ambiguous':
            raise OrderReturnError('unit_ambiguous', exc.detail) from None
        if exc.code == 'invalid_code':
            raise OrderReturnError('unit_code_required', exc.detail) from None
        raise OrderReturnError('unit_not_of_order', NOT_THIS_ORDERS_DEVICE) from None


NOT_THIS_ORDERS_DEVICE = 'Ese equipo no es uno de los que salieron con este pedido.'


def _plan(order, rows):
    """
    The reception, checked whole before anything moves: every line is of this
    order, every device named is one sold to it, and what comes back is
    exactly what was sold (phase 1, D1).
    """
    items = {item.pk: item for item in order.items.select_related('product')}
    planned, seen_units, returned = [], set(), {}
    for row in rows:
        item = items.get(row['order_item'])
        if item is None:
            raise OrderReturnError('lines_invalid', 'Una de las líneas no es de este pedido.')
        product = item.product
        if product.is_serialized:
            if row['quantity'] != 1:
                raise OrderReturnError('lines_invalid', f'«{product.name}» vuelve equipo por equipo: uno por línea.')
            unit = _find_device(order, row['code'])
            if unit.product_id != product.pk or unit.order_id != order.pk or unit.status != StockUnit.Status.SOLD:
                raise OrderReturnError('unit_not_of_order', NOT_THIS_ORDERS_DEVICE)
            if unit.pk in seen_units:
                raise OrderReturnError('lines_invalid', 'El mismo equipo figura dos veces.')
            seen_units.add(unit.pk)
            planned.append((item, unit, 1, row['condition']))
        else:
            if row['condition'] not in QUANTITY_CONDITIONS:
                raise OrderReturnError(
                    'condition_invalid',
                    f'«{product.name}» se cuenta por cantidad: vuelve «vendible» o «dañado» (a revisión, más adelante).')
            planned.append((item, None, row['quantity'], row['condition']))
        returned[product.pk] = returned.get(product.pk, 0) + row['quantity']

    sold, names = {}, {}
    for item in items.values():
        sold[item.product_id] = sold.get(item.product_id, 0) + item.quantity
        names[item.product_id] = item.product.name
    for product_id, quantity in sold.items():
        if returned.get(product_id, 0) != quantity:
            raise OrderReturnError(
                'lines_incomplete',
                f'La devolución es del pedido completo: de «{names[product_id]}» se vendieron {quantity} y se '
                f'indican {returned.get(product_id, 0)}.')
    # Review P2: goods of the sale put back by hand (`return_entry` after its
    # exit, same product and branch) are not entered a second time.
    by_hand = cancelling.products_returned_by_hand_after_sale(order)
    for item, unit, _quantity, _condition in planned:
        if unit is None and item.product_id in by_hand:
            raise OrderReturnError(
                'manual_return_after_sale',
                f'«{item.product.name}» ya tiene una devolución manual en el Kardex posterior a esta venta: la '
                'mercadería no entra dos veces. Revierte esa entrada manual o consulta a un superior antes de '
                'recibir la devolución.')
    # What comes back by quantity is what LEFT by quantity: a line short at
    # payment never left the stock, and must not come into it.
    for item, unit, quantity, _condition in planned:
        if unit is None and not item.product.is_serialized:
            left = sum(StockMovement.objects.filter(
                order=order, product_id=item.product_id, movement_type=StockMovement.SALE_EXIT,
            ).values_list('quantity', flat=True))
            if left != sold[item.product_id]:
                raise OrderReturnError(
                    'stock_mismatch',
                    f'«{item.product.name}» no salió completo del stock con esta venta: revisa el Kardex antes de '
                    'recibir la devolución.')
    return planned


def _exit_cost(order, unit):
    """(cost, source) of the sale exit that took `unit` out with `order`, or (None, None)."""
    for unit_cost, source, metadata in StockMovement.objects.filter(
        order=order, product_id=unit.product_id, movement_type=StockMovement.SALE_EXIT,
    ).values_list('unit_cost', 'cost_source', 'metadata'):
        if unit.pk in ((metadata or {}).get('unit_ids') or []):
            return unit_cost, (source or None)
    return None, None


def _movement(**kwargs):
    from . import inventory_services as inventory

    try:
        return inventory.create_stock_movement(**kwargs)
    except inventory.InventoryError as exc:
        raise OrderReturnError('stock_mismatch', str(exc)) from None


def _device_back(order, order_return, branch, unit, condition, actor, request, common):
    """
    One device the customer brought back: THE row that left, on the shelf of
    the receiving branch, with its `customer_return` line — then set aside or
    written off by its state. The `OrderUnit` stays: that sale named it.
    """
    from . import inventory_services as inventory
    from . import stock_unit_services as units
    from . import unit_identification

    inventory._locked_branch_stocks(branch, [unit.product])       # shelf first, then the device
    current_unit = StockUnit.objects.select_for_update().get(pk=unit.pk)
    if current_unit.status != StockUnit.Status.SOLD or current_unit.order_id != order.pk:
        raise OrderReturnError('unit_not_of_order', NOT_THIS_ORDERS_DEVICE)
    cost, source = _exit_cost(order, current_unit)
    now = timezone.now()
    if current_unit.branch_id != branch.pk:
        # A sold device is in no branch's count: it may come back to another shop.
        StockUnit.objects.filter(pk=current_unit.pk).update(branch=branch, updated_at=now)
    movement = _movement(
        quantity=1, units=[current_unit.pk], expected_unit_status=StockUnit.Status.SOLD,
        product_id=current_unit.product_id, unit_cost=cost, cost_source=source,
        metadata={**common['metadata'], 'condition': condition, 'from_branch_id': current_unit.branch_id},
        **{k: v for k, v in common.items() if k != 'metadata'},
    )
    StockUnit.objects.filter(pk=current_unit.pk).update(
        status=StockUnit.Status.AVAILABLE, sold_at=None, order=None, updated_at=now)
    back = StockUnit.objects.get(pk=current_unit.pk)
    unit_identification.released_by_return(back, order.pk, actor=actor, request=request)
    try:
        if condition == OrderReturnLine.CONDITION_REVIEW:
            units.reserve(back, reason=f'En revisión por devolución del pedido #{order.pk}', actor=actor,
                          request=request)
        elif condition == OrderReturnLine.CONDITION_DAMAGED:
            units.write_off(back, reason=f'Dañado al volver: devolución del pedido #{order.pk}', actor=actor,
                            request=request)
    except units.StockUnitError as exc:  # pragma: no cover — the device was just put on this shelf
        raise OrderReturnError('stock_mismatch', str(exc)) from None
    return movement


def receive_return(order, user, *, branch, lines, request=None) -> OrderReturn:
    """
    The goods are back in `branch`. Raises `OrderReturnError`; nothing is
    written then. Once: a second reception finds the return received.
    """
    if not may_receive(user, order.company):
        raise OrderReturnError('not_allowed', 'Recibir la mercadería corresponde a quien maneja el inventario.')
    branch = _clean_branch(user, order.company, branch)
    rows = _clean_lines(lines)
    actor = user if getattr(user, 'is_authenticated', False) else None

    with transaction.atomic():
        locked = Order.objects.select_for_update(of=('self',)).get(pk=order.pk)
        order_return = _locked_open_return(locked)
        if order_return is None:
            raise OrderReturnError('no_return', 'Este pedido no tiene una devolución registrada.')
        if order_return.status != OrderReturn.STATUS_REQUESTED:
            raise OrderReturnError('already_received', 'La mercadería de esta devolución ya se recibió.')
        refused = order_blockers(locked)
        if refused:
            raise OrderReturnError(**refused[0])
        planned = _plan(locked, rows)
        # Review P3: every shelf row of the reception up front, sorted — the
        # module's lock order (as a sale or a transfer takes them) — then the
        # devices by id. The per-line writers take them again, harmlessly.
        from . import inventory_services as inventory

        inventory._locked_branch_stocks(branch, sorted({item.product for item, *_ in planned}, key=lambda p: p.pk))
        devices = sorted(unit.pk for _item, unit, *_ in planned if unit is not None)
        if devices:
            list(StockUnit.objects.select_for_update().filter(pk__in=devices).order_by('pk'))

        movements, written = [], []
        for item, unit, quantity, condition in planned:
            common = dict(
                branch=branch, movement_type=StockMovement.CUSTOMER_RETURN, actor=actor, order=locked,
                reason=f'Devolución del pedido #{locked.pk}', reference_type='order_return',
                reference_id=order_return.pk,
                metadata={'order_id': locked.pk, 'return_id': order_return.pk, 'order_item_id': item.pk},
            )
            if unit is not None:
                movement = _device_back(locked, order_return, branch, unit, condition, actor, request, common)
            else:
                # No cost stated: the writer values it at the cost the order's
                # sale exits of this product left with — never today's average.
                movement = _movement(
                    product_id=item.product_id, quantity=quantity,
                    **{**common, 'metadata': {**common['metadata'], 'condition': condition}})
                if condition == OrderReturnLine.CONDITION_DAMAGED:
                    movements.append(_movement(
                        branch=branch, product_id=item.product_id, movement_type=StockMovement.DAMAGED_EXIT,
                        quantity=quantity, actor=actor, order=locked,
                        reason=f'Dañado al volver: devolución del pedido #{locked.pk}',
                        reference_type='order_return', reference_id=order_return.pk,
                        metadata={'order_id': locked.pk, 'return_id': order_return.pk, 'order_item_id': item.pk,
                                  'condition': condition},
                    ).pk)
            movements.append(movement.pk)
            written.append(OrderReturnLine.objects.create(
                order_return=order_return, order_item=item, unit=unit, quantity=quantity, condition=condition,
                movement=movement))

        order_return.status = OrderReturn.STATUS_RECEIVED
        order_return.received_by = actor
        order_return.received_at = timezone.now()
        order_return.receiving_branch = branch
        order_return.save(update_fields=['status', 'received_by', 'received_at', 'receiving_branch'])
        AdminAuditLog.log(
            actor=actor, action=ACTION_RECEIVED, target_type='order', target_id=locked.pk,
            metadata={
                'order_id': locked.pk, 'return_id': order_return.pk, 'branch_id': branch.pk,
                # Ids and states only: the devices are the record, not their numbers.
                'lines': [{'line_id': line.pk, 'order_item_id': line.order_item_id, 'unit_id': line.unit_id,
                           'quantity': line.quantity, 'condition': line.condition} for line in written],
                'movements': sorted(movements),
                'fulfillment_status': locked.fulfillment_status,
            },
            request=request, company=locked.company,
        )
    return order_return


# -- 3. close ----------------------------------------------------------------------------

def close_return(order, user, *, refund_amount, deduction_amount=None, deduction_reason='', refund_method,
                 request=None) -> OrderReturn:
    """
    A superior confirms the amount to give back (what was paid − the
    deduction) and the return becomes the order's commercial cancellation,
    with ONE pending refund of that amount. Raises `OrderReturnError`;
    nothing is written then.
    """
    from . import order_refunds

    if not may_close(user, order.company):
        raise OrderReturnError(
            'superior_required', 'Cerrar una devolución corresponde a un superior (administrador de la empresa).')
    deduction = ZERO if deduction_amount in (None, '') else _money(deduction_amount, field='el descuento')
    if refund_amount in (None, ''):
        raise OrderReturnError('amount_invalid', 'Escribe el importe a devolver.')
    stated = _money(refund_amount, field='el importe a devolver')
    note = _text(deduction_reason)
    if deduction > ZERO and len(note) < DEDUCTION_REASON_MIN:
        raise OrderReturnError(
            'deduction_reason_required',
            f'Escribe el motivo del descuento (al menos {DEDUCTION_REASON_MIN} caracteres), por ejemplo el flete '
            'de vuelta.')
    if len(note) > DEDUCTION_REASON_MAX:
        raise OrderReturnError('deduction_reason_too_long',
                               f'El motivo del descuento no puede pasar de {DEDUCTION_REASON_MAX} caracteres.')
    if deduction == ZERO:
        note = ''
    try:
        method = order_refunds.clean_method(refund_method)
    except order_refunds.RefundError as exc:
        raise OrderReturnError('method_invalid', exc.detail) from None
    actor = user if getattr(user, 'is_authenticated', False) else None

    with transaction.atomic():
        locked = Order.objects.select_for_update(of=('self',)).get(pk=order.pk)
        order_return = _locked_open_return(locked)
        if order_return is None:
            raise OrderReturnError('no_return', 'Este pedido no tiene una devolución registrada.')
        if order_return.status == OrderReturn.STATUS_CLOSED:
            raise OrderReturnError('already_closed', 'Esta devolución ya se cerró.')
        if order_return.status != OrderReturn.STATUS_RECEIVED:
            raise OrderReturnError('not_received', 'Primero se recibe la mercadería; después se cierra la devolución.')
        paid = (locked.total if locked.paid else ZERO).quantize(CENT)
        if deduction > paid:
            raise OrderReturnError('deduction_exceeds_paid',
                                   f'El descuento no puede pasar de lo cobrado (S/ {paid}).')
        refund = paid - deduction
        if stated != refund:
            raise OrderReturnError(
                'amount_mismatch',
                f'El importe a devolver es S/ {refund}: lo cobrado (S/ {paid}) menos el descuento (S/ {deduction}).')
        reason = f'Devolución posterior a la entrega: {order_return.reason}'[:cancelling.REASON_MAX]
        try:
            cancellation = cancelling.cancel_for_return(
                locked, user, reason, refund_method=method, refund_amount=refund, return_id=order_return.pk,
                request=request)
        except cancelling.OrderCancelError as exc:
            raise OrderReturnError(exc.code, exc.detail) from None
        except order_refunds.RefundError as exc:
            raise OrderReturnError(exc.code, exc.detail) from None

        order_return.status = OrderReturn.STATUS_CLOSED
        order_return.closed_by = actor
        order_return.closed_at = timezone.now()
        order_return.paid_amount = paid
        order_return.deduction_amount = deduction
        order_return.deduction_reason = note
        order_return.refund_amount = refund
        order_return.refund_method = method
        order_return.cancellation = cancellation
        order_return.save(update_fields=[
            'status', 'closed_by', 'closed_at', 'paid_amount', 'deduction_amount', 'deduction_reason',
            'refund_amount', 'refund_method', 'cancellation'])
        refund_row = (OrderRefund.objects.filter(order=locked, status=OrderRefund.STATUS_PENDING)
                      .order_by('-pk').first() if refund > ZERO else None)
        AdminAuditLog.log(
            actor=actor, action=ACTION_CLOSED, target_type='order', target_id=locked.pk,
            metadata={
                'order_id': locked.pk, 'return_id': order_return.pk, 'cancellation_id': cancellation.pk,
                'refund_id': refund_row.pk if refund_row else None, 'refund_method': method,
                'paid_amount': str(paid), 'deduction_amount': str(deduction), 'refund_amount': str(refund),
                # Whether a reason was given, not the text: it may name people.
                'deduction_reason_given': bool(note),
            },
            request=request, company=locked.company,
        )
    return order_return


# -- 4. withdraw ---------------------------------------------------------------------------

def withdraw_return(order, user, reason, *, request=None) -> OrderReturn:
    """Abandon a return before its goods are received, with the reason. The requester or a superior."""
    reason = clean_reason(reason, what='para retirar la devolución')
    actor = user if getattr(user, 'is_authenticated', False) else None

    with transaction.atomic():
        locked = Order.objects.select_for_update(of=('self',)).get(pk=order.pk)
        order_return = _locked_open_return(locked)
        if order_return is None:
            raise OrderReturnError('no_return', 'Este pedido no tiene una devolución registrada.')
        if order_return.status != OrderReturn.STATUS_REQUESTED:
            raise OrderReturnError(
                'already_received', 'La mercadería ya se recibió: esta devolución no se retira, se cierra.')
        if not may_withdraw(user, locked.company, order_return):
            raise OrderReturnError('not_the_requester', 'Retira la devolución quien la registró, o un superior.')
        order_return.status = OrderReturn.STATUS_WITHDRAWN
        order_return.withdrawn_by = actor
        order_return.withdrawn_at = timezone.now()
        order_return.withdrawn_reason = reason
        order_return.save(update_fields=['status', 'withdrawn_by', 'withdrawn_at', 'withdrawn_reason'])
        AdminAuditLog.log(
            actor=actor, action=ACTION_WITHDRAWN, target_type='order', target_id=locked.pk,
            metadata={'order_id': locked.pk, 'return_id': order_return.pk, 'reason': reason},
            request=request, company=locked.company,
        )
    return order_return


# -- reads ----------------------------------------------------------------------------------

def _label(user) -> str:
    from .left_marking import actor_label

    return actor_label(user) if user is not None else ''


def _iso(value):
    return value.isoformat() if value else None


def _money_str(value):
    return str(value) if value is not None else None


def describe(order_return) -> dict | None:
    """A return as the panel shows it. Device numbers masked, as on every screen read by more people."""
    from .device_identity import mask

    if order_return is None:
        return None
    lines = (order_return.lines.select_related('order_item__product', 'unit').order_by('pk'))
    branch = order_return.receiving_branch
    return {
        'id': order_return.pk,
        'status': order_return.status,
        'status_label': order_return.get_status_display(),
        'kind': order_return.kind,
        'kind_label': order_return.get_kind_display(),
        'reason': order_return.reason,
        'requested_at': _iso(order_return.requested_at),
        'requested_by': _label(order_return.requested_by),
        'received_at': _iso(order_return.received_at),
        'received_by': _label(order_return.received_by),
        'receiving_branch': {'id': branch.pk, 'name': branch.name} if branch else None,
        'closed_at': _iso(order_return.closed_at),
        'closed_by': _label(order_return.closed_by),
        'withdrawn_at': _iso(order_return.withdrawn_at),
        'withdrawn_by': _label(order_return.withdrawn_by),
        'withdrawn_reason': order_return.withdrawn_reason,
        'paid_amount': _money_str(order_return.paid_amount),
        'deduction_amount': _money_str(order_return.deduction_amount),
        'deduction_reason': order_return.deduction_reason,
        'refund_amount': _money_str(order_return.refund_amount),
        'refund_method': order_return.refund_method,
        'refund_method_label': order_return.get_refund_method_display() if order_return.refund_method else '',
        'cancellation_id': order_return.cancellation_id,
        'lines': [
            {
                'id': line.pk,
                'order_item': line.order_item_id,
                'product_id': line.order_item.product_id,
                'product_name': line.order_item.product.name if line.order_item.product_id else '',
                'quantity': line.quantity,
                'unit_id': line.unit_id,
                'serial_number': mask(line.unit.serial_number) if line.unit_id else '',
                'imei': mask(line.unit.imei) if line.unit_id else '',
                'condition': line.condition,
                'condition_label': line.get_condition_display(),
            }
            for line in lines
        ],
    }


def block(order, user, company) -> dict:
    """
    Everything the order detail says about returns, for THIS caller. Server-
    decided, like the cancellation's: what may be done now, and why not —
    the «why not» to whoever could act on it.
    """
    open_or_closed = current(order)
    shown = open_or_closed or OrderReturn.objects.filter(order_id=order.pk).order_by('-requested_at', '-pk').first()
    refused = order_blockers(order)
    requester, receiver, superior = may_request(user, company), may_receive(user, company), may_close(user, company)

    blockers = request_blockers(order)
    conditions = window_conditions(order) if not blockers else []
    if conditions and not superior:
        blockers = [superior_blocker(conditions)]
    status = open_or_closed.status if open_or_closed is not None else None

    branches = []
    can_receive = status == OrderReturn.STATUS_REQUESTED and receiver and not refused
    if can_receive:
        branches = [{'id': b.pk, 'name': b.name} for b in receiving_branches(user, company)]
        can_receive = bool(branches)
    return {
        'eligible': not refused,
        'can_request': requester and not blockers,
        'can_receive': can_receive,
        'can_close': status == OrderReturn.STATUS_RECEIVED and superior,
        'can_withdraw': status == OrderReturn.STATUS_REQUESTED and may_withdraw(user, company, open_or_closed),
        'superior_needed': bool(conditions),
        'superior_conditions': conditions if requester else [],
        'blockers': blockers if (requester or receiver) else [],
        'window_days': window_days(company),
        'paid_amount': str((order.total if order.paid else ZERO).quantize(CENT)),
        'receive_branches': branches,
        'return': describe(shown),
    }


__all__ = [
    'OrderReturnError', 'block', 'close_return', 'current', 'delivered_at', 'describe', 'may_close',
    'may_receive', 'may_request', 'may_withdraw', 'order_blockers', 'receive_return', 'request_blockers',
    'request_return', 'window_conditions', 'window_days', 'withdraw_return',
]
