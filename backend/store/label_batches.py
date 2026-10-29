"""
LABEL-A (LABEL-AUTO-01) — the label batch a confirmed physical entry leaves behind.
docs/adr-inventory-labels.md D8/D9.

WHEN IT RUNS. Never inside `inventory_services.create_stock_movement` (that single writer also serves
transfers, counts, returns and corrections, which must not label). Each ENTRY POINT calls this module
EXPLICITLY, right after its own movement. The allowed callers are pinned by
`test_label_batches.AllowedListTest`.

WHAT LABELS. Only a `purchase_entry` or an `initial_stock` movement (`AUTO_ENTRY_TYPES`). A
`manual_entry` does not: manual positive adjustments are often corrections or stock that was found,
not a reception, and a label for them would be noise (review of #194). Corrections, returns, transfers,
service returns and cancellations never label either.

  * devices: one item per newly received `StockUnit` (copies 1, with its unit);
  * accessories: copies = the RECEIVED quantity of the movement, never `BranchStock`. Above
    `MAX_COPIES` it is split in several items, never truncated. The item count is ceil(quantity / 500),
    so even an entry of 100 000 units is 200 small rows: no further cap is needed, and one would only
    reintroduce truncation;
  * idempotent: `source_key` is unique per company, so a retry returns the batch it already made.

TRANSACTION. The batch is made in a savepoint of its own, PREPARATION READS INCLUDED, so a database
error cannot leave the caller's transaction aborted. Whether it is in the SAME transaction as the
movement depends on the caller:
  * `registration_services.register`, `apply_units` and the product creation with opening stock run it
    inside the transaction of the event: a rolled-back reception has no batch;
  * `inventory_views`, `v1_inventory_views` and the "Registrar equipos" POST (`stock_unit_views`) call it
    AFTER the service has committed its own movement. A crash between that commit and the hook leaves a
    movement with no batch. The recovery path is LABEL-B's DERIVED list «pendientes sin lote» (entries
    of the allowed types with no batch), not a retry of the request: nothing is lost, it is only missing.

FAILURE. The goods are already on the shelf; refusing the reception because a label row could not be
written would lose the record of real stock. So every exception is caught and logged with its traceback
(`logger.exception`), and the caller gets `FAILED`, NOT `None`: `None` means «this movement does not
label», `FAILED` means «it should have and could not». Every response that carries the batch says so
with `label_error: true` and a summary with status `error`, so the screen can warn and the operator can
print by hand meanwhile. A programmer error is therefore a visible marker plus a log line; the tests
assert the batch exists for every allowed flow, so such an error cannot pass them.
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
#: The ONLY movement types of an entry that labels. A manual entry is NOT one (see the module docstring);
#: corrections, returns, transfers and the rest never label.
AUTO_ENTRY_TYPES = frozenset({StockMovement.PURCHASE_ENTRY, StockMovement.INITIAL_STOCK})


class _Failed:
    """«It should have labelled and could not». Falsy, with no pk, so `if batch:` skips it."""

    pk = None

    def __bool__(self):
        return False

    def __repr__(self):
        return 'label_batches.FAILED'


FAILED = _Failed()
_ERROR_SUMMARY = {
    'id': None, 'status': 'error', 'reason': LabelBatch.AUTO, 'branch_id': None, 'items': 0, 'labels_total': 0,
    'needs_review': 0,
}


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


def _make(*, company_id, branch_id, source_key, movement, actor, make_items):
    """
    get-or-create the batch of `source_key` in a savepoint; `make_items()` (reads included) runs inside
    it and only when the batch is new. FAILED, logged, on any failure; nothing half-made survives.
    """
    try:
        with transaction.atomic():
            batch, created = LabelBatch.objects.get_or_create(
                company_id=company_id, source_key=source_key,
                defaults={
                    'branch_id': branch_id, 'reason': LabelBatch.AUTO, 'movement': movement,
                    'created_by': actor if getattr(actor, 'pk', None) else None,
                },
            )
            if created:
                items = make_items()
                if not items:
                    raise ValueError('nothing to label')
                for item in items:
                    item.batch = batch
                LabelBatchItem.objects.bulk_create(items)
            return batch
    except Exception:  # noqa: BLE001 — see the module docstring
        logger.exception('No se pudo crear el lote de etiquetas %s.', source_key)
        return FAILED


def create_for_movement(movement, *, units=None, actor=None):
    """
    The batch of one confirmed entry. None: this movement does not label. FAILED: it should have and
    could not (logged). Otherwise the `LabelBatch`, new or the one a retry already made.

    `units` may be passed by the caller that just received them; they are re-read here, restricted to the
    movement's company and product. Without them, a serialized product's units come from the movement's
    own `unit_ids`.
    """
    if movement is None or movement.movement_type not in AUTO_ENTRY_TYPES:
        return None

    def make_items():
        product = movement.product
        if not product.is_serialized:
            return _quantity_items(product, movement.quantity)
        pks = [u.pk for u in units] if units is not None else (movement.metadata or {}).get('unit_ids') or []
        return _unit_items(_fresh_units(movement.company_id, pks, product.pk))

    return _make(company_id=movement.company_id, branch_id=movement.branch_id, source_key=movement_key(movement.pk),
                 movement=movement, actor=actor, make_items=make_items)


def create_for_import_job(job, units, *, actor=None):
    """
    One batch per branch of an applied units job (`auto:label:job:<job>:branch:<branch>`), as a list that
    may hold FAILED. The caller applies a job once (its APPLIED state), and the key makes a repeat
    harmless anyway.
    """
    try:
        with transaction.atomic():
            by_branch = {}
            for unit in _fresh_units(job.company_id, [u.pk for u in units]):
                by_branch.setdefault(unit.branch_id, []).append(unit)
    except Exception:  # noqa: BLE001
        logger.exception('No se pudieron preparar las etiquetas del trabajo %s.', job.pk)
        return [FAILED]
    return [
        _make(company_id=job.company_id, branch_id=branch_id, source_key=job_key(job.pk, branch_id),
              movement=None, actor=actor, make_items=lambda group=by_branch[branch_id]: _unit_items(group))
        for branch_id in sorted(by_branch)
    ]


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
    """The summaries of these batches, of THIS company only, in id order. A failing read is the error marker."""
    try:
        return [summary(b) for b in LabelBatch.objects.filter(company=company, pk__in=list(ids)).order_by('pk')]
    except Exception:  # noqa: BLE001 — the entry is already committed: say the labels failed, never 500
        logger.exception('No se pudieron leer los lotes de etiquetas.')
        return [dict(_ERROR_SUMMARY)]


def summaries_for_job(company, job_pk) -> list[dict]:
    try:
        rows = LabelBatch.objects.filter(company=company, source_key__startswith=f'auto:label:job:{job_pk}:')
        return [summary(b) for b in rows.order_by('pk')]
    except Exception:  # noqa: BLE001
        logger.exception('No se pudieron leer los lotes de etiquetas del trabajo %s.', job_pk)
        return [dict(_ERROR_SUMMARY)]


def has_error(summaries_) -> bool:
    """Whether any of these summaries is the error marker (a batch that failed, or could not be read)."""
    return any(item.get('status') == 'error' for item in summaries_)


def summary_or_none(batch):
    """None → None (does not label); FAILED → the error summary; a batch → its counts."""
    if batch is None:
        return None
    if batch is FAILED:
        return dict(_ERROR_SUMMARY)
    return summary(batch)


def answer(batch) -> dict:
    """
    What a single-batch response adds: the summary and whether the labelling failed. The batch is made AFTER the
    entry's commit, so reading it back must not raise: a failed read answers the error marker, the entry stands.
    """
    try:
        return {'label_batch': summary_or_none(batch), 'label_error': batch is FAILED}
    except Exception:  # noqa: BLE001
        logger.exception('No se pudo leer el lote de etiquetas.')
        return {'label_batch': dict(_ERROR_SUMMARY), 'label_error': True}


def failures(count: int) -> list[dict]:
    """The error entries of a list response (a registration or a job that lost `count` batches)."""
    return [dict(_ERROR_SUMMARY) for _ in range(int(count or 0))]
