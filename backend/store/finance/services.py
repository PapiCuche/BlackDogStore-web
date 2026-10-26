"""
FIN-B1 — every write on rules, periods, obligations and their ledger.

docs/adr-finance-obligations.md §3–§4. The rules this module enforces:

- A RULE IS NOT A DEBT. `generate_periods` creates each period once; the rule
  row is locked while it does, and (rule, index) is unique besides, so two cron
  passes at the same instant produce one period. A period is never deleted.
- THE BALANCE IS DERIVED (`figures`): the original amount, plus increases,
  minus decreases and write-offs, minus the payments that were not reversed.
  No total is stored anywhere that could drift from the rows behind it.
- ONE LOCK ORDER: the obligation, then its payments and adjustments. A payment
  reads the balance with the obligation locked, so two cashiers cannot both
  pay the last balance; a reversal locks the obligation FIRST too (the lesson
  of H-1 in the repair ledger).
- A KEY NAMES ONE ATTEMPT ON ONE OBLIGATION. The fingerprint includes the
  obligation, so a key reused elsewhere is a conflict, never a replay of
  another debt's payment (H-4).
- Nothing expires: an overdue obligation stays `open` until it is paid, voided
  with a reason, or corrected by an audited adjustment.
- Nothing here sends a message to anybody (FINANCE-MANUAL-REMINDERS-01).

Authority (capability and branch) is decided by the views; these functions
trust the company they are handed and refuse any related row of another one.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import DecimalField, Exists, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .. import sequences
from ..models import looks_like_card_number
from ..models import (
    AdminAuditLog, FinanceCategory, FinanceDirection, InternalSequence, Obligation, ObligationAdjustment,
    ObligationPayment, PaymentMethod, RecurringObligationRule, RulePause,
)
from . import calendar

logger = logging.getLogger(__name__)

#: How far ahead periods are generated (local days).
HORIZON_DAYS = 35
#: How far back «generar los anteriores» may reach (local days, ~24 months).
PAST_LIMIT_DAYS = 731
#: Highest period index a hand call may name (weekly: ~230 years), so a typo cannot overflow a date.
MAX_PERIOD_INDEX = 12000
#: «Por vencer»: due within this many days (FIN-C makes it a company policy).
DUE_SOON_DAYS = 7
MANUAL_METHODS = (PaymentMethod.CASH, PaymentMethod.CARD, PaymentMethod.TRANSFER, PaymentMethod.OTHER)
REFERENCE_PREFIX = 'OB-'
CENT = Decimal('0.01')
ZERO = Decimal('0.00')
_CURRENCY = re.compile(r'^[A-Z]{3}$')

STATE_VOIDED = 'voided'
STATE_PAID = 'paid'
STATE_OVERDUE = 'overdue'
STATE_DUE_TODAY = 'due_today'
STATE_DUE_SOON = 'due_soon'
STATE_PENDING = 'pending'
#: A period of a paused rule that falls due inside the pause, has no payment and
#: was not included by hand: not owed while the pause says so. DERIVED, never a
#: void: resuming earlier, or including it, makes it owed again (owner's rule:
#: pausing never removes a debt already owed).
STATE_PAUSED = 'paused'
STATES = (STATE_PENDING, STATE_DUE_SOON, STATE_DUE_TODAY, STATE_OVERDUE, STATE_PAID, STATE_VOIDED, STATE_PAUSED)

AUDIT_OBLIGATION = 'finance_obligation'
AUDIT_RULE = 'finance_rule'
AUDIT_CATEGORY = 'finance_category'


class FinanceError(Exception):
    """A refused finance operation. Views render `status`."""

    status = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class FinanceConflict(FinanceError):
    status = 409


class FinanceNotFound(FinanceError):
    status = 404


class _RaceLost(Exception):
    """The INSERT lost a unique-key race; the caller re-reads the winner."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def company_today(company) -> date:
    """Today, as the company reckons it (its timezone, else America/Lima)."""
    from ..sales_analytics_views import _company_today

    return _company_today(company)


def _text(value) -> str:
    """Free text from a JSON body: text or a bare number is kept as text, anything else is a 400."""
    if value is None:
        return ''
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    raise FinanceError('Se esperaba texto.')


def _money(value, *, field='Importe') -> Decimal:
    """A two-decimal amount. More decimals are REFUSED, never rounded in silence."""
    try:
        amount = Decimal(str(value).strip())
        if not amount.is_finite():
            raise FinanceError(f'{field} inválido.')
        exact = amount.quantize(CENT)
    except (InvalidOperation, ValueError, TypeError):
        raise FinanceError(f'{field} inválido.') from None
    if exact != amount:
        raise FinanceError(f'{field} admite como máximo dos decimales.')
    return exact


def _positive(value, *, field='Importe') -> Decimal:
    amount = _money(value, field=field)
    if amount <= 0:
        raise FinanceError(f'{field} debe ser mayor que cero.')
    if amount >= Decimal('10000000000'):
        raise FinanceError(f'{field} demasiado grande.')
    return amount


def _currency(company, value) -> str:
    code = _text(value).upper()
    if not code:
        settings_row = getattr(company, 'settings', None)
        code = (getattr(settings_row, 'currency', '') or 'PEN').upper()
    if not _CURRENCY.match(code):
        raise FinanceError('Moneda inválida.')
    return code


def _reason(value, *, what='el motivo') -> str:
    text = _text(value)
    if not text:
        raise FinanceError(f'Escribe {what}.')
    return text[:300]


def _fingerprint(**parts) -> str:
    canonical = json.dumps({k: str(v) for k, v in parts.items()}, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _key(value) -> str:
    key = _text(value)
    if len(key) > 64:
        raise FinanceError('Clave de idempotencia demasiado larga.')
    return key


def _audit(*, actor, action, target_type, target_id, company, metadata=None, request=None):
    """Audit with the target's BRANCH, so a branch-scoped review can read it."""
    metadata = dict(metadata or {})
    model = {AUDIT_OBLIGATION: Obligation, AUDIT_RULE: RecurringObligationRule}.get(target_type)
    if model is not None and 'branch_id' not in metadata:
        metadata['branch_id'] = model.objects.filter(pk=target_id).values_list('branch_id', flat=True).first()
    AdminAuditLog.log(
        actor=actor, action=action, target_type=target_type, target_id=target_id,
        metadata=metadata, request=request, company=company,
    )


def _same_company(company, row, what):
    if row is not None and row.company_id != company.pk:
        raise FinanceError(f'{what} no pertenece a esta empresa.')
    return row


def _category(company, category, direction):
    _same_company(company, category, 'La categoría')
    if category is not None:
        if category.direction != direction:
            raise FinanceError('La categoría es de otro tipo de obligación.')
        if not category.is_active:
            raise FinanceError('La categoría está desactivada.')
    return category


def _owner(company, owner):
    if owner is None:
        return None
    from ..tenancy import get_membership

    if get_membership(owner, company) is None:
        raise FinanceError('El responsable no pertenece a esta empresa.')
    return owner


def _method(value, *, allow_blank=False) -> str:
    method = _text(value)
    if allow_blank and not method:
        return ''
    if method not in MANUAL_METHODS:
        raise FinanceError('Ese medio de pago no se puede registrar.')
    return method


def _allocate_reference(company) -> str:
    sequence = sequences.ensure_company_sequence(
        company, InternalSequence.DOCUMENT_FINANCE_OBLIGATION, prefix=REFERENCE_PREFIX,
    )
    _value, formatted = sequences.allocate(sequence)
    return formatted


# ---------------------------------------------------------------------------
# Figures and state — derived, never stored
# ---------------------------------------------------------------------------

def _sum(model, *, filter_q=None):
    rows = model.objects.filter(obligation=OuterRef('pk'))
    if filter_q is not None:
        rows = rows.filter(filter_q)
    return Coalesce(
        Subquery(rows.order_by().values('obligation').annotate(total=Sum('amount')).values('total')[:1]),
        Value(ZERO), output_field=DecimalField(max_digits=14, decimal_places=2),
    )


def _pauses_covering(due_ref, rule_ref):
    """Pauses of the rule whose interval holds the due date: strictly after it began, before it ended."""
    return RulePause.objects.filter(rule=rule_ref, paused_from__lt=due_ref).filter(
        Q(resumed_on__isnull=True) | Q(resumed_on__gt=due_ref))


def with_figures(queryset):
    """Annotate `fin_paid`, `fin_increase`, `fin_decrease` (decreases + write-offs) and `fin_in_pause`."""
    return queryset.annotate(
        fin_paid=_sum(ObligationPayment, filter_q=Q(reversed_at__isnull=True)),
        fin_increase=_sum(ObligationAdjustment, filter_q=Q(kind=ObligationAdjustment.KIND_INCREASE)),
        fin_decrease=_sum(ObligationAdjustment, filter_q=~Q(kind=ObligationAdjustment.KIND_INCREASE)),
        fin_in_pause=Exists(_pauses_covering(OuterRef('due_date'), OuterRef('rule'))),
        fin_adjusted=Exists(ObligationAdjustment.objects.filter(obligation=OuterRef('pk'))),
    )


def is_paused(obligation, numbers=None) -> bool:
    if (obligation.lifecycle != Obligation.LIFECYCLE_OPEN or obligation.rule_id is None
            or obligation.due_date is None or obligation.owed_despite_pause_at is not None):
        return False
    numbers = numbers or figures(obligation)
    # A pause never hides money: a period with a payment that stands, or with
    # any adjustment, is owed whatever the pause says.
    if numbers['paid'] > 0:
        return False
    if hasattr(obligation, 'fin_in_pause'):
        return bool(obligation.fin_in_pause) and not obligation.fin_adjusted
    if obligation.adjustments.exists():
        return False
    return _pauses_covering(obligation.due_date, obligation.rule_id).exists()


def figures(obligation) -> dict:
    """What is owed on `obligation`, computed from its rows (or its annotations)."""
    if obligation.origin_type == Obligation.ORIGIN_REPAIR:
        raise FinanceError('El saldo de una reparación se calcula en su orden (FIN-E).')
    if hasattr(obligation, 'fin_paid'):
        paid, increase, decrease = obligation.fin_paid, obligation.fin_increase, obligation.fin_decrease
    else:
        paid = obligation.payments.filter(reversed_at__isnull=True).aggregate(t=Sum('amount'))['t'] or ZERO
        adjustments = obligation.adjustments.all()
        increase = adjustments.filter(kind=ObligationAdjustment.KIND_INCREASE).aggregate(t=Sum('amount'))['t'] or ZERO
        decrease = adjustments.exclude(kind=ObligationAdjustment.KIND_INCREASE).aggregate(t=Sum('amount'))['t'] or ZERO
    effective = (obligation.amount_original + increase - decrease).quantize(CENT)
    paid = Decimal(paid).quantize(CENT)
    balance = (effective - paid).quantize(CENT)
    return {
        'effective': effective, 'paid': paid, 'balance': balance,
        'has_payments': paid > 0, 'partially_paid': paid > 0 and balance > 0,
    }


def state(obligation, *, today: date, figures_=None) -> str:
    if obligation.lifecycle == Obligation.LIFECYCLE_VOIDED:
        return STATE_VOIDED
    numbers = figures_ or figures(obligation)
    if obligation.lifecycle == Obligation.LIFECYCLE_SETTLED or numbers['balance'] <= 0:
        return STATE_PAID
    if is_paused(obligation, numbers):
        return STATE_PAUSED
    due = obligation.due_date
    if due is None:
        return STATE_PENDING
    if due < today:
        return STATE_OVERDUE
    if due == today:
        return STATE_DUE_TODAY
    if due <= today + timedelta(days=DUE_SOON_DAYS):
        return STATE_DUE_SOON
    return STATE_PENDING


def totals(obligations, *, today: date) -> dict:
    """Open balances per currency, and how much of it is overdue, due today, due soon. Never mixed."""
    buckets = {name: {} for name in ('open', STATE_OVERDUE, STATE_DUE_TODAY, STATE_DUE_SOON)}
    counts = {name: 0 for name in buckets}
    for obligation in with_figures(obligations.filter(lifecycle=Obligation.LIFECYCLE_OPEN)
                                   .exclude(origin_type=Obligation.ORIGIN_REPAIR)):
        numbers = figures(obligation)
        if numbers['balance'] <= 0:
            continue
        current = state(obligation, today=today, figures_=numbers)
        if current == STATE_PAUSED:
            continue
        for name in ('open', current):
            if name in buckets:
                bucket = buckets[name]
                bucket[obligation.currency] = bucket.get(obligation.currency, ZERO) + numbers['balance']
                counts[name] += 1
    return {
        **{name: {cur: str(amount.quantize(CENT)) for cur, amount in sorted(bucket.items())}
           for name, bucket in buckets.items()},
        'counts': counts,
    }


def filter_state(queryset, code: str, *, today: date):
    """Narrow an annotated queryset to one derived state, in the database."""
    balance_positive = Q(fin_balance__gt=0)
    queryset = queryset.annotate(
        fin_balance=Coalesce('amount_original', Value(ZERO)) + Coalesce('fin_increase', Value(ZERO))
        - Coalesce('fin_decrease', Value(ZERO)) - Coalesce('fin_paid', Value(ZERO)),
    )
    paused = (Q(lifecycle=Obligation.LIFECYCLE_OPEN, rule__isnull=False, due_date__isnull=False,
                owed_despite_pause_at__isnull=True, fin_paid=0, fin_in_pause=True, fin_adjusted=False))
    is_open = Q(lifecycle=Obligation.LIFECYCLE_OPEN) & balance_positive & ~paused
    soon = today + timedelta(days=DUE_SOON_DAYS)
    conditions = {
        STATE_VOIDED: Q(lifecycle=Obligation.LIFECYCLE_VOIDED),
        STATE_PAID: Q(lifecycle=Obligation.LIFECYCLE_SETTLED),
        STATE_OVERDUE: is_open & Q(due_date__lt=today),
        STATE_DUE_TODAY: is_open & Q(due_date=today),
        STATE_DUE_SOON: is_open & Q(due_date__gt=today, due_date__lte=soon),
        STATE_PENDING: is_open & (Q(due_date__isnull=True) | Q(due_date__gt=soon)),
        STATE_PAUSED: paused & balance_positive,
        'open': is_open,
    }
    if code not in conditions:
        raise FinanceError('Estado inválido.')
    return queryset.filter(conditions[code])


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

def create_category(company, *, direction, name, actor, request=None) -> FinanceCategory:
    if direction not in FinanceDirection.values:
        raise FinanceError('Tipo de categoría inválido.')
    label = _text(name)
    if not label or len(label) > 80:
        raise FinanceError('Escribe un nombre de hasta 80 caracteres.')
    try:
        with transaction.atomic():
            category = FinanceCategory.objects.create(company=company, direction=direction, name=label,
                                                      created_by=actor)
    except IntegrityError:
        raise FinanceConflict('Ya existe una categoría con ese nombre.') from None
    _audit(actor=actor, action='finance_category_created', target_type=AUDIT_CATEGORY, target_id=category.pk,
           company=company, metadata={'direction': direction}, request=request)
    return category


def update_category(category, *, actor, name=None, is_active=None, request=None) -> FinanceCategory:
    changed = []
    if name is not None:
        label = _text(name)
        if not label or len(label) > 80:
            raise FinanceError('Escribe un nombre de hasta 80 caracteres.')
        category.name = label
        changed.append('name')
    if is_active is not None:
        category.is_active = bool(is_active)
        changed.append('is_active')
    if not changed:
        return category
    try:
        with transaction.atomic():
            category.save(update_fields=[*changed, 'updated_at'])
    except IntegrityError:
        raise FinanceConflict('Ya existe una categoría con ese nombre.') from None
    _audit(actor=actor, action='finance_category_updated', target_type=AUDIT_CATEGORY, target_id=category.pk,
           company=category.company, metadata={'fields': changed}, request=request)
    return category


# ---------------------------------------------------------------------------
# Rules and their periods
# ---------------------------------------------------------------------------

_FREQUENCIES = {code for code, _label in RecurringObligationRule.FREQUENCY_CHOICES}


def _validate_calendar(frequency, anchor_date, due_day, end_date):
    if not isinstance(frequency, str) or frequency not in _FREQUENCIES:
        raise FinanceError('Frecuencia inválida.')
    if not isinstance(anchor_date, date):
        raise FinanceError('Fecha de inicio inválida.')
    try:
        day = int(due_day)
    except (TypeError, ValueError):
        raise FinanceError('Día de vencimiento inválido.') from None
    if frequency == calendar.WEEKLY and not 0 <= day <= 6:
        raise FinanceError('En una regla semanal el día va de 0 (lunes) a 6 (domingo).')
    if frequency != calendar.WEEKLY and not 1 <= day <= 31:
        raise FinanceError('El día de vencimiento va del 1 al 31.')
    if end_date is not None and end_date < anchor_date:
        raise FinanceError('La fecha de fin no puede ser anterior al inicio.')
    return day


def _first_index_from(rule_like, today: date) -> int:
    index = calendar.index_on(rule_like.frequency, rule_like.anchor_date, rule_like.due_day, today)
    while calendar.period(rule_like.frequency, rule_like.anchor_date, rule_like.due_day, index).due < today:
        index += 1
    return index


def _in_pause(due: date, pauses) -> bool:
    # Strictly after the pause began, as `is_paused` reads it: a period due on
    # the day of the pause was due, and stays owed.
    return any(p.paused_from < due and (p.resumed_on is None or due < p.resumed_on) for p in pauses)


def _create_period(rule, current) -> Obligation | None:
    """INSERT one period in its own savepoint. None when another generator won."""
    try:
        with transaction.atomic():
            return Obligation.objects.create(
                company_id=rule.company_id, branch_id=rule.branch_id,
                direction=FinanceDirection.PAYABLE, origin_type=Obligation.ORIGIN_FIXED,
                rule=rule, period_index=current.index, period_start=current.start,
                supplier_id=rule.payee_supplier_id, payee_name=rule.payee_name, category_id=rule.category_id,
                concept=rule.concept, owner_id=rule.owner_id, planned_method=rule.planned_method,
                reference=_allocate_reference(rule.company), external_reference=rule.reference,
                amount_original=rule.amount, currency=rule.currency,
                issue_date=current.start, due_date=current.due,
            )
    except IntegrityError:
        return None


def generate_periods(rule, *, today: date | None = None) -> list[Obligation]:
    """Create the periods due up to the horizon. Idempotent; safe with two workers."""
    with transaction.atomic():
        locked = RecurringObligationRule.objects.select_for_update().select_related('company').get(pk=rule.pk)
        today = today or company_today(locked.company)
        existing = set(locked.periods.values_list('period_index', flat=True))
        start = locked.generate_from_index
        if existing:
            start = max(start, max(existing) + 1)
        pauses = list(locked.pauses.all())
        created = []
        for current in calendar.periods_until(
            locked.frequency, locked.anchor_date, locked.due_day, start_index=start,
            horizon=today + timedelta(days=HORIZON_DAYS), end_date=locked.end_date,
        ):
            if current.index in existing or _in_pause(current.due, pauses):
                continue
            period = _create_period(locked, current)
            if period is not None:
                created.append(period)
        return created


def generate_all(*, company=None, today: date | None = None) -> int:
    """The cron pass: every rule of every company (or of one), in its local date."""
    rules = RecurringObligationRule.objects.select_related('company').order_by('company_id', 'pk')
    if company is not None:
        rules = rules.filter(company=company)
    created = 0
    for rule in rules.iterator():
        # One rule that fails (bad data, a lock timeout) must not stop every
        # other company's calendar: each rule has its own transaction, the
        # failure is logged, and the pass goes on.
        try:
            created += len(generate_periods(rule, today=today or company_today(rule.company)))
        except Exception:  # noqa: BLE001 — logged, the next rule still runs
            logger.exception('finanzas: no se pudieron generar los períodos de la regla %s', rule.pk)
    return created


def create_rule(company, *, actor, concept, amount, frequency, anchor_date, due_day, branch=None, currency=None,
                end_date=None, category=None, payee_supplier=None, payee_name='', owner=None, planned_method='',
                reference='', notes='', generate_past=False, today: date | None = None,
                request=None) -> RecurringObligationRule:
    title = _text(concept)
    if not title or len(title) > 140:
        raise FinanceError('Escribe el concepto (hasta 140 caracteres).')
    value = _positive(amount)
    day = _validate_calendar(frequency, anchor_date, due_day, end_date)
    _same_company(company, branch, 'La sucursal')
    _category(company, category, FinanceDirection.PAYABLE)
    _same_company(company, payee_supplier, 'El proveedor')
    rule = RecurringObligationRule(
        company=company, branch=branch, concept=title, category=category, payee_supplier=payee_supplier,
        payee_name=_text(payee_name)[:140], amount=value, currency=_currency(company, currency),
        frequency=frequency, anchor_date=anchor_date, due_day=day, end_date=end_date,
        owner=_owner(company, owner), planned_method=_method(planned_method, allow_blank=True),
        reference=_text(reference)[:80], notes=_text(notes)[:1000], created_by=actor,
    )
    today = today or company_today(company)
    if generate_past and (today - anchor_date).days > PAST_LIMIT_DAYS:
        raise FinanceError('Los períodos anteriores se generan como máximo 24 meses hacia atrás: '
                           'usa una fecha de inicio más reciente.')
    rule.generate_from_index = 0 if generate_past else _first_index_from(rule, today)
    with transaction.atomic():
        rule.save()
        _audit(actor=actor, action='finance_rule_created', target_type=AUDIT_RULE, target_id=rule.pk,
               company=company, request=request,
               metadata={'frequency': frequency, 'generate_past': bool(generate_past),
                         'generate_from_index': rule.generate_from_index})
        generate_periods(rule, today=today)
    return rule


_RULE_EDITABLE = {'concept', 'amount', 'category', 'payee_supplier', 'payee_name', 'owner', 'planned_method',
                  'reference', 'notes'}
_RULE_CALENDAR = {'frequency', 'anchor_date', 'due_day', 'currency'}


def update_rule(rule, *, actor, request=None, **fields) -> RecurringObligationRule:
    unknown = set(fields) - _RULE_EDITABLE - _RULE_CALENDAR
    if unknown:
        raise FinanceError(f'Campos no editables: {", ".join(sorted(unknown))}.')
    with transaction.atomic():
        locked = RecurringObligationRule.objects.select_for_update().select_related('company').get(pk=rule.pk)
        company = locked.company
        if set(fields) & _RULE_CALENDAR and locked.periods.exists():
            raise FinanceError('El calendario y la moneda no cambian después del primer período: '
                               'termina esta regla y crea otra.')
        changed = []
        for name, value in fields.items():
            if name == 'concept':
                value = _text(value)
                if not value or len(value) > 140:
                    raise FinanceError('Escribe el concepto (hasta 140 caracteres).')
            elif name == 'amount':
                value = _positive(value)
            elif name == 'category':
                value = _category(company, value, FinanceDirection.PAYABLE)
            elif name == 'payee_supplier':
                value = _same_company(company, value, 'El proveedor')
            elif name == 'owner':
                value = _owner(company, value)
            elif name == 'planned_method':
                value = _method(value, allow_blank=True)
            elif name == 'currency':
                value = _currency(company, value)
            elif name in ('payee_name', 'reference', 'notes'):
                value = _text(value)
            setattr(locked, name, value)
            changed.append(name)
        if {'frequency', 'anchor_date', 'due_day'} & set(changed):
            _validate_calendar(locked.frequency, locked.anchor_date, locked.due_day, locked.end_date)
        if changed:
            locked.save(update_fields=[*changed, 'updated_at'])
            _audit(actor=actor, action='finance_rule_updated', target_type=AUDIT_RULE, target_id=locked.pk,
                   company=company, metadata={'fields': sorted(changed)}, request=request)
    return locked


def _void_never_due(locked_rule, *, after: date, reason: str, actor, request) -> list[int]:
    """Void the rule's pre-generated, unpaid periods due after `after`. Returns the kept ones."""
    kept = []
    for period in locked_rule.periods.filter(lifecycle=Obligation.LIFECYCLE_OPEN, due_date__gt=after):
        locked = Obligation.objects.select_for_update().get(pk=period.pk)
        if locked.payments.filter(reversed_at__isnull=True).exists():
            kept.append(locked.pk)
            continue
        _void_locked(locked, reason=reason, kind=Obligation.VOID_NEVER_DUE, actor=actor, request=request)
    return kept


def pause_rule(rule, *, reason, actor, today: date | None = None, request=None) -> RulePause:
    text = _reason(reason)
    with transaction.atomic():
        locked = RecurringObligationRule.objects.select_for_update().select_related('company').get(pk=rule.pk)
        if locked.ended_at is not None:
            raise FinanceError('La regla ya terminó.')
        if locked.pauses.filter(resumed_on__isnull=True).exists():
            raise FinanceConflict('La regla ya está pausada.')
        today = today or company_today(locked.company)
        # NOTHING IS VOIDED. A pre-generated period due inside the pause is
        # skipped by its derived state (`is_paused`), so resuming earlier — or
        # including it by hand — makes it owed again; one with a payment, or
        # already due, stays owed whatever the pause says.
        pause = RulePause.objects.create(rule=locked, paused_from=today, reason=text, paused_by=actor)
        _audit(actor=actor, action='finance_rule_paused', target_type=AUDIT_RULE, target_id=locked.pk,
               company=locked.company, metadata={'paused_from': today.isoformat()}, request=request)
    return pause


def resume_rule(rule, *, actor, today: date | None = None, request=None) -> RulePause:
    with transaction.atomic():
        locked = RecurringObligationRule.objects.select_for_update().select_related('company').get(pk=rule.pk)
        pause = locked.pauses.select_for_update().filter(resumed_on__isnull=True).first()
        if pause is None:
            raise FinanceConflict('La regla no está pausada.')
        today = today or company_today(locked.company)
        pause.resumed_on = max(today, pause.paused_from)
        pause.resumed_by = actor
        pause.save(update_fields=['resumed_on', 'resumed_by'])
        _audit(actor=actor, action='finance_rule_resumed', target_type=AUDIT_RULE, target_id=locked.pk,
               company=locked.company, metadata={'resumed_on': pause.resumed_on.isoformat(), 'backfill': False},
               request=request)
    return pause


def end_rule(rule, *, end_date, reason, actor, today: date | None = None, request=None) -> RecurringObligationRule:
    """
    End the rule. Only pre-generated periods that are NOT YET DUE (due after both
    the end and today) are voided as never due: a backdated end never voids an
    overdue period — that is a void, with `finance.void` and its own reason.
    """
    text = _reason(reason)
    if not isinstance(end_date, date):
        raise FinanceError('Fecha de fin inválida.')
    with transaction.atomic():
        locked = RecurringObligationRule.objects.select_for_update().select_related('company').get(pk=rule.pk)
        if locked.ended_at is not None:
            raise FinanceConflict('La regla ya terminó.')
        if end_date < locked.anchor_date:
            raise FinanceError('La fecha de fin no puede ser anterior al inicio.')
        today = today or company_today(locked.company)
        locked.end_date, locked.ended_at, locked.end_reason = end_date, timezone.now(), text
        locked.save(update_fields=['end_date', 'ended_at', 'end_reason', 'updated_at'])
        kept = _void_never_due(locked, after=max(end_date, today), reason='Regla terminada antes de su vencimiento',
                               actor=actor, request=request)
        _audit(actor=actor, action='finance_rule_ended', target_type=AUDIT_RULE, target_id=locked.pk,
               company=locked.company, metadata={'end_date': end_date.isoformat(), 'kept_with_payments': kept},
               request=request)
    return locked


def generate_period(rule, *, index, actor, today: date | None = None, request=None) -> Obligation:
    """«Generar este período», by hand: one a pause skipped, for instance."""
    try:
        index = int(index)
    except (TypeError, ValueError):
        raise FinanceError('Período inválido.') from None
    if not 0 <= index <= MAX_PERIOD_INDEX:
        raise FinanceError('Período inválido.')
    with transaction.atomic():
        locked = RecurringObligationRule.objects.select_for_update().select_related('company').get(pk=rule.pk)
        today = today or company_today(locked.company)
        try:
            current = calendar.period(locked.frequency, locked.anchor_date, locked.due_day, index)
        except (ValueError, OverflowError):
            raise FinanceError('Período inválido.') from None
        if locked.end_date is not None and current.due > locked.end_date:
            raise FinanceError('Ese período cae después del fin de la regla.')
        if current.due > today + timedelta(days=HORIZON_DAYS):
            raise FinanceError('Ese período todavía no se puede generar.')
        existing = locked.periods.filter(period_index=index).first()
        if existing is not None:
            # A period a pause skips is INCLUDED (owed despite the pause); any
            # other existing period is a conflict: it is already there.
            existing = Obligation.objects.select_for_update().get(pk=existing.pk)
            if not is_paused(existing):
                raise FinanceConflict('Ese período ya existe.')
            existing.owed_despite_pause_at, existing.owed_despite_pause_by = timezone.now(), actor
            existing.save(update_fields=['owed_despite_pause_at', 'owed_despite_pause_by'])
            _audit(actor=actor, action='finance_period_included', target_type=AUDIT_OBLIGATION,
                   target_id=existing.pk, company=locked.company, request=request,
                   metadata={'rule_id': locked.pk, 'period_index': index})
            return existing
        if current.due < today - timedelta(days=PAST_LIMIT_DAYS):
            raise FinanceError('Ese período es demasiado antiguo para generarlo.')
        period = _create_period(locked, current)
        if period is None:
            raise FinanceConflict('Ese período ya existe.')
        if _in_pause(current.due, list(locked.pauses.all())):
            period.owed_despite_pause_at, period.owed_despite_pause_by = timezone.now(), actor
            period.save(update_fields=['owed_despite_pause_at', 'owed_despite_pause_by'])
        _audit(actor=actor, action='finance_period_generated', target_type=AUDIT_OBLIGATION, target_id=period.pk,
               company=locked.company, metadata={'rule_id': locked.pk, 'period_index': index}, request=request)
    return period


# ---------------------------------------------------------------------------
# Manual obligations (one-off payables)
# ---------------------------------------------------------------------------

def create_obligation(company, *, actor, concept, amount, due_date, branch=None, currency=None, category=None,
                      supplier=None, payee_name='', owner=None, planned_method='', external_reference='', notes='',
                      issue_date=None, idempotency_key='', today: date | None = None, request=None) -> Obligation:
    """
    A one-off payable. Receivables by hand need a customer and their own
    capability (`finance.receivables.manual`) and arrive with FIN-E.
    """
    title = _text(concept)
    if not title or len(title) > 140:
        raise FinanceError('Escribe el concepto (hasta 140 caracteres).')
    value = _positive(amount)
    if not isinstance(due_date, date):
        raise FinanceError('Fecha de vencimiento inválida.')
    today = today or company_today(company)
    issued = issue_date or today
    if not isinstance(issued, date):
        raise FinanceError('Fecha de emisión inválida.')
    code = _currency(company, currency)
    key = _key(idempotency_key)
    fingerprint = _fingerprint(company=company.pk, concept=title, amount=value, due=due_date, currency=code,
                               branch=getattr(branch, 'pk', None), supplier=getattr(supplier, 'pk', None))
    if key:
        existing = Obligation.objects.filter(company=company, idempotency_key=key).first()
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise FinanceConflict('Esa clave ya se usó para otra obligación.')
            return existing
    _same_company(company, branch, 'La sucursal')
    _same_company(company, supplier, 'El proveedor')
    obligation = Obligation(
        company=company, branch=branch, direction=FinanceDirection.PAYABLE, origin_type=Obligation.ORIGIN_MANUAL,
        origin_key=f'manual:{uuid.uuid4().hex}', supplier=supplier, payee_name=_text(payee_name)[:140],
        category=_category(company, category, FinanceDirection.PAYABLE), concept=title,
        owner=_owner(company, owner), planned_method=_method(planned_method, allow_blank=True),
        external_reference=_text(external_reference)[:80], notes=_text(notes)[:1000],
        amount_original=value, currency=code, issue_date=issued, due_date=due_date, created_by=actor,
        idempotency_key=key, request_fingerprint=fingerprint if key else '',
    )
    try:
        with transaction.atomic():
            obligation.reference = _allocate_reference(company)
            obligation.save()
            _audit(actor=actor, action='finance_obligation_created', target_type=AUDIT_OBLIGATION,
                   target_id=obligation.pk, company=company, request=request,
                   metadata={'origin_type': obligation.origin_type, 'amount': str(value), 'currency': code})
    except IntegrityError:
        winner = Obligation.objects.filter(company=company, idempotency_key=key).first() if key else None
        if winner is None:
            raise
        if winner.request_fingerprint != fingerprint:
            raise FinanceConflict('Esa clave ya se usó para otra obligación.') from None
        return winner
    return obligation


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------

def _payment_fingerprint(obligation_id, amount, method, reference, paid_on) -> str:
    return _fingerprint(obligation=obligation_id, amount=amount, method=method, reference=reference,
                        paid_on=paid_on)


def record_payment(obligation, *, amount, method, actor, paid_on: date | None = None, reference='', notes='',
                   idempotency_key='', request=None) -> ObligationPayment:
    key = _key(idempotency_key)
    try:
        return _record_payment(obligation, amount=amount, method=method, actor=actor, paid_on=paid_on,
                               reference=reference, notes=notes, key=key, request=request)
    except _RaceLost:
        winner = ObligationPayment.objects.filter(company_id=obligation.company_id, idempotency_key=key).first()
        if winner is None:
            raise FinanceError('No se pudo registrar el pago.') from None
        expected = _payment_fingerprint(obligation.pk, _money(amount), _text(method),
                                        _text(reference)[:80], paid_on)
        if winner.obligation_id != obligation.pk or winner.request_fingerprint != expected:
            raise FinanceConflict('Esa clave ya se usó para un pago diferente.') from None
        return winner


@transaction.atomic
def _record_payment(obligation, *, amount, method, actor, paid_on, reference, notes, key, request):
    locked = Obligation.objects.select_for_update().select_related('company').get(pk=obligation.pk)
    company = locked.company
    today = company_today(company)
    method = _text(method)
    reference = _text(reference)[:80]
    value = _money(amount)
    # The fingerprint takes the date AS SENT (or none): a retry tomorrow of a
    # request that let the server pick today's date is the same attempt.
    fingerprint = _payment_fingerprint(locked.pk, value, method, reference, paid_on)
    paid_on = paid_on or today

    # Idempotency FIRST: a replay is answered from the key, never re-judged
    # against a balance that is smaller BECAUSE of it.
    if key:
        existing = ObligationPayment.objects.filter(company=company, idempotency_key=key).first()
        if existing is not None:
            if existing.obligation_id != locked.pk or existing.request_fingerprint != fingerprint:
                raise FinanceConflict('Esa clave ya se usó para un pago diferente.')
            return existing

    if locked.origin_type == Obligation.ORIGIN_REPAIR:
        raise FinanceError('El cobro de una reparación se registra en su orden.')
    _method(method)
    if value <= 0:
        raise FinanceError('Un pago debe ser mayor que cero.')
    if looks_like_card_number(reference):
        raise FinanceError('La referencia no puede contener un número de tarjeta.')
    if not isinstance(paid_on, date) or paid_on > today:
        raise FinanceError('La fecha del pago no puede ser futura.')
    if locked.lifecycle == Obligation.LIFECYCLE_VOIDED:
        raise FinanceError('La obligación está anulada.')
    if locked.lifecycle == Obligation.LIFECYCLE_SETTLED:
        raise FinanceError('La obligación ya está pagada.')
    numbers = figures(locked)
    # NO SILENT OVERPAYMENT (owner's P6, provisional): partial payments are the
    # point of a ledger; more than what is owed is a typo or a credit nobody
    # decided to keep.
    if value > numbers['balance']:
        raise FinanceError(f'El saldo pendiente es {numbers["balance"]} {locked.currency}.')

    payment = ObligationPayment(
        company=company, obligation=locked, amount=value, currency=locked.currency, method=method,
        paid_on=paid_on, reference=reference, notes=_text(notes)[:1000], recorded_by=actor,
        idempotency_key=key, request_fingerprint=fingerprint if key else '',
    )
    try:
        with transaction.atomic():
            payment.save()
    except IntegrityError:
        if not key:
            raise
        raise _RaceLost() from None

    settled = numbers['balance'] - value == 0
    if settled:
        locked.lifecycle, locked.settled_at = Obligation.LIFECYCLE_SETTLED, timezone.now()
        locked.save(update_fields=['lifecycle', 'settled_at'])
    _audit(actor=actor, action='finance_payment_recorded', target_type=AUDIT_OBLIGATION, target_id=locked.pk,
           company=company, request=request,
           metadata={'payment_id': payment.pk, 'amount': str(value), 'currency': locked.currency,
                     'method': method, 'settled': settled})
    return payment


def reverse_payment(payment, *, reason, actor, request=None) -> ObligationPayment:
    """Mark a payment as recorded in error. Idempotent. Not a refund: no money moves."""
    text = _reason(reason)
    with transaction.atomic():
        # The OBLIGATION first, then the payment: the lock order of every
        # writer here, so a reversal cannot interleave with a payment.
        obligation = Obligation.objects.select_for_update().select_related('company').get(pk=payment.obligation_id)
        locked = ObligationPayment.objects.select_for_update().get(pk=payment.pk)
        if locked.is_reversed:
            return locked
        locked.reversed_at, locked.reversed_by, locked.reversal_reason = timezone.now(), actor, text
        locked.save(update_fields=['reversed_at', 'reversed_by', 'reversal_reason'])
        if obligation.lifecycle == Obligation.LIFECYCLE_SETTLED:
            obligation.lifecycle, obligation.settled_at = Obligation.LIFECYCLE_OPEN, None
            obligation.save(update_fields=['lifecycle', 'settled_at'])
        _audit(actor=actor, action='finance_payment_reversed', target_type=AUDIT_OBLIGATION,
               target_id=obligation.pk, company=obligation.company, request=request,
               metadata={'payment_id': locked.pk, 'amount': str(locked.amount), 'currency': locked.currency})
    return locked


# ---------------------------------------------------------------------------
# Adjustments, void, dispute
# ---------------------------------------------------------------------------

ADJUST_KINDS = ('increase', 'decrease', 'write_off', 'amount_change')


def adjust(obligation, *, kind, reason, actor, amount=None, new_amount=None, idempotency_key='',
           request=None) -> ObligationAdjustment:
    if kind not in ADJUST_KINDS:
        raise FinanceError('Tipo de ajuste inválido.')
    text = _reason(reason)
    key = _key(idempotency_key)
    with transaction.atomic():
        locked = Obligation.objects.select_for_update().select_related('company').get(pk=obligation.pk)
        company = locked.company
        value = _positive(new_amount if kind == 'amount_change' else amount,
                          field='El nuevo importe' if kind == 'amount_change' else 'Importe')
        fingerprint = _fingerprint(obligation=locked.pk, kind=kind, value=value, reason=text)
        if key:
            existing = ObligationAdjustment.objects.filter(company=company, idempotency_key=key).first()
            if existing is not None:
                if existing.obligation_id != locked.pk or existing.request_fingerprint != fingerprint:
                    raise FinanceConflict('Esa clave ya se usó para otro ajuste.')
                return existing
        if locked.origin_type == Obligation.ORIGIN_REPAIR:
            raise FinanceError('El importe de una reparación se corrige en su cotización.')
        if locked.lifecycle == Obligation.LIFECYCLE_VOIDED:
            raise FinanceError('La obligación está anulada.')
        numbers = figures(locked)
        if kind == 'amount_change':
            if locked.origin_type != Obligation.ORIGIN_FIXED:
                raise FinanceError('Sólo el período de un pago fijo cambia de importe así.')
            delta = value - numbers['effective']
            if delta == 0:
                raise FinanceError('Ese ya es el importe del período.')
            stored_kind = ObligationAdjustment.KIND_INCREASE if delta > 0 else ObligationAdjustment.KIND_DECREASE
            stored_amount, new_effective = abs(delta), value
        else:
            if kind == 'write_off' and locked.direction != FinanceDirection.RECEIVABLE:
                raise FinanceError('Sólo una cuenta por cobrar se castiga.')
            if kind != 'increase' and locked.lifecycle != Obligation.LIFECYCLE_OPEN:
                raise FinanceError('La obligación ya está pagada.')
            stored_kind, stored_amount = kind, value
            sign = 1 if kind == 'increase' else -1
            new_effective = numbers['effective'] + sign * value
        if new_effective < numbers['paid']:
            raise FinanceError(f'Ya se pagó {numbers["paid"]} {locked.currency}: lo debido no puede ser menor.')
        if new_effective <= 0:
            raise FinanceError('Lo debido no puede quedar en cero: anula la obligación con su motivo.')
        row = ObligationAdjustment(
            company=company, obligation=locked, kind=stored_kind, amount=stored_amount,
            is_amount_change=kind == 'amount_change', previous_effective=numbers['effective'],
            new_effective=new_effective, reason=text, actor=actor, idempotency_key=key,
            request_fingerprint=fingerprint if key else '',
        )
        try:
            with transaction.atomic():
                row.save()
        except IntegrityError:
            winner = ObligationAdjustment.objects.filter(company=company, idempotency_key=key).first() if key else None
            if winner is not None and winner.obligation_id == locked.pk and winner.request_fingerprint == fingerprint:
                return winner
            raise FinanceConflict('Esa clave ya se usó para otro ajuste.') from None
        balance = new_effective - numbers['paid']
        target = Obligation.LIFECYCLE_SETTLED if balance == 0 else Obligation.LIFECYCLE_OPEN
        if target != locked.lifecycle:
            locked.lifecycle = target
            locked.settled_at = timezone.now() if target == Obligation.LIFECYCLE_SETTLED else None
            locked.save(update_fields=['lifecycle', 'settled_at'])
        _audit(actor=actor, action='finance_obligation_adjusted', target_type=AUDIT_OBLIGATION,
               target_id=locked.pk, company=company, request=request,
               metadata={'adjustment_id': row.pk, 'kind': kind, 'amount': str(stored_amount),
                         'previous': str(numbers['effective']), 'new': str(new_effective)})
    return row


def _void_locked(locked, *, reason, kind, actor, request):
    locked.lifecycle = Obligation.LIFECYCLE_VOIDED
    locked.voided_at, locked.voided_by, locked.void_reason, locked.void_kind = timezone.now(), actor, reason, kind
    locked.save(update_fields=['lifecycle', 'voided_at', 'voided_by', 'void_reason', 'void_kind'])
    _audit(actor=actor, action='finance_obligation_voided', target_type=AUDIT_OBLIGATION, target_id=locked.pk,
           company=locked.company, request=request, metadata={'kind': kind, 'reason': reason})


def void(obligation, *, reason, actor, request=None) -> Obligation:
    text = _reason(reason)
    with transaction.atomic():
        locked = Obligation.objects.select_for_update().select_related('company').get(pk=obligation.pk)
        if locked.lifecycle == Obligation.LIFECYCLE_VOIDED:
            return locked
        if locked.origin_type == Obligation.ORIGIN_REPAIR:
            raise FinanceError('Una reparación se cancela en su orden.')
        # Owner's P7 (provisional): confirmed money is reversed first, on purpose.
        if locked.payments.filter(reversed_at__isnull=True).exists():
            raise FinanceConflict('Tiene pagos confirmados: revértelos antes de anular.')
        _void_locked(locked, reason=text, kind=Obligation.VOID_MANUAL, actor=actor, request=request)
    return locked


def mark_disputed(obligation, *, reason, actor, request=None) -> Obligation:
    text = _reason(reason)
    with transaction.atomic():
        locked = Obligation.objects.select_for_update().select_related('company').get(pk=obligation.pk)
        if locked.lifecycle != Obligation.LIFECYCLE_OPEN:
            raise FinanceError('Sólo una obligación abierta puede estar en disputa.')
        if locked.is_disputed:
            raise FinanceConflict('Ya está en disputa.')
        locked.disputed_at, locked.disputed_by, locked.dispute_reason = timezone.now(), actor, text
        locked.dispute_resolved_at, locked.dispute_resolved_by, locked.dispute_resolution = None, None, ''
        locked.save(update_fields=['disputed_at', 'disputed_by', 'dispute_reason', 'dispute_resolved_at',
                                   'dispute_resolved_by', 'dispute_resolution'])
        _audit(actor=actor, action='finance_obligation_disputed', target_type=AUDIT_OBLIGATION,
               target_id=locked.pk, company=locked.company, request=request, metadata={'reason': text})
    return locked


def resolve_dispute(obligation, *, resolution, actor, request=None) -> Obligation:
    text = _reason(resolution, what='cómo se resolvió')
    with transaction.atomic():
        locked = Obligation.objects.select_for_update().select_related('company').get(pk=obligation.pk)
        if not locked.is_disputed:
            raise FinanceConflict('No está en disputa.')
        locked.dispute_resolved_at, locked.dispute_resolved_by = timezone.now(), actor
        locked.dispute_resolution = text
        locked.save(update_fields=['dispute_resolved_at', 'dispute_resolved_by', 'dispute_resolution'])
        _audit(actor=actor, action='finance_obligation_dispute_resolved', target_type=AUDIT_OBLIGATION,
               target_id=locked.pk, company=locked.company, request=request, metadata={'resolution': text})
    return locked


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------

def visible(queryset, user, company):
    """Rows of `company` the caller may see: their branches, and general rows only with company-wide scope."""
    from ..tenancy import has_company_wide_scope, visible_branch_ids

    queryset = queryset.filter(company=company)
    if has_company_wide_scope(user, company):
        return queryset
    return queryset.filter(branch_id__in=visible_branch_ids(user, company))

