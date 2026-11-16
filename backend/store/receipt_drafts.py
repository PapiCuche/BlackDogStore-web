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
import re
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from . import receipt_services as receipts
from .models import ReceiptDraft

MAX_BYTES = 256 * 1024
# The pasted list waiting to be resolved (`receive-list.ts` MAX_STAGED).
MAX_STAGED = 600
# What the server adds to the answer: a payload cannot spell them.
SERVER_KEYS = ('branch', 'updated_at', 'expires_at')
# The version a client names (`base`): an ISO date as `version()` writes it, never longer than this.
MAX_BASE = 64
# A recognizer's notice that speaks of a cost («El costo 900 se dedujo…»): only for who holds `costs.view`.
COST_WORDS = re.compile(r'cost', re.IGNORECASE)
# What a staged row keeps of the pasted text: it can spell a cost, so it is not given back without `costs.view`.
STAGED_TEXT = {'source': '', 'amounts': [], 'hints': []}


class DraftConflict(receipts.ReceiptError):
    """The draft changed (another tab, a confirmation, a discard) since the version the client names."""

    def __init__(self):
        super().__init__(
            'Hay un borrador más reciente guardado (u otra pestaña lo cambió, o ya se confirmó). '
            'Recárgalo o descártalo para seguir guardando.', status=409, code='draft_conflict')


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


def _has_nul(value) -> bool:
    """A NUL anywhere (a key or a text, at any depth): PostgreSQL's jsonb cannot keep it."""
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            if '\x00' in item:
                return True
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return False


def _staged_rows(payload: dict):
    return [row for row in payload.get('staged') or [] if isinstance(row, dict)]


def _staged_costs(payload: dict) -> bool:
    """Undecided amounts (a cost by convention) or a notice that names a cost, in the rows of a pasted list."""
    for row in _staged_rows(payload):
        if row.get('amounts'):
            return True
        hints = row.get('hints')
        if isinstance(hints, list) and any(isinstance(hint, str) and COST_WORDS.search(hint) for hint in hints):
            return True
    return False


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
    if _has_nul(payload):
        raise receipts.ReceiptError('El borrador tiene caracteres no válidos.', field='payload')
    if not may_view_costs and (any(_has_amount(holder.get(key)) for holder, key in _cost_fields(payload))
                               or _staged_costs(payload)):
        # The receipt's own rule (DEC-INV-05): a cost needs `costs.view`, a missing permission is a 403.
        raise receipts.ReceiptError(receipts.COSTS_REQUIRED, status=403, code=receipts.cost_services.COSTS_FORBIDDEN,
                                    field='payload')
    return {key: value for key, value in payload.items() if key not in SERVER_KEYS}


def without_costs(payload: dict) -> dict:
    """A copy with every cost blanked, and the pasted text of the list's rows (it can spell one) emptied: for a
    reader who does not hold `costs.view`."""
    copy = json.loads(json.dumps(payload))
    for holder, key in _cost_fields(copy):
        if key in holder:
            holder[key] = ''
    for row in _staged_rows(copy):
        for key, empty in STAGED_TEXT.items():
            if key in row:
                row[key] = type(empty)()
    return copy


def version(draft: ReceiptDraft) -> str:
    """The version of a draft, exactly as the GET and the PUT give it (`updated_at`)."""
    return draft.updated_at.isoformat()


def base_of(body: dict) -> str | None:
    """The version the client last saw: text, or null (none; a missing key counts as null)."""
    base = body.get('base')
    if base is not None and (not isinstance(base, str) or not base or len(base) > MAX_BASE):
        raise receipts.ReceiptError('Versión del borrador no válida.', field='base')
    return base


def _locked(*, company, user) -> ReceiptDraft | None:
    return ReceiptDraft.objects.select_for_update().filter(company=company, user=user).first()


def upsert(*, company, user, branch, payload: dict, base: str | None) -> ReceiptDraft:
    """Save the one draft of (company, user) only over the version the client saw (`base`, None: there was none);
    the expiry starts again. A draft changed or deleted since is a `DraftConflict`: never overwritten, never brought
    back. An expired one counts as none."""
    now = timezone.now()
    with transaction.atomic():
        draft = _locked(company=company, user=user)
        if draft is None:
            if base is not None:
                raise DraftConflict()
            try:
                with transaction.atomic():
                    return ReceiptDraft.objects.create(company=company, user=user, branch=branch, payload=payload,
                                                       expires_at=now + ttl())
            except IntegrityError:
                # Another request created it between the look and the insert.
                raise DraftConflict() from None
        if base != version(draft) and not (base is None and draft.expires_at <= now):
            raise DraftConflict()
        draft.branch, draft.payload, draft.expires_at = branch, payload, now + ttl()
        draft.save(update_fields=['branch', 'payload', 'expires_at', 'updated_at'])
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
        'updated_at': version(draft),
        'expires_at': draft.expires_at.isoformat(),
    }
