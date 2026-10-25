"""
MIGRATION-C — the accessory count engine around `InventoryCount`
(docs/adr-migration-count.md §3, §5, §6, §8).

There is NO second count engine and NO second stock writer here. The line rules
(snapshot on observing, watermark, entry choice) live in
`inventory_services.set_count_item`; the application is
`inventory_services.approve_inventory_count` → `create_stock_movement`. This
module adds the verbs a scanner needs and the read model the screen shows:

* `create_migration_count` — one per branch, lines listed up front WITHOUT a
  snapshot and with the old quantity as a reference only;
* `scan` — resolve a code (or a product) and either find the line (`lookup`) or
  add one under the line's lock (`increment`), refusing a stale `expected`;
* `recount`, `exclude`, `submit_for_review`;
* `search_products` — the count's own search, by name or code;
* `describe_lines` / `line_reviews` — what each line reads as, in bulk.

Nothing in this module moves stock.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from . import inventory_services as inv
from .models import (
    AdminAuditLog,
    BranchStock,
    BulkImportJob,
    InventoryCount,
    InventoryCountItem,
    Order,
    OrderItem,
    Product,
    ProductBarcode,
    StockTransfer,
    StockTransferItem,
    normalize_barcode,
)
from .unit_identification import UnitIdentificationError, find_unit

MODE_LOOKUP = 'lookup'
MODE_INCREMENT = 'increment'
MAX_STEP = 1000
SEARCH_LIMIT = 20

# Paid on the web, not yet out of the shop: the sale already took the stock.
_AWAITING_DISPATCH = (
    Order.FulfillmentStatus.PENDING,
    Order.FulfillmentStatus.CONFIRMED,
    Order.FulfillmentStatus.PREPARING,
    Order.FulfillmentStatus.READY_FOR_PICKUP,
)


class CountCodeNotFound(inv.InventoryCountError):
    code = 'not_found'


class CountSerializedProduct(inv.InventoryCountError):
    code = 'serialized_product'

    def __init__(self, message, product=None):
        super().__init__(message)
        self.product = product


class CountExpectedMismatch(inv.InventoryCountError):
    code = 'expected_mismatch'

    def __init__(self, message, item):
        super().__init__(message)
        self.item = item


class CountRecountRequired(inv.InventoryCountError):
    code = 'recount_required'

    def __init__(self, message, item):
        super().__init__(message)
        self.item = item


# ---------------------------------------------------------------------------
# Opening a migration count
# ---------------------------------------------------------------------------

def _reference_quantities(job, branch) -> dict[int, int]:
    """
    `product_id → old quantity in this branch`, from MIGRATION-B's rows.

    The contract (ADR §8): `normalized_data.product_id` and
    `normalized_data.reference_quantities = [{branch_id, quantity}]`. Anything
    else — a missing product, another branch, a non-integer — is ignored: the
    worst a broken row can do is lose a reference, never move stock.
    """
    found = {}
    for data in job.rows.values_list('normalized_data', flat=True):
        if not isinstance(data, dict):
            continue
        product_id = data.get('product_id')
        if not isinstance(product_id, int):
            continue
        for entry in data.get('reference_quantities') or []:
            if not isinstance(entry, dict) or entry.get('branch_id') != branch.pk:
                continue
            quantity = entry.get('quantity')
            if isinstance(quantity, int) and not isinstance(quantity, bool) and quantity >= 0:
                found[product_id] = quantity
    return found


def create_migration_count(*, company, branch, actor=None, reason: str = '', source_job=None) -> InventoryCount:
    """
    Open the migration count of one branch and list its pending lines.

    Lines: every non-serialized product of the company with stock in the branch
    or a reference from the import. Each is created WITHOUT a snapshot and
    WITHOUT a quantity — blank, «not counted» — and with `reference_quantity`
    for the screen to show. Devices are not listed: they are counted by unit.
    """
    references = {}
    if source_job is not None:
        if source_job.company_id != company.pk:
            raise inv.InventoryCountError('Importación no encontrada.')
        if source_job.import_type != BulkImportJob.PRODUCTS or source_job.status != BulkImportJob.APPLIED:
            raise inv.InventoryCountError(
                'El recuento de migración se abre sobre una importación de productos ya aplicada: '
                'nunca se cuenta contra un catálogo sin reconciliar.'
            )
        references = _reference_quantities(source_job, branch)

    with transaction.atomic():
        count = inv.create_inventory_count(
            company=company, branch=branch, actor=actor, reason=reason,
            kind=InventoryCount.KIND_MIGRATION, source_job=source_job,
        )
        stocked = set(
            BranchStock.objects.filter(branch=count.branch, quantity__gt=0).values_list('product_id', flat=True)
        )
        products = Product.objects.filter(company=company, is_serialized=False).filter(
            Q(pk__in=stocked) | Q(pk__in=list(references))
        )
        InventoryCountItem.objects.bulk_create([
            InventoryCountItem(count=count, product=product, reference_quantity=references.get(product.pk))
            for product in products.order_by('pk')
        ])
    return count


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

def resolve_product(count: InventoryCount, *, code=None, product=None) -> Product:
    """
    The accessory a scan names, in THIS count's company.

    A code is matched exactly as stored (`normalize_barcode`: only the outer
    whitespace a scanner adds is trimmed — never the case, never the zeros). A
    code that is no product's barcode but is a device's IMEI or serial is
    answered as a device; anything else is «not found», the same answer another
    company's code gets.
    """
    if product is not None:
        found = Product.objects.filter(company=count.company, pk=product).first()
        if found is None:
            raise CountCodeNotFound('Producto no encontrado.')
    else:
        text = normalize_barcode(str(code or ''))
        if not text:
            raise CountCodeNotFound('Escanea o escribe un código.')
        barcode = (
            ProductBarcode.objects.filter(company=count.company, code=text).select_related('product').first()
        )
        if barcode is None:
            try:
                unit = find_unit(count.company, text)
            except UnitIdentificationError as exc:
                if exc.code == 'ambiguous':
                    raise CountSerializedProduct(
                        'Ese código es de un equipo con serie: se cuenta equipo por equipo, no aquí.'
                    ) from None
                raise CountCodeNotFound(f'Código no encontrado: {text}.') from None
            raise CountSerializedProduct(
                f'"{unit.product.name}" es un equipo con serie: se cuenta equipo por equipo '
                '(IMEI o serie), no en el recuento de accesorios.',
                product=unit.product,
            )
        found = barcode.product
    if found.is_serialized:
        raise CountSerializedProduct(
            f'"{found.name}" es un equipo con serie: se cuenta equipo por equipo '
            '(IMEI o serie), no en el recuento de accesorios.',
            product=found,
        )
    return found


def _pending_line(count, product) -> InventoryCountItem:
    """The line of `product`, created blank (no snapshot, no quantity) if missing."""
    item, _created = InventoryCountItem.objects.get_or_create(count=count, product=product)
    return item


def scan(count: InventoryCount, *, mode: str, code=None, product=None, expected=None,
         step: int = 1, actor=None) -> tuple[str, InventoryCountItem]:
    """
    `lookup` → `('found', line)`, writing no quantity.
    `increment` → `('incremented', line)`: `step` added under the line's lock.

    `expected` is the value the screen SHOWED (None = «not counted»). If the line
    holds anything else, nothing is added (`CountExpectedMismatch`, with the line
    as it is): a retried request or a second scanner never adds twice.

    An increment on a line whose pair moved since its snapshot is refused
    (`CountRecountRequired`) and the line is left AMBIGUOUS: the units scanned
    before the movement and the ones after it are not one observation.
    """
    if mode not in (MODE_LOOKUP, MODE_INCREMENT):
        raise inv.InventoryCountError('Modo de lectura inválido.')
    target = resolve_product(count, code=code, product=product)

    if mode == MODE_LOOKUP:
        with transaction.atomic():
            locked = InventoryCount.objects.select_for_update().get(pk=count.pk)
            if not inv.counters_may_write(locked):
                raise inv.InventoryCountError('Este recuento ya no admite cambios.')
            item = _pending_line(locked, target)
        return 'found', item

    try:
        step = int(step)
    except (TypeError, ValueError):
        raise inv.InventoryCountError('El paso debe ser un número entero.') from None
    if not 1 <= step <= MAX_STEP:
        raise inv.InventoryCountError(f'El paso va de 1 a {MAX_STEP}.')
    if expected is not None:
        try:
            expected = int(expected)
        except (TypeError, ValueError):
            raise inv.InventoryCountError('"expected" debe ser un número entero o null.') from None

    refusal = None
    with transaction.atomic():
        locked = InventoryCount.objects.select_for_update().get(pk=count.pk)
        if not inv.counters_may_write(locked):
            raise inv.InventoryCountError('Este recuento ya no admite cambios.')
        item = (
            InventoryCountItem.objects.select_for_update()
            .filter(count=locked, product=target).first()
        ) or _pending_line(locked, target)
        now = timezone.now()

        if item.physical_quantity != expected:
            refusal = CountExpectedMismatch(
                f'"{target.name}" ya no va en {expected if expected is not None else "sin contar"}: '
                'no se sumó. Vuelve a escanear si esa unidad no estaba contada.',
                item,
            )
        else:
            stock = inv._locked_branch_stocks(locked.branch, [target])[target.pk]
            watermark = inv.pair_watermark(locked.branch, target.pk)
            if inv.is_fresh(item):
                inv.take_snapshot(item, quantity=stock.quantity, watermark=watermark, now=now)
                item.physical_quantity = step
            elif item.snapshot_movement_id is not None and watermark > item.snapshot_movement_id:
                # Saved, not rolled back: the line must read as ambiguous.
                item.last_entry_movement_id = max(item.last_entry_movement_id or 0, watermark)
                refusal = CountRecountRequired(
                    f'"{target.name}" tuvo movimientos de stock mientras se escaneaba: '
                    'recuéntalo desde cero.',
                    item,
                )
            else:
                item.physical_quantity += step
                item.last_entry_movement_id = watermark
            if refusal is None:
                item.last_counted_at = now
                item.excluded = False
                item.exclusion_reason = ''
                if actor is not None:
                    item.counted_by = actor
            item.save()
            if refusal is None:
                fields = []
                if locked.status == InventoryCount.STATUS_DRAFT:
                    locked.status = InventoryCount.STATUS_COUNTING
                    fields.append('status')
                if locked.counting_started_at is None:
                    locked.counting_started_at = now
                    fields.append('counting_started_at')
                if fields:
                    locked.save(update_fields=[*fields, 'updated_at'])
    if refusal is not None:
        raise refusal
    return 'incremented', item


# ---------------------------------------------------------------------------
# Recount, exclusion, review
# ---------------------------------------------------------------------------

def _lines(locked, products) -> list[InventoryCountItem]:
    ids = sorted({int(p) for p in products})
    if not ids:
        raise inv.InventoryCountError('Indica al menos un producto.')
    found = list(
        InventoryCountItem.objects.select_for_update()
        .filter(count=locked, product_id__in=ids).select_related('product').order_by('product_id')
    )
    if len(found) != len(ids):
        raise CountCodeNotFound('Ese producto no está en este recuento.')
    return found


def _may_touch(locked, *, approver: bool) -> None:
    """Counters write while counting; in `review` only an approver sends lines back."""
    if locked.status == InventoryCount.STATUS_REVIEW and locked.kind == InventoryCount.KIND_MIGRATION:
        if not approver:
            raise PermissionError('En revisión sólo quien aprueba puede cambiar líneas.')
        return
    if not locked.is_editable:
        raise inv.InventoryCountError('Este recuento ya no admite cambios.')


def recount(count, *, products, actor=None, approver=False, request=None) -> InventoryCount:
    """Those lines go back to «not counted»; their next entry takes a new snapshot."""
    with transaction.atomic():
        locked = InventoryCount.objects.select_for_update().get(pk=count.pk)
        _may_touch(locked, approver=approver)
        items = _lines(locked, products)
        for item in items:
            inv.clear_observation(item)
            item.save()
        if locked.status == InventoryCount.STATUS_REVIEW and locked.kind == InventoryCount.KIND_MIGRATION:
            locked.status = InventoryCount.STATUS_COUNTING
            locked.save(update_fields=['status', 'updated_at'])
    AdminAuditLog.log(
        actor=actor, action='inventory_count_recount', target_type='inventory_count',
        target_id=locked.pk,
        metadata={'inventory_count_id': locked.pk, 'branch_id': locked.branch_id,
                  'products': [i.product_id for i in items]},
        request=request, company=locked.company,
    )
    return locked


def exclude(count, *, products, reason, actor=None, approver=False, request=None) -> InventoryCount:
    """Leave lines out of the approval, with a reason. Nothing is applied for them."""
    reason = (reason or '').strip()[:250]
    if not reason:
        raise inv.InventoryCountError('Indica el motivo de la exclusión.')
    with transaction.atomic():
        locked = InventoryCount.objects.select_for_update().get(pk=count.pk)
        _may_touch(locked, approver=approver)
        items = _lines(locked, products)
        for item in items:
            item.excluded = True
            item.exclusion_reason = reason
            item.save(update_fields=['excluded', 'exclusion_reason', 'updated_at'])
    AdminAuditLog.log(
        actor=actor, action='inventory_count_lines_excluded', target_type='inventory_count',
        target_id=locked.pk,
        metadata={'inventory_count_id': locked.pk, 'branch_id': locked.branch_id,
                  'products': [i.product_id for i in items], 'reason': reason},
        request=request, company=locked.company,
    )
    return locked


def submit_for_review(count, *, actor=None, request=None) -> InventoryCount:
    """`counting` → `review`. Counters stop writing (migration). No stock moves."""
    with transaction.atomic():
        locked = InventoryCount.objects.select_for_update().get(pk=count.pk)
        if locked.status == InventoryCount.STATUS_REVIEW:
            return locked
        if locked.status != InventoryCount.STATUS_COUNTING:
            raise inv.InventoryCountError('Sólo un recuento en conteo se envía a revisión.')
        locked.status = InventoryCount.STATUS_REVIEW
        locked.submitted_at = timezone.now()
        locked.submitted_by = actor
        locked.save(update_fields=['status', 'submitted_at', 'submitted_by', 'updated_at'])
    AdminAuditLog.log(
        actor=actor, action='inventory_count_submitted', target_type='inventory_count',
        target_id=locked.pk,
        metadata={'inventory_count_id': locked.pk, 'branch_id': locked.branch_id, 'kind': locked.kind},
        request=request, company=locked.company,
    )
    return locked


# ---------------------------------------------------------------------------
# Read model
# ---------------------------------------------------------------------------

def line_reviews(count, items) -> dict[int, tuple[str, str]]:
    """`product_id → (state, detail)` for every line, in one query."""
    since = inv.pair_movements_since(count.branch, items)
    return {i.product_id: inv.line_review(count, i, since.get(i.product_id, [])) for i in items}


def _codes(company, product_ids) -> dict[int, list[str]]:
    codes: dict[int, list[str]] = {}
    for product_id, code in (
        ProductBarcode.objects.filter(company=company, product_id__in=product_ids)
        .order_by('-is_primary', 'code').values_list('product_id', 'code')
    ):
        codes.setdefault(product_id, []).append(code)
    return codes


def _sums(queryset, field='quantity') -> dict[int, int]:
    return {row['product_id']: row['total'] or 0 for row in queryset.values('product_id').annotate(total=Sum(field))}


def describe_lines(count, items, *, costs: bool) -> list[dict]:
    """
    Every line as the screen reads it. Bulk: a fixed number of queries per count.

    `difference_value` is present ONLY when `costs` (the caller has
    `costs.view`): absent means «you may not see it», never zero.
    """
    from .serializers import InventoryCountItemSerializer

    items = list(items)
    product_ids = [i.product_id for i in items]
    codes = _codes(count.company, product_ids)
    stocks = {
        s.product_id: s for s in BranchStock.objects.filter(branch=count.branch, product_id__in=product_ids)
    }
    reviews = line_reviews(count, items)
    transit = _sums(StockTransferItem.objects.filter(
        transfer__destination_branch=count.branch, transfer__status=StockTransfer.STATUS_IN_TRANSIT,
        product_id__in=product_ids,
    ))
    awaiting = _sums(OrderItem.objects.filter(
        order__company=count.company, order__fulfillment_branch=count.branch,
        order__status=Order.Status.PAID, order__fulfillment_status__in=_AWAITING_DISPATCH,
        product_id__in=product_ids,
    ))

    rows = []
    for item, data in zip(items, InventoryCountItemSerializer(items, many=True).data):
        stock = stocks.get(item.product_id)
        own = codes.get(item.product_id, [])
        state, detail = reviews[item.product_id]
        expected = (
            item.physical_quantity - item.theoretical_at_start
            if item.physical_quantity is not None and item.theoretical_at_start is not None else None
        )
        row = {
            **data,
            'product_code': own[0] if own else None,
            'product_codes': own,
            'current_theoretical': stock.quantity if stock else 0,
            'expected_difference': expected,
            'review_state': state,
            'review_detail': detail,
            'in_transit_quantity': transit.get(item.product_id, 0),
            'paid_pending_dispatch': awaiting.get(item.product_id, 0),
        }
        if costs:
            unit_cost = (stock.average_cost if stock and stock.average_cost is not None
                         else item.product.reference_cost)
            difference = item.difference if item.difference is not None else expected
            row['difference_value'] = (
                None if unit_cost is None or difference is None
                else str((Decimal(difference) * unit_cost).quantize(Decimal('0.01')))
            )
        rows.append(row)
    return rows


def search_products(count, term: str) -> list[dict]:
    """The company's products by name, slug or code prefix — the count's own search."""
    term = (term or '').strip()[:64]
    if not term or '\x00' in term:
        return []
    products = list(
        Product.objects.filter(company=count.company)
        .filter(Q(name__icontains=term) | Q(slug__icontains=term) | Q(barcodes__code__istartswith=term))
        .distinct().order_by('name', 'pk')[:SEARCH_LIMIT]
    )
    codes = _codes(count.company, [p.pk for p in products])
    lines = {
        i.product_id: i for i in InventoryCountItem.objects.filter(
            count=count, product_id__in=[p.pk for p in products],
        )
    }
    return [
        {
            'id': p.pk,
            'name': p.name,
            'is_serialized': p.is_serialized,
            'is_active': p.is_active,
            'product_code': (codes.get(p.pk) or [None])[0],
            'product_codes': codes.get(p.pk, []),
            'physical_quantity': lines[p.pk].physical_quantity if p.pk in lines else None,
            'in_count': p.pk in lines,
        }
        for p in products
    ]
