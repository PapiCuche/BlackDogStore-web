"""
INV-DRAFT-01 — the unfinished «Recibir mercadería» of one person, kept on the server (`ReceiptDraft`).

The wizard keeps what is typed in the memory of the page; a reload used to lose 200 scanned serials. The draft is
the same content saved by the screen (debounced), tied to (company, user) and, when chosen, a branch among the
caller's grants. It is an opaque note for the screen that wrote it: the server checks its SIZE, its shape as far
as the caps of a reception go, and its COSTS (only from and to whoever holds `costs.view`). It never reads a
product, a serial or an IMEI out of it: the preview and the confirmation check everything again.

Nothing here writes stock, a Kardex line, a unit, a label or an audit line (an autosave every few seconds is not
an event worth a trail). The draft expires `RECEIPT_DRAFT_TTL_SECONDS` after its last save.
"""
from __future__ import annotations

import json
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import receipt_services as receipts
from .models import ReceiptDraft

MAX_BYTES = 256 * 1024
# The pasted list waiting to be resolved (`receive-list.ts` MAX_STAGED).
MAX_STAGED = 600
# What the server adds to the answer: a payload cannot spell them.
SERVER_KEYS = ('branch', 'updated_at', 'expires_at')


def ttl() -> timedelta:
    return timedelta(seconds=settings.RECEIPT_DRAFT_TTL_SECONDS)


def _rows(value, field: str, limit: int) -> list:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > limit:
        raise receipts.ReceiptError(f'El borrador admite hasta {limit} elementos en «{field}».', field=field)
    if not all(isinstance(row, dict) for row in value):
        raise receipts.ReceiptError('Borrador no válido.', field=field)
    return value


def _has_amount(value) -> bool:
    return value not in (None, '') and not (isinstance(value, str) and not value.strip())


def _cost_fields(payload: dict):
    """Every (holder, key) of the payload that carries a cost, as the screen names them (`DraftLine.unitCost`,
    `DraftUnit.cost`, `StagedRow.cost`)."""
    for line in payload.get('lines') or []:
        yield line, 'unitCost'
        for unit in line.get('units') or []:
            if isinstance(unit, dict):
                yield unit, 'cost'
    for row in payload.get('staged') or []:
        yield row, 'cost'


def validate(payload, *, may_view_costs: bool) -> dict:
    """The payload to store, or a `ReceiptError`. Touches no database."""
    if not isinstance(payload, dict):
        raise receipts.ReceiptError('El borrador debe ser un objeto JSON.', field='payload')
    try:
        size = len(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    except (TypeError, ValueError, RecursionError):
        raise receipts.ReceiptError('Borrador no válido.', field='payload') from None
    if size > MAX_BYTES:
        raise receipts.ReceiptError(
            'El borrador es demasiado grande para guardarse. Divide la mercadería en varias recepciones.',
            code='too_large', field='payload')
    if payload.get('origin') is not None and not isinstance(payload['origin'], dict):
        raise receipts.ReceiptError('Borrador no válido.', field='origin')
    lines = _rows(payload.get('lines'), 'lines', receipts.MAX_LINES)
    devices = 0
    for index, line in enumerate(lines):
        units = _rows(line.get('units'), f'lines[{index}].units', receipts.MAX_DEVICES)
        devices += len(units)
        if devices > receipts.MAX_DEVICES:
            raise receipts.ReceiptError(f'Se reciben hasta {receipts.MAX_DEVICES} equipos por vez.',
                                        field=f'lines[{index}].units')
    _rows(payload.get('staged'), 'staged', MAX_STAGED)
    if not may_view_costs and any(_has_amount(holder.get(key)) for holder, key in _cost_fields(payload)):
        # The receipt's own rule (DEC-INV-05): a cost needs `costs.view`, a missing permission is a 403.
        raise receipts.ReceiptError(receipts.COSTS_REQUIRED, status=403, code=receipts.cost_services.COSTS_FORBIDDEN,
                                    field='payload')
    return {key: value for key, value in payload.items() if key not in SERVER_KEYS}


def without_costs(payload: dict) -> dict:
    """A copy with every cost blanked: for a reader who no longer holds `costs.view`."""
    copy = json.loads(json.dumps(payload))
    for holder, key in _cost_fields(copy):
        if key in holder:
            holder[key] = ''
    return copy


def upsert(*, company, user, branch, payload: dict) -> ReceiptDraft:
    """Upsert the one draft of (company, user); the expiry starts again."""
    with transaction.atomic():
        draft, _ = ReceiptDraft.objects.select_for_update().update_or_create(
            company=company, user=user,
            defaults={'branch': branch, 'payload': payload, 'expires_at': timezone.now() + ttl()},
        )
    return draft


def current(*, company, user) -> ReceiptDraft | None:
    """The live draft of (company, user). An expired one is deleted here and never given back."""
    draft = ReceiptDraft.objects.filter(company=company, user=user).first()
    if draft is None:
        return None
    if draft.expires_at <= timezone.now():
        ReceiptDraft.objects.filter(pk=draft.pk, expires_at__lte=timezone.now()).delete()
        return None
    return draft


def discard(*, company, user) -> None:
    ReceiptDraft.objects.filter(company=company, user=user).delete()


def purge(now=None) -> int:
    """Delete every expired draft. Idempotent; returns how many went."""
    deleted, _ = ReceiptDraft.objects.filter(expires_at__lte=now or timezone.now()).delete()
    return deleted


def present(draft: ReceiptDraft, *, branch_id, may_view_costs: bool) -> dict:
    payload = draft.payload if isinstance(draft.payload, dict) else {}
    if not may_view_costs:
        payload = without_costs(payload)
    return {
        **{key: value for key, value in payload.items() if key not in SERVER_KEYS},
        'branch': branch_id,
        'updated_at': draft.updated_at.isoformat(),
        'expires_at': draft.expires_at.isoformat(),
    }
