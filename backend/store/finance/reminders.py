"""
FIN-REMIND-01 — internal reminders of fixed payments (docs/adr-finance-obligations.md §7.1–7.2).

The question this module answers is WHEN and TO WHOM; it never touches money. A reminder is a
`Notification` made through `notification_services.emit`: reading, snoozing or dismissing one writes
`read_at` / a snooze row and nothing else. No payment is registered, no state changed, no obligation closed.

  * ONE EVENT PER (obligation, schedule point, due date). `reminder_key` is the idempotency key of
    `NotificationEvent`, which is UNIQUE: a second run, a retried cron or two runs at once cannot repeat a notice.
  * AFTER A GAP, THE LATEST POINT ONLY. If the cron slept, the points that passed in between are not replayed:
    that would be a burst of the same message. Overdue notices are paced (`overdue_every_days`) and capped
    (`overdue_max`) so a forgotten bill does not become spam.
  * ONLY WHAT IS OWED: open, unpaused payables with a positive balance. A dispute still reminds.
  * RECIPIENTS ARE THOSE WHO MAY SEE THE PAYMENT: `finance.view`, in this company, with the obligation's branch
    (a general one needs company-wide scope). The body carries the amount, so nobody without that authority
    gets it — the obligation's owner included. The platform master is never addressed.
  * COMPANY-LOCAL DATE: «hoy» is `company_today(company)`.
  * E-MAIL IS A SECOND, OPT-IN CHANNEL, after commit, and its failure is a `NotificationDelivery` row, never a lost notice.
  * NO WHATSAPP: that channel only serves customers' repair notices.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .. import notification_events as events
from .. import notification_services as notices
from ..models import (
    FinanceDirection, FinanceReminderPolicy, Notification, Obligation, ObligationReminderSnooze,
)
from ..tenancy import has_branch_access, has_company_wide_scope
from . import services

logger = logging.getLogger(__name__)

CAP_VIEW = 'finance.view'
TARGET = 'finance_obligation'
MAX_SNOOZE_DAYS = 30
#: States in which a payment is still being waited for. Paused, paid, voided and cancelled-origin ones are not.
_REMINDABLE = (services.STATE_PENDING, services.STATE_DUE_SOON, services.STATE_DUE_TODAY, services.STATE_OVERDUE)

_SYMBOLS = {'PEN': 'S/', 'USD': 'US$', 'EUR': '€'}


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

def policy_for(company) -> FinanceReminderPolicy:
    policy, _ = FinanceReminderPolicy.objects.get_or_create(company=company)
    return policy


def _clean_offsets(value, *, field, allow_empty=False) -> list[int]:
    if not isinstance(value, (list, tuple)) or any(isinstance(v, bool) or not isinstance(v, int) for v in value):
        raise services.FinanceError(f'{field}: indica una lista de días.')
    cleaned = sorted({int(v) for v in value}, reverse=True)
    if not allow_empty and not cleaned:
        raise services.FinanceError(f'{field}: indica al menos un día.')
    if len(cleaned) > 8 or any(v < 0 or v > FinanceReminderPolicy.MAX_OFFSET for v in cleaned):
        raise services.FinanceError(
            f'{field}: hasta 8 días, entre 0 y {FinanceReminderPolicy.MAX_OFFSET}.')
    return cleaned


def update_policy(company, *, actor, request=None, **fields) -> FinanceReminderPolicy:
    """The company's reminder schedule. Every value is validated; unknown keys are refused."""
    allowed = {'enabled', 'offsets', 'overdue_every_days', 'overdue_max', 'email_enabled', 'email_offsets',
               'email_overdue'}
    unknown = set(fields) - allowed
    if unknown:
        raise services.FinanceError('Campo desconocido en la política de recordatorios.')
    with transaction.atomic():
        policy = FinanceReminderPolicy.objects.select_for_update().get_or_create(company=company)[0]
        for name in ('enabled', 'email_enabled', 'email_overdue'):
            if name in fields:
                if not isinstance(fields[name], bool):
                    raise services.FinanceError('Valor inválido.')
                setattr(policy, name, fields[name])
        if 'offsets' in fields:
            policy.offsets = _clean_offsets(fields['offsets'], field='Avisos antes del vencimiento')
        if 'email_offsets' in fields:
            policy.email_offsets = _clean_offsets(fields['email_offsets'], field='Avisos por correo', allow_empty=True)
        if 'overdue_every_days' in fields:
            value = fields['overdue_every_days']
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 60:
                raise services.FinanceError('Los avisos de vencido se repiten cada 1 a 60 días.')
            policy.overdue_every_days = value
        if 'overdue_max' in fields:
            value = fields['overdue_max']
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 12:
                raise services.FinanceError('Los avisos de vencido son de 0 a 12.')
            policy.overdue_max = value
        policy.updated_by = actor
        policy.save()
        services._audit(actor=actor, action='finance_reminder_policy_updated', target_type='finance_reminder_policy',
                        target_id=policy.pk, company=company, request=request,
                        metadata={'fields': sorted(fields)})
    return policy


# ---------------------------------------------------------------------------
# The schedule
# ---------------------------------------------------------------------------

def schedule_point(policy, due: date, today: date):
    """
    The reminder point that applies on `today` to a payment due on `due`: `(kind, number)` or None.

    `('before', n)`: the latest configured «n days before» that has passed (0 = the due day).
    `('overdue', k)`: the k-th paced notice (0-based) of an overdue payment, none past the cap.
    """
    days = (due - today).days
    if days >= 0:
        passed = [n for n in policy.offsets if days <= n]
        return ('before', min(passed)) if passed else None
    if policy.overdue_max <= 0:
        return None
    k = (-days - 1) // max(1, policy.overdue_every_days)
    return ('overdue', k) if k < policy.overdue_max else None


def reminder_key(obligation, kind: str, number: int) -> str:
    discriminator = f'{number}@{obligation.due_date.isoformat()}' if kind == 'before' else \
        f'overdue{number}@{obligation.due_date.isoformat()}'
    return events.event_key(events.FINANCE_OBLIGATION_DUE, TARGET, obligation.pk, discriminator)


def _money(value, currency) -> str:
    amount = Decimal(value).quantize(Decimal('0.01'))
    return f'{_SYMBOLS.get(currency, currency)} {amount:,.2f}'


def _when(days: int) -> str:
    if days > 1:
        return f'en {days} días'
    if days == 1:
        return 'mañana'
    if days == 0:
        return 'hoy'
    if days == -1:
        return 'ayer'
    return f'hace {-days} días'


def _text(obligation, numbers, days: int, *, postponed=False):
    concept = obligation.concept
    if postponed:
        head = 'Aviso pospuesto'
    elif days < 0:
        head = 'Pago vencido'
    elif days == 0:
        head = 'Vence hoy'
    else:
        head = 'Pago por vencer'
    title = f'{head}: {concept}'[:140]
    payee = obligation.payee_name or (obligation.supplier.name if obligation.supplier_id else '')
    lines = [f'Beneficiario: {payee}'] if payee else []
    lines += [
        f'Pendiente: {_money(numbers["balance"], numbers["currency"])}',
        f'Vence: {obligation.due_date.strftime("%d/%m/%Y")} ({_when(days)})',
    ]
    return title, '\n'.join(lines)


def recipients(company, obligation):
    """Who may be told about `obligation`: `finance.view` here, with its branch (a general one: company-wide scope)."""
    users = notices.resolve_internal_recipients(company, capability=CAP_VIEW, branch=obligation.branch)
    if obligation.branch_id is None:
        users = [u for u in users if has_company_wide_scope(u, company)]
    return users


def _candidates(company, policy, today):
    """Open payables whose due date is inside the window where a reminder can exist."""
    horizon = today + timedelta(days=max(policy.offsets or [0]))
    floor = today - timedelta(days=max(1, policy.overdue_every_days) * max(1, policy.overdue_max))
    qs = Obligation.objects.filter(
        company=company, direction=FinanceDirection.PAYABLE, lifecycle=Obligation.LIFECYCLE_OPEN,
        due_date__isnull=False, due_date__lte=horizon, due_date__gte=floor - timedelta(days=1),
    ).select_related('branch', 'supplier')
    return services.with_figures(qs).order_by('due_date', 'pk')


def _snoozed_users(obligation, today):
    return set(ObligationReminderSnooze.objects.filter(obligation=obligation, until__gte=today)
               .values_list('user_id', flat=True))


def _wants_email(policy, kind, number) -> bool:
    if not policy.email_enabled:
        return False
    return policy.email_overdue if kind == 'overdue' else number in (policy.email_offsets or [])


def _send_reminder(obligation, policy, today):
    """Emit the notice for `obligation` if it is due one and it was not already sent. Returns 1 if emitted."""
    numbers = services.figures(obligation)
    if numbers['balance'] <= 0:
        return 0
    if services.state(obligation, today=today, figures_=numbers) not in _REMINDABLE:
        return 0
    point = schedule_point(policy, obligation.due_date, today)
    if point is None:
        return 0
    kind, number = point
    days = (obligation.due_date - today).days
    snoozed = _snoozed_users(obligation, today)
    people = [u for u in recipients(obligation.company, obligation) if u.pk not in snoozed]
    title, body = _text(obligation, numbers, days)
    priority = (Notification.Priority.WARNING if days < 0
                else Notification.Priority.ACTION if days <= 1 else Notification.Priority.INFO)
    with transaction.atomic():
        event = notices.emit(
            company=obligation.company, event_type=events.FINANCE_OBLIGATION_DUE,
            event_key=reminder_key(obligation, kind, number), title=title, body=body,
            target_type=TARGET, target_id=obligation.pk, users=people, priority=priority,
            payload={'obligation': obligation.pk, 'kind': kind, 'days': days},
            email=_wants_email(policy, kind, number),
        )
    return 0 if event is None else 1


def _fire_ended_snoozes(company, today) -> int:
    """One «aviso pospuesto» to each person whose snooze is over, if the payment is still owed; then it is done."""
    fired = 0
    due = ObligationReminderSnooze.objects.filter(company=company, until__lt=today, fired_at__isnull=True) \
        .select_related('obligation', 'user')
    for row in due:
        obligation = services.with_figures(Obligation.objects.filter(pk=row.obligation_id)) \
            .select_related('branch', 'supplier', 'company').first()
        with transaction.atomic():
            claimed = ObligationReminderSnooze.objects.filter(pk=row.pk, fired_at__isnull=True) \
                .update(fired_at=timezone.now())
            if not claimed or obligation is None:
                continue
            numbers = services.figures(obligation)
            if (obligation.direction != FinanceDirection.PAYABLE or numbers['balance'] <= 0
                    or services.state(obligation, today=today, figures_=numbers) not in _REMINDABLE
                    or row.user not in recipients(company, obligation)):
                continue
            title, body = _text(obligation, numbers, (obligation.due_date - today).days, postponed=True)
            key = events.event_key(events.FINANCE_OBLIGATION_DUE, TARGET, obligation.pk,
                                   f'snooze:{row.user_id}:{row.until.isoformat()}')
            if notices.emit(
                company=company, event_type=events.FINANCE_OBLIGATION_DUE, event_key=key, title=title, body=body,
                target_type=TARGET, target_id=obligation.pk, users=[row.user], priority=Notification.Priority.ACTION,
                payload={'obligation': obligation.pk, 'kind': 'snooze'}, email=False,
            ) is not None:
                fired += 1
    return fired


def run_company(company, *, today: date | None = None) -> dict:
    """One pass for one company. Idempotent; a failing obligation is logged and the rest still run."""
    policy = policy_for(company)
    today = today or services.company_today(company)
    result = {'considered': 0, 'emitted': 0, 'snooze_fired': 0, 'errors': 0}
    if policy.enabled:
        for obligation in _candidates(company, policy, today).iterator():
            result['considered'] += 1
            try:
                result['emitted'] += _send_reminder(obligation, policy, today)
            except Exception:  # noqa: BLE001 — logged; the next obligation still gets its notice
                result['errors'] += 1
                logger.exception('finanzas: no se pudo avisar de la obligación %s', obligation.pk)
        try:
            result['snooze_fired'] = _fire_ended_snoozes(company, today)
        except Exception:  # noqa: BLE001
            result['errors'] += 1
            logger.exception('finanzas: no se pudieron enviar los avisos pospuestos de la empresa %s', company.pk)
    FinanceReminderPolicy.objects.filter(pk=policy.pk).update(
        last_run_at=timezone.now(),
        last_error=(f'{result["errors"]} aviso(s) fallaron; revisa el registro del servidor.'
                    if result['errors'] else ''))
    return result


def run_all(*, company=None, today: date | None = None) -> dict:
    from ..models import Company

    totals = {'considered': 0, 'emitted': 0, 'snooze_fired': 0, 'errors': 0}
    rows = Company.objects.filter(is_active=True).order_by('pk')
    if company is not None:
        rows = rows.filter(pk=company.pk)
    for row in rows:
        try:
            result = run_company(row, today=today)
        except Exception:  # noqa: BLE001 — one company must not stop the others
            logger.exception('finanzas: falló la pasada de recordatorios de la empresa %s', row.pk)
            totals['errors'] += 1
            continue
        for name in totals:
            totals[name] += result[name]
    return totals


# ---------------------------------------------------------------------------
# «Posponer aviso»
# ---------------------------------------------------------------------------

def snooze(obligation, *, user, days: int, today: date | None = None, request=None) -> ObligationReminderSnooze:
    """Silence THIS person's reminders for `obligation` for `days` days. Nothing about the debt changes."""
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_SNOOZE_DAYS:
        raise services.FinanceError(f'Pospón el aviso entre 1 y {MAX_SNOOZE_DAYS} días.')
    today = today or services.company_today(obligation.company)
    row, _ = ObligationReminderSnooze.objects.update_or_create(
        obligation=obligation, user=user,
        defaults={'company': obligation.company, 'until': today + timedelta(days=days), 'fired_at': None})
    return row


def unsnooze(obligation, *, user) -> None:
    ObligationReminderSnooze.objects.filter(obligation=obligation, user=user).delete()
