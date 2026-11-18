"""
SERIAL-TRANSFER-01 — the devices of a transfer, read one by one (docs/adr-serial-transfer.md §4).

    add_unit      DRAFT: a code (IMEI, serial or internal code) names a device AVAILABLE at the ORIGIN. The device
                  stays on sale (ST-D1): the dispatch checks it again under the shelf lock.
    remove_unit   DRAFT: it leaves the document; the line goes when its last device goes.
    verify_unit   IN TRANSIT: the destination reads each device again (ST-D2). `receive_transfer` refuses until all are.

Dispatch and receipt stay in `inventory_services` (the single writer and the transfer document live there); this
module only says WHICH devices. Lock order, as everywhere: the transfer row, then (dispatch / receive) the shelves,
then the devices. Nothing here takes a shelf lock: nothing here moves stock.

The audit names ids and the method, NEVER an IMEI or a serial.
"""
from __future__ import annotations

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from . import unit_identification
from .inventory_services import TransferError
from .models import AdminAuditLog, StockTransfer, StockTransferItem, StockTransferUnit, StockUnit

#: ST-D8 — devices per transfer (the precedent of a reception: `stock_unit_services.MAX_BATCH`).
MAX_UNITS = 200


def _method(value) -> str:
    try:
        return unit_identification.clean_method(value)
    except unit_identification.UnitIdentificationError as exc:
        raise TransferError(str(exc)) from None


def _identify(company, code, method):
    try:
        unit = unit_identification.find_unit(company, code)
        unit_identification._typed_by_hand_is_an_imei(unit, code, method)
    except unit_identification.UnitIdentificationError as exc:
        raise TransferError(str(exc)) from None
    return unit


def _locked(transfer) -> StockTransfer:
    return StockTransfer.objects.select_for_update().get(pk=transfer.pk)


def _audit(action, locked, unit, method, actor, request):
    AdminAuditLog.log(
        actor=actor, action=action, target_type='stock_transfer', target_id=locked.pk,
        metadata={'transfer_id': locked.pk, 'unit_id': unit.pk, 'product_id': unit.product_id, 'method': method},
        request=request, company=locked.company,
    )


@transaction.atomic
def add_unit(transfer, *, code, method, actor=None, request=None) -> StockTransferUnit:
    method = _method(method)
    locked = _locked(transfer)
    if locked.status != StockTransfer.STATUS_DRAFT:
        raise TransferError('Sólo se agregan equipos a una transferencia en borrador.')
    unit = _identify(locked.company, code, method)
    if unit.branch_id != locked.source_branch_id:
        # DEC-SERIAL-02: never where it is.
        raise TransferError('Ese equipo no está en esta sucursal.')
    if StockTransferUnit.objects.filter(transfer=locked, unit=unit).exists():
        raise TransferError('Ese equipo ya está en esta transferencia.')
    if unit.status != StockUnit.Status.AVAILABLE:
        raise TransferError(f'Ese equipo está {unit.get_status_display().lower()}: no se puede transferir.')
    if StockTransferUnit.objects.filter(transfer=locked).count() >= MAX_UNITS:
        raise TransferError(f'Una transferencia lleva hasta {MAX_UNITS} equipos.')

    item = StockTransferItem.objects.filter(transfer=locked, product_id=unit.product_id).first()
    if item is None:
        item = StockTransferItem.objects.create(transfer=locked, product_id=unit.product_id, quantity=1)
    else:
        StockTransferItem.objects.filter(pk=item.pk).update(quantity=F('quantity') + 1)
    try:
        with transaction.atomic():
            row = StockTransferUnit.objects.create(
                company_id=locked.company_id, transfer=locked, item=item, unit=unit, method=method, added_by=actor,
            )
    except IntegrityError:
        raise TransferError('Ese equipo ya está en esta transferencia.') from None
    _audit('stock_transfer_unit_added', locked, unit, method, actor, request)
    return row


@transaction.atomic
def remove_unit(transfer, *, unit_id, actor=None, request=None) -> None:
    locked = _locked(transfer)
    if locked.status != StockTransfer.STATUS_DRAFT:
        raise TransferError('Sólo se quitan equipos de una transferencia en borrador.')
    row = StockTransferUnit.objects.filter(transfer=locked, unit_id=unit_id).select_related('unit').first()
    if row is None:
        raise TransferError('Ese equipo no está en esta transferencia.')
    item_id = row.item_id
    row.delete()
    remaining = StockTransferUnit.objects.filter(item_id=item_id).count()
    if remaining:
        StockTransferItem.objects.filter(pk=item_id).update(quantity=remaining)
    else:
        StockTransferItem.objects.filter(pk=item_id).delete()
    _audit('stock_transfer_unit_removed', locked, row.unit, row.method, actor, request)


@transaction.atomic
def verify_unit(transfer, *, code, method, actor=None, request=None) -> StockTransferUnit:
    """ST-D2 — the destination reads a device of this transfer. Twice is still once."""
    method = _method(method)
    locked = _locked(transfer)
    if locked.status != StockTransfer.STATUS_IN_TRANSIT:
        raise TransferError('Sólo se verifican los equipos de una transferencia en tránsito.')
    try:
        unit = unit_identification.find_unit(locked.company, code)
        unit_identification._typed_by_hand_is_an_imei(unit, code, method)
    except unit_identification.UnitIdentificationError as exc:
        raise TransferError(str(exc)) from None
    row = StockTransferUnit.objects.filter(transfer=locked, unit=unit).first()
    if row is None:
        raise TransferError('Ese equipo no viaja en esta transferencia: sepáralo y revisa el despacho.')
    if row.received_at is None:
        row.received_method = method
        row.received_by = actor if getattr(actor, 'pk', None) else None
        row.received_at = timezone.now()
        row.save(update_fields=['received_method', 'received_by', 'received_at'])
        _audit('stock_transfer_unit_verified', locked, unit, method, actor, request)
    return row


def units_payload(transfer) -> dict:
    """item id -> the devices of that line, WHOLE identifiers (staff screen of inventory), and whether each was read."""
    out: dict[int, list] = {}
    rows = (StockTransferUnit.objects.filter(transfer_id=transfer.pk)
            .select_related('unit').order_by('pk'))
    for row in rows:
        unit = row.unit
        out.setdefault(row.item_id, []).append({
            'id': unit.pk, 'identifier': unit.identifier, 'serial_number': unit.serial_number or '',
            'imei': unit.imei or '', 'label_code': unit.label_code or '', 'status': unit.status,
            'method': row.method, 'received_at': row.received_at.isoformat() if row.received_at else None,
        })
    return out
