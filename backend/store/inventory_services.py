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
from django.db.models import Count, DecimalField, F, Max, Q, Sum
from django.db.models.functions import Coalesce, Least
from django.utils import timezone

from . import cost_services
from .models import (
    AdminAuditLog,
    Branch,
    BranchStock,
    InventoryCount,
    InventoryCountItem,
    Order,
    OrderItem,
    Product,
    StockMovement,
    StockTransfer,
    StockTransferItem,
    StockUnit,
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
    units=None,
    expected_unit_status: str | None = None,
    assign_units_for_sale: bool = False,
    sell_units: bool = False,
    unit_cost=None,
    cost_source: str | None = None,
) -> StockMovement:
    """
    Apply one stock movement to one branch atomically and return the Kardex row.

    SERIALIZED PRODUCTS MOVE BY UNIT, AND THIS IS WHERE THAT IS ENFORCED. For a
    product with `is_serialized`, the quantity is not the caller's to state: it
    has to arrive with the `StockUnit`s it is about (`units`, exactly
    `quantity` of them, of this branch and product) — or, for a sale, with
    `assign_units_for_sale`, in which case the oldest AVAILABLE units are taken
    here, under the shelf lock, and marked sold. A bare number is refused.
    Because every stock change in the system passes through this function,
    that one refusal is what stops a manual adjustment, an import, a transfer
    or a count from making the quantity disagree with the devices on the shelf.
    See `stock_unit_services`.

    MUST be called inside (or will open) a transaction. The BranchStock row is
    locked with select_for_update() so concurrent movements on the same
    (branch, product) serialise — this is what makes "never negative" true under
    load rather than merely on paper.

    The PRODUCT is deliberately not the lock: locking it would serialise every
    branch of a chain against every other for the same article, turning
    unrelated shops into each other's queue.

    DC1 — COST. `unit_cost` is the cost the caller KNOWS for an entry (a
    purchase, the origin of a transfer, with `cost_source` saying which). The
    line's cost and the branch's weighted average are decided by
    `cost_services.resolve` HERE, under the same lock as the quantity, so two
    entries at once cannot both read the old average. See `cost_services`.
    """
    if movement_type not in dict(StockMovement.MOVEMENT_TYPE_CHOICES):
        raise InvalidMovementError(f'Tipo de movimiento no válido: {movement_type}')
    try:
        unit_cost = cost_services.clean_cost(unit_cost)
    except ValueError as exc:
        raise InvalidMovementError(str(exc)) from None

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

        # Units are locked AFTER the shelf row — the module's one lock order.
        unit_ids = _units_for_movement(
            branch=branch, product=product, quantity=quantity, units=units,
            expected_status=expected_unit_status, assign_for_sale=assign_units_for_sale,
            order=order, sell=sell_units,
        )
        if unit_ids is not None:
            metadata = {**(metadata or {}), 'unit_ids': unit_ids}

        stock_before = stock.quantity
        is_entry = movement_type in StockMovement.ENTRY_TYPES
        delta = quantity if is_entry else -quantity
        stock_after = stock_before + delta

        if stock_after < 0:
            raise InsufficientStockError(
                f'Stock insuficiente para "{product.name}" en {branch.name}. '
                f'Stock actual: {stock_before}, salida solicitada: {quantity}.'
            )

        # Stock coming BACK from an order's sale, with no cost stated, comes
        # back at the cost its sale exit left with — whatever the entry's type
        # is called (integration review of PR #123, P2-2). Not a purchase, an
        # opening count or a transfer: those bring new stock, not the sale's.
        if (is_entry and unit_cost is None and order is not None and unit_ids is None
                and movement_type not in _NEW_STOCK_TYPES):
            left_with = _cost_the_sale_left_with(order, product)
            if left_with is not None:
                unit_cost, cost_source, exit_ids = left_with
                metadata = {**(metadata or {}), 'cost_from_movement_ids': exit_ids}

        cost = cost_services.resolve(
            is_entry=is_entry,
            carries_average_in=movement_type in StockMovement.RETURN_TYPES,
            quantity=quantity, stock_before=stock_before,
            average_before=stock.average_cost, reference_cost=product.reference_cost,
            uncosted_before=stock.uncosted_quantity,
            stated_cost=unit_cost if is_entry else None,
            stated_source=cost_source,
            unit_costs=(
                list(StockUnit.objects.filter(pk__in=unit_ids).values_list('cost', flat=True))
                if unit_ids is not None else None
            ),
        )

        # Write through the locked row — no read-modify-write race.
        BranchStock.objects.filter(pk=stock.pk).update(
            quantity=stock_after, average_cost=cost.average_after,
            uncosted_quantity=cost.uncosted_after,
        )
        # Keep the compatibility aggregate in the SAME transaction. An F()
        # expression, not a recomputed sum, so two branches moving the same
        # product concurrently add up instead of overwriting each other.
        Product.objects.filter(pk=product.pk).update(inventory=F('inventory') + delta)

        # That UPDATE holds the product row until this transaction ends, and
        # `stock_unit_services.set_serialized` takes the same row before it
        # changes how the product is counted. So from here the answer is
        # stable — and if it is not the one this movement was validated
        # against, the movement is undone: a number written to a product that
        # became serialized meanwhile would have no devices behind it.
        if Product.objects.filter(pk=product.pk).values_list('is_serialized', flat=True).get() \
                != product.is_serialized:
            raise InvalidMovementError(
                f'"{product.name}" cambió su forma de control mientras se registraba el '
                'movimiento. Vuelve a intentarlo.'
            )

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
            unit_cost=cost.unit_cost,
            cost_source=cost.source,
        )


#: Entries that bring NEW stock: never valued at an order's sale exit.
_NEW_STOCK_TYPES = frozenset([
    StockMovement.INITIAL_STOCK, StockMovement.PURCHASE_ENTRY, StockMovement.TRANSFER_IN,
])


def _cost_the_sale_left_with(order, product):
    """
    (unit cost, source, exit ids) the order's sale exits of `product` left
    with — quantity-weighted when there are several — or None when there is
    none or any of them left without a known cost. A source that is not a real
    cost (the catalogue's reference) stays said, so those units come back
    pending, as the transfer rule does.
    """
    exits = list(StockMovement.objects.filter(
        order=order, product=product, movement_type=StockMovement.SALE_EXIT,
    ).order_by('pk').values_list('pk', 'quantity', 'unit_cost', 'cost_source'))
    if not exits or any(cost is None for _pk, _qty, cost, _source in exits):
        return None
    total = sum(qty for _pk, qty, _cost, _source in exits)
    if total <= 0:
        return None
    mean = sum((Decimal(qty) * cost for _pk, qty, cost, _source in exits), Decimal('0')) / Decimal(total)
    sources = {source for *_rest, source in exits}
    if not sources <= cost_services.REAL_SOURCES:
        source = cost_services.REFERENCE
    else:
        source = sources.pop() if len(sources) == 1 else cost_services.AVERAGE
    return cost_services.quantize_cost(mean), source, [pk for pk, *_rest in exits]


def _units_for_movement(*, branch, product, quantity, units, expected_status,
                        assign_for_sale, order, sell=False):
    """
    The ids of the units a movement of a serialized product is about, locked.
    None for an ordinary product. Called with the `BranchStock` row locked.
    """
    from .models import StockUnit

    if not product.is_serialized:
        if units:
            raise InvalidMovementError(
                f'"{product.name}" no se controla por número de serie.'
            )
        return None

    if assign_for_sale:
        picked = list(
            StockUnit.objects.select_for_update()
            .filter(company_id=branch.company_id, branch=branch, product=product,
                    status=StockUnit.Status.AVAILABLE)
            .order_by('received_at', 'pk')[:quantity]
        )
        if len(picked) < quantity:
            raise InsufficientStockError(
                f'Stock insuficiente para "{product.name}" en {branch.name}. '
                f'Equipos disponibles: {len(picked)}, salida solicitada: {quantity}.'
            )
        ids = [unit.pk for unit in picked]
        StockUnit.objects.filter(pk__in=ids).update(
            status=StockUnit.Status.SOLD, sold_at=timezone.now(), order=order,
            updated_at=timezone.now(),
        )
        return ids

    wanted = [getattr(unit, 'pk', unit) for unit in (units or [])]
    if len(wanted) != quantity or len(set(wanted)) != quantity:
        raise InvalidMovementError(
            f'"{product.name}" se controla por número de serie: su stock no se mueve por '
            'cantidad. Usa Inventario › Equipos para registrar cada equipo.'
        )
    locked = list(
        StockUnit.objects.select_for_update()
        .filter(pk__in=wanted, company_id=branch.company_id, branch=branch, product=product)
    )
    if len(locked) != quantity:
        raise InvalidMovementError('Alguno de los equipos no pertenece a esta sucursal y producto.')
    if expected_status is not None:
        for unit in locked:
            if unit.status != expected_status:
                raise InvalidMovementError(
                    f'El equipo {unit.serial_number} está {unit.get_status_display().lower()}: '
                    'no admite esta operación.'
                )
    ids = sorted(unit.pk for unit in locked)
    if sell:
        # SERIAL-PICK-01: THESE devices, the ones somebody identified, leave in
        # this sale — not the oldest of the branch.
        StockUnit.objects.filter(pk__in=ids).update(
            status=StockUnit.Status.SOLD, sold_at=timezone.now(), order=order,
            updated_at=timezone.now(),
        )
    return ids


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
    order: Order, *, actor=None, strict: bool = False, identified=None,
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

    # DC1 — each line's share of the order's base and discount, frozen now:
    # it is the order's, not the shelf's, so a shortfall does not delay it.
    from . import margin_services

    margin_services.snapshot_nets(order)

    # DC3 — lines sold ON ORDER take nothing from any shelf: their payment
    # commits the supplier's offer (offer rows before shelf rows: the module's
    # lock order), and their goods enter and leave when received
    # (`supply_services.receive_line`). Never a movement here. Committed FIRST,
    # before the branch is even looked at: an order with no branch anywhere
    # still owes its supplier lines (DC3 review P3.7).
    from . import supply_services

    with transaction.atomic():
        supply_services.commit_order_lines(order, strict=strict)

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

        items = [
            item for item in order.items.select_related('product').all()
            if item.supply_origin != OrderItem.SUPPLY_SUPPLIER
        ]

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

        # DC2 — lines that name their device (the web sold it by unit). All
        # the devices of one product leave in ONE exit, so the idempotency key
        # (order, product) still means what it meant.
        chosen: dict[int, list] = {}
        for item in items:
            if item.stock_unit_id:
                chosen.setdefault(item.product_id, []).append(item)

        for item in items:
            if item.product_id in already_recorded:
                continue  # idempotency guard

            if item.product_id in chosen:
                if strict:
                    raise InvalidMovementError('Una venta de caja no lleva equipos elegidos en la web.')
                movement = _sell_chosen_units(order, branch, chosen[item.product_id], actor=actor)
                already_recorded.add(item.product_id)
                if movement is not None:
                    movements.append(movement)
                continue

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
                    # SERIAL-PICK-01. `identified` (product id -> unit ids) is
                    # what the till knows: the devices somebody identified
                    # leave, and no other. Without it — a WEB payment — the
                    # oldest units of the branch are SET ASIDE so the shelf
                    # count is right; nobody has identified them, and no paper
                    # names them until somebody does (`unit_identification`).
                    **(
                        {'units': list(identified[item.product_id]),
                         'expected_unit_status': 'available', 'sell_units': True}
                        if item.product.is_serialized and identified and item.product_id in identified
                        else {'assign_units_for_sale': item.product.is_serialized}
                    ),
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
                # DC1 — the cost this line left at, from its own Kardex line.
                margin_services.snapshot_cost(item, movement)

    return movements


def _sell_chosen_units(order: Order, branch, items, *, actor=None):
    """
    DC2 — the payment of a web order sells THE devices its lines name.

    Each device is, by now, either still held for this buyer (its hold steps
    aside, inside this transaction, under the shelf lock) or back on the
    shelf because the hold ran out before the gateway answered — then it is
    sold if nobody took it. A device sold or held by somebody else cannot be
    this order's: that line is flagged like any shortfall (the money is
    captured; a person decides), and the others still leave.

    Each line's cost is THAT device's cost, and the device is recorded as the
    one the buyer chose (`OrderUnit`, method 'buyer'): there is nothing left
    to identify when it is handed over.
    """
    from . import margin_services, unit_hold_services, unit_identification
    from .models import OrderUnit, StockUnit

    product = items[0].product
    _locked_branch_stocks(branch, [product])
    buyer_key = unit_hold_services.key_of(order.cart_session_key) if order.cart_session_key else None
    # Only a PAID order of the same cart takes a device from this payment: a
    # later order that may never be paid loses it to money in hand.
    superseded = unit_hold_services.superseded_lines(order, selling=True)
    sellable = []
    for item in items:
        if item.stock_unit_id in superseded:
            # The same cart checked out again and that order was paid: the device is its.
            _flag_stock_shortfall(
                order, None,
                f'El equipo elegido de "{product.name}" pasó al pedido #{superseded[item.stock_unit_id].pk} '
                'del mismo carrito; este cobro no lo vende.',
            )
            continue
        unit = (
            StockUnit.objects.select_for_update()
            .filter(pk=item.stock_unit_id, company_id=order.company_id, branch=branch, product_id=item.product_id)
            .first()
        )
        held_for_us = (
            unit is not None and unit.status == StockUnit.Status.RESERVED and unit.hold_key
            and (unit.hold_order_id == order.pk or (buyer_key and unit.hold_key == buyer_key))
        )
        if held_for_us:
            unit_hold_services.release_for_sale(unit, order)
        elif unit is None or unit.status != StockUnit.Status.AVAILABLE:
            _flag_stock_shortfall(
                order, item,
                f'El equipo elegido de "{product.name}" ya no estaba disponible en {branch.name}.',
            )
            continue
        sellable.append((item, unit))

    if not sellable:
        return None
    movement = create_stock_movement(
        branch=branch, product_id=product.pk, movement_type=StockMovement.SALE_EXIT,
        quantity=len(sellable), reason=f'Venta confirmada — orden #{order.pk}', actor=actor,
        order=order, reference_type='order', reference_id=order.pk,
        metadata={'order_id': order.pk, 'branch_id': branch.pk, 'chosen_by_buyer': True,
                  'unit_prices': [str(item.price) for item, _unit in sellable]},
        units=[unit for _item, unit in sellable], expected_unit_status='available', sell_units=True,
    )
    for item, unit in sellable:
        margin_services.snapshot_unit_cost(item, unit)
        if not OrderUnit.objects.filter(order=order, unit=unit).exists():
            unit_identification.record(
                order=order, unit=unit, method=OrderUnit.Method.BUYER.value, actor=actor,
            )
    return movement


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


# ---------------------------------------------------------------------------
# INV-04 — the oversell/shortfall a paid order leaves, DERIVED not stored
# ---------------------------------------------------------------------------
#
# When a payment is captured and the shelf cannot cover a line, the sale exit
# is not written (create_stock_movement refuses to go below zero) and the only
# trace is a free-text note on order.payment_error — no quantity, not queryable.
# The shortfall is fully derivable from facts the system already keeps:
#
#     ordered   = Σ OrderItem.quantity for the product in the order
#     fulfilled = Σ SALE_EXIT quantity LINKED TO THE ORDER for the product
#     shortfall = max(ordered − fulfilled, 0)
#
# It is NOT persisted: recomputing it from the Kardex means it HEALS on its own
# once stock is replenished and the exit re-runs, and it can never drift from
# the movements it is derived from. No new field, no migration.
#
# Exists(SALE_EXIT) is deliberately NOT used: a line can be partly covered (some
# units out, some not — e.g. two order lines of one product, where the second
# hits the idempotency guard), and an order can have one product covered and
# another short. Coverage is a QUANTITY comparison, never a boolean.
#
# Only meaningful once the exit was ATTEMPTED, which is at payment: for any
# status other than PAID this is empty (an unpaid order has simply not
# decremented stock yet; a refunded/cancelled one is no longer owed).


#: DC3 — on-order lines whose goods have not arrived: not a shelf shortfall.
_ON_ORDER_NOT_RECEIVED = Q(supply_origin=OrderItem.SUPPLY_SUPPLIER) & ~Q(supply_status=OrderItem.SUPPLY_RECEIVED)


def order_stock_shortfall(order: Order) -> list[dict]:
    """
    Per-product shortfall for one order, derived. Empty unless the order is PAID.

    Each row is {product_id, product_name, ordered, fulfilled, shortfall} for a
    product whose ordered quantity exceeds what actually left the shelf for this
    order; rows are ordered by product_id. See the module note above for why
    this is a quantity comparison and why nothing is stored.
    """
    if order.status != Order.Status.PAID:
        return []

    ordered: dict[int, int] = {}
    names: dict[int, str] = {}
    # DC3 — a line on order owes nothing to the shelf until its goods arrived.
    for item in order.items.select_related('product').exclude(_ON_ORDER_NOT_RECEIVED):
        ordered[item.product_id] = ordered.get(item.product_id, 0) + item.quantity
        names[item.product_id] = item.product.name

    fulfilled = {
        row['product_id']: row['q']
        for row in (
            StockMovement.objects
            .filter(order=order, movement_type=StockMovement.SALE_EXIT)
            .values('product_id')
            .annotate(q=Sum('quantity'))
        )
    }

    rows = []
    for product_id, needed in ordered.items():
        got = fulfilled.get(product_id, 0)
        short = needed - got
        if short > 0:
            rows.append({
                'product_id': product_id,
                'product_name': names[product_id],
                'ordered': needed,
                'fulfilled': got,
                'shortfall': short,
            })
    rows.sort(key=lambda r: r['product_id'])
    return rows


def order_ids_with_stock_shortfall(order_ids) -> set[int]:
    """
    The subset of `order_ids` with a positive derived shortfall on at least one
    product — two aggregate queries, independent of page size.

    The caller is responsible for scoping `order_ids` to what the user may see
    AND to status=PAID (shortfall is meaningless before payment). This function
    does not re-check status, so pass only paid orders.
    """
    order_ids = list(order_ids)
    if not order_ids:
        return set()

    ordered: dict[tuple[int, int], int] = {}
    for row in (
        OrderItem.objects.filter(order_id__in=order_ids).exclude(_ON_ORDER_NOT_RECEIVED)
        .values('order_id', 'product_id')
        .annotate(q=Sum('quantity'))
    ):
        ordered[(row['order_id'], row['product_id'])] = row['q']

    fulfilled: dict[tuple[int, int], int] = {}
    for row in (
        StockMovement.objects
        .filter(order_id__in=order_ids, movement_type=StockMovement.SALE_EXIT)
        .values('order_id', 'product_id')
        .annotate(q=Sum('quantity'))
    ):
        fulfilled[(row['order_id'], row['product_id'])] = row['q']

    short_ids: set[int] = set()
    for (order_id, product_id), needed in ordered.items():
        if needed - fulfilled.get((order_id, product_id), 0) > 0:
            short_ids.add(order_id)
    return short_ids


def reprocess_order_stock_exit(order: Order, *, actor=None, request=None) -> dict:
    """
    Re-run ONLY the still-missing SALE_EXITs of an already-paid order.

    The operator's half of INV-04: once the branch is replenished, this creates
    the exits that could not be made at payment time and clears the shortfall.

    It does NOT touch payment — no re-capture, no gateway call, no change to the
    amount, to `paid`, or to `status`; no new order; no manual movement; no
    stock from another branch; no edit to the order lines. It REUSES
    record_sale_stock_movements (idempotent per (order, product), taking stock
    only from the order's own fulfillment branch), so a product already
    dispatched is left untouched and nothing is ever double-subtracted.

    Concurrency-safe by the same lock the payment webhook uses: the order row is
    held for the whole operation, so two simultaneous retries serialise and the
    second sees the first's exits and creates none. If stock is still short the
    missing lines stay unfulfilled — flagged, not raised, exactly as at payment
    — and the caller reads what remains from order_stock_shortfall().

    Returns {'created_movements': [...], 'shortfall': [...]}.
    """
    if order.status != Order.Status.PAID:
        raise InventoryError(
            'Solo un pedido pagado puede reprocesar su salida de stock.'
        )

    with transaction.atomic():
        locked = Order.objects.select_for_update().get(pk=order.pk)
        # Re-check on the LOCKED row: the status is the authority for whether a
        # sale is still owed, and reading it before the lock is a TOCTOU. If a
        # (future) refund/cancel flow flipped it while we waited for the lock, we
        # must not create exits for an order no longer owed.
        if locked.status != Order.Status.PAID:
            raise InventoryError(
                'Solo un pedido pagado puede reprocesar su salida de stock.'
            )
        created = record_sale_stock_movements(locked, actor=actor)

    shortfall = order_stock_shortfall(locked)
    AdminAuditLog.log(
        actor=actor,
        action='order_stock_exit_reprocessed',
        target_type='order',
        target_id=locked.pk,
        company=locked.company,
        request=request,
        metadata={
            'order_id': locked.pk,
            'branch_id': locked.fulfillment_branch_id,
            'created_movements': len(created),
            'remaining_shortfall_products': len(shortfall),
        },
    )
    return {'created_movements': created, 'shortfall': shortfall}


def apply_initial_stock(
    *, branch, product, quantity: int, actor=None, reason: str = '', request=None, unit_cost=None,
) -> StockMovement | None:
    """
    Open a branch's balance for a product with an `initial_stock` Kardex line.

    Used when a product is created with an opening stock figure. The entry
    exists so that no unit ever appears in a branch without a movement
    explaining it — a balance with no first line is exactly the kind of
    unexplained stock a Kardex is supposed to make impossible.

    Returns None for a zero quantity: nothing happened, so nothing is recorded.

    `unit_cost` (COMMERCE-ADMIN-UX-01): what one unit cost, when the caller
    knows it — it opens the branch's weighted average (DC1). Without it the
    line is valued as before (reference cost, else «costo pendiente»).
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
        unit_cost=unit_cost,
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

    The transfer row is LOCKED and re-read before deciding (INV-LEGACY-V1-F2).
    The object the caller holds was loaded earlier and may predate a dispatch;
    judging "is it still a draft" on it let a line be written onto a document
    whose units had already left. `dispatch_transfer` takes the same lock, so
    the two cannot interleave.
    """
    if product.company_id != transfer.company_id:
        raise TransferError('El producto no pertenece a la empresa de esta transferencia.')
    if product.is_serialized:
        # Refused when the line is written, not when the van is loaded: a
        # transfer of devices has to say WHICH devices, and it cannot yet.
        raise TransferError(
            f'"{product.name}" se controla por número de serie y todavía no se transfiere '
            'entre sucursales desde aquí.'
        )

    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise TransferError('La cantidad debe ser un número entero.')
    if quantity < 0:
        raise TransferError('La cantidad no puede ser negativa.')

    with transaction.atomic():
        locked = StockTransfer.objects.select_for_update().get(pk=transfer.pk)
        if not locked.is_editable:
            raise TransferError(
                'Solo se pueden editar las líneas de una transferencia en borrador.'
            )

        if quantity == 0:
            StockTransferItem.objects.filter(transfer=locked, product=product).delete()
            return None

        item, _created = StockTransferItem.objects.update_or_create(
            transfer=locked, product=product, defaults={'quantity': quantity},
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

        # DC1 — the units arrive at the cost they LEFT with: the origin's
        # average when it was dispatched, not whatever it is now.
        departed = {
            row.product_id: (row.unit_cost, row.cost_source)
            for row in StockMovement.objects.filter(
                transfer=locked, movement_type=StockMovement.TRANSFER_OUT,
            )
        }

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
                unit_cost=departed.get(item.product_id, (None, None))[0],
                cost_source=departed.get(item.product_id, (None, None))[1] or None,
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
#
# MIGRATION-C (docs/adr-migration-count.md §3, rule D). Per accessory line:
#
#   * the snapshot is taken when the line is FIRST COUNTED, under the lock of its
#     BranchStock row: `theoretical_at_start` and `snapshot_movement_id` (the
#     highest Kardex id of the pair). That id is an exact watermark because every
#     movement of the pair is written with that same row locked;
#   * every later entry moves `last_entry_movement_id`; a movement of the pair
#     with an id in (snapshot, last_entry] happened WHILE the line was being
#     observed, so the count cannot say whether it includes it: the line is
#     AMBIGUOUS. A migration count is not approved until it is recounted;
#   * a movement after the last entry is fine: the delta of ERP-1 respects it —
#     except a CORRECTION from somewhere else (another count, a manual
#     adjustment, an initial or reconciling stock load), which would correct the
#     same discrepancy twice: it blocks the line;
#   * an ambiguous or overlapping line blocks the approval of EVERY kind of count.
#
# Annotating locks the COUNT first (then the stock row), exactly like approval
# does, so no annotation ever lands on a count that was approved meanwhile.

ENTRY_RECOUNT = 'recount'
ENTRY_CORRECTION = 'correction'
ENTRY_CHOICES = (ENTRY_RECOUNT, ENTRY_CORRECTION)

LINE_PENDING = 'pending'
LINE_COUNTED = 'counted'
LINE_AMBIGUOUS = 'ambiguous'
LINE_OVERLAPPING = 'overlapping'
LINE_EXCLUDED = 'excluded'

# What corrects a shelf instead of recording a physical event (ADR §3.2 rule 4):
# a correction (another count, a reconcile load), an INITIAL load — it states
# what is there, and passes its own check after a count because counting writes
# no Kardex — and a manual adjustment.
_OVERLAPPING_TYPES = frozenset([
    'correction_positive', 'correction_negative', 'initial_stock', 'manual_entry', 'manual_exit',
])
# Every movement a stock import writes, whatever its type
# (`stock_import_services.REFERENCE_TYPE`; a test keeps the two equal).
OVERLAPPING_REFERENCE_TYPES = frozenset(['bulk_import'])


class CountEntryRequired(InventoryCountError):
    """A line moved since its snapshot: the caller must say `recount` or `correction`."""

    code = 'entry_required'

    def __init__(self, message, products):
        super().__init__(message)
        self.products = list(products)


class CountApprovalBlocked(InventoryCountError):
    """Approval refused with one reason per line. Nothing was written."""

    code = 'approval_blocked'

    def __init__(self, message, problems):
        super().__init__(message)
        self.problems = list(problems)


def counters_may_write(count: InventoryCount) -> bool:
    """A migration count freezes its counters at `review`; an ordinary one does not."""
    if count.kind == InventoryCount.KIND_MIGRATION:
        return count.status in (InventoryCount.STATUS_DRAFT, InventoryCount.STATUS_COUNTING)
    return count.is_editable


def pair_watermark(branch, product_id) -> int:
    """The highest Kardex id of (branch, product). Read with the stock row locked."""
    return (
        StockMovement.objects.filter(branch=branch, product_id=product_id)
        .aggregate(top=Max('id'))['top'] or 0
    )


def clear_observation(item: InventoryCountItem) -> None:
    """
    Back to «not counted»: the next entry takes a new snapshot (§5, `recount`).
    `entry_note` stays: it is the reason of the last correction, and the audit
    log keeps every earlier one.
    """
    item.physical_quantity = None
    item.theoretical_at_start = None
    item.snapshot_movement_id = None
    item.last_entry_movement_id = None
    item.first_counted_at = None
    item.last_counted_at = None


def take_snapshot(item: InventoryCountItem, *, quantity: int, watermark: int, now) -> None:
    item.theoretical_at_start = quantity
    item.snapshot_movement_id = watermark
    item.last_entry_movement_id = watermark
    item.first_counted_at = now


def is_fresh(item: InventoryCountItem) -> bool:
    """No observation yet: the next quantity is the first and takes the snapshot."""
    return item.physical_quantity is None or item.theoretical_at_start is None


def pair_movements_since(branch, items) -> dict[int, list[tuple[int, str, int | None, str]]]:
    """
    `product_id → [(id, movement_type, inventory_count_id, reference_type)]` of the movements of
    each line's pair after its snapshot. One query for the whole count. Lines
    without a watermark (created before MIGRATION-C) contribute nothing.
    """
    snapshots = {
        i.product_id: i.snapshot_movement_id for i in items if i.snapshot_movement_id is not None
    }
    if not snapshots:
        return {}
    rows = (
        StockMovement.objects
        .filter(branch=branch, product_id__in=list(snapshots), id__gt=min(snapshots.values()))
        .order_by('id')
        .values_list('product_id', 'id', 'movement_type', 'inventory_count_id', 'reference_type')
    )
    found: dict[int, list] = {}
    for product_id, movement_id, movement_type, count_id, reference_type in rows:
        if movement_id > snapshots[product_id]:
            found.setdefault(product_id, []).append((movement_id, movement_type, count_id, reference_type))
    return found


def is_overlapping(count, movement) -> bool:
    """A movement that corrects the shelf from outside this count."""
    _id, movement_type, count_id, reference_type = movement
    if count_id == count.pk:
        return False
    return movement_type in _OVERLAPPING_TYPES or reference_type in OVERLAPPING_REFERENCE_TYPES


def line_review(count: InventoryCount, item: InventoryCountItem, since) -> tuple[str, str]:
    """(state, detail) of one line, from the movements of its pair after the snapshot."""
    if item.excluded:
        return LINE_EXCLUDED, item.exclusion_reason
    if item.physical_quantity is None:
        return LINE_PENDING, ''
    overlapping = [m for m in since if is_overlapping(count, m)]
    if overlapping:
        return LINE_OVERLAPPING, (
            f'Otra corrección de stock (movimiento #{overlapping[0][0]}) cayó sobre este producto '
            'después de contarlo: aplicar las dos corregiría dos veces la misma diferencia.'
        )
    last_entry = item.last_entry_movement_id or 0
    inside = [m for m in since if m[0] <= last_entry]
    if inside:
        return LINE_AMBIGUOUS, (
            f'Hubo {len(inside)} movimiento(s) de stock mientras se contaba: no se sabe si lo contado '
            'los incluye. Recuéntalo.'
        )
    return LINE_COUNTED, ''


def create_inventory_count(
    *, company, branch, actor=None, reason: str = '', kind: str = InventoryCount.KIND_REGULAR,
    source_job=None,
) -> InventoryCount:
    """Open a DRAFT physical count for one branch."""
    branch = _resolve_branch(branch)
    if branch.company_id != company.pk:
        raise InventoryCountError('La sucursal no pertenece a esta empresa.')
    if not branch.is_active:
        raise InventoryCountError('No se puede inventariar una sucursal inactiva.')
    if kind not in dict(InventoryCount.KIND_CHOICES):
        raise InventoryCountError('Tipo de recuento inválido.')

    try:
        with transaction.atomic():
            return InventoryCount.objects.create(
                company=company,
                branch=branch,
                kind=kind,
                source_job=source_job,
                status=InventoryCount.STATUS_DRAFT,
                reason=(reason or '').strip()[:MAX_REASON_LENGTH],
                created_by=actor,
            )
    except IntegrityError:
        raise InventoryCountError(
            'Ya hay un recuento de migración abierto en esta sucursal. Termínalo o anúlalo antes de abrir otro.'
        ) from None


def set_count_item(
    count: InventoryCount, *, product, physical_quantity=None, note: str = '',
    entry: str | None = None, entry_note: str = '', actor=None, request=None,
) -> InventoryCountItem:
    """
    Record what was physically found for one product — a TOTAL, which replaces
    whatever the line held.

    `physical_quantity=None` means «not counted», which is not zero and is never
    treated as such; on a counted line it puts the observation back to «not
    counted» (the next entry takes a new snapshot).

    The snapshot (`theoretical_at_start` + watermark) is taken by the line's
    FIRST quantity, not when the line is listed. A later entry on a line whose
    pair moved since the snapshot needs `entry`:
      * `recount`    — a new observation: new snapshot;
      * `correction` — the same observation, mistyped: the snapshot stays, and
                       `entry_note` says why (who: `counted_by`).
    A migration count refuses the entry without it (`CountEntryRequired`); an
    ordinary count accepts it, keeps the snapshot and the line reads as
    ambiguous — which blocks its approval until it is recounted.

    Every `correction` and every `recount` of a counted line leaves an
    `AdminAuditLog` row with the company (AUDIT-03).
    """
    if product.company_id != count.company_id:
        raise InventoryCountError('El producto no pertenece a la empresa de este recuento.')
    if product.is_serialized:
        raise InventoryCountError(
            f'"{product.name}" se controla por número de serie: se revisa equipo por equipo '
            'en Inventario › Equipos, no por cantidad.'
        )
    if physical_quantity is not None:
        try:
            physical_quantity = int(physical_quantity)
        except (TypeError, ValueError):
            raise InventoryCountError('La cantidad física debe ser un número entero.')
        if physical_quantity < 0:
            raise InventoryCountError('La cantidad física no puede ser negativa.')
    if entry is not None and entry not in ENTRY_CHOICES:
        raise InventoryCountError('Indica "recount" o "correction".')
    entry_note = (entry_note or '').strip()[:250]
    if entry == ENTRY_CORRECTION and not entry_note:
        raise InventoryCountError('Explica la corrección: queda registrada con tu nombre.')

    with transaction.atomic():
        locked = InventoryCount.objects.select_for_update().get(pk=count.pk)
        if not counters_may_write(locked):
            raise InventoryCountError('Este recuento ya no admite cambios.')

        item = (
            InventoryCountItem.objects.select_for_update()
            .filter(count=locked, product=product).first()
        ) or InventoryCountItem(count=locked, product=product)
        now = timezone.now()
        previous = item.physical_quantity
        previous_snapshot = item.snapshot_movement_id
        audit = None

        unchanged = (
            physical_quantity is not None and entry is None and not is_fresh(item)
            and physical_quantity == item.physical_quantity
        )
        if unchanged:
            # The same value re-sent (a screen saves every field it holds) is not
            # a new observation: the window does not move and nothing is asked.
            pass
        elif physical_quantity is None:
            clear_observation(item)
        else:
            stock = _locked_branch_stocks(locked.branch, [product])[product.pk]
            watermark = pair_watermark(locked.branch, product.pk)
            if entry == ENTRY_RECOUNT and not is_fresh(item):
                audit = 'inventory_count_line_recounted'
            if is_fresh(item) or entry == ENTRY_RECOUNT:
                take_snapshot(item, quantity=stock.quantity, watermark=watermark, now=now)
            elif item.snapshot_movement_id is None:
                pass  # a line counted before MIGRATION-C: no watermark to compare
            elif watermark > item.snapshot_movement_id:
                if entry == ENTRY_CORRECTION:
                    item.entry_note = entry_note  # same observation: snapshot and window stay
                    audit = 'inventory_count_line_corrected'
                elif locked.kind == InventoryCount.KIND_MIGRATION:
                    raise CountEntryRequired(
                        f'"{product.name}" tuvo movimientos de stock desde que se contó: '
                        'indica si es un reconteo o la corrección de un error de tecleo.',
                        [product.pk],
                    )
                else:
                    item.last_entry_movement_id = watermark
            elif entry == ENTRY_CORRECTION:
                item.entry_note = entry_note
                audit = 'inventory_count_line_corrected'
            item.physical_quantity = physical_quantity
            item.last_counted_at = now
            item.excluded = False  # counting it is the opposite of excluding it
            item.exclusion_reason = ''
            if actor is not None:
                item.counted_by = actor

        item.note = (note or '').strip()[:250]
        item.save()

        fields = []
        if locked.status == InventoryCount.STATUS_DRAFT:
            locked.status = InventoryCount.STATUS_COUNTING
            fields.append('status')
        if physical_quantity is not None and locked.counting_started_at is None:
            locked.counting_started_at = now
            fields.append('counting_started_at')
        if fields:
            locked.save(update_fields=[*fields, 'updated_at'])

    if audit:
        AdminAuditLog.log(
            actor=actor, action=audit, target_type='inventory_count', target_id=locked.pk,
            metadata={
                'inventory_count_id': locked.pk, 'branch_id': locked.branch_id,
                'product_id': product.pk, 'previous': previous,
                'physical_quantity': physical_quantity, 'entry_note': entry_note,
                'previous_snapshot_movement_id': previous_snapshot,
                'snapshot_movement_id': item.snapshot_movement_id,
            },
            request=request, company=locked.company,
        )
    count.refresh_from_db()
    return item


def stocked_products_missing(count) -> list:
    """
    Non-serialized products with stock in the count's branch that have no line.
    A migration count is complete only when there are none: a product that
    gained stock while the count was open must not escape it.
    """
    stocks = BranchStock.objects.filter(
        branch=count.branch, quantity__gt=0, product__is_serialized=False,
    ).exclude(product_id__in=count.items.values('product_id'))
    return [s.product for s in stocks.select_related('product').order_by('product_id')]


def add_missing_lines(count) -> list[InventoryCountItem]:
    """List, blank, every stocked product the migration count lacks. Commits on its own."""
    if count.kind != InventoryCount.KIND_MIGRATION:
        return []
    with transaction.atomic():
        locked = InventoryCount.objects.select_for_update().get(pk=count.pk)
        if locked.status not in InventoryCount.OPEN_STATUSES:
            return []
        return InventoryCountItem.objects.bulk_create(
            [InventoryCountItem(count=locked, product=p) for p in stocked_products_missing(locked)],
            ignore_conflicts=True,
        )


def _approval_problems(count, items, stocks, since) -> list[dict]:
    """One entry per line that keeps the count from being approved (all-or-nothing)."""
    problems = []
    migration = count.kind == InventoryCount.KIND_MIGRATION
    for item in items:
        if item.excluded:
            continue
        if item.physical_quantity is None:
            if migration:
                theoretical = stocks[item.product_id].quantity if item.product_id in stocks else 0
                if theoretical > 0 or (item.reference_quantity or 0) > 0:
                    problems.append({
                        'product': item.product_id, 'product_name': item.product.name,
                        'reason': LINE_PENDING,
                        'detail': 'Sin contar: cuéntalo o exclúyelo con un motivo.',
                    })
            continue
        state, detail = line_review(count, item, since.get(item.product_id, []))
        if state in (LINE_OVERLAPPING, LINE_AMBIGUOUS):
            problems.append({
                'product': item.product_id, 'product_name': item.product.name,
                'reason': state, 'detail': detail,
            })
    return problems


def approve_inventory_count(
    count: InventoryCount, *, actor=None, request=None,
) -> list[StockMovement]:
    """
    Apply the discrepancy each count DISCOVERED as a correction movement, under
    lock — through `create_stock_movement`, the one stock writer.

    THE CORRECTION IS A DELTA, NOT A REPLACEMENT (ERP-1 · INV-02). It is

        physical_quantity − theoretical_at_start

    applied on top of the current, locked stock. `theoretical_at_start` is what
    the system believed WHEN THE LINE WAS OBSERVED, so this difference is exactly
    the unrecorded discrepancy the counter found — theft, breakage, an earlier
    miscount. Applying it as a delta corrects that discrepancy while LEAVING
    every legitimate movement recorded after the observation — a sale, a
    receipt — standing.

    Using `physical_quantity − theoretical_at_approval` instead (the value
    re-read here under lock) telescopes to `current + (physical − current) =
    physical`: it overwrites the shelf to the count's photograph. A sale made
    while somebody walked the aisles would be silently un-sold and its units
    invented back; a receipt would be destroyed. `theoretical_at_approval` is
    still recorded as evidence of what the system said at approval, but the
    arithmetic must not use it.

    A product whose physical quantity was never entered is SKIPPED in an ordinary
    count. Treating "nobody counted this" as "there are none" would write off
    inventory nobody looked at. A MIGRATION count goes further: it is approved
    only from `review`, and only when every line with stock or a reference is
    counted or excluded with a reason and no line is ambiguous.

    For every kind, a correction from elsewhere on a counted pair after its
    snapshot blocks the approval (it would correct the same discrepancy twice).

    ALL OR NOTHING: a refusal writes nothing; a failure half-way rolls back.

    IDEMPOTENT: approving an already-approved count returns its existing
    correction movements.
    """
    # A migration count first lists, and commits, what gained stock while it was
    # open: the refusal below then names lines that exist and can be counted.
    add_missing_lines(count)
    with transaction.atomic():
        locked = InventoryCount.objects.select_for_update().get(pk=count.pk)

        if locked.status == InventoryCount.STATUS_APPROVED:
            return list(StockMovement.objects.filter(inventory_count=locked).order_by('id'))
        if locked.kind == InventoryCount.KIND_MIGRATION:
            if locked.status != InventoryCount.STATUS_REVIEW:
                raise InventoryCountError(
                    'Un recuento de migración se aprueba desde revisión: envíalo a revisión primero.'
                )
        elif locked.status not in InventoryCount.APPROVABLE_STATUSES:
            raise InventoryCountError(
                'Solo un recuento en conteo o en revisión puede aprobarse.'
            )

        lines = list(locked.items.select_related('product').order_by('product_id'))
        items = [i for i in lines if i.physical_quantity is not None and not i.excluded]
        if not items and not any(i.excluded for i in lines):
            raise InventoryCountError('El recuento no tiene productos contados.')

        # ONE sorted lock call for every stock row the approval reads: the lines
        # and, for a migration count, the stocked products it still lacks (they
        # gained stock after add_missing_lines). Locking those afterwards would
        # break the module's (branch, product) order against a multi-product sale.
        missing = (
            stocked_products_missing(locked) if locked.kind == InventoryCount.KIND_MIGRATION else []
        )
        stocks = _locked_branch_stocks(
            locked.branch, [i.product for i in lines if not i.excluded] + missing,
        )
        since = pair_movements_since(locked.branch, items)
        problems = _approval_problems(locked, lines, stocks, since)
        problems += [
            {'product': p.pk, 'product_name': p.name, 'reason': LINE_PENDING,
             'detail': 'Entró stock durante el conteo y no tiene línea: cuéntalo o exclúyelo.'}
            for p in missing if stocks[p.pk].quantity > 0
        ]
        if problems:
            raise CountApprovalBlocked(
                'El recuento no puede aprobarse todavía: revisa las líneas indicadas.', problems,
            )

        movements: list[StockMovement] = []
        for item in items:
            theoretical = stocks[item.product_id].quantity

            # ERP-1 · INV-02. The correction is the discrepancy the COUNT
            # discovered — physical minus what the system believed WHEN THE LINE
            # WAS OBSERVED — applied as a delta on the current (locked) stock. It
            # is not `physical − theoretical_at_approval`: that delta telescopes to
            #     current + (physical − current) = physical,
            # overwriting the shelf to the count's photograph and discarding
            # every legitimate movement recorded between counting and approving.
            difference = item.physical_quantity - item.theoretical_at_start

            item.theoretical_at_approval = theoretical
            item.difference = difference
            item.save(update_fields=[
                'theoretical_at_approval', 'difference', 'updated_at',
            ])

            if difference == 0:
                continue

            last_entry = item.last_entry_movement_id or 0
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
                    'inventory_count_kind': locked.kind,
                    'theoretical_at_start': item.theoretical_at_start,
                    'snapshot_movement_id': item.snapshot_movement_id,
                    'last_entry_movement_id': item.last_entry_movement_id,
                    'theoretical_at_approval': theoretical,
                    'physical_quantity': item.physical_quantity,
                    'difference': difference,
                    'after_entry_movement_ids': [
                        m[0] for m in since.get(item.product_id, []) if m[0] > last_entry
                    ],
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
            'kind': locked.kind,
            'branch_id': locked.branch_id,
            'counted_items': len(items),
            'excluded_items': sum(1 for i in lines if i.excluded),
            'adjusted_items': len(movements),
            'units_added': sum(m.quantity for m in movements if m.movement_type == StockMovement.CORRECTION_POSITIVE),
            'units_removed': sum(m.quantity for m in movements if m.movement_type == StockMovement.CORRECTION_NEGATIVE),
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
    include_cost: bool = False,
) -> dict:
    """
    Headline inventory counters for the branches the caller may see.

    `inventory_value` is stock × SALE PRICE, and it is labelled that way
    everywhere it is rendered. It is NOT the cost of the inventory.

    DC1 — `include_cost` (the caller holds `costs.view`) adds the value AT COST
    next to it: `inventory_cost_value` (see `_inventory_at_cost`) and the
    units that have no cost yet, counted apart rather than valued at zero.
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
        **_equipment_counts(company, branches),
        **(_inventory_at_cost(company, stock_rows, branches) if include_cost else {}),
    }


def _inventory_at_cost(company, stock_rows, branches) -> dict:
    """
    DC1 — what the shelf cost, by the owner's rule.

      · a product counted by quantity: its units × THEIR BRANCH's weighted
        average (`BranchStock.average_cost`);
      · a serialized product: the specific cost of each AVAILABLE unit — the
        units `BranchStock.quantity` counts (a reserved one has left it);
      · anything without a cost: counted in `inventory_cost_pending_units`,
        never valued at zero.
    """
    from .models import StockUnit

    money = DecimalField(max_digits=20, decimal_places=4)
    loose = stock_rows.filter(product__is_serialized=False, quantity__gt=0)
    # DC1 review: only the units of KNOWN cost are valued at the average; the
    # ones that came in without a cost are pending, whatever the average says.
    pending_part = Least(F('uncosted_quantity'), F('quantity'))
    by_quantity = loose.aggregate(
        value=Coalesce(Sum((F('quantity') - pending_part) * F('average_cost'),
                           filter=Q(average_cost__isnull=False), output_field=money),
                       Decimal('0'), output_field=money),
        pending=Coalesce(Sum('quantity', filter=Q(average_cost__isnull=True)), 0)
        + Coalesce(Sum(pending_part, filter=Q(average_cost__isnull=False)), 0),
    )
    devices = StockUnit.objects.filter(product__is_active=True, status=StockUnit.Status.AVAILABLE)
    if branches is not None:
        devices = devices.filter(branch_id__in=_branch_ids(branches))
    elif company is not None:
        devices = devices.filter(company=company)
    by_unit = devices.aggregate(
        value=Coalesce(Sum('cost', output_field=money), Decimal('0'), output_field=money),
        pending=Count('id', filter=Q(cost__isnull=True)),
    )
    value = Decimal(by_quantity['value']) + Decimal(by_unit['value'])
    return {
        'inventory_cost_value': str(value.quantize(Decimal('0.01'))),
        'inventory_cost_pending_units': by_quantity['pending'] + by_unit['pending'],
        'inventory_cost_basis': 'average_and_unit_cost',
    }


def _equipment_counts(company, branches) -> dict:
    """
    Serialized devices on the shelf, for the same scope as the rest.

    A SUBSET OF `total_units`, NEVER AN ADDITION TO IT. An available device is
    already one of the units counted above — that is the invariant of
    serialized stock — so these figures say how many of those units are
    devices tracked by serial, not how many more there are.
    """
    from .models import StockUnit

    rows = StockUnit.objects.filter(product__is_active=True)
    if branches is not None:
        rows = rows.filter(branch_id__in=_branch_ids(branches))
    elif company is not None:
        rows = rows.filter(company=company)
    counts = rows.aggregate(
        available=Count('id', filter=Q(status=StockUnit.Status.AVAILABLE)),
        reserved=Count('id', filter=Q(status=StockUnit.Status.RESERVED)),
    )
    return {
        'equipment_available': counts['available'],
        'equipment_reserved': counts['reserved'],
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
