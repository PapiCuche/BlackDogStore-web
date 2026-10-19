"""
ORDER-FULFILLMENT-SAFETY-01 — correcting an order marked «sent» or «delivered» by mistake.

`ever_left` treats ANY audited move to sent or delivered as proof that the
goods left, forever, so that moving the label back cannot reopen a
cancellation. That is right, and it is also what an accidental click made
permanent: a pickup order «sent» twenty minutes after its payment, the goods
never touched.

The way out is not to edit the record. A superior of the company declares
that ONE named entry was a mistake and that the goods never left; that
declaration is a new audit entry (`order_left_marking_corrected`) pointing at
the old one by id, and `ever_left` stops counting that id. The old entry stays
exactly as it was, and every other entry still counts.

It is refused when anything else says the goods did leave: a device stamped
as gone by anything but this entry (owner, 2026-10-19), a shipping date or a waybill/tracking code the delivery record holds
or ever held (an edit that blanks them does not unsay them), or
a later move to sent or delivered that is not corrected (correct the newest
first). And for an order already cancelled — there is nothing left to unlock.

What it does NOT do: cancel, refund, touch stock, the Kardex, payments, costs,
margin or commissions. If the order still stands where the mistaken entry put
it (`restore_target`), its status goes back to the one before, audited in
the same entry: otherwise a «sent» order would stay «sent» with nothing to
say it ever left. Afterwards a cancellation is judged by its own
rules, as for any order whose goods are in the shop.

One transaction with the order locked, like every other move of the order;
correcting an entry already corrected returns that correction and writes
nothing (idempotent).
"""
from __future__ import annotations

from django.db import transaction

from . import order_cancellation as cancelling
from .models import AdminAuditLog, Order, OrderUnit

ACTION = cancelling.LEFT_MARKING_CORRECTED
REASON_MIN, REASON_MAX = 5, 500


class CorrectionError(Exception):
    def __init__(self, code: str, detail: str, http_status: int = 409):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.http_status = http_status


def may_correct(user, company) -> bool:
    """The authority that annuls an old till sale: orders AND the company (effective capabilities)."""
    return cancelling.may_cancel_as_superior(user, company)


def _corrections(order) -> dict[int, AdminAuditLog]:
    """corrected entry id -> the correction that names it (the first, if ever two)."""
    found = {}
    for row in AdminAuditLog.objects.filter(
        action=ACTION, target_type='order', target_id=str(order.pk),
    ).order_by('created_at', 'pk'):
        ref = (row.metadata or {}).get('corrected_audit_id')
        if isinstance(ref, int) and ref not in found:
            found[ref] = row
    return found


def blockers(order, event, corrections=None) -> list[dict]:
    """Why `event` (a leaving entry of `order`) cannot be corrected now. Empty when it can."""
    corrections = _corrections(order) if corrections is None else corrections
    if cancelling.is_cancelled(order):
        return [{'code': 'order_cancelled', 'detail': 'El pedido ya está cancelado: no hay nada que corregir.'}]
    found = []
    # A device stamped by a move to sent/delivered is explained by that move
    # (owner, 2026-10-19): by this one, by one already corrected, or by an
    # EARLIER one not corrected yet — that one still says the goods left
    # (`ever_left`) and is checked when it is corrected itself. Review P2: sent
    # then delivered stamps the device on «sent», so correcting «delivered»
    # first must not trip over it. Any other stamp (a till hand-over) counts.
    explained = cancelling.units_stamped_by(
        order, cancelling.leaving_events(order).filter(created_at__lte=event.created_at)
        | cancelling.leaving_events(order).filter(pk__in=set(corrections) | {event.pk}))
    if OrderUnit.objects.filter(order_id=order.pk, left_at__isnull=False).exclude(pk__in=explained).exists():
        found.append({'code': 'unit_left', 'detail': 'Un equipo identificado de este pedido figura como salido de la tienda.'})
    # Now or ever: a waybill or a date blanked afterwards still says it left,
    # and so does what the leaving entry itself recorded when it was written.
    snapshot = (event.metadata or {}).get('logistics') or {}
    if cancelling.logistics_ever_recorded(order, ('shipped_at',)) or snapshot.get('shipped_at'):
        found.append({'code': 'shipped_at', 'detail': 'La tarjeta «Entrega» tiene (o tuvo) una fecha de envío registrada.'})
    if (cancelling.logistics_ever_recorded(order, ('waybill_number', 'tracking_code'))
            or snapshot.get('waybill') or snapshot.get('tracking')):
        found.append({'code': 'waybill', 'detail': 'La tarjeta «Entrega» tiene (o tuvo) una guía o un código de seguimiento.'})
    later = cancelling.leaving_events(order).filter(created_at__gte=event.created_at).exclude(pk=event.pk)
    if any(row.pk not in corrections and (row.created_at, row.pk) > (event.created_at, event.pk) for row in later):
        found.append({'code': 'later_leaving', 'detail': 'Hay un envío o una entrega registrados después de este, sin corregir: corrige primero el más reciente.'})
    elif restore_target(order, event, corrections) is None:
        found.append({'code': 'status_leaving', 'detail': 'El pedido figura ahora como enviado o entregado por un cambio posterior a este registro.'})
    return found


def restore_target(order, event, corrections) -> str | None:
    """
    The status `order` goes back to when `event` is corrected: '' to stay where
    it is, None when it cannot be corrected without leaving it «sent» or
    «delivered» with nothing to say it ever left.

    Only an order that STANDS at a leaving status moves, and only when every
    change after `event` is itself a corrected leaving entry: then it goes back
    to the status `event` started from. If that one is also leaving (sent, then
    delivered), it stays: the earlier entry, still uncorrected, says it left.
    """
    FS = Order.FulfillmentStatus
    if order.fulfillment_status not in cancelling.LEAVING:
        return ''
    after = [
        row for row in AdminAuditLog.objects.filter(
            action='order_fulfillment_status_changed', target_type='order', target_id=str(order.pk),
            created_at__gte=event.created_at,
        ).exclude(pk=event.pk)
        if (row.created_at, row.pk) > (event.created_at, event.pk)
    ]
    if any(row.pk not in corrections for row in after):
        return None
    old = (event.metadata or {}).get('old_fulfillment_status') or FS.PENDING
    if old in cancelling.LEAVING:
        return ''
    return old


def markings(order, user=None, company=None) -> list[dict]:
    """Every leaving entry of `order`, newest first, with whether it was corrected and whether it can be."""
    corrections = _corrections(order)
    may = user is not None and may_correct(user, company)
    rows = []
    for event in reversed(list(cancelling.leaving_events(order).select_related('actor'))):
        meta = event.metadata or {}
        correction = corrections.get(event.pk)
        item = {
            'audit_id': event.pk,
            'at': event.created_at.isoformat(),
            'old_status': meta.get('old_fulfillment_status', ''),
            'new_status': meta.get('new_fulfillment_status', ''),
            'actor': actor_label(event.actor),
            'corrected': correction is not None,
            'correction': None,
            'can_correct': False,
            'blockers': [],
        }
        if correction is not None:
            cmeta = correction.metadata or {}
            item['correction'] = {
                'at': correction.created_at.isoformat(), 'actor': actor_label(correction.actor),
                'reason': cmeta.get('reason', ''),
            }
        elif may:
            item['blockers'] = blockers(order, event, corrections)
            item['can_correct'] = not item['blockers']
        rows.append(item)
    return rows


def actor_label(user) -> str:
    if user is None:
        return 'Sistema'
    return (user.get_full_name() or '').strip() or user.get_username()


def _clean_reason(reason) -> str:
    text = ' '.join(str(reason or '').split())
    if not REASON_MIN <= len(text) <= REASON_MAX:
        raise CorrectionError(
            'reason_required', f'Escribe el motivo de la corrección ({REASON_MIN} a {REASON_MAX} caracteres).', 400)
    if any(ord(ch) < 32 for ch in text):
        raise CorrectionError('reason_invalid', 'El motivo tiene caracteres no válidos.', 400)
    return text


def correct(*, order, audit_id, actor, company, reason, never_left, request=None) -> tuple[AdminAuditLog, bool]:
    """
    Declare entry `audit_id` of `order` accidental. Returns (correction, created).
    `created` is False when that entry was already corrected (nothing written).
    """
    if not may_correct(actor, company):
        raise CorrectionError(
            'superior_required',
            'Corregir un marcado de salida corresponde a un superior (administrador de la empresa).', 403)
    if never_left is not True:
        raise CorrectionError(
            'declaration_required', 'Confirma que el producto nunca salió de la tienda.', 400)
    text = _clean_reason(reason)
    try:
        audit_id = int(audit_id)
    except (TypeError, ValueError):
        raise CorrectionError('event_not_found', 'Ese registro de salida no existe en este pedido.', 404)

    with transaction.atomic():
        locked = Order.objects.select_for_update(of=('self',)).get(pk=order.pk)
        event = cancelling.leaving_events(locked).filter(pk=audit_id).first()
        if event is None:
            raise CorrectionError('event_not_found', 'Ese registro de salida no existe en este pedido.', 404)
        corrections = _corrections(locked)
        if event.pk in corrections:
            return corrections[event.pk], False
        found = blockers(locked, event, corrections)
        if found and found[0]['code'] == 'order_cancelled':
            raise CorrectionError('order_cancelled', found[0]['detail'])
        if found:
            raise CorrectionError(
                'contradicting_evidence',
                'No se puede corregir: ' + ' '.join(b['detail'] for b in found)
                + ' Si el producto salió, corresponde una devolución.')
        meta = event.metadata or {}
        restored = restore_target(locked, event, corrections) or ''
        if restored:
            locked.fulfillment_status = restored
            locked.save(update_fields=['fulfillment_status'])
        correction = AdminAuditLog.log(
            actor=actor, action=ACTION, target_type='order', target_id=locked.pk, company=company,
            metadata={
                'order_id': locked.pk,
                'corrected_audit_id': event.pk,
                'corrected_at': event.created_at.isoformat(),
                'corrected_old_status': meta.get('old_fulfillment_status', ''),
                'corrected_new_status': meta.get('new_fulfillment_status', ''),
                'reason': text,
                'declared_never_left': True,
                'status_restored_to': restored,
            },
            request=request,
        )
    return correction, True
