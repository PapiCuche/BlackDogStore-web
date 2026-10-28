"""
LABEL-A (LABEL-AUTO-01) — the label batch a confirmed physical entry leaves behind.
docs/adr-inventory-labels.md D8/D9.

WHEN IT RUNS. Never inside `inventory_services.create_stock_movement` (that single writer also serves
transfers, counts, returns and corrections, which must not label). Each ENTRY POINT calls this module
EXPLICITLY, right after its own movement and inside the same transaction. The allowed callers are
pinned by `test_label_batches.AllowedListTest`.

  * devices: one item per newly received `StockUnit` (copies 1, with its unit);
  * accessories: copies = the RECEIVED quantity of the movement, never `BranchStock`; above
    `MAX_COPIES` it is split in several items, never truncated;
  * idempotent: `source_key` is unique per company, so a retry returns the batch it already made;
  * a savepoint of its own: a rolled-back reception has no batch, and a failure to label does not undo
    a confirmed reception.

WHY EVERY EXCEPTION IS CAUGHT HERE (and logged with its traceback). The goods are already on the
shelf; refusing the reception because a label row could not be written would lose the record of real
stock, and the operator has the manual label screen meanwhile. The cost is that a programmer error
shows up as a log line and a missing batch instead of a 500: the tests above assert the batch exists
for every allowed flow, so such an error cannot pass them. A failure is never silent: `logger.exception`.
"""
import logging

from django.db import transaction
from django.db.models import Count, Q, Sum

from .models import LabelBatch, LabelBatchItem, StockMovement, StockUnit

logger = logging.getLogger(__name__)

#: One item never asks for more copies than the label screen allows per product.
MAX_COPIES = 500
#: A device in any other state is not on the shelf to be labelled: flagged for review, not dropped.
LABELLABLE_UNIT_STATUSES = (StockUnit.Status.AVAILABLE, StockUnit.Status.RESERVED)
#: The ONLY movement types of an entry that labels. Corrections, returns, transfers and the rest never do.
AUTO_ENTRY_TYPES = frozenset({
    StockMovement.PURCHASE_ENTRY, StockMovement.INITIAL_STOCK, StockMovement.MANUAL_ENTRY,
})


def movement_key(movement_pk) -> str:
    return f'auto:label:movement:{movement_pk}'


def job_key(job_pk, branch_pk) -> str:
    return f'auto:label:job:{job_pk}:branch:{branch_pk}'


def _quantity_items(product, quantity):
    items, left = [], int(quantity)
    while left > 0:
        copies = min(left, MAX_COPIES)
        items.append(LabelBatchItem(product=product, copies=copies))
        left -= copies
    return items


def _unit_items(units):
    items = []
    for unit in sorted(units, key=lambda u: u.pk):
        reason = ''
        if unit.status not in LABELLABLE_UNIT_STATUSES:
            reason = 'unit_not_labellable'
        elif not unit.label_code:
            reason = 'unit_without_code'
        items.append(LabelBatchItem(unit=unit, product_id=unit.product_id, copies=1,
                                    needs_review=bool(reason), review_reason=reason))
    return items


def _fresh_units(company_id, pks, product_id=None):
    """The units as they are NOW, and only those of this company (and product): never trust the caller."""
    rows = StockUnit.objects.filter(company_id=company_id, pk__in=list(pks))
    if product_id is not None:
        rows = rows.filter(product_id=product_id)
    return list(rows)


def _make(*, company_id, branch, source_key, movement, actor, items):
    """get-or-create the batch of `source_key` in a savepoint; its items only when it is new. None on failure."""
    try:
        with transaction.atomic():
            batch, created = LabelBatch.objects.get_or_create(
                company_id=company_id, source_key=source_key,
                defaults={
                    'branch': branch, 'reason': LabelBatch.AUTO, 'movement': movement,
                    'created_by': actor if getattr(actor, 'pk', None) else None,
                },
            )
            if created:
                for item in items:
                    item.batch = batch
                LabelBatchItem.objects.bulk_create(items)
            return batch
    except Exception:  # noqa: BLE001 — see the module docstring
        logger.exception('No se pudo crear el lote de etiquetas %s.', source_key)
        return None


def create_for_movement(movement, *, units=None, actor=None):
    """
    The batch of one confirmed entry (or None: not an entry that labels, nothing to label, or a failure).

    `units` may be passed by the caller that just received them; they are re-read here, restricted to the
    movement's company and product. Without them, a serialized product's units come from the movement's
    own `unit_ids`.
    """
    if movement is None or movement.movement_type not in AUTO_ENTRY_TYPES:
        return None
    try:
        product = movement.product
        if product.is_serialized:
            pks = [u.pk for u in units] if units is not None else (movement.metadata or {}).get('unit_ids') or []
            found = _fresh_units(movement.company_id, pks, product.pk)
            if not found:
                logger.error('El movimiento %s es de un producto con serie y no trae equipos.', movement.pk)
                return None
            items = _unit_items(found)
        else:
            items = _quantity_items(product, movement.quantity)
    except Exception:  # noqa: BLE001
        logger.exception('No se pudieron preparar las etiquetas del movimiento %s.', movement.pk)
        return None
    return _make(company_id=movement.company_id, branch=movement.branch, source_key=movement_key(movement.pk),
                 movement=movement, actor=actor, items=items)


def create_for_import_job(job, units, *, actor=None):
    """
    One batch per branch of an applied units job (`auto:label:job:<job>:branch:<branch>`). The caller
    applies a job once (its APPLIED state), and the key makes a repeat harmless anyway.
    """
    by_branch = {}
    for unit in _fresh_units(job.company_id, [u.pk for u in units]):
        by_branch.setdefault(unit.branch_id, []).append(unit)
    made = []
    for branch_id in sorted(by_branch):
        group = by_branch[branch_id]
        batch = _make(company_id=job.company_id, branch=group[0].branch, source_key=job_key(job.pk, branch_id),
                      movement=None, actor=actor, items=_unit_items(group))
        if batch is not None:
            made.append(batch)
    return made


# ---------------------------------------------------------------------------
# What the receiving endpoints answer
# ---------------------------------------------------------------------------

def summary(batch) -> dict:
    """Counts only: no code, serial or IMEI rides here."""
    totals = batch.items.aggregate(
        items=Count('pk'), labels=Sum('copies'), review=Count('pk', filter=Q(needs_review=True)))
    return {
        'id': batch.pk, 'status': batch.status, 'reason': batch.reason, 'branch_id': batch.branch_id,
        'items': totals['items'], 'labels_total': totals['labels'] or 0, 'needs_review': totals['review'],
    }


def summaries(company, ids) -> list[dict]:
    """The summaries of these batches, of THIS company only, in id order."""
    return [summary(b) for b in LabelBatch.objects.filter(company=company, pk__in=list(ids)).order_by('pk')]


def summaries_for_job(company, job_pk) -> list[dict]:
    rows = LabelBatch.objects.filter(company=company, source_key__startswith=f'auto:label:job:{job_pk}:')
    return [summary(b) for b in rows.order_by('pk')]


def summary_or_none(batch):
    return summary(batch) if batch is not None else None
