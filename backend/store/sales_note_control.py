"""
SALES-NOTE-CONTROL-01 — the platform master administers issued sales notes.

THE OWNER'S REQUEST (2026-10-14): «las primeras notas de venta fueron de
prueba y el usuario Master necesita administrarlas».

WHO. «Master» in this codebase is the PLATFORM MASTER (`is_platform_admin`,
today `is_superuser`), acting inside the company it names (`?company=`). No
company role or capability is the master: a company administrator holding
every capability is refused, exactly like the integrations console.

WHAT VOIDING A NOTE IS — AND IS NOT. It marks an internal document as void,
with a reason, a date and a user, and every paper of it says ANULADA from then
on. It is NOT cancelling the order (ORDER-CANCEL-01), NOT reversing a payment,
NOT a return, NOT a credit note and NOT a SUNAT void (`fiscal_void_services`).
None of those is touched: the order stays paid, the money stays collected, the
stock stays where it is and the series keeps its counter — the number of a void
note is never handed out again.

WHAT NEVER CHANGES on an issued note: its number, its series and ordinal, its
issue date, its issuer and everything it printed (lines, amounts, taxes,
payment). There is no endpoint that writes them. What may be ADDED is an
internal remark (`SalesNoteRemark`), append-only and never printed.

ONE WRITER OF THE VOID RECORD. `mark_void` is called by `void_sales_note` and by
the order cancellation, so both paths leave the same record.

LOCK ORDER: order, then note — the order cancellation's order. A void and a
cancellation of the same order queue instead of racing.
"""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from .models import AdminAuditLog, Order, SalesNote, SalesNoteRemark

CAP_SALES_NOTES = 'sales.notes.manage'

REASON_TEST = SalesNote.VOID_REASON_TEST
REASON_ORDER_CANCELLED = SalesNote.VOID_REASON_ORDER_CANCELLED
#: What a person may choose. «pedido_cancelado» is written only by the order
#: cancellation: choosing it by hand would claim a cancellation that never was.
MANUAL_REASON_TYPES = (SalesNote.VOID_REASON_TEST, SalesNote.VOID_REASON_ERROR, SalesNote.VOID_REASON_OTHER)

REASON_MIN, REASON_MAX = 5, 500
REMARK_MIN, REMARK_MAX = 1, 1000


class SalesNoteControlError(Exception):
    """A request the rules refuse. `code` says which rule; `detail` is for the person."""

    _BAD_REQUEST = frozenset(['reason_required', 'reason_too_long', 'reason_type_invalid',
                              'remark_required', 'remark_too_long'])

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail

    @property
    def http_status(self) -> int:
        return 400 if self.code in self._BAD_REQUEST else 409

    def body(self) -> dict:
        return {'detail': self.detail, 'code': self.code}


def _normalise(text) -> str:
    return ' '.join(str(text or '').split())


def clean_reason(reason) -> str:
    text = _normalise(reason)
    if len(text) < REASON_MIN:
        raise SalesNoteControlError(
            'reason_required', f'Escribe el motivo de la anulación (al menos {REASON_MIN} caracteres).')
    if len(text) > REASON_MAX:
        raise SalesNoteControlError('reason_too_long', f'El motivo no puede pasar de {REASON_MAX} caracteres.')
    return text


def clean_reason_type(reason_type) -> str:
    if reason_type not in MANUAL_REASON_TYPES:
        raise SalesNoteControlError(
            'reason_type_invalid', 'Elige el tipo de motivo: prueba, error de emisión u otro motivo.')
    return reason_type


def clean_remark(text) -> str:
    # Line breaks are kept inside a remark; only the edges are trimmed.
    text = str(text or '').strip()
    if len(text) < REMARK_MIN:
        raise SalesNoteControlError('remark_required', 'Escribe la observación.')
    if len(text) > REMARK_MAX:
        raise SalesNoteControlError('remark_too_long', f'La observación no puede pasar de {REMARK_MAX} caracteres.')
    return text


def may_void(user, company) -> bool:
    """Only the platform master, and only inside a company it has named."""
    from .tenancy import is_platform_admin

    return company is not None and is_platform_admin(user)


def is_void(note) -> bool:
    return note.status == SalesNote.STATUS_VOID


def mark_void(note, *, actor, reason_type, reason, extra_metadata=None) -> None:
    """
    THE ONE WRITER of the void record. The caller holds the note's row lock and
    has checked it is issued. Writes only the void columns (and the metadata
    note #119 already kept) — never the number, the dates or the lines.
    """
    note.status = SalesNote.STATUS_VOID
    note.voided_at = timezone.now()
    note.voided_by = actor if getattr(actor, 'is_authenticated', False) else None
    note.void_reason_type = reason_type
    note.void_reason = reason
    fields = ['status', 'voided_at', 'voided_by', 'void_reason_type', 'void_reason']
    if extra_metadata:
        note.metadata = {**(note.metadata or {}), **extra_metadata}
        fields.append('metadata')
    note.save(update_fields=fields)


def void_sales_note(note, user, reason_type, reason, *, request=None) -> SalesNote:
    """
    Void `note` — already resolved through the caller's scoped queryset — as
    the platform master. Raises `SalesNoteControlError` when a rule says no;
    nothing is written then. The authority check is the caller's (`may_void`).
    """
    reason_type = clean_reason_type(reason_type)
    reason = clean_reason(reason)
    actor = user if getattr(user, 'is_authenticated', False) else None

    with transaction.atomic():
        order = Order.objects.select_for_update(of=('self',)).get(pk=note.order_id)
        locked = SalesNote.objects.select_for_update().get(pk=note.pk)
        if is_void(locked):
            raise SalesNoteControlError('already_void', f'La nota {locked.number} ya está anulada.')
        mark_void(locked, actor=actor, reason_type=reason_type, reason=reason)
        AdminAuditLog.log(
            actor=actor, action='sales_note_voided', target_type='sales_note', target_id=locked.pk,
            metadata={
                'sales_note_id': locked.pk,
                'sales_note_number': locked.number,
                'sequence_id': locked.sequence_id,
                'sequence_value': locked.sequence_value,
                'order_id': order.pk,
                'branch_id': order.fulfillment_branch_id,
                'reason_type': reason_type,
                'reason': reason,
                # Said, so nobody reads a void note as a refund or a cancellation.
                'order_status_kept': order.status,
                'paid_kept': bool(order.paid),
            },
            request=request, company=order.company,
        )
    return locked


def add_remark(note, user, text, *, request=None) -> SalesNoteRemark:
    """Append an internal remark. The authority check is the caller's."""
    text = clean_remark(text)
    actor = user if getattr(user, 'is_authenticated', False) else None
    order = note.order
    with transaction.atomic():
        remark = SalesNoteRemark.objects.create(sales_note=note, author=actor, text=text)
        AdminAuditLog.log(
            actor=actor, action='sales_note_remark_added', target_type='sales_note', target_id=note.pk,
            metadata={
                'sales_note_id': note.pk,
                'sales_note_number': note.number,
                'remark_id': remark.pk,
                'order_id': order.pk,
                'branch_id': order.fulfillment_branch_id,
                'text': text,
            },
            request=request, company=order.company,
        )
    return remark


__all__ = [
    'MANUAL_REASON_TYPES', 'REASON_ORDER_CANCELLED', 'REASON_TEST', 'SalesNoteControlError', 'add_remark',
    'clean_reason', 'clean_reason_type', 'clean_remark', 'is_void', 'mark_void', 'may_void', 'void_sales_note',
]
