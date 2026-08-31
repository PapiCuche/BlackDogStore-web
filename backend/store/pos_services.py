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
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from . import inventory_services
from .models import (
    AdminAuditLog,
    Branch,
    Customer,
    Order,
    OrderItem,
    PaymentMethod,
    Product,
    SalesChannel,
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


def request_fingerprint(*, company, branch, customer, payment_method, items) -> str:
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
    """
    payload = {
        'company': int(getattr(company, 'pk', company)),
        'branch': int(getattr(branch, 'pk', branch)),
        'customer': int(getattr(customer, 'pk', customer)) if customer else None,
        'payment_method': str(payment_method),
        'items': [
            {'p': int(i['product']), 'q': int(i['quantity'])}
            for i in items
        ],
    }
    blob = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()


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


@transaction.atomic
def create_pos_sale(
    *,
    actor,
    company,
    branch,
    items,
    customer=None,
    payment_method: str = PaymentMethod.CASH,
    idempotency_key: str = '',
    request=None,
):
    """
    Complete a counter sale: order, payment, stock and audit, all or nothing.

    Returns `(order, created)`. `created=False` means this exact sale had
    already been completed under the same idempotency key and is being returned
    again rather than repeated.

    ORDER OF OPERATIONS, and why it is this one:

      1. resolve and validate everything (tenant, branch, products, customer)
      2. take server-side prices — never the browser's
      3. write the order and its lines
      4. decrement stock in STRICT mode, which raises rather than shorting
      5. audit

    Step 4 last, and strict: if any article is short, the exception unwinds
    every one of the steps above it and no order was ever created. The
    alternative — writing a paid order and then discovering the shelf is empty —
    is precisely the online channel's problem, and the online channel only
    tolerates it because the money is already gone.
    """
    if payment_method not in PaymentMethod.values:
        raise PosValidationError('Método de pago inválido.')

    items = normalize_items(items)
    products = resolve_pos_products(company, items)
    customer = resolve_pos_customer(company, customer)

    fingerprint = request_fingerprint(
        company=company, branch=branch, customer=customer,
        payment_method=payment_method, items=items,
    )

    # IDEMPOTENCY, checked before doing any work.
    #
    # Same key + same sale  → hand back what was already created.
    # Same key + other sale → refuse. Returning the earlier order would tell the
    #                         caller their new basket was sold when it was not.
    if idempotency_key:
        existing = _existing_for_key(company, idempotency_key)
        if existing is not None:
            if existing.pos_request_fingerprint == fingerprint:
                return existing, False
            raise PosIdempotencyConflict(existing)

    subtotal = Decimal('0.00')
    lines = []
    for entry in items:
        product = products[entry['product']]
        # THE SERVER DECIDES THE PRICE. The browser is shown one so the operator
        # can read a total out loud; it is never asked what to charge.
        unit_price = Decimal(str(product.price))
        quantity = entry['quantity']
        subtotal += unit_price * quantity
        lines.append((product, quantity, unit_price))

    total = subtotal.quantize(Decimal('0.01'))
    now = timezone.now()

    from .company_settings import build_identity_snapshot

    order = Order(
        company=company,
        fulfillment_branch=branch,
        company_snapshot=build_identity_snapshot(company, branch),
        sales_channel=SalesChannel.POS,
        payment_method=payment_method,
        sold_by=actor if getattr(actor, 'is_authenticated', False) else None,
        pos_idempotency_key=idempotency_key or '',
        pos_request_fingerprint=fingerprint if idempotency_key else '',
        total=total,
        discount_amount=Decimal('0.00'),
        status=Order.Status.PAID,
        paid=True,
        paid_at=now,
        # DELIVERED, because the customer is holding the goods. Leaving a
        # counter sale "pending fulfilment" would fill the dispatch queue with
        # work that was finished before the screen refreshed.
        fulfillment_status=Order.FulfillmentStatus.DELIVERED,
        # Terms are accepted in person, at the counter, by handing the article
        # over. Recording that as false would be less true, not more careful.
        accepted_terms=True,
        accepted_warranty_policy=True,
    )
    _customer_snapshot(order, customer)

    try:
        order.save()
    except IntegrityError:
        # Two tills, one key, at the same instant: the unique constraint picks a
        # winner. Re-read rather than fail — the winner's order is the one this
        # request was asking for.
        if idempotency_key:
            existing = _existing_for_key(company, idempotency_key)
            if existing is not None:
                if existing.pos_request_fingerprint == fingerprint:
                    return existing, False
                raise PosIdempotencyConflict(existing)
        raise

    OrderItem.objects.bulk_create([
        OrderItem(order=order, product=product, quantity=quantity, price=unit_price)
        for product, quantity, unit_price in lines
    ])

    # STRICT: raises InsufficientStockError, which unwinds this whole
    # transaction. Nothing was captured, so nothing needs repairing.
    inventory_services.record_sale_stock_movements(order, actor=actor, strict=True)

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
            'lines': len(lines),
            'units': sum(q for _p, q, _u in lines),
            'total': str(total),
            'customer_id': customer.pk if customer else None,
        },
        request=request,
        company=company,
    )

    return order, True
