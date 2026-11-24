"""
INV-LOAD-02 — what can be done with a device load AFTER it was registered.

    1. ANNUL IT WHOLE (`void_job`). Every device the load created leaves through `stock_unit_services.void_unit`, the
       same «Anular ingreso» as one device on screen: a CORRECTION_NEGATIVE line in the Kardex (nothing is deleted),
       the row kept as VOIDED with its identifiers, which no longer occupy anything. ALL OR NOTHING: while a single
       device of the load has done anything since it entered — sold, reserved, held by a cart, transferred, moved to
       another variant, written off — nothing is annulled and the answer names each one. Idempotent: a second call
       answers what the first did.
    2. SAY WHAT IT DID (`report_rows`). Per branch × product: the shelf before (as the preview simulated it), what
       entered, what the preview expected after, what the shelf held right after the registration, and today.

Only loads applied since INV-LOAD-02 know exactly which devices they created (`summary.applied.unit_ids`); an older
one is refused rather than guessed from identifiers.

LOCK ORDER: the job, then every shelf of its devices (`receipt_services.lock_shelves`, one `(branch, product)` order),
then the devices by id — the order of `apply_units` (job → shelves → identifiers) and of `void_unit` (shelf → device).
"""
from __future__ import annotations

import csv
import io

from django.db import transaction
from django.utils import timezone

from . import stock_unit_services
from .models import AdminAuditLog, Branch, BulkImportJob, Product, StockUnit

BATCH_REASON_CODE = 'bad_import'
NOT_APPLIED = 'job_not_applied'
NOT_VOIDABLE = 'batch_not_voidable'
UNKNOWN_UNITS = 'batch_unknown_units'


class BatchError(Exception):
    """Why a load cannot be annulled (or reported). `blocking`: the devices that stop an annulment."""

    def __init__(self, detail, *, code='', status=400, field='', blocking=None):
        super().__init__(detail)
        self.code, self.status, self.field, self.blocking = code, status, field, list(blocking or [])


def _units_job(job):
    if job.import_type != BulkImportJob.UNITS:
        raise BatchError('Esta carga no es de equipos.', code=NOT_APPLIED, status=409)
    if job.status != BulkImportJob.APPLIED:
        raise BatchError('Esta carga todavía no se registró: no hay nada que anular ni informar.',
                         code=NOT_APPLIED, status=409)


def _blocking(unit, why: str) -> dict:
    """A device that stops the annulment: how a person finds it (its label code), never its serial or IMEI."""
    return {'unit_id': unit.pk, 'label_code': unit.label_code or '', 'product_id': unit.product_id,
            'branch_id': unit.branch_id, 'status': unit.status, 'reason': why}


CONFIRM_USED = 'confirm_used_ready'


def reason_room(job) -> int:
    """How long the reason of an annulment may be: each device's Kardex line reads «Anulación de ingreso (…): Carga
    #N: <motivo>» and the whole of it fits the line."""
    kardex = f'Anulación de ingreso ({stock_unit_services.VOID_REASONS[BATCH_REASON_CODE]}): '
    return stock_unit_services.MAX_REASON - len(kardex) - len(f'Carga #{job.pk}: ')


def _unit_ids(job) -> list:
    unit_ids = ((job.summary or {}).get('applied') or {}).get('unit_ids')
    if unit_ids is None:
        raise BatchError(
            'Esta carga se registró antes de que el sistema guardara la lista de sus equipos: no se adivina cuáles '
            'eran. Anúlalos uno por uno desde Inventario › Equipos («Anular ingreso»).',
            code=UNKNOWN_UNITS, status=409,
        )
    return unit_ids


def _assess(devices) -> tuple[list, list]:
    """(blocking, to_void) — the same verdict for the preview and the annulment."""
    blocking, to_void = [], []
    for unit in devices:
        if unit.status == StockUnit.Status.VOIDED:
            continue                    # annulled on its own already: nothing left to do for it
        if unit.status != StockUnit.Status.AVAILABLE:
            blocking.append(_blocking(unit, f'Está {unit.get_status_display().lower()}.'))
        elif stock_unit_services.entry_movement_if_clean(unit) is None:
            blocking.append(_blocking(unit, 'Tiene movimientos después de su ingreso.'))
        elif stock_unit_services.in_draft_transfer(unit):
            blocking.append(_blocking(unit, 'Está en una transferencia en borrador: quítalo de ella primero.'))
        else:
            to_void.append(unit)
    return blocking, to_void


def _prepared(units) -> dict:
    """INV-LOAD-02 review — how many of `units` somebody already worked on: inspected, published, photographed."""
    from .models import UnitPhoto

    ids = [unit.pk for unit in units]
    return {
        'inspected': sum(unit.inspection_status != StockUnit.Inspection.PENDING for unit in units),
        'published': sum(bool(unit.published_online) for unit in units),
        'with_photos': UnitPhoto.objects.filter(unit_id__in=ids).values('unit_id').distinct().count() if ids else 0,
    }


def void_preview(job) -> dict:
    """
    What annulling the load would do NOW, without writing anything: how many devices, which ones stop it, and how many
    were already inspected, published or photographed (that work is lost with the annulment, so it is confirmed).
    """
    _units_job(job)
    summary = job.summary or {}
    base = {'voided': summary.get('voided'), 'reason_max': reason_room(job)}
    if summary.get('voided'):
        return {**base, 'units': 0, 'blocking': [], 'inspected': 0, 'published': 0, 'with_photos': 0}
    devices = list(StockUnit.objects.filter(company=job.company, pk__in=_unit_ids(job)).order_by('pk'))
    blocking, to_void = _assess(devices)
    return {**base, 'units': len(to_void), 'blocking': blocking, **_prepared(to_void)}


@transaction.atomic
def void_job(job, *, reason, actor=None, request=None, confirm_used=False):
    """
    Annul every device an applied units load created. Returns `(job, changed)`; a second call is `(job, False)`.
    Raises `BatchError` (409 `batch_not_voidable` with `blocking`) and writes nothing when any device moved, and 409
    `confirm_used_ready` when a device was already inspected, published or photographed and `confirm_used` is not set.
    """
    job = BulkImportJob.objects.select_for_update().get(pk=job.pk)
    _units_job(job)
    summary = job.summary or {}
    if summary.get('voided'):
        return job, False
    try:
        text = stock_unit_services.clean_reason(reason)
    except stock_unit_services.StockUnitError:
        raise BatchError('Escribe el motivo de la anulación de la carga.', field='reason') from None
    room = reason_room(job)
    if len(text) > room:
        raise BatchError(f'El motivo de la anulación admite hasta {room} caracteres.', field='reason')
    line_reason = f'Carga #{job.pk}: {text}'
    unit_ids = _unit_ids(job)

    from .receipt_services import lock_shelves

    pairs = set(StockUnit.objects.filter(company=job.company, pk__in=unit_ids).values_list('branch_id', 'product_id'))
    branches = {b.pk: b for b in Branch.objects.filter(company=job.company, pk__in={b for b, _ in pairs})}
    products = {p.pk: p for p in Product.objects.filter(company=job.company, pk__in={p for _, p in pairs})}
    lock_shelves((branches[b], products[p]) for b, p in sorted(pairs))
    devices = list(StockUnit.objects.select_for_update().filter(company=job.company, pk__in=unit_ids).order_by('pk'))

    blocking, to_void = _assess(devices)
    if blocking:
        raise BatchError(
            f'No se anuló ningún equipo: {len(blocking)} equipo(s) de esta carga ya se movieron (venta, apartado, '
            f'traslado, cambio de variante o baja). Resuélvelos uno por uno o anula los demás desde su ficha.',
            code=NOT_VOIDABLE, status=409, blocking=blocking,
        )
    prepared = _prepared(to_void)
    if any(prepared.values()) and not confirm_used:
        raise BatchError(
            f'No se anuló ningún equipo: {prepared["inspected"]} ya se inspeccionaron, {prepared["published"]} están '
            f'publicados y {prepared["with_photos"]} tienen fotos. Confírmalo para anular igual.',
            code=CONFIRM_USED, status=409,
        )

    # Products in id order, then shelves, then devices: each line updates its product row (`create_stock_movement`).
    to_void.sort(key=lambda unit: (unit.product_id, unit.branch_id, unit.pk))
    for unit in to_void:
        try:
            stock_unit_services.void_unit(unit, reason=line_reason, reason_code=BATCH_REASON_CODE, actor=actor,
                                          request=request)
        except stock_unit_services.StockUnitError as exc:
            # Rolled back whole by the transaction: nothing of the batch stays annulled.
            raise BatchError(f'No se anuló ningún equipo. {exc}', code=NOT_VOIDABLE, status=409,
                             blocking=[_blocking(unit, str(exc))]) from None

    job.summary = {**summary, 'voided': {
        'units': len(to_void), 'at': timezone.now().isoformat(), 'by': getattr(actor, 'username', '') or '',
        'reason': text}}
    job.save(update_fields=['summary'])
    AdminAuditLog.log(
        actor=actor, action='stock_units_import_voided', target_type='bulk_import_job', target_id=job.pk,
        # How many and where; the free reason may carry an identifier and stays on the job, never in the trail.
        metadata={'company_id': job.company_id, 'job_id': job.pk, 'units': len(to_void),
                  'branch_ids': sorted({unit.branch_id for unit in to_void}), 'reason_code': BATCH_REASON_CODE,
                  **{key: value for key, value in prepared.items() if value}},
        request=request, company=job.company,
    )
    return job, True


# ---------------------------------------------------------------------------
# Before / after
# ---------------------------------------------------------------------------

REPORT_HEADERS = [
    'Sucursal', 'Producto', 'Equipos antes', 'Stock antes', 'Entran',
    'Equipos después (previsto)', 'Stock después (previsto)',
    'Equipos después (al registrar)', 'Stock después (al registrar)', 'Cuadra',
    'Equipos disponibles hoy', 'Stock hoy',
]


def report_rows(job) -> list[list]:
    """
    One row per branch × product the load touched: the preview's simulation (before, entering, expected after), the
    shelf right after the registration (`summary.applied.shelves`; empty for a load applied before INV-LOAD-02) and
    the shelf today. «Cuadra»: what the registration left is what the preview said.
    """
    from .unit_import_services import shelves_now

    _units_job(job)
    summary = job.summary or {}
    simulated = (summary.get('simulation') or {}).get('shelves') or []
    applied = {(s['branch_id'], s['product_id']): s for s in (summary.get('applied') or {}).get('shelves') or []}
    pairs = {(s['branch_id'], s['product_id']) for s in simulated}
    today = shelves_now(job.company, pairs)
    branches = dict(Branch.objects.filter(company=job.company, pk__in={b for b, _ in pairs}).values_list('pk', 'name'))
    products = dict(Product.objects.filter(company=job.company, pk__in={p for _, p in pairs}).values_list('pk', 'name'))

    rows = []
    for shelf in simulated:
        pair = (shelf['branch_id'], shelf['product_id'])
        after = applied.get(pair)
        matches = '' if after is None else (
            'Sí' if (after['units'], after['stock']) == (shelf['units_after'], shelf['stock_after']) else 'No')
        rows.append([
            branches.get(pair[0], shelf.get('branch', '')), products.get(pair[1], shelf.get('product', '')),
            shelf['units_before'], shelf['stock_before'], shelf['adds'],
            shelf['units_after'], shelf['stock_after'],
            None if after is None else after['units'], None if after is None else after['stock'], matches,
            today[pair]['units'], today[pair]['stock'],
        ])
    rows.sort(key=lambda row: (str(row[0]), str(row[1])))
    return rows


def report_csv(job) -> str:
    """UTF-8 with a BOM (Excel on Windows), every text guarded: a product name is a tenant's text."""
    from .xlsx_reader import guard_text

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(REPORT_HEADERS)
    for row in report_rows(job):
        writer.writerow([guard_text(value) if isinstance(value, str) else ('' if value is None else value)
                         for value in row])
    return '﻿' + buffer.getvalue()


def report_xlsx(job) -> bytes:
    from .inventory_exports import _workbook_bytes

    return _workbook_bytes('Antes y después', REPORT_HEADERS, report_rows(job),
                           widths=(22, 40, 14, 12, 10, 16, 16, 18, 18, 10, 16, 12))


def report_filename(job, extension: str) -> str:
    return f'carga-{job.pk}-antes-despues.{extension}'
