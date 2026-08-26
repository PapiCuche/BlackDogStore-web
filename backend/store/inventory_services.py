"""
Inventory service layer — Phase 6.0.

Single entry point for every change to Product.inventory. Views, the Stripe
webhook and the Django admin must go through these functions so that stock and
the Kardex (StockMovement) always move together inside one transaction.

Hard rules:
  - Stock never goes negative. A movement that would do so raises
    InsufficientStockError and the transaction rolls back.
  - The current stock is always re-read from the DB under select_for_update();
    a client-supplied stock value is never trusted.
  - quantity is always positive; movement_type decides whether it adds or subtracts.
  - Manual movements require an actor and a non-empty reason.
  - Sale exits are idempotent per (order, product): replaying a Stripe webhook
    never subtracts twice.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, DecimalField, F, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import AdminAuditLog, Order, Product, StockMovement

# Default threshold for "low stock" reports when the caller does not pass one.
DEFAULT_LOW_STOCK_THRESHOLD = 5
MAX_REASON_LENGTH = 500


class InventoryError(Exception):
    """Base class for inventory rule violations. Views map these to HTTP 400."""


class InsufficientStockError(InventoryError):
    """Raised when a movement would leave stock below zero."""


class InvalidMovementError(InventoryError):
    """Raised when the movement payload breaks a business rule."""


# ---------------------------------------------------------------------------
# Core write path
# ---------------------------------------------------------------------------

def create_stock_movement(
    *,
    product_id: int,
    movement_type: str,
    quantity: int,
    reason: str = '',
    actor=None,
    order=None,
    reference_type: str = '',
    reference_id: str = '',
    metadata: dict | None = None,
) -> StockMovement:
    """
    Apply one stock movement atomically and return the created Kardex row.

    MUST be called inside (or will open) a transaction. The product row is
    locked with select_for_update() so concurrent movements serialise.
    """
    if movement_type not in dict(StockMovement.MOVEMENT_TYPE_CHOICES):
        raise InvalidMovementError(f'Tipo de movimiento no válido: {movement_type}')

    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise InvalidMovementError('La cantidad debe ser un número entero.')

    if quantity <= 0:
        raise InvalidMovementError('La cantidad debe ser mayor que cero.')

    reason = (reason or '').strip()[:MAX_REASON_LENGTH]

    with transaction.atomic():
        try:
            product = Product.objects.select_for_update().get(pk=product_id)
        except Product.DoesNotExist:
            raise InvalidMovementError('Producto no encontrado.')

        stock_before = product.inventory
        is_entry = movement_type in StockMovement.ENTRY_TYPES
        delta = quantity if is_entry else -quantity
        stock_after = stock_before + delta

        if stock_after < 0:
            raise InsufficientStockError(
                f'Stock insuficiente para "{product.name}". '
                f'Stock actual: {stock_before}, salida solicitada: {quantity}.'
            )

        # Write the new stock through the locked row — no read-modify-write race.
        Product.objects.filter(pk=product.pk).update(inventory=stock_after)

        return StockMovement.objects.create(
            product=product,
            movement_type=movement_type,
            quantity=quantity,
            stock_before=stock_before,
            stock_after=stock_after,
            reason=reason,
            reference_type=reference_type,
            reference_id=str(reference_id or ''),
            order=order,
            actor=actor,
            metadata=metadata or {},
        )


def apply_manual_stock_movement(
    *,
    product_id: int,
    movement_type: str,
    quantity: int,
    reason: str,
    actor,
    request=None,
) -> StockMovement:
    """
    Operator-initiated entry or exit. Requires actor + reason, writes an audit log.

    `sale_exit` is rejected here on purpose: sale movements are only ever created
    by the payment pipeline, never by hand.
    """
    if movement_type not in StockMovement.MANUAL_TYPES:
        raise InvalidMovementError(
            f'El tipo "{movement_type}" no puede registrarse manualmente.'
        )
    if actor is None or not getattr(actor, 'is_authenticated', False):
        raise InvalidMovementError('Todo movimiento manual requiere un usuario responsable.')
    if not (reason or '').strip():
        raise InvalidMovementError('El motivo es obligatorio para movimientos manuales.')

    movement = create_stock_movement(
        product_id=product_id,
        movement_type=movement_type,
        quantity=quantity,
        reason=reason,
        actor=actor,
        reference_type='manual',
    )

    action = (
        'stock_entry_created'
        if movement.movement_type in StockMovement.ENTRY_TYPES
        else 'stock_exit_created'
    )
    # Metadata is deliberately limited to inventory facts — never payment data.
    AdminAuditLog.log(
        actor=actor,
        action=action,
        target_type='stock_movement',
        target_id=movement.pk,
        metadata={
            'product_id': movement.product_id,
            'product_name': movement.product.name,
            'movement_type': movement.movement_type,
            'quantity': movement.quantity,
            'stock_before': movement.stock_before,
            'stock_after': movement.stock_after,
            'reason': movement.reason,
        },
        request=request,
    )
    return movement


def record_sale_stock_movements(order: Order, *, actor=None) -> list[StockMovement]:
    """
    Register one `sale_exit` per order item and decrement stock.

    IDEMPOTENT: if a sale_exit already exists for (order, product) that product
    is skipped, so a replayed Stripe webhook never subtracts stock twice.

    Never raises on insufficient stock — the money is already captured at this
    point. The shortfall is recorded on the movement metadata and returned to the
    caller so it can be surfaced to an operator, mirroring the previous behaviour.
    """
    movements: list[StockMovement] = []

    with transaction.atomic():
        already_recorded = set(
            StockMovement.objects
            .filter(order=order, movement_type=StockMovement.SALE_EXIT)
            .values_list('product_id', flat=True)
        )

        for item in order.items.select_related('product').all():
            if item.product_id in already_recorded:
                continue  # idempotency guard

            try:
                movement = create_stock_movement(
                    product_id=item.product_id,
                    movement_type=StockMovement.SALE_EXIT,
                    quantity=item.quantity,
                    reason=f'Venta confirmada — orden #{order.pk}',
                    actor=actor,
                    order=order,
                    reference_type='order',
                    reference_id=order.pk,
                    metadata={'order_id': order.pk, 'unit_price': str(item.price)},
                )
            except InsufficientStockError as exc:
                # Payment already captured: flag the discrepancy, do not roll back.
                movement = None
                _flag_stock_shortfall(order, item, str(exc))

            if movement is not None:
                movements.append(movement)
                already_recorded.add(item.product_id)

    return movements


def _flag_stock_shortfall(order: Order, item, message: str) -> None:
    """Append a shortfall note to the order for admin review (no payment data)."""
    note = f'Stock insuficiente para producto ID={item.product_id} al confirmar pago.'
    existing = order.payment_error or ''
    order.payment_error = (existing + '\n' if existing else '') + note
    # Caller persists payment_error together with the payment fields.


# ---------------------------------------------------------------------------
# Read path — reports
# ---------------------------------------------------------------------------

def _active_products():
    return Product.objects.filter(is_active=True)


def get_low_stock_products(threshold: int = DEFAULT_LOW_STOCK_THRESHOLD, limit: int = 50):
    """Active products at or below `threshold` units, lowest first."""
    return list(
        _active_products()
        .filter(inventory__lte=threshold)
        .order_by('inventory', 'name')[:limit]
    )


def get_high_stock_products(limit: int = 20):
    """Active products with the most units on hand."""
    return list(_active_products().order_by('-inventory', 'name')[:limit])


def get_best_selling_products(date_from=None, date_to=None, limit: int = 10):
    """
    Units sold and revenue per product, derived from PAID orders only.

    Returns a list of dicts: product_id, product_name, units_sold, revenue.
    """
    from .models import OrderItem

    qs = OrderItem.objects.filter(order__status=Order.Status.PAID)
    if date_from:
        qs = qs.filter(order__paid_at__gte=date_from)
    if date_to:
        qs = qs.filter(order__paid_at__lte=date_to)

    rows = (
        qs.values('product_id', 'product__name')
        .annotate(
            units_sold=Coalesce(Sum('quantity'), 0),
            revenue=Coalesce(
                Sum(F('quantity') * F('price'), output_field=DecimalField(max_digits=14, decimal_places=2)),
                Decimal('0.00'),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            ),
        )
        .order_by('-units_sold', 'product__name')[:limit]
    )

    return [
        {
            'product_id': r['product_id'],
            'product_name': r['product__name'],
            'units_sold': r['units_sold'],
            'revenue': str(Decimal(r['revenue']).quantize(Decimal('0.01'))),
        }
        for r in rows
    ]


def get_inventory_summary(low_stock_threshold: int = DEFAULT_LOW_STOCK_THRESHOLD) -> dict:
    """Headline inventory counters for the operational dashboard."""
    agg = Product.objects.aggregate(
        total_products=Count('id'),
        active_products=Count('id', filter=Q(is_active=True)),
    )
    active_agg = _active_products().aggregate(
        out_of_stock_count=Count('id', filter=Q(inventory__lte=0)),
        low_stock_count=Count(
            'id', filter=Q(inventory__gt=0, inventory__lte=low_stock_threshold)
        ),
        total_units=Coalesce(Sum('inventory', filter=Q(inventory__gt=0)), 0),
        inventory_value=Coalesce(
            Sum(
                F('inventory') * F('price'),
                filter=Q(inventory__gt=0),
                output_field=DecimalField(max_digits=16, decimal_places=2),
            ),
            Decimal('0.00'),
            output_field=DecimalField(max_digits=16, decimal_places=2),
        ),
    )

    best = get_best_selling_products(limit=1)

    return {
        'total_products': agg['total_products'],
        'active_products': agg['active_products'],
        'out_of_stock_count': active_agg['out_of_stock_count'],
        'low_stock_count': active_agg['low_stock_count'],
        'total_units': active_agg['total_units'],
        'inventory_value': str(
            Decimal(active_agg['inventory_value']).quantize(Decimal('0.01'))
        ),
        'low_stock_threshold': low_stock_threshold,
        'best_selling_product': best[0] if best else None,
    }


def get_stock_card(product: Product, limit: int = 200):
    """Kardex for one product — newest movement first."""
    return list(
        product.stock_movements
        .select_related('actor', 'order')
        .order_by('-created_at', '-id')[:limit]
    )


def get_products_without_movement(days: int = 60, limit: int = 50):
    """Active products with no Kardex entry in the last `days` days."""
    cutoff = timezone.now() - timedelta(days=days)
    moved_ids = (
        StockMovement.objects.filter(created_at__gte=cutoff)
        .values_list('product_id', flat=True)
        .distinct()
    )
    return list(
        _active_products().exclude(pk__in=moved_ids).order_by('-inventory', 'name')[:limit]
    )
