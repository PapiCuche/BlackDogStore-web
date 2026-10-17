"""
COMMERCE-ADMIN-UX-01 — recording the money given back for a cancelled order.

CANCELLING IS NOT REFUNDING, AND RECORDING IS NOT PAYING. A cancellation
changes the order; this module records that money is owed back (`pending`) or
that a person gave it back, with its reference (`confirmed`). It never calls a
gateway and never touches a `PaymentTransaction`: an Izipay charge is given
back from Izipay's panel, and its reference is written here afterwards.

WHAT CAN BE GIVEN BACK (`summary`)
    collected   the order's total if it was paid, plus every charge filed as
                a duplicate (a payment that arrived after the order was paid
                or cancelled: money the shop holds all the same) — except
                those made with TEST keys, which are not money.
    refundable  collected − pending − confirmed. A new refund never exceeds it.
    held        collected − confirmed: still in the shop's hands.

CONFIRMING IS EVIDENCE (owner, 2026-10-16). «Reembolso confirmado requiere
company.manage por ahora, más evidencia verificable, importe, referencia y
registro de auditoría. No registrar un reembolso como ejecutado solo porque se
canceló una venta.» A confirmation names the reference, the EXACT amount of
the pending row (a partial refund is a smaller pending row, recorded first)
and its evidence — operation number, bank or channel, date. A cancellation
only ever records a PENDING refund, and only when asked.

WHO
    record   `sales.orders.manage` — the authority that cancels the order.
    confirm  `sales.orders.manage` AND `company.manage` (the company's own
             administrator, or the platform master). No financial capability
             exists in the catalogue; `costs.view` is about costs, not money
             going out. Legacy bridge: admin and superadmin.

ONE ORDER LOCK. Every write locks the order first — the lock a cancellation
and a payment take — so two refunds, or a refund and a late payment, queue
instead of racing past the ceiling.
"""
from __future__ import annotations

import hashlib
import unicodedata
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import AdminAuditLog, Order, OrderRefund, PaymentTransaction, UserProfile

CAP_ORDERS_MANAGE = 'sales.orders.manage'
CAP_COMPANY_MANAGE = 'company.manage'
LEGACY_CONFIRM_ROLES = frozenset([UserProfile.ROLE_ADMIN, UserProfile.ROLE_SUPERADMIN])

REFERENCE_MIN, REFERENCE_MAX = 3, 120
EVIDENCE_MIN, EVIDENCE_MAX = 10, 300
#: Review P2: what makes text say something. Evidence: 6+ letters or digits,
#: at least 4 of them different ('aaaaaaaaaa' is not an operation number);
#: a reference: 3+ letters or digits.
EVIDENCE_MIN_ALNUM, EVIDENCE_MIN_DISTINCT = 6, 4
REFERENCE_MIN_ALNUM = 3
NOTE_MAX = 500
ZERO = Decimal('0.00')
CENT = Decimal('0.01')
METHODS = frozenset(code for code, _label in OrderRefund.METHOD_CHOICES)
#: A charge made with TEST keys is not money (review P3). An order PAID with
#: TEST keys keeps counting as it does everywhere today (sales, its total as
#: collected): only the extra charges filed as duplicates are left out here.
#: A row older than the column (environment '') counts, as reconciliation reads it.
TEST_ENVIRONMENT = 'test'


class RefundError(Exception):
    _BAD_REQUEST = frozenset([
        'amount_invalid', 'method_invalid', 'reference_required', 'reference_too_long', 'note_too_long',
        'evidence_required', 'evidence_invalid', 'evidence_too_long', 'amount_mismatch',
        'reference_invalid', 'note_invalid',
    ])

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail

    @property
    def http_status(self) -> int:
        return 400 if self.code in self._BAD_REQUEST else 409

    def body(self) -> dict:
        return {'detail': self.detail, 'code': self.code}


# -- who ---------------------------------------------------------------------

def may_record(user, company) -> bool:
    from .order_cancellation import may_cancel

    return may_cancel(user, company)


def may_confirm(user, company) -> bool:
    from .permissions import get_user_role
    from .tenancy import has_capability, uses_legacy_bridge

    if company is None or user is None or not getattr(user, 'is_authenticated', False):
        return False
    if uses_legacy_bridge(user, company):
        return get_user_role(user) in LEGACY_CONFIRM_ROLES
    return has_capability(user, company, CAP_ORDERS_MANAGE) and has_capability(user, company, CAP_COMPANY_MANAGE)


# -- how much ------------------------------------------------------------------

def collected(order) -> Decimal:
    """Everything the shop took for `order`: its total if paid, plus charges filed as duplicates."""
    late = PaymentTransaction.objects.filter(
        order_id=order.pk, status=PaymentTransaction.Status.DUPLICATE,
    ).exclude(environment=TEST_ENVIRONMENT).aggregate(total=Coalesce(Sum('amount'), ZERO))['total']
    return ((order.total if order.paid else ZERO) + late).quantize(CENT)


def _sum(order, status) -> Decimal:
    return OrderRefund.objects.filter(order_id=order.pk, status=status).aggregate(
        total=Coalesce(Sum('amount'), ZERO))['total'].quantize(CENT)


def summary(order) -> dict:
    taken = collected(order)
    confirmed = _sum(order, OrderRefund.STATUS_CONFIRMED)
    pending = _sum(order, OrderRefund.STATUS_PENDING)
    return {
        'collected': taken,
        'confirmed': confirmed,
        'pending': pending,
        'refundable': max(taken - confirmed - pending, ZERO),
        'held': max(taken - confirmed, ZERO),
    }


def clean_amount(value) -> Decimal:
    if isinstance(value, float):
        value = str(value)
    try:
        amount = Decimal(str(value).strip().replace(',', '.'))
    except (InvalidOperation, ValueError, TypeError):
        raise RefundError('amount_invalid', 'Escribe el importe del reembolso.') from None
    if not amount.is_finite() or amount <= ZERO or amount != amount.quantize(CENT):
        raise RefundError('amount_invalid', 'El importe debe ser mayor que cero, con dos decimales como máximo.')
    return amount.quantize(CENT)


def _visible(value, *, field, label) -> str:
    """
    Review P2. The text as people see it: invisible format characters
    (zero-width spaces, joiners, bidi marks — Unicode Cf) are dropped,
    whitespace is collapsed, and any other control character (NUL, BEL…:
    Unicode Cc that is not whitespace) is a 400 `<field>_invalid` instead of
    reaching the database (a NUL there was a 500).
    """
    kept = []
    for char in str(value or ''):
        category = unicodedata.category(char)
        if category == 'Cf':
            continue
        if category == 'Cc' and not char.isspace():
            raise RefundError(f'{field}_invalid', f'{label} lleva caracteres de control no permitidos.')
        kept.append(char)
    return ' '.join(''.join(kept).split())


def _alnum(text) -> list[str]:
    return [char for char in text if char.isalnum()]


def _clean_text(value, *, max_length, code, label, field='note') -> str:
    text = _visible(value, field=field, label=label)
    if len(text) > max_length:
        raise RefundError(code, f'{label} no puede pasar de {max_length} caracteres.')
    return text


# -- writes --------------------------------------------------------------------

def clean_method(method) -> str:
    """The refund's method, or `RefundError` — also for anything that is not text (review P3)."""
    if not isinstance(method, str) or method not in METHODS:
        raise RefundError('method_invalid', 'Elige cómo se devuelve el dinero.')
    return method


def record_pending(order, user, *, amount=None, method, note='', request=None) -> OrderRefund:
    """
    A refund that is owed, for a CANCELLED order. `amount` None = everything
    still refundable. Raises `RefundError`; nothing is written then.
    """
    method = clean_method(method)
    note = _clean_text(note, max_length=NOTE_MAX, code='note_too_long', label='La nota')
    wanted = clean_amount(amount) if amount not in (None, '') else None
    actor = user if getattr(user, 'is_authenticated', False) else None

    with transaction.atomic():
        locked = Order.objects.select_for_update(of=('self',)).get(pk=order.pk)
        if locked.status != Order.Status.CANCELLED:
            raise RefundError('not_cancelled', 'Sólo se registra un reembolso de un pedido cancelado.')
        figures = summary(locked)
        if figures['refundable'] <= ZERO:
            raise RefundError('nothing_to_refund', 'No queda dinero cobrado por devolver en este pedido.')
        amount = figures['refundable'] if wanted is None else wanted
        if amount > figures['refundable']:
            raise RefundError(
                'amount_exceeds',
                f'El reembolso no puede pasar de S/ {figures["refundable"]} '
                '(lo cobrado menos lo ya registrado como reembolso).')
        refund = OrderRefund.objects.create(
            company_id=locked.company_id, order=locked, amount=amount, method=method,
            note=note, created_by=actor,
        )
        AdminAuditLog.log(
            actor=actor, action='order_refund_recorded', target_type='order', target_id=locked.pk,
            metadata={'order_id': locked.pk, 'refund_id': refund.pk, 'amount': str(amount), 'method': method,
                      'collected': str(figures['collected']), 'status': refund.status},
            request=request, company=locked.company,
        )
    return refund


def clean_evidence(value) -> str:
    """What makes the confirmation verifiable: text only, at least `EVIDENCE_MIN` characters."""
    if value is not None and not isinstance(value, str):
        raise RefundError('evidence_invalid', 'La evidencia del reembolso debe ser texto.')
    text = _clean_text(value, max_length=EVIDENCE_MAX, code='evidence_too_long', label='La evidencia',
                       field='evidence')
    letters = _alnum(text)
    if (len(text) < EVIDENCE_MIN or len(letters) < EVIDENCE_MIN_ALNUM
            or len(set(letters)) < EVIDENCE_MIN_DISTINCT):
        raise RefundError(
            'evidence_required',
            'Escribe la evidencia del reembolso: número de operación, banco o canal y fecha '
            f'(al menos {EVIDENCE_MIN} caracteres, con {EVIDENCE_MIN_ALNUM} letras o números).')
    return text


def confirm(refund, user, *, reference, evidence=None, amount=None, note='', request=None) -> OrderRefund:
    """
    The money went back: who, when, with which reference, for EXACTLY the
    pending amount, and the evidence that proves it. Once.
    """
    if not isinstance(reference, str):
        reference = ''
    reference = _clean_text(reference, max_length=REFERENCE_MAX, code='reference_too_long', label='La referencia',
                            field='reference')
    if len(reference) < REFERENCE_MIN or len(_alnum(reference)) < REFERENCE_MIN_ALNUM:
        raise RefundError(
            'reference_required',
            f'Escribe la referencia del reembolso (número de operación o voucher, al menos {REFERENCE_MIN} caracteres).')
    evidence = clean_evidence(evidence)
    stated = clean_amount(amount)
    note = _clean_text(note, max_length=NOTE_MAX, code='note_too_long', label='La nota')
    actor = user if getattr(user, 'is_authenticated', False) else None

    with transaction.atomic():
        locked_order = Order.objects.select_for_update(of=('self',)).get(pk=refund.order_id)
        locked = OrderRefund.objects.select_for_update().get(pk=refund.pk)
        if locked.status != OrderRefund.STATUS_PENDING:
            raise RefundError('already_confirmed', 'Este reembolso ya fue confirmado.')
        if stated != locked.amount:
            raise RefundError(
                'amount_mismatch',
                f'El importe confirmado debe ser exactamente el del reembolso pendiente (S/ {locked.amount}). '
                'Para un reembolso parcial, registra antes un pendiente por ese importe.')
        figures = summary(locked_order)
        if figures['confirmed'] + locked.amount > figures['collected']:
            raise RefundError(
                'amount_exceeds', 'Confirmarlo devolvería más de lo cobrado por este pedido.')
        now = timezone.now()
        locked.status = OrderRefund.STATUS_CONFIRMED
        locked.reference = reference
        locked.evidence = evidence
        locked.confirmed_by = actor
        locked.confirmed_at = now
        if note:
            locked.note = (f'{locked.note} · {note}' if locked.note else note)[:NOTE_MAX]
        locked.save(update_fields=['status', 'reference', 'evidence', 'confirmed_by', 'confirmed_at', 'note'])
        AdminAuditLog.log(
            actor=actor, action='order_refund_confirmed', target_type='order', target_id=locked_order.pk,
            # The evidence is on the refund, where only who may see the order
            # reads it; the audit proves it by its fingerprint and never copies
            # free text (a name, a document, an account) into the log.
            metadata={'order_id': locked_order.pk, 'refund_id': locked.pk, 'amount': str(locked.amount),
                      'method': locked.method, 'reference': reference,
                      'evidence_sha256': hashlib.sha256(evidence.encode()).hexdigest()},
            request=request, company=locked_order.company,
        )
    return locked


# -- reads ---------------------------------------------------------------------

def _name(user) -> str:
    return (user.get_full_name() or user.username) if user else ''


def payload(order) -> dict:
    figures = summary(order)
    rows = OrderRefund.objects.filter(order_id=order.pk).select_related('created_by', 'confirmed_by')
    return {
        **{key: str(value) for key, value in figures.items()},
        'items': [
            {
                'id': row.pk, 'amount': str(row.amount), 'method': row.method,
                'method_label': row.get_method_display(), 'status': row.status,
                'status_label': row.get_status_display(), 'reference': row.reference, 'note': row.note,
                'evidence': row.evidence,
                'created_at': row.created_at.isoformat(), 'created_by': _name(row.created_by),
                'confirmed_at': row.confirmed_at.isoformat() if row.confirmed_at else None,
                'confirmed_by': _name(row.confirmed_by),
            }
            for row in rows
        ],
    }


__all__ = ['RefundError', 'clean_evidence', 'collected', 'confirm', 'may_confirm', 'may_record', 'payload', 'record_pending', 'summary']
