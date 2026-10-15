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

OWNER'S DECISION OF 2026-10-15. The times are the company's, per channel
(`CompanySettings.device_cart_hold_minutes` / `device_checkout_hold_minutes`
/ `device_hold_ceiling_minutes`: 15 / 45 / 60 as created). A hold whose order
has a payment the gateway may still confirm is not released by its clock —
reconciliation decides (`payment_keeps_hold`). Releasing is decided under the
shelf and device locks, so it happens once (`release_one_expired`).
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
from .models import CartItem, Order, Product, ProductModel, StockMovement, StockUnit
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


def _company_minutes(company, field):
    """DEVICE-COMMERCE-01 (owner, 2026-10-15) — the company's own hold time, or None without a settings row."""
    if company is None:
        return None
    from .models import CompanySettings

    company_id = getattr(company, 'pk', company)
    return CompanySettings.objects.filter(company_id=company_id).values_list(field, flat=True).first()


def hold_minutes(company=None) -> int:
    """How long a CART holds a device: the company's (`device_cart_hold_minutes`), 15 as created."""
    value = _company_minutes(company, 'device_cart_hold_minutes')
    return int(value if value is not None else getattr(settings, 'DEVICE_HOLD_MINUTES', 15))


def order_hold_minutes(company=None) -> int:
    """How long a CHECKOUT holds it: the company's (`device_checkout_hold_minutes`), 45 as created."""
    value = _company_minutes(company, 'device_checkout_hold_minutes')
    return int(value if value is not None else getattr(settings, 'DEVICE_ORDER_HOLD_MINUTES', 45))


def max_holds_per_cart() -> int:
    return int(getattr(settings, 'DEVICE_HOLD_MAX_PER_CART', 3))


def max_holds_per_origin() -> int:
    return int(getattr(settings, 'DEVICE_HOLD_MAX_PER_ORIGIN', 3))


def max_share_percent() -> int:
    return int(getattr(settings, 'DEVICE_WEB_HOLD_MAX_SHARE_PERCENT', 50))


def hold_ceiling_minutes(company=None) -> int:
    """
    The longest a web hold lives BY ITS CLOCK from the moment a cart took it,
    checkouts included: the company's (`device_hold_ceiling_minutes`), 60 as
    created. A pending payment may keep it longer (`payment_keeps_hold`).
    """
    value = _company_minutes(company, 'device_hold_ceiling_minutes')
    return int(value) if value is not None else hold_minutes() + order_hold_minutes()


#: OWNER'S DECISION OF 2026-10-15 — never release a hold bound to a payment the
#: gateway may still confirm, without reconciliation. «May still confirm», by
#: this codebase's own rules (`payments.reconcile`): an attempt still PENDING
#: and younger than the reconciliation window (2 h). Inside it the gateway's
#: answer — its notification, or `reconcile_payments` asking `Order/Get` every
#: 5 minutes — settles it: authorised → the payment sells the device;
#: rejected, or abandoned (closed in the window's last 10 minutes when the
#: gateway holds nothing) → back to the shelf. Past it nothing asks the
#: gateway any more, so the clock decides again — except for an answer nobody
#: could settle (marked `undecided: ` / `unreadable: `): money may have moved,
#: and the hold waits for a person (`ops_status` names them).
def payment_horizon() -> timedelta:
    from .payments import reconcile

    return reconcile.WINDOW


def payment_keeps_hold(unit, now=None, *, include_unsettled: bool = True) -> bool:
    """
    Whether the payment of the order that carries this web hold may still be
    confirmed, so the hold's clock must not release it.

    Only attempts opened while the hold was alive (`created_at <= hold_until`):
    a payment opened after the hold ran out cannot revive it. Only while the
    order may still be paid. `include_unsettled=False` is the till's question:
    a person releasing by hand after looking at the gateway's panel IS the
    reconciliation of an answer nobody could settle.
    """
    if not unit.hold_order_id or not unit.hold_key:
        return False
    if not _order_is_live(unit.hold_order_id):
        return False
    from django.db.models import Q

    from .models import PaymentTransaction
    from .payments import reconcile

    now = now or timezone.now()
    attempts = PaymentTransaction.objects.filter(
        order_id=unit.hold_order_id, status=PaymentTransaction.Status.PENDING,
    )
    if unit.hold_until is not None:
        attempts = attempts.filter(created_at__lte=unit.hold_until)
    alive = Q(created_at__gt=now - payment_horizon())
    if include_unsettled:
        alive |= Q(failure_reason__startswith=reconcile.UNDECIDED_MARK) | \
            Q(failure_reason__startswith=reconcile.UNREADABLE_MARK)
    return attempts.filter(alive).exists()


def share_min_units() -> int:
    return int(getattr(settings, 'DEVICE_WEB_HOLD_SHARE_MIN_UNITS', 4))


def company_share_percent() -> int:
    return int(getattr(settings, 'DEVICE_WEB_HOLD_COMPANY_SHARE_PERCENT', 50))


def company_min_holds() -> int:
    return int(getattr(settings, 'DEVICE_WEB_HOLD_COMPANY_MIN', 3))


def web_hold_cap(pool: int) -> int | None:
    """
    How many devices of one product in one branch web carts may hold at once.

    `pool` = devices on the shelf + devices web carts hold. At most
    `DEVICE_WEB_HOLD_MAX_SHARE_PERCENT` of it (50 %), rounded down — but only
    from `DEVICE_WEB_HOLD_SHARE_MIN_UNITS` (4) devices up: on a smaller shelf
    (a used variant of two or three devices, each a different phone) the share
    blocked real buyers of distinct devices (DC2 second review, P1). None = no
    share for this shelf; the company ceiling and the per-origin cap still hold.
    """
    if pool < share_min_units():
        return None
    return max(1, pool * max_share_percent() // 100)


def company_hold_cap(pool: int) -> int:
    """
    How many devices sold by unit web carts may hold at once in one company.

    `pool` = the company's web-available devices sold by unit + those web
    carts hold. `DEVICE_WEB_HOLD_COMPANY_SHARE_PERCENT` of it (50 %), never
    less than `DEVICE_WEB_HOLD_COMPANY_MIN` (3): what stops many origins from
    holding every small shelf at once.
    """
    return max(company_min_holds(), pool * company_share_percent() // 100)


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
    if CartItem.objects.filter(session_key=session_key, product=product,
                               supply_origin=CartItem.SUPPLY_SUPPLIER).exists():
        return        # DC3 — taken on order: it holds no device and says its own quantity.
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
    """
    The web holds that take a device from the shelf right now: unexpired, OR
    past their clock but bound to an order that may still be paid — a pending
    payment keeps those (`payment_keeps_hold`) up to the reconciliation window
    and beyond, so they count toward every cap and pool like any other hold
    (integration review of PR #123, P1: «add, checkout, open the payment form,
    close the tab» repeated from one origin reserved a whole shelf).

    An expired hold without a live order is NOT counted: nothing keeps it and
    the next sweep returns it — `hold()` sweeps only its own product, so
    counting it would refuse a buyer over another shelf's leftovers.
    """
    from django.db.models import Q

    return StockUnit.objects.filter(
        company=company, status=StockUnit.Status.RESERVED,
    ).exclude(hold_key='').filter(
        Q(hold_until__gt=now)
        | Q(hold_order__status=Order.Status.PENDING_PAYMENT, hold_order__paid=False),
    )


def _web_available_by_unit(branch) -> int:
    """The devices the web could hold right now in the branch that ships: sold by unit, online, on the shelf."""
    from django.db.models import Q

    return StockUnit.objects.filter(
        company_id=branch.company_id, branch=branch, status=StockUnit.Status.AVAILABLE,
        product__is_serialized=True, product__product_model__isnull=False,
        product__is_active=True, product__is_published_online=True,
    ).filter(
        Q(product__condition_line=Product.CONDITION_NEW) | Q(sale_price__isnull=False),
    ).count()


def _lock_holders(company_id, key, origin) -> None:
    """
    Transaction-scoped advisory locks on «this company», «this cart» and «this origin», in one
    fixed order (sorted), always before any shelf lock — so two holds wait
    for each other instead of deadlocking.
    """
    if connection.vendor != 'postgresql':
        return
    ids = sorted({
        int.from_bytes(hashlib.sha256(f'dc2-hold|{company_id}|{kind}|{value}'.encode()).digest()[:8],
                       'big', signed=True)
        # «this company» too (DC2 second review, P1): the company ceiling is a
        # count over every cart of the company, decided under one lock.
        for kind, value in (('company', 'all'), ('cart', key), ('origin', origin)) if value
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

    `origin`: `order_risk.origin_hash` of the request ('' = unknown). Four
    limits keep one client from emptying a shelf: holds per cart, holds per
    origin across every cart, the share of a shelf of 4+ devices web holds may
    take, and the company ceiling over every web hold (DC2 second review).
    """
    if not session_key:
        raise HoldError('invalid', 'session_key es requerido.')
    if branch is None:
        raise HoldError('sold_out', 'La tienda no tiene una sucursal de despacho configurada.')
    if not product.sells_by_unit or not product.is_sold_online:
        raise HoldError('not_found', 'Producto no encontrado o no disponible.')
    if not ProductModel.objects.filter(pk=product.product_model_id, is_published_online=True).exists():
        # A model taken off the web: its page is gone, and so are its devices.
        raise HoldError('not_found', 'Producto no encontrado o no disponible.')
    if product.condition_line == Product.CONDITION_USED and not unit_public_id:
        raise HoldError('unit_required', 'Elige el equipo que quieres comprar.')

    key = key_of(session_key)
    # The counts below are decided under a lock of this cart and this origin,
    # taken BEFORE the shelf: two adds of one cart for two products lock two
    # different shelves, and would otherwise both count the same «2 of 3».
    # ONE LOCK ORDER (DC2 second review, P3): these advisory locks, THEN any
    # shelf — the sweep below takes the shelf row, so it comes after them.
    _lock_holders(product.company_id, key, origin)
    release_expired(company=product.company, product=product)
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
    cap = web_hold_cap(pool)
    company_held = _live_web_holds(product.company, now).count()
    if (cap is not None and web_held >= cap) or \
            company_held >= company_hold_cap(_web_available_by_unit(branch) + company_held):
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
                'hold_key': key, 'hold_until': now + timedelta(minutes=hold_minutes(product.company_id)),
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
        try:
            with transaction.atomic():
                _release(unit)
        except StockUnitError:
            continue      # it moved meanwhile (a payment is selling it): not this cart's to drop
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
        if release_one_expired(pk, now=now):
            released += 1
    return released


@transaction.atomic
def release_one_expired(pk, *, now=None) -> bool:
    """
    One expired web hold back to the shelf — IDEMPOTENT and safe under
    concurrency (owner, 2026-10-15). Decided under the locks a payment and a
    checkout take: the shelf first, then the device's row (the module's one
    lock order). So the sweep, a cart read and the cron at the same instant
    write ONE release line, a checkout that extended the hold after the sweep
    listed it wins, and a hold whose order the gateway may still confirm
    stays where it is (`payment_keeps_hold`).
    """
    now = now or timezone.now()
    unit = StockUnit.objects.select_related('branch', 'product').filter(pk=pk).first()
    if unit is None or unit.branch_id is None:
        return False
    inventory_services._locked_branch_stocks(unit.branch, [unit.product])
    unit = StockUnit.objects.select_for_update().filter(pk=pk).first()
    # Read again, locked: the payment may have sold it, a checkout extended it,
    # another sweep released it.
    if (unit is None or unit.status != StockUnit.Status.RESERVED or not unit.hold_key
            or unit.hold_until is None or unit.hold_until > now):
        return False
    if payment_keeps_hold(unit, now):
        return False
    try:
        with transaction.atomic():
            _release(unit)
    except StockUnitError:
        return False      # it moved meanwhile: whoever moved it decided
    return True


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
    # The company's times (owner, 2026-10-15).
    ceiling = ExpressionWrapper(
        Coalesce(F('hold_started_at'), Value(now)) + Value(timedelta(minutes=hold_ceiling_minutes(order.company_id))),
        output_field=DateTimeField(),
    )
    bound = StockUnit.objects.filter(
        pk__in=[unit.pk for unit in units], status=StockUnit.Status.RESERVED,
        hold_key=key_of(session_key), hold_until__gt=now,
    ).update(
        hold_order=order,
        hold_until=Least(Value(now + timedelta(minutes=order_hold_minutes(order.company_id)),
                               output_field=DateTimeField()),
                         ceiling),
        updated_at=now,
    )
    if bound < len({unit.pk for unit in units}):
        raise CheckoutError(
            'Problemas con el carrito.', code='cart_changed',
            errors=['Uno de los equipos de tu carrito ya no está apartado para ti. Revisa tu carrito.'],
        )


def superseded_lines(order, *, selling: bool = False) -> dict:
    """
    The device lines of `order` that another checkout of the same cart took:
    {stock unit id: that order}. Empty for anything else.

    One cart, two checkouts (two tabs, a retry): both orders name the same
    devices, and only one of them may sell each — one device, one payment.
    Derived from the order lines, so it survives the hold running out.

      * A PAID order wins, earlier or later: its payment sold the device.
      * While none is paid, the LATER order is the one that may open a
        payment; a later order that failed or was cancelled takes nothing.
      * `selling=True` — this order's payment arrived (its form was already
        open). Only a PAID order takes the device from it: money in hand wins
        over an order that may never be paid (DC2 second review, P2), and the
        later pending order is the superseded one from then on.
    """
    from django.db.models import Q

    from .models import OrderItem

    if not order.cart_session_key:
        return {}
    mine = list(order.items.exclude(stock_unit=None).values_list('stock_unit_id', flat=True))
    if not mine:
        return {}
    winners = Q(order__status=Order.Status.PAID)
    if not selling:
        winners |= Q(order__pk__gt=order.pk, order__status=Order.Status.PENDING_PAYMENT)
    others = (
        OrderItem.objects.filter(
            stock_unit_id__in=mine, order__company_id=order.company_id,
            order__cart_session_key=order.cart_session_key,
        )
        .exclude(order__pk=order.pk).filter(winners)
        .select_related('order').order_by('order_id')
    )
    return {item.stock_unit_id: item.order for item in others}


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
