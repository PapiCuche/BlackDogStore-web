"""
Inventory service layer — Phase 6.0, rebuilt for multiple branches in Phase 2D.

THE ONE RULE
------------
Stock is `BranchStock.quantity`, and it changes HERE or nowhere. Views, the
payment notification, transfers, physical counts and the Django admin all come
through these functions so that stock, the Kardex (StockMovement) and the
`Product.inventory` compatibility aggregate move together inside one
transaction. There is no second inventory system and no shortcut around this
module — a direct `update(inventory=...)` anywhere else is a bug.

WHAT CHANGED IN PHASE 2D
------------------------
Every write now needs a BRANCH. "Add 5 units of the iPhone" is not an
instruction a multi-branch business can execute; "add 5 units to Cayma" is.
Concretely:

  - `BranchStock(branch, product)` is the source of truth, locked with
    select_for_update() on every write.
  - `Product.inventory` is maintained as the SUM of a product's branch stocks,
    in the same transaction, so the public catalogue keeps working unchanged.
    It is derived. Nothing reads it to decide whether a sale can be fulfilled.
  - `stock_before` / `stock_after` on a Kardex line are THIS BRANCH's running
    balance, not a company total.
  - A movement carries `company` and `branch` explicitly, so the Kardex can be
    filtered per tenant and per location without joining through the product.

HARD RULES, UNCHANGED SINCE 6.0
-------------------------------
  - Stock never goes negative. A movement that would do so raises
    InsufficientStockError and the transaction rolls back.
  - The current stock is always re-read from the DB under select_for_update();
    a client-supplied stock value is never trusted.
  - quantity is always positive; movement_type decides whether it adds or subtracts.
  - Manual movements require an actor and a non-empty reason.
  - Sale exits are idempotent per (order, product): replaying a gateway notification
    never subtracts twice.

LOCK ORDERING — deadlock safety
-------------------------------
Any operation touching more than one stock row (a transfer, a count approval)
locks them in ascending `(branch_id, product_id)` order, via
`_locked_branch_stocks()`. Two concurrent operations over overlapping sets
therefore request their locks in the same sequence and queue instead of
deadlocking. Never hand-roll a second locking order.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Count, DecimalField, F, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import (
    AdminAuditLog,
    Branch,
    BranchStock,
    InventoryCount,
    InventoryCountItem,
    Order,
    Product,
    StockMovement,
    StockTransfer,
    StockTransferItem,
)

# Fallback threshold for "low stock" when a branch has configured no minimum
# for a product. Per-product/per-branch `minimum_stock` is the real answer; this
# only keeps the pre-2D reports meaningful for stock nobody has configured yet.
DEFAULT_LOW_STOCK_THRESHOLD = 5
MAX_REASON_LENGTH = 500


class InventoryError(Exception):
    """Base class for inventory rule violations. Views map these to HTTP 400."""


class InsufficientStockError(InventoryError):
    """Raised when a movement would leave stock below zero."""


class InvalidMovementError(InventoryError):
    """Raised when the movement payload breaks a business rule."""


class TransferError(InventoryError):
    """Raised when a transfer operation breaks a business rule."""


class InventoryCountError(InventoryError):
    """Raised when a physical count operation breaks a business rule."""


# ---------------------------------------------------------------------------
# BranchStock access
# ---------------------------------------------------------------------------

def _resolve_branch(branch) -> Branch:
    """Accept a Branch or its pk. A missing branch is a movement with no place."""
    if isinstance(branch, Branch):
        return branch
    if branch is None:
        raise InvalidMovementError('Todo movimiento de stock requiere una sucursal.')
    try:
        return Branch.objects.get(pk=branch)
    except (Branch.DoesNotExist, ValueError, TypeError):
        raise InvalidMovementError('Sucursal no encontrada.')


def get_or_create_branch_stock(branch, product) -> BranchStock:
    """
    The stock row for (branch, product), created at zero if absent.

    ABSENCE MEANS ZERO. A product that has never been stocked in a branch has
    none there, so no caller has to distinguish "no row" from "0 units" — and
    creating the row is not itself a stock movement, because nothing moved.

    Rejects a product from a different company than the branch: that pairing is
    meaningless and, if it were ever written, would put one tenant's units on
    another tenant's shelf.
    """
    branch = _resolve_branch(branch)
    if product.company_id != branch.company_id:
        raise InvalidMovementError(
            'El producto no pertenece a la empresa de esta sucursal.'
        )
    try:
        stock, _created = BranchStock.objects.get_or_create(
            branch=branch, product=product, defaults={'quantity': 0},
        )
    except IntegrityError:
        # Lost a race against a concurrent creator: the row now exists.
        stock = BranchStock.objects.get(branch=branch, product=product)
    return stock


def _locked_branch_stocks(branch, products) -> dict[int, BranchStock]:
    """
    Lock the stock rows for `products` in `branch`, in deadlock-safe order.

    Rows are created first (creation is not a lock-ordered operation, and a
    missing row cannot be locked), then re-read with select_for_update() sorted
    by `(branch_id, product_id)` — the single ordering the whole module uses.
    """
    branch = _resolve_branch(branch)
    for product in products:
        get_or_create_branch_stock(branch, product)

    product_ids = sorted({p.pk for p in products})
    locked = (
        BranchStock.objects
        .select_for_update()
        .filter(branch=branch, product_id__in=product_ids)
        .order_by('branch_id', 'product_id')
    )
    return {row.product_id: row for row in locked}


def branch_quantity(branch, product) -> int:
    """Units of `product` in `branch` right now. Zero when never stocked."""
    row = BranchStock.objects.filter(branch=branch, product=product).first()
    return row.quantity if row else 0


def recalculate_product_inventory(product_id: int) -> int:
    """
    Rewrite `Product.inventory` from the branch stocks it aggregates.

    The compatibility field is normally kept in step by every movement; this is
    the repair path — used by the Phase 2D migration and by the consistency
    checks. It is idempotent and safe to call at any time.
    """
    total = (
        BranchStock.objects
        .filter(product_id=product_id)
        .aggregate(total=Coalesce(Sum('quantity'), 0))['total']
    )
    Product.objects.filter(pk=product_id).update(inventory=total)
    return total


def product_inventory_drift(company=None):
    """
    Products whose compatibility aggregate disagrees with their branch stocks.

    Returns a list of dicts (product_id, inventory, branch_total). An empty list
    is the invariant holding. Used by the consistency tests and available to an
    operator who wants to prove the two never diverged.
    """
    products = Product.objects.all()
    if company is not None:
        products = products.filter(company=company)

    rows = products.annotate(
        branch_total=Coalesce(Sum('branch_stocks__quantity'), 0),
    ).exclude(branch_total=F('inventory')).values('id', 'inventory', 'branch_total')

    return [
        {
            'product_id': r['id'],
            'inventory': r['inventory'],
            'branch_total': r['branch_total'],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Core write path
# ---------------------------------------------------------------------------

def create_stock_movement(
    *,
    branch,
    product_id: int,
    movement_type: str,
    quantity: int,
    reason: str = '',
    actor=None,
    order=None,
    transfer=None,
    inventory_count=None,
    reference_type: str = '',
    reference_id: str = '',
    metadata: dict | None = None,
) -> StockMovement:
    """
    Apply one stock movement to one branch atomically and return the Kardex row.

    MUST be called inside (or will open) a transaction. The BranchStock row is
    locked with select_for_update() so concurrent movements on the same
    (branch, product) serialise — this is what makes "never negative" true under
    load rather than merely on paper.

    The PRODUCT is deliberately not the lock: locking it would serialise every
    branch of a chain against every other for the same article, turning
    unrelated shops into each other's queue.
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
    branch = _resolve_branch(branch)

    with transaction.atomic():
        try:
            product = Product.objects.get(pk=product_id)
        except (Product.DoesNotExist, ValueError, TypeError):
            raise InvalidMovementError('Producto no encontrado.')

        if product.company_id != branch.company_id:
            raise InvalidMovementError(
                'El producto no pertenece a la empresa de esta sucursal.'
            )
        if order is not None and order.company_id != branch.company_id:
            raise InvalidMovementError(
                'El pedido no pertenece a la empresa de esta sucursal.'
            )

        stock = _locked_branch_stocks(branch, [product])[product.pk]

        stock_before = stock.quantity
        is_entry = movement_type in StockMovement.ENTRY_TYPES
        delta = quantity if is_entry else -quantity
        stock_after = stock_before + delta

        if stock_after < 0:
            raise InsufficientStockError(
                f'Stock insuficiente para "{product.name}" en {branch.name}. '
                f'Stock actual: {stock_before}, salida solicitada: {quantity}.'
            )

        # Write through the locked row — no read-modify-write race.
        BranchStock.objects.filter(pk=stock.pk).update(quantity=stock_after)
        # Keep the compatibility aggregate in the SAME transaction. An F()
        # expression, not a recomputed sum, so two branches moving the same
        # product concurrently add up instead of overwriting each other.
        Product.objects.filter(pk=product.pk).update(inventory=F('inventory') + delta)

        return StockMovement.objects.create(
            company_id=branch.company_id,
            branch=branch,
            product=product,
            movement_type=movement_type,
            quantity=quantity,
            stock_before=stock_before,
            stock_after=stock_after,
            reason=reason,
            reference_type=reference_type,
            reference_id=str(reference_id or ''),
            order=order,
            transfer=transfer,
            inventory_count=inventory_count,
            actor=actor,
            metadata=metadata or {},
        )


def apply_manual_stock_movement(
    *,
    branch,
    product_id: int,
    movement_type: str,
    quantity: int,
    reason: str,
    actor,
    request=None,
) -> StockMovement:
    """
    Operator-initiated entry or exit in one branch. Requires actor + reason.

    `sale_exit` is rejected here on purpose, and so are the two transfer types:
    sale movements are only ever created by the payment pipeline, and a
    hand-written `transfer_out` with no matching `transfer_in` would be stock
    that simply vanished from the company.

    AUTHORITY IS NOT CHECKED HERE. This function assumes the caller has already
    established that the actor holds `inventory.adjust` in the company AND may
    operate `branch` — see tenancy.assert_branch_access. Mixing the two would
    make the service layer depend on request context it does not have.
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
        branch=branch,
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
            'branch_id': movement.branch_id,
            'branch_name': movement.branch.name,
            'product_id': movement.product_id,
            'product_name': movement.product.name,
            'movement_type': movement.movement_type,
            'quantity': movement.quantity,
            'stock_before': movement.stock_before,
            'stock_after': movement.stock_after,
            'reason': movement.reason,
        },
        request=request,
        company=movement.company,
    )
    return movement


def record_sale_stock_movements(
    order: Order, *, actor=None, strict: bool = False,
) -> list[StockMovement]:
    """
    Register one `sale_exit` per order item and decrement the FULFILLING branch.

    TWO CHANNELS, TWO POLICIES ON SHORTFALL — and one implementation
    ----------------------------------------------------------------
    `strict=False` (ONLINE, the default and the original behaviour): the money
    is already captured by the time the gateway's notification arrives, so a shortfall is
    recorded on the order and the item is skipped. Refusing here would leave a
    paid order that never decremented stock.

    `strict=True` (POS): nothing has been captured yet — the operator is
    standing at the counter and the whole sale is inside one transaction. A
    shortfall RAISES, the transaction rolls back, and no order exists. Selling
    something the shop does not have is the failure to prevent, not to record.

    The two share this body on purpose. Two copies would drift, and the one that
    drifted would be the one nobody was watching.

    WHICH BRANCH: `order.fulfillment_branch`, decided once at checkout and
    stored. Never re-derived here — a company that changes its default branch
    between payment and webhook must not have already-sold units taken from a
    shop that never had them.

    IDEMPOTENT: if a sale_exit already exists for (order, product) that product
    is skipped, so a replayed gateway notification never subtracts stock twice. The
    key stays (order, product) rather than (order, product, branch) because an
    order has exactly ONE fulfillment branch — adding the branch would widen the
    key and weaken the guarantee, not strengthen it. If orders ever ship from
    several branches, this key must change with that design.

    Never raises on insufficient stock — the money is already captured at this
    point. The shortfall is recorded on order.payment_error and the item is
    skipped, mirroring the pre-2D behaviour. Stock is NEVER taken from another
    branch to cover it: that would silently create a second discrepancy
    somewhere nobody is looking.
    """
    movements: list[StockMovement] = []

    branch = order.fulfillment_branch
    if branch is None:
        # AN ORDER WITH NO BRANCH — and why this repairs rather than refuses.
        #
        # Checkout always stamps one and migration 0025 backfilled every
        # historical order, so this is the path for an order created by
        # something that predates or bypasses checkout. The money is already
        # captured; refusing would leave a paid order that silently never
        # decremented stock, which is the worse of the two failures.
        #
        # The company's configured fulfillment branch is not a guess: it is the
        # same value checkout would have used, chosen by the tenant. It is
        # STAMPED on the order so the Kardex and the order agree afterwards
        # rather than diverging quietly.
        from .tenancy import company_fulfillment_branch

        branch = company_fulfillment_branch(order.company)
        if branch is None:
            _flag_stock_shortfall(
                order, None,
                'El pedido no tiene sucursal de despacho y la empresa no tiene una '
                'configurada; no se descontó stock.',
            )
            return movements
        order.fulfillment_branch = branch
        Order.objects.filter(pk=order.pk).update(fulfillment_branch=branch)

    with transaction.atomic():
        already_recorded = set(
            StockMovement.objects
            .filter(order=order, movement_type=StockMovement.SALE_EXIT)
            .values_list('product_id', flat=True)
        )

        items = list(order.items.select_related('product').all())

        if strict:
            # VALIDATE EVERYTHING BEFORE WRITING ANYTHING.
            #
            # Without this, a two-line sale whose second product is short would
            # decrement the first, then raise, then roll back — correct, but it
            # holds a write lock on a row it was always going to release. Worse,
            # the error would name the second product while the operator watched
            # the first one's stock flicker.
            #
            # Locking every row up front, in the module's one ordering, also
            # means two tills selling overlapping baskets queue instead of
            # deadlocking.
            pending = [i for i in items if i.product_id not in already_recorded]
            if pending:
                locked = _locked_branch_stocks(
                    branch, [i.product for i in pending],
                )
                required: dict[int, int] = {}
                for item in pending:
                    required[item.product_id] = (
                        required.get(item.product_id, 0) + item.quantity
                    )
                for product_id, needed in sorted(required.items()):
                    row = locked.get(product_id)
                    available = row.quantity if row else 0
                    if available < needed:
                        product = next(
                            i.product for i in pending if i.product_id == product_id
                        )
                        raise InsufficientStockError(
                            f'Stock insuficiente para "{product.name}" en '
                            f'{branch.name}. Stock actual: {available}, '
                            f'salida solicitada: {needed}.'
                        )

        for item in items:
            if item.product_id in already_recorded:
                continue  # idempotency guard

            try:
                movement = create_stock_movement(
                    branch=branch,
                    product_id=item.product_id,
                    movement_type=StockMovement.SALE_EXIT,
                    quantity=item.quantity,
                    reason=f'Venta confirmada — orden #{order.pk}',
                    actor=actor,
                    order=order,
                    reference_type='order',
                    reference_id=order.pk,
                    metadata={
                        'order_id': order.pk,
                        'branch_id': branch.pk,
                        'unit_price': str(item.price),
                    },
                )
            except InsufficientStockError as exc:
                if strict:
                    # POS: nothing captured yet, so refuse the whole sale.
                    raise
                # Payment already captured: flag the discrepancy, do not roll back.
                movement = None
                _flag_stock_shortfall(order, item, str(exc))

            if movement is not None:
                movements.append(movement)
                already_recorded.add(item.product_id)

    return movements


def _flag_stock_shortfall(order: Order, item, message: str) -> None:
    """
    Append a shortfall note to the order for admin review.

    Names the product, the branch and the order — the three facts an operator
    needs to fix it — and nothing about the payment. `message` is not appended
    verbatim: it may carry a product name, and the note is read in a screen that
    already shows one.
    """
    if item is None:
        note = message
    else:
        branch_id = order.fulfillment_branch_id
        note = (
            f'Stock insuficiente para producto ID={item.product_id} '
            f'en sucursal ID={branch_id} al confirmar pago.'
        )
    existing = order.payment_error or ''
    order.payment_error = (existing + '\n' if existing else '') + note
    # Caller persists payment_error together with the payment fields.


def apply_initial_stock(
    *, branch, product, quantity: int, actor=None, reason: str = '', request=None,
) -> StockMovement | None:
    """
    Open a branch's balance for a product with an `initial_stock` Kardex line.

    Used when a product is created with an opening stock figure. The entry
    exists so that no unit ever appears in a branch without a movement
    explaining it — a balance with no first line is exactly the kind of
    unexplained stock a Kardex is supposed to make impossible.

    Returns None for a zero quantity: nothing happened, so nothing is recorded.
    """
    quantity = int(quantity or 0)
    if quantity <= 0:
        return None

    movement = create_stock_movement(
        branch=branch,
        product_id=product.pk,
        movement_type=StockMovement.INITIAL_STOCK,
        quantity=quantity,
        reason=reason or 'Stock inicial al crear el producto',
        actor=actor,
        reference_type='product',
        reference_id=product.pk,
        metadata={'product_id': product.pk},
    )
    AdminAuditLog.log(
        actor=actor,
        action='stock_initial_recorded',
        target_type='stock_movement',
        target_id=movement.pk,
        metadata={
            'branch_id': movement.branch_id,
            'product_id': product.pk,
            'product_name': product.name,
            'quantity': quantity,
        },
        request=request,
        company=movement.company,
    )
    return movement


# ---------------------------------------------------------------------------
# Inter-branch transfers
# ---------------------------------------------------------------------------
#
# Stock moves at the EDGES of the lifecycle, never on a status change:
#
#     DRAFT ──dispatch──▶ IN_TRANSIT ──receive──▶ RECEIVED
#       │                (source −q)              (dest +q)
#       └──cancel──▶ CANCELLED
#
# Both edges are idempotent by CHECKING THE STATUS UNDER A ROW LOCK, not by
# trusting the caller not to click twice. A second dispatch of an IN_TRANSIT
# transfer is a no-op that returns the existing movements; it is not an error,
# because a retried request is the normal way networks behave.

def create_stock_transfer(
    *, company, source_branch, destination_branch, actor=None,
    reason: str = '', reference: str = '',
) -> StockTransfer:
    """Open a DRAFT transfer between two branches of one company."""
    source_branch = _resolve_branch(source_branch)
    destination_branch = _resolve_branch(destination_branch)

    if source_branch.pk == destination_branch.pk:
        raise TransferError('El origen y el destino no pueden ser la misma sucursal.')
    if source_branch.company_id != company.pk or destination_branch.company_id != company.pk:
        raise TransferError('Ambas sucursales deben pertenecer a la misma empresa.')
    if not source_branch.is_active or not destination_branch.is_active:
        raise TransferError('No se puede transferir desde o hacia una sucursal inactiva.')

    return StockTransfer.objects.create(
        company=company,
        source_branch=source_branch,
        destination_branch=destination_branch,
        status=StockTransfer.STATUS_DRAFT,
        reason=(reason or '').strip()[:MAX_REASON_LENGTH],
        reference=(reference or '').strip()[:120],
        created_by=actor,
    )


def set_transfer_item(transfer: StockTransfer, *, product, quantity: int) -> StockTransferItem | None:
    """
    Add, update or remove one line of a DRAFT transfer.

    A quantity of zero REMOVES the line — a transfer of nothing is not a line
    worth keeping, and expressing removal as a quantity avoids a second verb.

    Refuses once anything physical has happened: after dispatch the document
    describes units already on a van, and editing it would make the Kardex
    disagree with the paperwork somebody is holding.
    """
    if not transfer.is_editable:
        raise TransferError(
            'Solo se pueden editar las líneas de una transferencia en borrador.'
        )
    if product.company_id != transfer.company_id:
        raise TransferError('El producto no pertenece a la empresa de esta transferencia.')

    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise TransferError('La cantidad debe ser un número entero.')
    if quantity < 0:
        raise TransferError('La cantidad no puede ser negativa.')

    if quantity == 0:
        StockTransferItem.objects.filter(transfer=transfer, product=product).delete()
        return None

    item, _created = StockTransferItem.objects.update_or_create(
        transfer=transfer, product=product, defaults={'quantity': quantity},
    )
    return item


def dispatch_transfer(transfer: StockTransfer, *, actor=None, request=None) -> list[StockMovement]:
    """
    Take the units out of the source branch and put the transfer in transit.

    ATOMIC AND ALL-OR-NOTHING: if any line has insufficient stock the whole
    dispatch is refused. A partially dispatched transfer would be a document
    claiming to carry more than it does, which nobody downstream could reconcile.

    IDEMPOTENT: dispatching an already-dispatched transfer changes nothing and
    returns its existing `transfer_out` movements.
    """
    with transaction.atomic():
        locked = StockTransfer.objects.select_for_update().get(pk=transfer.pk)

        if locked.status == StockTransfer.STATUS_IN_TRANSIT:
            return list(
                StockMovement.objects.filter(
                    transfer=locked, movement_type=StockMovement.TRANSFER_OUT,
                )
            )
        if locked.status != StockTransfer.STATUS_DRAFT:
            raise TransferError(
                'Solo una transferencia en borrador puede despacharse.'
            )

        items = list(locked.items.select_related('product').order_by('product_id'))
        if not items:
            raise TransferError('La transferencia no tiene productos.')
        if not locked.source_branch.is_active or not locked.destination_branch.is_active:
            raise TransferError('No se puede despachar con una sucursal inactiva.')

        # Lock every source row first, in the module's single ordering, then
        # verify the whole set before writing anything.
        stocks = _locked_branch_stocks(
            locked.source_branch, [i.product for i in items],
        )
        for item in items:
            available = stocks[item.product_id].quantity
            if available < item.quantity:
                raise InsufficientStockError(
                    f'Stock insuficiente de "{item.product.name}" en '
                    f'{locked.source_branch.name}. Disponible: {available}, '
                    f'solicitado: {item.quantity}.'
                )

        movements = [
            create_stock_movement(
                branch=locked.source_branch,
                product_id=item.product_id,
                movement_type=StockMovement.TRANSFER_OUT,
                quantity=item.quantity,
                reason=f'Transferencia #{locked.pk} hacia {locked.destination_branch.name}',
                actor=actor,
                transfer=locked,
                reference_type='transfer',
                reference_id=locked.pk,
                metadata={
                    'transfer_id': locked.pk,
                    'destination_branch_id': locked.destination_branch_id,
                },
            )
            for item in items
        ]

        locked.status = StockTransfer.STATUS_IN_TRANSIT
        locked.dispatched_by = actor
        locked.dispatched_at = timezone.now()
        locked.save(update_fields=['status', 'dispatched_by', 'dispatched_at', 'updated_at'])

    AdminAuditLog.log(
        actor=actor,
        action='stock_transfer_dispatched',
        target_type='stock_transfer',
        target_id=locked.pk,
        metadata={
            'transfer_id': locked.pk,
            'source_branch_id': locked.source_branch_id,
            'destination_branch_id': locked.destination_branch_id,
            'lines': len(movements),
            'units': sum(m.quantity for m in movements),
        },
        request=request,
        company=locked.company,
    )
    transfer.refresh_from_db()
    return movements


def receive_transfer(transfer: StockTransfer, *, actor=None, request=None) -> list[StockMovement]:
    """
    Put the units on the destination branch's shelf and close the transfer.

    V1 receives the transfer COMPLETE. Partial receipt is deliberately not
    implemented (see docs/saas-multiempresa.md, "PENDIENTE — recepción
    parcial"): doing it properly needs a per-line received quantity, a
    discrepancy workflow and a decision about who owns the missing units, and a
    half-built version of that would quietly lose stock.

    IDEMPOTENT: receiving an already-received transfer returns its existing
    `transfer_in` movements and changes nothing.
    """
    with transaction.atomic():
        locked = StockTransfer.objects.select_for_update().get(pk=transfer.pk)

        if locked.status == StockTransfer.STATUS_RECEIVED:
            return list(
                StockMovement.objects.filter(
                    transfer=locked, movement_type=StockMovement.TRANSFER_IN,
                )
            )
        if locked.status != StockTransfer.STATUS_IN_TRANSIT:
            raise TransferError('Solo una transferencia en tránsito puede recibirse.')

        items = list(locked.items.select_related('product').order_by('product_id'))
        if not items:
            raise TransferError('La transferencia no tiene productos.')

        movements = [
            create_stock_movement(
                branch=locked.destination_branch,
                product_id=item.product_id,
                movement_type=StockMovement.TRANSFER_IN,
                quantity=item.quantity,
                reason=f'Transferencia #{locked.pk} desde {locked.source_branch.name}',
                actor=actor,
                transfer=locked,
                reference_type='transfer',
                reference_id=locked.pk,
                metadata={
                    'transfer_id': locked.pk,
                    'source_branch_id': locked.source_branch_id,
                },
            )
            for item in items
        ]

        locked.status = StockTransfer.STATUS_RECEIVED
        locked.received_by = actor
        locked.received_at = timezone.now()
        locked.save(update_fields=['status', 'received_by', 'received_at', 'updated_at'])

    AdminAuditLog.log(
        actor=actor,
        action='stock_transfer_received',
        target_type='stock_transfer',
        target_id=locked.pk,
        metadata={
            'transfer_id': locked.pk,
            'source_branch_id': locked.source_branch_id,
            'destination_branch_id': locked.destination_branch_id,
            'lines': len(movements),
            'units': sum(m.quantity for m in movements),
        },
        request=request,
        company=locked.company,
    )
    transfer.refresh_from_db()
    return movements


def cancel_transfer(transfer: StockTransfer, *, actor=None, request=None) -> StockTransfer:
    """
    Cancel a transfer that has NOT been dispatched.

    An IN_TRANSIT transfer cannot be cancelled, and this is a deliberate refusal
    rather than a missing feature. Its units have physically left the source
    branch; flipping a status back would return them to the shelf in the
    database while they sit in a van, and the shop would sell stock it does not
    have. Undoing a dispatch requires compensating movements — effectively a
    return transfer — which V1 does not implement. Until it does, the honest
    answer is no.
    """
    with transaction.atomic():
        locked = StockTransfer.objects.select_for_update().get(pk=transfer.pk)

        if locked.status == StockTransfer.STATUS_CANCELLED:
            return locked
        if locked.status != StockTransfer.STATUS_DRAFT:
            raise TransferError(
                'Solo una transferencia en borrador puede anularse. Una transferencia '
                'ya despachada tiene stock fuera de la sucursal de origen y debe '
                'recibirse; revertirla exige movimientos compensatorios que todavía '
                'no están implementados.'
            )

        locked.status = StockTransfer.STATUS_CANCELLED
        locked.cancelled_by = actor
        locked.cancelled_at = timezone.now()
        locked.save(update_fields=['status', 'cancelled_by', 'cancelled_at', 'updated_at'])

    AdminAuditLog.log(
        actor=actor,
        action='stock_transfer_cancelled',
        target_type='stock_transfer',
        target_id=locked.pk,
        metadata={
            'transfer_id': locked.pk,
            'source_branch_id': locked.source_branch_id,
            'destination_branch_id': locked.destination_branch_id,
        },
        request=request,
        company=locked.company,
    )
    transfer.refresh_from_db()
    return locked


# ---------------------------------------------------------------------------
# Physical counts
# ---------------------------------------------------------------------------

def create_inventory_count(*, company, branch, actor=None, reason: str = '') -> InventoryCount:
    """Open a DRAFT physical count for one branch."""
    branch = _resolve_branch(branch)
    if branch.company_id != company.pk:
        raise InventoryCountError('La sucursal no pertenece a esta empresa.')
    if not branch.is_active:
        raise InventoryCountError('No se puede inventariar una sucursal inactiva.')

    return InventoryCount.objects.create(
        company=company,
        branch=branch,
        status=InventoryCount.STATUS_DRAFT,
        reason=(reason or '').strip()[:MAX_REASON_LENGTH],
        created_by=actor,
    )


def set_count_item(
    count: InventoryCount, *, product, physical_quantity=None, note: str = '',
) -> InventoryCountItem:
    """
    Record what was physically found for one product.

    `theoretical_at_start` is captured the FIRST time the product enters the
    count and never overwritten: it is the evidence of what the system claimed
    when the person began counting. It is not used to compute the correction —
    see approve_inventory_count() for why.

    `physical_quantity=None` means "not counted yet", which is not the same as
    counting zero and is never treated as such.
    """
    if not count.is_editable:
        raise InventoryCountError('Este recuento ya no admite cambios.')
    if product.company_id != count.company_id:
        raise InventoryCountError('El producto no pertenece a la empresa de este recuento.')

    if physical_quantity is not None:
        try:
            physical_quantity = int(physical_quantity)
        except (TypeError, ValueError):
            raise InventoryCountError('La cantidad física debe ser un número entero.')
        if physical_quantity < 0:
            raise InventoryCountError('La cantidad física no puede ser negativa.')

    item = InventoryCountItem.objects.filter(count=count, product=product).first()
    if item is None:
        item = InventoryCountItem(
            count=count,
            product=product,
            theoretical_at_start=branch_quantity(count.branch, product),
        )

    item.physical_quantity = physical_quantity
    item.note = (note or '').strip()[:250]
    item.save()

    if count.status == InventoryCount.STATUS_DRAFT:
        count.status = InventoryCount.STATUS_COUNTING
        count.save(update_fields=['status', 'updated_at'])

    return item


def approve_inventory_count(
    count: InventoryCount, *, actor=None, request=None,
) -> list[StockMovement]:
    """
    Apply the counted differences as correction movements, under lock.

    THE RE-READ IS THE WHOLE POINT. The correction is

        physical_quantity − theoretical_at_approval

    where `theoretical_at_approval` is read from BranchStock inside this
    transaction, with the row already locked. Using `theoretical_at_start`
    instead would apply a delta computed from an hour-old photograph: every sale
    made while somebody walked the shelves would be silently un-sold, destroying
    real stock and real revenue in the same stroke.

    A product whose physical quantity was never entered is SKIPPED. Treating
    "nobody counted this" as "there are none" would write off inventory nobody
    looked at.

    IDEMPOTENT: approving an already-approved count returns its existing
    correction movements.
    """
    with transaction.atomic():
        locked = InventoryCount.objects.select_for_update().get(pk=count.pk)

        if locked.status == InventoryCount.STATUS_APPROVED:
            return list(StockMovement.objects.filter(inventory_count=locked))
        if locked.status not in InventoryCount.APPROVABLE_STATUSES:
            raise InventoryCountError(
                'Solo un recuento en conteo o en revisión puede aprobarse.'
            )

        items = list(
            locked.items.select_related('product')
            .filter(physical_quantity__isnull=False)
            .order_by('product_id')
        )
        if not items:
            raise InventoryCountError('El recuento no tiene productos contados.')

        stocks = _locked_branch_stocks(locked.branch, [i.product for i in items])

        movements: list[StockMovement] = []
        for item in items:
            theoretical = stocks[item.product_id].quantity
            difference = item.physical_quantity - theoretical

            item.theoretical_at_approval = theoretical
            item.difference = difference
            item.save(update_fields=[
                'theoretical_at_approval', 'difference', 'updated_at',
            ])

            if difference == 0:
                continue

            movement_type = (
                StockMovement.CORRECTION_POSITIVE if difference > 0
                else StockMovement.CORRECTION_NEGATIVE
            )
            movements.append(create_stock_movement(
                branch=locked.branch,
                product_id=item.product_id,
                movement_type=movement_type,
                quantity=abs(difference),
                reason=f'Ajuste por recuento físico #{locked.pk}',
                actor=actor,
                inventory_count=locked,
                reference_type='inventory_count',
                reference_id=locked.pk,
                metadata={
                    'inventory_count_id': locked.pk,
                    'theoretical_at_start': item.theoretical_at_start,
                    'theoretical_at_approval': theoretical,
                    'physical_quantity': item.physical_quantity,
                    'difference': difference,
                },
            ))

        locked.status = InventoryCount.STATUS_APPROVED
        locked.approved_by = actor
        locked.approved_at = timezone.now()
        locked.save(update_fields=['status', 'approved_by', 'approved_at', 'updated_at'])

    AdminAuditLog.log(
        actor=actor,
        action='inventory_count_approved',
        target_type='inventory_count',
        target_id=locked.pk,
        metadata={
            'inventory_count_id': locked.pk,
            'branch_id': locked.branch_id,
            'counted_items': len(items),
            'adjusted_items': len(movements),
        },
        request=request,
        company=locked.company,
    )
    count.refresh_from_db()
    return movements


def cancel_inventory_count(
    count: InventoryCount, *, actor=None, request=None,
) -> InventoryCount:
    """Cancel a count that was never approved. Approved counts are history."""
    with transaction.atomic():
        locked = InventoryCount.objects.select_for_update().get(pk=count.pk)

        if locked.status == InventoryCount.STATUS_CANCELLED:
            return locked
        if locked.status == InventoryCount.STATUS_APPROVED:
            raise InventoryCountError(
                'Un recuento aprobado ya generó movimientos y no puede anularse. '
                'Corrija con un nuevo recuento.'
            )

        locked.status = InventoryCount.STATUS_CANCELLED
        locked.cancelled_by = actor
        locked.cancelled_at = timezone.now()
        locked.save(update_fields=['status', 'cancelled_by', 'cancelled_at', 'updated_at'])

    AdminAuditLog.log(
        actor=actor,
        action='inventory_count_cancelled',
        target_type='inventory_count',
        target_id=locked.pk,
        metadata={'inventory_count_id': locked.pk, 'branch_id': locked.branch_id},
        request=request,
        company=locked.company,
    )
    count.refresh_from_db()
    return locked


# ---------------------------------------------------------------------------
# Read path — reports
# ---------------------------------------------------------------------------
#
# EVERY REPORT TAKES A BRANCH SET, AND THAT IS THE ISOLATION.
#
# The functions below never resolve authority themselves; they receive the
# branches the caller may see and aggregate over exactly those. A user granted
# Centro and Cayma but not Norte gets Centro+Cayma in every total, including the
# ones labelled "toda la empresa" — because for them, that IS the company they
# can see. Passing an unfiltered branch list is the only way to leak, and no
# view does it: they all pass tenancy.visible_branches().
#
# None of these read `Product.inventory`. It is a compatibility aggregate; a
# report that used it would silently include branches the caller cannot see.

def _branch_ids(branches) -> list[int]:
    """Normalise a queryset / list of Branch / list of ids into a list of ids."""
    if branches is None:
        return []
    if hasattr(branches, 'values_list'):
        return list(branches.values_list('pk', flat=True))
    return [getattr(b, 'pk', b) for b in branches]


def branch_stock_queryset(branches, *, active_products_only: bool = True):
    """BranchStock rows for `branches`. Empty when the caller reaches none."""
    ids = _branch_ids(branches)
    if not ids:
        return BranchStock.objects.none()
    qs = BranchStock.objects.filter(branch_id__in=ids)
    if active_products_only:
        qs = qs.filter(product__is_active=True)
    return qs.select_related('product', 'branch')


def low_stock_filter(threshold: int) -> Q:
    """
    "Low" means: at or below the branch minimum, or — when no minimum is
    configured for that row — at or below the global fallback threshold.

    The per-row minimum wins wherever it exists. A single company-wide number
    cannot express that a charger running low at 20 units downtown is perfectly
    stocked at 3 in a satellite shop, and pretending otherwise produced alerts
    everyone learned to ignore.
    """
    return (
        Q(minimum_stock__gt=0, quantity__lte=F('minimum_stock'))
        | Q(minimum_stock=0, quantity__lte=threshold)
    )


def get_low_stock_rows(branches, threshold: int = DEFAULT_LOW_STOCK_THRESHOLD, limit: int = 50):
    """Stock rows needing attention, scarcest first."""
    return list(
        branch_stock_queryset(branches)
        .filter(low_stock_filter(threshold))
        .order_by('quantity', 'product__name')[:limit]
    )


def get_high_stock_rows(branches, limit: int = 20):
    """Stock rows with the most units on hand."""
    return list(
        branch_stock_queryset(branches)
        .filter(quantity__gt=0)
        .order_by('-quantity', 'product__name')[:limit]
    )


def get_replenishment_rows(branches, limit: int = 100):
    """
    Replenishment SUGGESTIONS for the given branches.

        suggested = max(target_stock − quantity, 0)   when quantity <= minimum

    It is a suggestion and nothing else: this function creates no purchase, no
    transfer and no side effect of any kind. A human decides, and the backend
    checks their permissions when they do.

    Rows with no target configured are still listed — an operator needs to see
    that a product is below its minimum even when nobody has said how much to
    restock — with a suggestion of zero rather than a guess.
    """
    rows = (
        branch_stock_queryset(branches)
        .filter(minimum_stock__gt=0, quantity__lte=F('minimum_stock'))
        .order_by('quantity', 'product__name')[:limit]
    )
    return [
        {
            'branch_id': row.branch_id,
            'branch_name': row.branch.name,
            'product_id': row.product_id,
            'product_name': row.product.name,
            'current': row.quantity,
            'minimum': row.minimum_stock,
            'target': row.target_stock,
            'suggested_quantity': row.suggested_quantity,
        }
        for row in rows
    ]


def get_surplus_branches(product, branches, *, exclude_branch=None, limit: int = 5):
    """
    Other branches of the same company holding more than their own minimum.

    Shown next to a replenishment suggestion so an operator can see that the
    units may already be in the company. It CREATES NOTHING — no transfer is
    opened, nothing is reserved. The decision, and the transfer, are theirs.
    """
    qs = (
        branch_stock_queryset(branches)
        .filter(product=product, quantity__gt=0)
        .filter(quantity__gt=F('minimum_stock'))
    )
    if exclude_branch is not None:
        qs = qs.exclude(branch_id=getattr(exclude_branch, 'pk', exclude_branch))
    return [
        {
            'branch_id': row.branch_id,
            'branch_name': row.branch.name,
            'quantity': row.quantity,
            'minimum': row.minimum_stock,
            'surplus': row.quantity - row.minimum_stock,
        }
        for row in qs.order_by('-quantity')[:limit]
    ]


def get_best_selling_products(company=None, date_from=None, date_to=None, limit: int = 10,
                              branches=None):
    """
    Units sold and revenue per product, derived from PAID orders only.

    Company-scoped, and branch-scoped when `branches` is given — an order knows
    which branch fulfilled it, so a branch manager sees their own sales rather
    than the chain's.
    """
    from .models import OrderItem

    qs = OrderItem.objects.filter(order__status=Order.Status.PAID)
    if company is not None:
        qs = qs.filter(order__company=company)
    if branches is not None:
        ids = _branch_ids(branches)
        if not ids:
            return []
        qs = qs.filter(order__fulfillment_branch_id__in=ids)
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


def get_inventory_summary(
    company=None, branches=None, low_stock_threshold: int = DEFAULT_LOW_STOCK_THRESHOLD,
) -> dict:
    """
    Headline inventory counters for the branches the caller may see.

    `inventory_value` is stock × SALE PRICE, and it is labelled that way
    everywhere it is rendered. It is NOT the cost of the inventory and NOT
    capital invested: there is no cost model in the system, so any figure
    claiming to be either would be a number with a false name on it. Real
    valuation waits for purchase costs.
    """
    stock_rows = branch_stock_queryset(branches)

    products = Product.objects.all()
    if company is not None:
        products = products.filter(company=company)

    agg = products.aggregate(
        total_products=Count('id'),
        active_products=Count('id', filter=Q(is_active=True)),
    )

    stock_agg = stock_rows.aggregate(
        out_of_stock_count=Count('id', filter=Q(quantity__lte=0)),
        stocked_count=Count('id', filter=Q(quantity__gt=0)),
        low_stock_count=Count('id', filter=Q(quantity__gt=0) & low_stock_filter(low_stock_threshold)),
        total_units=Coalesce(Sum('quantity'), 0),
        inventory_value=Coalesce(
            Sum(
                F('quantity') * F('product__price'),
                output_field=DecimalField(max_digits=16, decimal_places=2),
            ),
            Decimal('0.00'),
            output_field=DecimalField(max_digits=16, decimal_places=2),
        ),
    )

    best = get_best_selling_products(company=company, limit=1, branches=branches)

    return {
        'total_products': agg['total_products'],
        'active_products': agg['active_products'],
        'out_of_stock_count': stock_agg['out_of_stock_count'],
        'low_stock_count': stock_agg['low_stock_count'],
        'stocked_count': stock_agg['stocked_count'],
        'total_units': stock_agg['total_units'],
        'inventory_value': str(
            Decimal(stock_agg['inventory_value']).quantize(Decimal('0.01'))
        ),
        'inventory_value_basis': 'sale_price',
        'low_stock_threshold': low_stock_threshold,
        'best_selling_product': best[0] if best else None,
    }


def get_stock_card(product: Product, branches=None, limit: int = 200):
    """
    Kardex for one product — newest movement first.

    Restricted to the caller's branches: a running balance that mixed in
    movements from a shop they cannot see would be unreadable and would leak
    that shop's activity in the process.
    """
    qs = product.stock_movements.select_related('actor', 'order', 'branch', 'transfer')
    if branches is not None:
        ids = _branch_ids(branches)
        if not ids:
            return []
        qs = qs.filter(branch_id__in=ids)
    return list(qs.order_by('-created_at', '-id')[:limit])


def get_products_without_movement(company=None, branches=None, days: int = 60, limit: int = 50):
    """
    Stock rows with units on hand and no Kardex activity in the last `days`.

    Branch-scoped by construction: a product selling briskly downtown can be
    dead stock in a satellite shop, and only the per-branch answer is actionable.
    """
    cutoff = timezone.now() - timedelta(days=days)
    ids = _branch_ids(branches)
    if not ids:
        return []

    moved = (
        StockMovement.objects
        .filter(created_at__gte=cutoff, branch_id__in=ids)
        .values_list('branch_id', 'product_id')
    )
    moved_pairs = set(moved)

    rows = (
        branch_stock_queryset(branches)
        .filter(quantity__gt=0)
        .order_by('-quantity', 'product__name')
    )
    stale = [r for r in rows if (r.branch_id, r.product_id) not in moved_pairs]
    return stale[:limit]


# ---------------------------------------------------------------------------
# Chart series for the inventory dashboard
# ---------------------------------------------------------------------------

def get_stock_by_branch(branches, limit: int = 8):
    """Units on hand per branch, biggest first. Only branches the caller sees."""
    rows = (
        branch_stock_queryset(branches)
        .values('branch_id', 'branch__name')
        .annotate(units=Coalesce(Sum('quantity'), 0))
        .order_by('-units', 'branch__name')[:limit]
    )
    return [{'label': r['branch__name'], 'value': r['units']} for r in rows]


def get_low_stock_by_branch(branches, threshold: int = DEFAULT_LOW_STOCK_THRESHOLD, limit: int = 8):
    """How many products sit below their minimum, per branch."""
    rows = (
        branch_stock_queryset(branches)
        .filter(low_stock_filter(threshold))
        .values('branch_id', 'branch__name')
        .annotate(products=Count('id'))
        .order_by('-products', 'branch__name')[:limit]
    )
    return [{'label': r['branch__name'], 'value': r['products']} for r in rows]


def get_movement_flow_trend(branches, days: int = 7):
    """
    Units in and units out per day for the last `days` days, oldest first.

    Days with no activity are rendered as zero rather than skipped: a gap in a
    trend line reads as missing data, not as a quiet day.
    """
    ids = _branch_ids(branches)
    today = timezone.localdate()
    start = today - timedelta(days=days - 1)

    entries: dict = {}
    exits: dict = {}
    if ids:
        rows = (
            StockMovement.objects
            .filter(branch_id__in=ids, created_at__date__gte=start, created_at__date__lte=today)
            .values('created_at__date', 'movement_type')
            .annotate(units=Coalesce(Sum('quantity'), 0))
        )
        for row in rows:
            bucket = entries if row['movement_type'] in StockMovement.ENTRY_TYPES else exits
            day = row['created_at__date']
            bucket[day] = bucket.get(day, 0) + row['units']

    series_in, series_out = [], []
    for offset in range(days):
        day = start + timedelta(days=offset)
        label = day.strftime('%d/%m')
        series_in.append({'label': label, 'value': entries.get(day, 0)})
        series_out.append({'label': label, 'value': exits.get(day, 0)})
    return {'entries': series_in, 'exits': series_out}


def get_movement_type_distribution(branches, days: int = 30, limit: int = 8):
    """How the last `days` of Kardex activity break down by movement type."""
    ids = _branch_ids(branches)
    if not ids:
        return []
    cutoff = timezone.now() - timedelta(days=days)
    labels = dict(StockMovement.MOVEMENT_TYPE_CHOICES)
    rows = (
        StockMovement.objects
        .filter(branch_id__in=ids, created_at__gte=cutoff)
        .values('movement_type')
        .annotate(units=Coalesce(Sum('quantity'), 0))
        .order_by('-units')[:limit]
    )
    return [
        {'label': labels.get(r['movement_type'], r['movement_type']), 'value': r['units']}
        for r in rows
    ]


def get_transfers_in_transit_count(branches) -> int:
    """
    Open transfers touching the caller's branches — sent OR expected.

    Counted from either end: a transfer leaving a branch the caller manages and
    one arriving at it are both work in progress they need to see.
    """
    ids = _branch_ids(branches)
    if not ids:
        return 0
    return (
        StockTransfer.objects
        .filter(status=StockTransfer.STATUS_IN_TRANSIT)
        .filter(Q(source_branch_id__in=ids) | Q(destination_branch_id__in=ids))
        .count()
    )


def get_pending_counts_count(branches) -> int:
    """Physical counts started and not yet approved or cancelled."""
    ids = _branch_ids(branches)
    if not ids:
        return 0
    return InventoryCount.objects.filter(
        branch_id__in=ids,
        status__in=[
            InventoryCount.STATUS_DRAFT,
            InventoryCount.STATUS_COUNTING,
            InventoryCount.STATUS_REVIEW,
        ],
    ).count()
