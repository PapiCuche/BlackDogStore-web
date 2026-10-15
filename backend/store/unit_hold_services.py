"""
DEVICE-COMMERCE-01 · DC2 — a buyer's cart holds a CONCRETE device.

    AVAILABLE ──(cart)──▶ RESERVED ──(payment)──▶ SOLD
        ▲                    │
        └──(expiry, removal)─┘

The owner's decision of 2026-10-14: what the web sells by unit — every
serialized variant of a model, new or used — is bought device by device. The
cart takes one device off the shelf and holds it for a while; the device the
buyer paid for is the one that leaves, at the price the buyer was shown.

A HOLD IS STOCK. It is written like a staff «apartado»: through
`stock_unit_services._move`, so the Kardex says the device left the shelf and
came back, and `BranchStock.quantity == AVAILABLE units` stays true at every
commit. A till cannot sell a device a buyer holds, and two buyers cannot hold
the same one: both queue on the shelf row and the second finds it RESERVED.

WHO HOLDS IT. The SHA-256 of the cart's key (`hold_key`), never the key. A
staff reservation has an empty `hold_key`: it never expires and no cart can
touch it.

EXPIRY. `hold_until`; extended once an order carries it (`hold_order`), so
the payment form has time. An expired hold goes back to the shelf when
anybody next reads that shelf (`release_expired`) and every few minutes
(`release_expired_unit_holds`). A hold whose order was paid never expires:
the payment sold the device in the same transaction.
"""
from __future__ import annotations

import hashlib
from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Count, DateTimeField, ExpressionWrapper, F, Value
from django.db.models.functions import Coalesce, Least
from django.utils import timezone

from . import inventory_services
from .models import CartItem, Order, Product, StockMovement, StockUnit
from .stock_unit_services import StockUnitError, _move

HOLD_REASON = 'Apartado web: carrito'
RELEASE_REASON = 'Apartado web liberado'
SALE_RELEASE_REASON = 'Apartado web: pasa a la venta'


class HoldError(Exception):
    """The buyer cannot have that device. `code` is stable for the shop's page."""

    STATUS = {'not_found': 404, 'unit_taken': 409, 'sold_out': 409, 'too_many': 409, 'held_by_others': 409}

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail

    @property
    def http_status(self) -> int:
        return self.STATUS.get(self.code, 400)

    def body(self) -> dict:
        return {'detail': self.detail, 'code': self.code}


def hold_minutes() -> int:
    return int(getattr(settings, 'DEVICE_HOLD_MINUTES', 15))


def order_hold_minutes() -> int:
    return int(getattr(settings, 'DEVICE_ORDER_HOLD_MINUTES', 45))


def max_holds_per_cart() -> int:
    return int(getattr(settings, 'DEVICE_HOLD_MAX_PER_CART', 3))


def max_holds_per_origin() -> int:
    return int(getattr(settings, 'DEVICE_HOLD_MAX_PER_ORIGIN', 3))


def max_share_percent() -> int:
    return int(getattr(settings, 'DEVICE_WEB_HOLD_MAX_SHARE_PERCENT', 50))


def hold_ceiling_minutes() -> int:
    """The longest a web hold lives from the moment a cart took it, checkouts included."""
    return hold_minutes() + order_hold_minutes()


def web_hold_cap(pool: int) -> int:
    """
    How many devices of one product in one branch web carts may hold at once.

    `pool` = devices on the shelf + devices web carts hold. At most
    `DEVICE_WEB_HOLD_MAX_SHARE_PERCENT` of it (50 %), rounded down, and never
    less than one: a shelf of one can still be bought online.
    """
    return max(1, pool * max_share_percent() // 100)


def key_of(session_key: str) -> str:
    return hashlib.sha256(str(session_key or '').encode('utf-8')).hexdigest()


def price_for(unit, product) -> object:
    """The web price of one device: its own when used, its variant's when new. None = not for sale."""
    if product.condition_line == Product.CONDITION_USED:
        return unit.sale_price
    return product.price


def held_units(session_key, company, *, product=None, now=None):
    """The devices THIS cart holds in this company, unexpired, oldest hold first."""
    now = now or timezone.now()
    qs = StockUnit.objects.filter(
        company=company, status=StockUnit.Status.RESERVED, hold_key=key_of(session_key),
        hold_until__gt=now,
    )
    if product is not None:
        qs = qs.filter(product=product)
    return qs.order_by('updated_at', 'pk')


def _sync_cart_line(session_key, product) -> None:
    """The cart line of a by-unit product says how many devices the cart holds."""
    count = held_units(session_key, product.company, product=product).count()
    if count:
        CartItem.objects.update_or_create(
            session_key=session_key, product=product, defaults={'quantity': count},
        )
    else:
        CartItem.objects.filter(session_key=session_key, product=product).delete()


def _clear_hold_fields():
    return {'hold_key': '', 'hold_until': None, 'hold_price': None, 'hold_order': None,
            'hold_origin': '', 'hold_started_at': None}


def _live_web_holds(company, now):
    return StockUnit.objects.filter(
        company=company, status=StockUnit.Status.RESERVED, hold_until__gt=now,
    ).exclude(hold_key='')


def _lock_holders(company_id, key, origin) -> None:
    """
    Transaction-scoped advisory locks on «this cart» and «this origin», in one
    fixed order (sorted), always before any shelf lock — so two holds wait
    for each other instead of deadlocking.
    """
    if connection.vendor != 'postgresql':
        return
    ids = sorted({
        int.from_bytes(hashlib.sha256(f'dc2-hold|{company_id}|{kind}|{value}'.encode()).digest()[:8],
                       'big', signed=True)
        for kind, value in (('cart', key), ('origin', origin)) if value
    })
    with connection.cursor() as cursor:
        for lock_id in ids:
            cursor.execute('SELECT pg_advisory_xact_lock(%s)', [lock_id])


@transaction.atomic
def hold(*, product, branch, session_key, unit_public_id=None, origin='') -> StockUnit:
    """
    Put one device of `product` in this cart.

    `unit_public_id`: the device the buyer picked (a used one). Without it —
    a new device — the oldest available of the variant, decided under the
    shelf lock so two buyers never pick the same one.

    `origin`: `order_risk.origin_hash` of the request ('' = unknown). Three
    limits keep one client from emptying a shelf: holds per cart, holds per
    origin across every cart, and the share of a shelf web holds may take.
    """
    if not session_key:
        raise HoldError('invalid', 'session_key es requerido.')
    if branch is None:
        raise HoldError('sold_out', 'La tienda no tiene una sucursal de despacho configurada.')
    if not product.sells_by_unit or not product.is_sold_online:
        raise HoldError('not_found', 'Producto no encontrado o no disponible.')
    if product.condition_line == Product.CONDITION_USED and not unit_public_id:
        raise HoldError('unit_required', 'Elige el equipo que quieres comprar.')

    release_expired(company=product.company, product=product)
    key = key_of(session_key)
    # The counts below are decided under a lock of this cart and this origin,
    # taken BEFORE the shelf: two adds of one cart for two products lock two
    # different shelves, and would otherwise both count the same «2 of 3».
    _lock_holders(product.company_id, key, origin)
    now = timezone.now()
    holding = _live_web_holds(product.company, now).filter(hold_key=key).count()
    if holding >= max_holds_per_cart():
        raise HoldError('too_many', f'Puedes apartar hasta {max_holds_per_cart()} equipos a la vez.')
    if origin and _live_web_holds(product.company, now).filter(hold_origin=origin).count() >= max_holds_per_origin():
        raise HoldError('too_many', f'Puedes apartar hasta {max_holds_per_origin()} equipos a la vez.')

    # THE SHELF FIRST — the module's one lock order — then the device.
    inventory_services._locked_branch_stocks(branch, [product])
    base = StockUnit.objects.select_for_update().filter(
        company_id=branch.company_id, branch=branch, product=product,
    )
    if unit_public_id:
        try:
            unit = base.filter(public_id=unit_public_id).first()
        except Exception:  # noqa: BLE001 - not a UUID: no such device
            unit = None
        if unit is None:
            raise HoldError('not_found', 'Ese equipo no existe o ya no está a la venta.')
        if unit.status != StockUnit.Status.AVAILABLE:
            raise HoldError('unit_taken', 'Otra persona acaba de apartar ese equipo.')
    else:
        unit = base.filter(status=StockUnit.Status.AVAILABLE).order_by('received_at', 'pk').first()
        if unit is None:
            raise HoldError('sold_out', f'«{product.name}» está agotado.')

    price = price_for(unit, product)
    if price is None:
        raise HoldError('not_found', 'Ese equipo no existe o ya no está a la venta.')

    # THE SHARE, under the shelf lock: two buyers of this product count the same
    # shelf. After the device is chosen, so «that one is taken» and «sold out»
    # keep their own answers.
    web_held = _live_web_holds(product.company, now).filter(branch=branch, product=product).count()
    pool = base.filter(status=StockUnit.Status.AVAILABLE).count() + web_held
    if web_held >= web_hold_cap(pool):
        raise HoldError(
            'held_by_others',
            f'Los demás equipos de «{product.name}» están apartados por otros compradores. '
            'Vuelve a intentarlo en unos minutos.',
        )

    try:
        _move(
            unit, expected=StockUnit.Status.AVAILABLE, to_status=StockUnit.Status.RESERVED,
            movement_type=StockMovement.MANUAL_EXIT, reason=HOLD_REASON, actor=None,
            action='stock_unit_web_hold', metadata={'web_hold': True},
            extra_fields={
                'hold_key': key, 'hold_until': now + timedelta(minutes=hold_minutes()),
                'hold_price': price, 'hold_order': None, 'hold_origin': origin or '', 'hold_started_at': now,
            },
        )
    except StockUnitError:
        raise HoldError('unit_taken', 'Otra persona acaba de apartar ese equipo.') from None
    _sync_cart_line(session_key, product)
    return StockUnit.objects.get(pk=unit.pk)


def _release(unit, *, reason=RELEASE_REASON) -> None:
    _move(
        unit, expected=StockUnit.Status.RESERVED, to_status=StockUnit.Status.AVAILABLE,
        movement_type=StockMovement.MANUAL_ENTRY, reason=reason, actor=None,
        action='stock_unit_web_hold_released', metadata={'web_hold': True},
        extra_fields=_clear_hold_fields(),
    )


@transaction.atomic
def release(*, company, session_key, unit_public_id=None, product=None) -> int:
    """
    The buyer takes devices out of the cart: one (`unit_public_id`) or every
    one of `product`. Only THIS cart's holds; a pending order's holds are
    left alone — that order may still be paid.
    """
    qs = StockUnit.objects.filter(
        company=company, status=StockUnit.Status.RESERVED, hold_key=key_of(session_key),
    )
    if unit_public_id:
        try:
            qs = qs.filter(public_id=unit_public_id)
            list(qs[:1])
        except Exception:  # noqa: BLE001 - not a UUID
            return 0
    if product is not None:
        qs = qs.filter(product=product)
    released, touched = 0, set()
    for unit in qs.select_related('product'):
        if unit.hold_order_id and _order_is_live(unit.hold_order_id):
            continue
        _release(unit)
        touched.add(unit.product)
        released += 1
    for item in touched:
        _sync_cart_line(session_key, item)
    return released


def _order_is_live(order_id) -> bool:
    """An order that may still be paid keeps its devices until its hold runs out."""
    return Order.objects.filter(
        pk=order_id, status=Order.Status.PENDING_PAYMENT, paid=False,
    ).exists()


ROLLBACK_RELEASE_REASON = 'Apartado web liberado: vuelta a la versión anterior'


def release_all_web(*, company=None) -> int:
    """
    EVERY web hold back to the shelf, expired or not, whatever order carries it
    — before going back to a release that does not know web holds (its staff
    reservations never expire, so a web hold left behind would stay reserved
    for ever). Each one with its Kardex line and its audit row. Never a staff
    reservation.
    """
    qs = StockUnit.objects.filter(status=StockUnit.Status.RESERVED).exclude(hold_key='')
    if company is not None:
        qs = qs.filter(company=company)
    released = 0
    for pk in list(qs.values_list('pk', flat=True)):
        with transaction.atomic():
            unit = StockUnit.objects.select_related('product').get(pk=pk)
            if unit.status != StockUnit.Status.RESERVED or not unit.hold_key:
                continue
            try:
                _release(unit, reason=ROLLBACK_RELEASE_REASON)
            except StockUnitError:
                continue
            released += 1
    return released


def release_expired(*, company=None, product=None, now=None) -> int:
    """
    Every web hold whose time ran out goes back to the shelf, with its Kardex
    line. Never a staff reservation, never a device of a paid order.
    """
    now = now or timezone.now()
    qs = StockUnit.objects.filter(
        status=StockUnit.Status.RESERVED, hold_until__lte=now,
    ).exclude(hold_key='')
    if company is not None:
        qs = qs.filter(company=company)
    if product is not None:
        qs = qs.filter(product=product)
    released = 0
    for pk in list(qs.values_list('pk', flat=True)):
        with transaction.atomic():
            unit = StockUnit.objects.select_related('product').get(pk=pk)
            # Read again: the payment may have sold it, or a buyer extended it.
            if (unit.status != StockUnit.Status.RESERVED or not unit.hold_key
                    or unit.hold_until is None or unit.hold_until > now):
                continue
            try:
                _release(unit)
            except StockUnitError:
                continue      # it moved meanwhile: whoever moved it decided
            released += 1
    return released


def sync_cart(session_key, company) -> None:
    """Expired holds back to the shelf, and every by-unit line says what the cart still holds."""
    if not session_key or company is None:
        return
    release_expired(company=company)
    items = CartItem.objects.filter(session_key=session_key, product__company=company).select_related('product')
    for item in items:
        if item.product.sells_by_unit:
            _sync_cart_line(session_key, item.product)


def bind_to_order(units, order, session_key) -> None:
    """
    Checkout: these holds belong to `order` now, and live long enough to pay.

    ONLY THIS CART'S LIVE HOLDS. Time passed since the cart was read: a hold
    may have run out, gone back to the shelf and been taken by another cart.
    Binding by pk alone would hand that other buyer's device to this order.
    Anything short of every device still held here refuses the whole order —
    the caller's transaction rolls back and the buyer is told.
    """
    from .checkout_services import CheckoutError

    now = timezone.now()
    # NEVER PAST THE CEILING. Every checkout binds again; without a ceiling a
    # cart that checks out every 40 minutes would hold a device for ever.
    ceiling = ExpressionWrapper(
        Coalesce(F('hold_started_at'), Value(now)) + Value(timedelta(minutes=hold_ceiling_minutes())),
        output_field=DateTimeField(),
    )
    bound = StockUnit.objects.filter(
        pk__in=[unit.pk for unit in units], status=StockUnit.Status.RESERVED,
        hold_key=key_of(session_key), hold_until__gt=now,
    ).update(
        hold_order=order,
        hold_until=Least(Value(now + timedelta(minutes=order_hold_minutes()), output_field=DateTimeField()),
                         ceiling),
        updated_at=now,
    )
    if bound < len({unit.pk for unit in units}):
        raise CheckoutError(
            'Problemas con el carrito.', code='cart_changed',
            errors=['Uno de los equipos de tu carrito ya no está apartado para ti. Revisa tu carrito.'],
        )


def superseded_lines(order) -> dict:
    """
    The device lines of `order` that a LATER checkout of the same cart took:
    {stock unit id: the later order}. Empty for anything else.

    One cart, two checkouts (two tabs, a retry): the second order binds the
    same devices, and the first must not be able to sell them too — that is
    one device and two payments. The later order wins while it may still be
    paid or was paid; a later order that failed or was cancelled takes
    nothing from the first. Derived from the order lines, so it survives the
    hold running out and going back to the shelf.
    """
    from .models import OrderItem

    if not order.cart_session_key:
        return {}
    mine = list(order.items.exclude(stock_unit=None).values_list('stock_unit_id', flat=True))
    if not mine:
        return {}
    later = (
        OrderItem.objects.filter(
            stock_unit_id__in=mine, order__company_id=order.company_id,
            order__cart_session_key=order.cart_session_key, order__pk__gt=order.pk,
            order__status__in=(Order.Status.PENDING_PAYMENT, Order.Status.PAID),
        )
        .select_related('order').order_by('order_id')
    )
    return {item.stock_unit_id: item.order for item in later}


def holds_by_product(session_key, company) -> dict:
    """product id -> how many devices this cart holds (for a cart line)."""
    return dict(
        held_units(session_key, company).values('product_id').annotate(n=Count('pk')).values_list('product_id', 'n')
    )


def release_for_sale(unit, order) -> None:
    """
    The payment confirmed: the hold steps aside so the SALE exit can name the
    device. Inside the payment's transaction, under the shelf lock the sale
    takes next — nobody can take the device in between.
    """
    _move(
        unit, expected=StockUnit.Status.RESERVED, to_status=StockUnit.Status.AVAILABLE,
        movement_type=StockMovement.MANUAL_ENTRY, reason=SALE_RELEASE_REASON, actor=None,
        action='stock_unit_web_hold_sold', metadata={'web_hold': True, 'order_id': order.pk},
        extra_fields=_clear_hold_fields(),
    )
