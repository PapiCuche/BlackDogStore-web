"""
Point-of-sale sales — Commercial Phase C1.

ONE SALES CORE, TWO CHANNELS
----------------------------
A counter sale becomes an `Order`, exactly like a storefront sale. The rejected
alternative was a separate `PosSale` model, and it is worth saying why: every
report, every stock movement, every internal document and every customer history
would have had to be computed twice and then reconciled. A shop that sells the
same cable online and over the counter has made one sale either way.

What differs is not the record, it is the moment:

    ONLINE   money is captured elsewhere, a webhook arrives later, and stock is
             decremented AFTER the fact. A shortfall is recorded, not refused —
             the money is already taken.

    POS      the operator is standing at the counter. Payment, stock and
             document happen in one breath, inside one transaction. A shortfall
             REFUSES the sale, because selling what the shop does not have is
             the failure this exists to prevent.

Both go through `inventory_services`. This module never touches `BranchStock`
directly — a second way to move stock is a second way to get it wrong.
"""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.db import IntegrityError, transaction
from django.utils import timezone

from . import inventory_services
from .models import (
    AdminAuditLog,
    Branch,
    Coupon,
    Customer,
    DiscountSource,
    Membership,
    Order,
    OrderItem,
    PaymentMethod,
    Product,
    SalesChannel,
    SalesCommission,
)


class PosError(Exception):
    """A counter sale could not be completed."""


class PosValidationError(PosError):
    """The request is malformed or asks for something that does not exist here."""


class PosIdempotencyConflict(PosError):
    """This idempotency key was already used for a DIFFERENT sale."""

    def __init__(self, existing_order):
        self.existing_order = existing_order
        super().__init__(
            'Esta clave de idempotencia ya se usó para una venta distinta.'
        )


MAX_LINES = 100
MAX_QUANTITY_PER_LINE = 1000
MAX_IDEMPOTENCY_KEY = 64

_KEY_ALLOWED = re.compile(r'^[\x21-\x7E]{8,64}$')


def validate_idempotency_key(value) -> str:
    """
    The key is REQUIRED, and it is never silently repaired.

    Two decisions worth stating, because both are ways this could have been
    written wrong:

      IT IS NOT OPTIONAL. An empty key meant a sale with no protection at all —
      the one code path where a double click charges twice. The browser already
      mints one per basket; the server now refuses to sell without it rather
      than trusting every client to remember.

      IT IS NOT TRUNCATED. `str(value)[:64]` looks harmless and is not: two
      distinct 80-character keys that share their first 64 become ONE key, and
      the second sale is silently answered with the first one's order. A key
      that is too long is rejected.
    """
    if value is None:
        raise PosValidationError('Falta la clave de idempotencia de la venta.')
    key = str(value).strip()
    if not key:
        raise PosValidationError('Falta la clave de idempotencia de la venta.')
    if len(key) > MAX_IDEMPOTENCY_KEY:
        raise PosValidationError(
            f'La clave de idempotencia supera {MAX_IDEMPOTENCY_KEY} caracteres.'
        )
    if not _KEY_ALLOWED.fullmatch(key):
        raise PosValidationError(
            'La clave de idempotencia debe tener entre 8 y 64 caracteres '
            'imprimibles, sin espacios ni saltos de línea.'
        )
    return key


# ---------------------------------------------------------------------------
# Request shaping
# ---------------------------------------------------------------------------

def normalize_items(raw_items) -> list[dict]:
    """
    Turn the browser's basket into a canonical list of (product_id, quantity).

    TWO LINES OF THE SAME ARTICLE ARE MERGED, and this is not tidiness — it is
    required for correctness. `record_sale_stock_movements` is idempotent per
    `(order, product)`: given two OrderItems for one product it would write the
    exit for the first and SKIP the second, selling two units while decrementing
    one. Collapsing here means the invariant that protects a replayed webhook
    cannot be turned into a stock leak by a POS basket.

    Prices are deliberately NOT read from the input. See `create_pos_sale`.
    """
    if not isinstance(raw_items, (list, tuple)) or not raw_items:
        raise PosValidationError('La venta no tiene productos.')
    if len(raw_items) > MAX_LINES:
        raise PosValidationError(f'Demasiadas líneas (máximo {MAX_LINES}).')

    merged: dict[int, int] = {}
    for entry in raw_items:
        if not isinstance(entry, dict):
            raise PosValidationError('Formato de línea inválido.')
        try:
            product_id = int(entry.get('product'))
            quantity = int(entry.get('quantity', 1))
        except (TypeError, ValueError):
            raise PosValidationError('Producto o cantidad inválidos.')
        if quantity <= 0:
            raise PosValidationError('La cantidad debe ser mayor que cero.')
        if quantity > MAX_QUANTITY_PER_LINE:
            raise PosValidationError(
                f'Cantidad máxima por línea: {MAX_QUANTITY_PER_LINE}.'
            )
        merged[product_id] = merged.get(product_id, 0) + quantity

    for product_id, quantity in merged.items():
        if quantity > MAX_QUANTITY_PER_LINE:
            raise PosValidationError(
                f'Cantidad total del producto {product_id} demasiado alta.'
            )

    return [
        {'product': pid, 'quantity': qty}
        for pid, qty in sorted(merged.items())
    ]


def request_fingerprint(
    *, company, branch, customer, payment_method, items,
    seller=None, discount=None, payment_reference='', external_reference='',
    sale_notes='',
) -> str:
    """
    A short, stable hash of WHAT this sale is.

    The idempotency key alone is not enough. A key says "this is the same
    attempt"; it cannot say whether the attempt is the same SALE. Without the
    fingerprint, a client that reused a key — a stale tab, a bug, a copied
    request — would be handed somebody else's completed order and told it
    succeeded.

    Prices are excluded on purpose: the server decides those, so including them
    would make the fingerprint depend on a value the client does not control and
    turn an ordinary price change into a spurious conflict.

    Everything that materially changes the sale IS included — the seller who
    gets credited, the discount that was actually resolved, and the references
    typed alongside it. Leaving any of them out would let a retry with different
    contents be answered with the earlier order.
    """
    discount = discount or {}
    payload = {
        'company': int(getattr(company, 'pk', company)),
        'branch': int(getattr(branch, 'pk', branch)),
        'customer': int(getattr(customer, 'pk', customer)) if customer else None,
        # WHO IS CREDITED is part of what the sale IS. Without it, retrying a
        # request with the seller changed would return the earlier order and
        # report success while the commission stayed with the wrong person.
        'seller': int(getattr(seller, 'pk', seller)) if seller else None,
        'payment_method': str(payment_method),
        'items': [
            {'p': int(i['product']), 'q': int(i['quantity'])}
            for i in items
        ],
        # The RESOLVED discount, not the raw request: what actually came off,
        # from where, and why. A retry that changes the coupon changes the sale.
        'discount': {
            'source': str(discount.get('source', '')),
            'amount': str(discount.get('amount', '')),
            'code': str(discount.get('coupon_code', '')),
            'reason': str(discount.get('reason', '')),
        },
        'payment_reference': str(payment_reference or ''),
        'external_reference': str(external_reference or ''),
        'sale_notes': str(sale_notes or ''),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------

MAX_REASON = 200
MAX_REFERENCE = 100
MAX_NOTES = 1000
CENT = Decimal('0.01')


class DiscountError(PosValidationError):
    """The requested discount is not one this caller may apply."""


def _money(value) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def resolve_discount(
    company, subtotal: Decimal, *,
    coupon_code: str = '',
    manual_type: str = '',
    manual_value=None,
    reason: str = '',
    may_apply_manual: bool = False,
):
    """
    Work out what comes off, from which source, and whether it was allowed.

    ONE SOURCE AT A TIME. A coupon and a hand-typed discount together is a
    stacking policy, and stacking is a business decision with rules — which
    promotion applies first, whether they compound, what the floor is. Guessing
    one here would bake an unexamined policy into a till. Asking for both is
    refused rather than silently resolved in some order.

    A COUPON NEEDS NO PERMISSION. The company configured that promotion in
    advance; honouring it is not a decision the cashier is making. A MANUAL
    discount is a decision, so it needs both the authority to make it and a
    reason recorded next to it.
    """
    coupon_code = (coupon_code or '').strip()
    manual_type = (manual_type or '').strip()
    has_manual = bool(manual_type) or manual_value not in (None, '')

    if coupon_code and has_manual:
        raise DiscountError(
            'Aplica un código promocional o un descuento manual, no ambos.'
        )

    if not coupon_code and not has_manual:
        return {
            'source': DiscountSource.NONE, 'amount': Decimal('0.00'),
            'coupon': None, 'coupon_code': '', 'reason': '',
        }

    if coupon_code:
        coupon = Coupon.objects.filter(
            company=company, code=coupon_code, is_active=True,
        ).first()
        if coupon is None:
            raise DiscountError('Cupón no válido o inactivo.')
        if coupon.expires_at and coupon.expires_at < timezone.now():
            raise DiscountError('El cupón ha expirado.')
        amount = _money(subtotal * Decimal(coupon.discount_percent) / Decimal('100'))
        return {
            'source': DiscountSource.COUPON, 'amount': min(amount, subtotal),
            'coupon': coupon, 'coupon_code': coupon.code, 'reason': '',
        }

    # --- manual ----------------------------------------------------------
    if not may_apply_manual:
        raise DiscountError(
            'No tienes permiso para aplicar descuentos manuales.'
        )
    reason = (reason or '').strip()
    if not reason:
        raise DiscountError('Indica el motivo del descuento.')
    if len(reason) > MAX_REASON:
        raise DiscountError(f'El motivo supera {MAX_REASON} caracteres.')

    try:
        value = Decimal(str(manual_value))
    except (InvalidOperation, TypeError, ValueError):
        raise DiscountError('Valor de descuento inválido.')

    if manual_type == 'percent':
        if not (Decimal('0') < value <= Decimal('100')):
            raise DiscountError('El porcentaje debe estar entre 0 y 100.')
        amount = _money(subtotal * value / Decimal('100'))
    elif manual_type == 'amount':
        if value <= 0:
            raise DiscountError('El monto debe ser mayor que cero.')
        amount = _money(value)
    else:
        raise DiscountError('Tipo de descuento inválido.')

    if amount > subtotal:
        raise DiscountError('El descuento no puede superar el subtotal.')

    return {
        'source': DiscountSource.MANUAL, 'amount': amount,
        'coupon': None, 'coupon_code': '', 'reason': reason,
    }


def commission_rate_for(company, seller) -> Decimal:
    """The rate agreed with this seller IN THIS COMPANY. Zero if none."""
    if seller is None:
        return Decimal('0.00')
    membership = Membership.objects.filter(
        company=company, user=seller, is_active=True,
    ).first()
    if membership is None:
        return Decimal('0.00')
    return Decimal(membership.commission_rate_percent or 0)


def calculate_pos_totals(company, products, items, *, discount) -> dict:
    """
    Subtotal, discount and total — computed HERE and nowhere else.

    Shared verbatim between the preview and the sale. Two implementations of
    one formula is how a till ends up showing a number it then does not charge,
    and the customer is standing right there when the difference appears.
    """
    subtotal = Decimal('0.00')
    lines = []
    for entry in items:
        product = products[entry['product']]
        unit_price = Decimal(str(product.price))
        quantity = entry['quantity']
        subtotal += unit_price * quantity
        lines.append((product, quantity, unit_price))

    subtotal = _money(subtotal)
    discount_amount = min(_money(discount['amount']), subtotal)
    total = _money(subtotal - discount_amount)
    return {
        'lines': lines,
        'subtotal': subtotal,
        'discount_amount': discount_amount,
        'total': total,
    }


def calculate_commission(rate: Decimal, *, subtotal: Decimal, discount: Decimal) -> dict:
    """
    Commission on the NET sale.

        base   = subtotal − discount
        amount = base × rate / 100

    The discount comes off FIRST. A shop that gave 10% away did not collect that
    money, and paying a percentage of it would mean the discount costs more than
    it appears to. Decimal throughout, quantised to the cent — floats lose
    fractions of currency in exactly the place a ledger cannot afford them.
    """
    base = _money(max(subtotal - discount, Decimal('0.00')))
    amount = _money(base * Decimal(rate) / Decimal('100'))
    return {'rate': Decimal(rate), 'base': base, 'amount': amount}


def resolve_cash(payment_method: str, total: Decimal, amount_received):
    """
    Cash and change, or nothing at all.

    Returns `(received, change)`. For anything but cash both are None: a card
    payment has no change to give, and writing zero there would make "paid the
    exact amount in cash" indistinguishable from "did not pay in cash".
    """
    if payment_method != PaymentMethod.CASH:
        return None, None

    if amount_received in (None, ''):
        raise PosValidationError('Indica el efectivo recibido.')
    try:
        received = _money(Decimal(str(amount_received)))
    except (InvalidOperation, TypeError, ValueError):
        raise PosValidationError('Efectivo recibido inválido.')

    if received < total:
        raise PosValidationError(
            f'El efectivo recibido ({received}) es menor que el total ({total}).'
        )
    return received, _money(received - total)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def resolve_pos_branch(user, company, requested_branch_id):
    """
    The branch this till is operating, verified rather than trusted.

    The id arrives from the browser and is used only to SELECT among branches
    the caller already reaches — it can never widen access. A branch of another
    company, an inactive one, or one the user has no grant for answers like a
    branch that does not exist.
    """
    from .tenancy import visible_branches

    if requested_branch_id in (None, ''):
        raise PosValidationError('Selecciona una sucursal para vender.')
    try:
        branch_id = int(requested_branch_id)
    except (TypeError, ValueError):
        raise PosValidationError('Sucursal inválida.')

    branch = (
        visible_branches(user, company)
        .filter(pk=branch_id, is_active=True)
        .first()
    )
    if branch is None:
        raise PosValidationError('Sucursal no encontrada o sin acceso.')
    return branch


def resolve_pos_products(company, items) -> dict[int, Product]:
    """
    The products being sold, scoped to the company that is selling them.

    Resolved by walking DOWN from the company rather than by loading ids and
    checking them afterwards: another tenant's product id is not rejected by a
    check somebody could remove, it is simply not in the set being searched.
    """
    ids = [i['product'] for i in items]
    found = {
        p.pk: p
        for p in Product.objects.filter(company=company, pk__in=ids, is_active=True)
    }
    missing = [i for i in ids if i not in found]
    if missing:
        raise PosValidationError(
            'Producto no encontrado o no disponible en esta empresa.'
        )
    return found


def resolve_pos_seller(company, operator, requested_seller_id, *, may_assign: bool):
    """
    Who the sale is CREDITED to, which is not always who rang it up.

    OPERATOR versus SELLER, and why they are two things:

      the OPERATOR is `request.user` — whoever physically worked the till. That
      is what the audit trail records, and it is never negotiable.

      the SELLER is who the commission belongs to. Usually the same person, so
      it defaults to the operator and nobody has to pick themselves off a list
      for every sale. But a supervisor ringing up a sale that a colleague made
      is an ordinary shop, and pretending otherwise would either lose the
      attribution or force staff to share logins.

    Reassigning is gated on `sales.pos.assign_seller` because it moves money:
    without the gate, anyone could credit any colleague — or themselves.
    """
    if requested_seller_id in (None, ''):
        return operator
    try:
        seller_id = int(requested_seller_id)
    except (TypeError, ValueError):
        raise PosValidationError('Vendedor inválido.')

    if seller_id == getattr(operator, 'pk', None):
        return operator

    if not may_assign:
        raise PosValidationError(
            'No tienes permiso para atribuir la venta a otro vendedor.'
        )

    # Resolved by walking DOWN from this company's active memberships. A seller
    # from another tenant is not rejected by a check somebody could delete — it
    # is simply not in the set being searched, so the answer is the same as for
    # a user that does not exist.
    membership = (
        Membership.objects
        .filter(company=company, user_id=seller_id, is_active=True)
        .select_related('user')
        .first()
    )
    if membership is None or not membership.user.is_active:
        raise PosValidationError('Vendedor no encontrado en esta empresa.')
    return membership.user


def seller_display_name(user) -> str:
    """The name to freeze on the order and the commission."""
    if user is None:
        return ''
    full = (user.get_full_name() or '').strip()
    return (full or user.get_username())[:150]


def resolve_pos_customer(company, customer_id):
    """The customer, if one was chosen. A counter sale may be anonymous."""
    if customer_id in (None, ''):
        return None
    try:
        pk = int(customer_id)
    except (TypeError, ValueError):
        raise PosValidationError('Cliente inválido.')
    customer = Customer.objects.filter(company=company, pk=pk, is_active=True).first()
    if customer is None:
        raise PosValidationError('Cliente no encontrado.')
    return customer


# ---------------------------------------------------------------------------
# The sale
# ---------------------------------------------------------------------------

def _existing_for_key(company, key: str):
    return (
        Order.objects
        .filter(company=company, pos_idempotency_key=key)
        .prefetch_related('items')
        .first()
    )


def _customer_snapshot(order: Order, customer) -> None:
    """
    Copy the customer's details ONTO the order, at the moment of sale.

    `Order.customer` is who they are today; these fields are who they were when
    they bought. A phone number changed next year must not rewrite what this
    receipt says — the same rule the storefront already follows.
    """
    if customer is None:
        return
    order.customer = customer
    order.customer_name = customer.display_name[:255]
    order.customer_email = customer.email or ''
    order.customer_phone = customer.phone or ''
    order.document_type = customer.document_type or ''
    order.document_number = customer.document_number or ''


def build_pos_sale(
    *,
    operator,
    company,
    branch,
    items,
    customer=None,
    seller_id=None,
    payment_method: str = PaymentMethod.CASH,
    coupon_code: str = '',
    manual_discount_type: str = '',
    manual_discount_value=None,
    discount_reason: str = '',
    amount_received=None,
    may_assign_seller: bool = False,
    may_apply_manual_discount: bool = False,
    validate_cash: bool = True,
):
    """
    Resolve and price a sale WITHOUT writing anything.

    Shared by the preview endpoint and by `create_pos_sale`, so the number the
    operator reads aloud is produced by the same code that later charges it.
    Every rejection a real sale would hit happens here too, which means the
    preview refuses an over-limit discount instead of showing a total that the
    charge would then decline.
    """
    if payment_method not in PaymentMethod.values:
        raise PosValidationError('Método de pago inválido.')

    items = normalize_items(items)
    products = resolve_pos_products(company, items)
    customer = resolve_pos_customer(company, customer)
    seller = resolve_pos_seller(
        company, operator, seller_id, may_assign=may_assign_seller,
    )

    subtotal = sum(
        (Decimal(str(products[e['product']].price)) * e['quantity'] for e in items),
        Decimal('0.00'),
    )
    discount = resolve_discount(
        company, _money(subtotal),
        coupon_code=coupon_code,
        manual_type=manual_discount_type,
        manual_value=manual_discount_value,
        reason=discount_reason,
        may_apply_manual=may_apply_manual_discount,
    )
    totals = calculate_pos_totals(company, products, items, discount=discount)
    commission = calculate_commission(
        commission_rate_for(company, seller),
        subtotal=totals['subtotal'], discount=totals['discount_amount'],
    )
    # The PREVIEW skips this: the operator is still counting the money out, and
    # refusing to show them the total until they have finished would defeat the
    # purpose of a preview. The SALE always validates it.
    if validate_cash:
        received, change = resolve_cash(payment_method, totals['total'], amount_received)
    else:
        received, change = None, None

    return {
        'items': items,
        'products': products,
        'customer': customer,
        'seller': seller,
        'discount': discount,
        'commission': commission,
        'amount_received': received,
        'change_amount': change,
        'payment_method': payment_method,
        **totals,
    }


@transaction.atomic
def create_pos_sale(
    *,
    actor,
    company,
    branch,
    items,
    customer=None,
    seller_id=None,
    payment_method: str = PaymentMethod.CASH,
    idempotency_key: str = '',
    terms_confirmed: bool = False,
    coupon_code: str = '',
    manual_discount_type: str = '',
    manual_discount_value=None,
    discount_reason: str = '',
    amount_received=None,
    payment_reference: str = '',
    external_reference: str = '',
    sale_notes: str = '',
    may_assign_seller: bool = False,
    may_apply_manual_discount: bool = False,
    request=None,
):
    """
    Complete a counter sale: order, payment, stock, commission and audit — all
    or nothing.

    Returns `(order, created)`. `created=False` means this exact sale had
    already been completed under the same idempotency key and is being returned
    again rather than repeated.

    ORDER OF OPERATIONS, and why it is this one:

      1. confirm consent, validate the key
      2. resolve and price everything (`build_pos_sale`) — no writes yet
      3. check idempotency against the priced fingerprint
      4. write the order and its lines
      5. decrement stock in STRICT mode, which raises rather than shorting
      6. record the commission
      7. audit

    Step 5 before step 6 and both inside one transaction: if the shelf is empty
    the exception unwinds the order, the lines AND the commission. A ledger
    entry for a sale that did not happen is worse than no entry at all.
    """
    if terms_confirmed is not True:
        # CONSENT IS ASSERTED BY THE OPERATOR, NOT INFERRED FROM THE SALE.
        # Handing the article over proves nothing was explained.
        raise PosValidationError(
            'Confirma que informaste al cliente las condiciones de venta y la '
            'política de garantía antes de cobrar.'
        )

    idempotency_key = validate_idempotency_key(idempotency_key)

    payment_reference = (payment_reference or '').strip()[:MAX_REFERENCE]
    external_reference = (external_reference or '').strip()[:MAX_REFERENCE]
    sale_notes = (sale_notes or '').strip()
    if len(sale_notes) > MAX_NOTES:
        raise PosValidationError(f'Las observaciones superan {MAX_NOTES} caracteres.')

    priced = build_pos_sale(
        operator=actor, company=company, branch=branch, items=items,
        customer=customer, seller_id=seller_id, payment_method=payment_method,
        coupon_code=coupon_code, manual_discount_type=manual_discount_type,
        manual_discount_value=manual_discount_value, discount_reason=discount_reason,
        amount_received=amount_received,
        may_assign_seller=may_assign_seller,
        may_apply_manual_discount=may_apply_manual_discount,
    )

    fingerprint = request_fingerprint(
        company=company, branch=branch, customer=priced['customer'],
        payment_method=payment_method, items=priced['items'],
        seller=priced['seller'], discount=priced['discount'],
        payment_reference=payment_reference,
        external_reference=external_reference, sale_notes=sale_notes,
    )

    # IDEMPOTENCY, checked before doing any work.
    #
    # Same key + same sale  → hand back what was already created.
    # Same key + other sale → refuse. Returning the earlier order would tell the
    #                         caller their new basket was sold when it was not.
    existing = _existing_for_key(company, idempotency_key)
    if existing is not None:
        if existing.pos_request_fingerprint == fingerprint:
            return existing, False
        raise PosIdempotencyConflict(existing)

    now = timezone.now()
    seller = priced['seller']
    discount = priced['discount']

    from .company_settings import build_identity_snapshot

    order = Order(
        company=company,
        fulfillment_branch=branch,
        company_snapshot=build_identity_snapshot(company, branch),
        sales_channel=SalesChannel.POS,
        payment_method=payment_method,
        sold_by=seller if getattr(seller, 'is_authenticated', False) else None,
        seller_name_snapshot=seller_display_name(seller),
        pos_idempotency_key=idempotency_key,
        pos_request_fingerprint=fingerprint,
        total=priced['total'],
        discount_amount=priced['discount_amount'],
        coupon_code=discount['coupon_code'],
        discount_source=discount['source'],
        discount_reason=discount['reason'],
        discount_authorized_by=(
            actor if discount['source'] == DiscountSource.MANUAL else None
        ),
        amount_received=priced['amount_received'],
        change_amount=priced['change_amount'],
        payment_reference=payment_reference,
        external_reference=external_reference,
        sale_notes=sale_notes,
        status=Order.Status.PAID,
        paid=True,
        paid_at=now,
        # DELIVERED, because the customer is holding the goods. Leaving a
        # counter sale "pending fulfilment" would fill the dispatch queue with
        # work that was finished before the screen refreshed.
        fulfillment_status=Order.FulfillmentStatus.DELIVERED,
        accepted_terms=True,
        accepted_warranty_policy=True,
    )
    _customer_snapshot(order, priced['customer'])

    try:
        # NESTED ATOMIC = SAVEPOINT. An IntegrityError marks the whole
        # transaction for rollback, so catching it and then querying inside the
        # SAME atomic block raises TransactionManagementError instead of
        # answering. Wrapping just this INSERT keeps the outer transaction
        # usable and makes the read below a real read.
        with transaction.atomic():
            order.save()
    except IntegrityError:
        existing = _existing_for_key(company, idempotency_key)
        if existing is None:
            # The collision was NOT this constraint. Some other invariant was
            # violated and swallowing it here would turn a real defect into a
            # confusing "sale not created" with no explanation anywhere.
            raise
        if existing.pos_request_fingerprint != fingerprint:
            raise PosIdempotencyConflict(existing)
        return existing, False

    OrderItem.objects.bulk_create([
        OrderItem(order=order, product=product, quantity=quantity, price=unit_price)
        for product, quantity, unit_price in priced['lines']
    ])

    # STRICT: raises InsufficientStockError, which unwinds this whole
    # transaction. Nothing was captured, so nothing needs repairing.
    inventory_services.record_sale_stock_movements(order, actor=actor, strict=True)

    # THE COMMISSION IS WRITTEN ONLY WHEN THERE IS SOMETHING TO OWE.
    #
    # A seller on 0% produces no row. The ledger lists obligations, and "nothing
    # is owed" is not one — a table of zeros would have to be filtered out of
    # every report that reads it.
    commission = priced['commission']
    if commission['amount'] > 0:
        SalesCommission.objects.create(
            company=company,
            order=order,
            seller=seller if getattr(seller, 'is_authenticated', False) else None,
            seller_name_snapshot=order.seller_name_snapshot,
            rate_percent=commission['rate'],
            base_amount=commission['base'],
            amount=commission['amount'],
            status=SalesCommission.STATUS_ACCRUED,
        )

    AdminAuditLog.log(
        actor=actor,
        action='pos_sale_completed',
        target_type='order',
        target_id=order.pk,
        metadata={
            'company_id': company.pk,
            'branch_id': branch.pk,
            'order_id': order.pk,
            'payment_method': payment_method,
            'lines': len(priced['lines']),
            'units': sum(q for _p, q, _u in priced['lines']),
            'subtotal': str(priced['subtotal']),
            'discount': str(priced['discount_amount']),
            'discount_source': discount['source'],
            'total': str(priced['total']),
            'customer_id': priced['customer'].pk if priced['customer'] else None,
            # WHO RANG IT UP versus WHO IS CREDITED. Recording both is the point
            # of separating them at all.
            'seller_id': seller.pk if seller else None,
            'reassigned_seller': bool(seller and seller.pk != getattr(actor, 'pk', None)),
            'commission': str(commission['amount']),
        },
        request=request,
        company=company,
    )

    if discount['source'] == DiscountSource.MANUAL:
        # A separate entry, because a hand-typed discount is a decision somebody
        # made and an auditor looks for it by name.
        AdminAuditLog.log(
            actor=actor,
            action='pos_manual_discount_applied',
            target_type='order',
            target_id=order.pk,
            metadata={
                'company_id': company.pk,
                'order_id': order.pk,
                'subtotal': str(priced['subtotal']),
                'discount': str(priced['discount_amount']),
                # The reason is operator-written text about the sale, not about
                # the customer, so it is safe to keep next to the decision.
                'reason': discount['reason'][:MAX_REASON],
            },
            request=request,
            company=company,
        )

    return order, True
