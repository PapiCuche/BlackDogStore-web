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
import time
from datetime import date, timedelta
from decimal import Decimal

from django.db import connection, transaction
from django.utils import timezone

from .. import notification_events as events
from .. import notification_services as notices
from ..models import (
    FinanceDirection, FinanceReminderPolicy, Notification, NotificationEvent, Obligation, ObligationReminderSnooze,
)
from ..tenancy import has_company_wide_scope
from . import services

logger = logging.getLogger(__name__)

CAP_VIEW = 'finance.view'
TARGET = 'finance_obligation'
MAX_SNOOZE_DAYS = 30
#: A pass stops starting new obligations after this long; the rest wait for the next one (idempotent).
RUN_BUDGET_SECONDS = 600
#: Snoozes already told are kept this long, then purged.
SNOOZE_KEEP_DAYS = 90
#: pg advisory lock key of the finance pass: two cron runs never overlap.
_LOCK_KEY = 7_340_011
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
        before = {name: getattr(policy, name) for name in allowed}
        for name in ('enabled', 'email_enabled', 'email_overdue'):
            if name in fields:
                if not isinstance(fields[name], bool):
                    raise services.FinanceError('Valor inválido.')
                setattr(policy, name, fields[name])
        if 'offsets' in fields:
            policy.offsets = _clean_offsets(fields['offsets'], field='Avisos antes del vencimiento')
        if 'email_offsets' in fields:
            policy.email_offsets = _clean_offsets(fields['email_offsets'], field='Avisos por correo', allow_empty=True)
        # Changing the reminder points without naming the mailed ones keeps only the mailed ones that still exist.
        if 'offsets' in fields and 'email_offsets' not in fields:
            policy.email_offsets = [n for n in policy.email_offsets if n in policy.offsets]
        # A mailed point that is not a reminder point would simply never be mailed: say so instead of saving it.
        if not set(policy.email_offsets) <= set(policy.offsets):
            raise services.FinanceError('Los avisos por correo deben ser alguno de los avisos antes del vencimiento.')
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
        changes = {name: {'from': before[name], 'to': getattr(policy, name)}
                   for name in allowed if before[name] != getattr(policy, name)}
        services._audit(actor=actor, action='finance_reminder_policy_updated', target_type='finance_reminder_policy',
                        target_id=policy.pk, company=company, request=request, metadata={'changes': changes})
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
    ).select_related('company', 'branch', 'supplier')
    return services.with_figures(qs).order_by('due_date', 'pk')


def _wants_email(policy, kind, number) -> bool:
    if not policy.email_enabled:
        return False
    return policy.email_overdue if kind == 'overdue' else number in (policy.email_offsets or [])


class _Recipients:
    """Who may be told about a branch's payments, resolved ONCE per pass for each branch (and for «general»)."""

    def __init__(self, company):
        self.company = company
        self._by_branch: dict = {}

    def for_obligation(self, obligation):
        key = obligation.branch_id
        if key not in self._by_branch:
            self._by_branch[key] = recipients(self.company, obligation)
        return self._by_branch[key]


def _send_reminder(obligation, policy, today, *, people, skip_users=frozenset()):
    """
    Emit the notice for `obligation` if it is due one. `people` are the already-resolved recipients with the
    snoozed and the already-notified-today taken out. Returns 1 if an event was created.
    """
    numbers = services.figures(obligation)
    point = schedule_point(policy, obligation.due_date, today)
    kind, number = point
    days = (obligation.due_date - today).days
    people = [u for u in people if u.pk not in skip_users]
    if not people:
        return 0           # nobody to tell: the point stays free for whoever can see it later
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


def _remindable(obligation, today):
    """`(numbers, state)` if the payment is still being waited for, else None."""
    numbers = services.figures(obligation)
    if numbers['balance'] <= 0:
        return None
    current = services.state(obligation, today=today, figures_=numbers)
    return (numbers, current) if current in _REMINDABLE else None


def _fire_ended_snoozes(company, today) -> tuple[int, int, set]:
    """
    One «aviso pospuesto» to each person whose snooze is over, if the payment is still owed.

    A row is CLAIMED only when it is settled for good (sent, or the payment is paid / voided / the person lost
    access). If the payment is only temporarily not remindable (paused, under review) the row stays for a later
    pass. Each row has its own try: one bad row does not stop the others. Returns (fired, errors, {(obligation, user)}).
    """
    fired, errors, told = 0, 0, set()
    rows = (ObligationReminderSnooze.objects.filter(company=company, until__lt=today, fired_at__isnull=True)
            .select_related('user').order_by('pk'))
    pool = _Recipients(company)
    for row in rows:
        try:
            obligation = (services.with_figures(Obligation.objects.filter(pk=row.obligation_id))
                          .select_related('company', 'branch', 'supplier').first())
            settled = (obligation is None or obligation.direction != FinanceDirection.PAYABLE
                       or obligation.lifecycle != Obligation.LIFECYCLE_OPEN)
            ready = None if settled else _remindable(obligation, today)
            if not settled and ready is None:
                numbers = services.figures(obligation)
                settled = numbers['balance'] <= 0                     # paid for good; paused/under review: retry later
            if settled:
                ObligationReminderSnooze.objects.filter(pk=row.pk, fired_at__isnull=True).update(fired_at=timezone.now())
                continue
            if ready is None:
                continue
            numbers, _state = ready
            if row.user not in pool.for_obligation(obligation):
                ObligationReminderSnooze.objects.filter(pk=row.pk, fired_at__isnull=True).update(fired_at=timezone.now())
                continue
            title, body = _text(obligation, numbers, (obligation.due_date - today).days, postponed=True)
            key = events.event_key(events.FINANCE_OBLIGATION_DUE, TARGET, obligation.pk,
                                   f'snooze:{row.user_id}:{row.until.isoformat()}')
            with transaction.atomic():
                claimed = ObligationReminderSnooze.objects.filter(pk=row.pk, fired_at__isnull=True) \
                    .update(fired_at=timezone.now())
                if not claimed:
                    continue
                event = notices.emit(
                    company=company, event_type=events.FINANCE_OBLIGATION_DUE, event_key=key, title=title, body=body,
                    target_type=TARGET, target_id=obligation.pk, users=[row.user],
                    priority=Notification.Priority.ACTION,
                    payload={'obligation': obligation.pk, 'kind': 'snooze'}, email=False)
            if event is not None:
                fired += 1
                told.add((obligation.pk, row.user_id))
        except Exception:  # noqa: BLE001 — this row stays unclaimed and is retried; the others still run
            errors += 1
            logger.exception('finanzas: no se pudo enviar el aviso pospuesto %s', row.pk)
    return fired, errors, told


def run_company(company, *, today: date | None = None, deadline: float | None = None) -> dict:
    """
    One pass for one company. Idempotent. A failing obligation is logged and the rest still run; past `deadline`
    (a `time.monotonic()` instant) no new obligation is started — `deferred` counts them for the next pass.
    """
    policy = policy_for(company)
    today = today or services.company_today(company)
    result = {'considered': 0, 'emitted': 0, 'snooze_fired': 0, 'errors': 0, 'deferred': 0}
    if policy.enabled:
        # 1. Snoozes that ended come first, so a person gets ONE notice the day their silence ends, not two.
        try:
            result['snooze_fired'], snooze_errors, told = _fire_ended_snoozes(company, today)
            result['errors'] += snooze_errors
        except Exception:  # noqa: BLE001
            told = set()
            result['errors'] += 1
            logger.exception('finanzas: falló la pasada de avisos pospuestos de la empresa %s', company.pk)

        # 2. What is due: decide each point from the rows alone, and touch notifications only for the NEW ones.
        due = []
        for obligation in _candidates(company, policy, today).iterator():
            result['considered'] += 1
            try:
                ready = _remindable(obligation, today)
                point = schedule_point(policy, obligation.due_date, today) if ready else None
                if point is not None:
                    due.append((obligation, reminder_key(obligation, *point)))
            except Exception:  # noqa: BLE001
                result['errors'] += 1
                logger.exception('finanzas: no se pudo evaluar la obligación %s', obligation.pk)
        already = set(NotificationEvent.objects.filter(
            company=company, event_key__in=[key for _o, key in due]).values_list('event_key', flat=True)) if due else set()
        fresh = [obligation for obligation, key in due if key not in already]
        snoozed: dict = {}
        if fresh:
            for obligation_id, user_id in (ObligationReminderSnooze.objects
                                           .filter(obligation__in=fresh, until__gte=today)
                                           .values_list('obligation_id', 'user_id')):
                snoozed.setdefault(obligation_id, set()).add(user_id)
        pool = _Recipients(company)
        for obligation in fresh:
            if deadline is not None and time.monotonic() >= deadline:
                result['deferred'] += 1
                continue
            try:
                skip = snoozed.get(obligation.pk, set()) | {u for (o, u) in told if o == obligation.pk}
                result['emitted'] += _send_reminder(
                    obligation, policy, today, people=pool.for_obligation(obligation), skip_users=skip)
            except Exception:  # noqa: BLE001 — logged; the next obligation still gets its notice
                result['errors'] += 1
                logger.exception('finanzas: no se pudo avisar de la obligación %s', obligation.pk)
        ObligationReminderSnooze.objects.filter(
            company=company, fired_at__lt=timezone.now() - timedelta(days=SNOOZE_KEEP_DAYS)).delete()
    FinanceReminderPolicy.objects.filter(pk=policy.pk).update(
        last_run_at=timezone.now(),
        last_error=(f'{result["errors"]} aviso(s) fallaron; revisa el registro del servidor.'
                    if result['errors'] else ''))
    return result


def _try_lock() -> bool:
    """A session-level advisory lock on PostgreSQL: a second pass started while one runs does nothing."""
    if connection.vendor != 'postgresql':
        return True
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_try_advisory_lock(%s)', [_LOCK_KEY])
        return bool(cursor.fetchone()[0])


def _unlock() -> None:
    if connection.vendor != 'postgresql':
        return
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_unlock(%s)', [_LOCK_KEY])


def run_all(*, company=None, today: date | None = None, budget_seconds: float = RUN_BUDGET_SECONDS) -> dict:
    from ..models import Company

    totals = {'considered': 0, 'emitted': 0, 'snooze_fired': 0, 'errors': 0, 'deferred': 0, 'skipped': False}
    if not _try_lock():
        totals['skipped'] = True
        return totals
    try:
        deadline = time.monotonic() + budget_seconds
        rows = Company.objects.filter(is_active=True).order_by('pk')
        if company is not None:
            rows = rows.filter(pk=company.pk)
        for row in rows:
            try:
                result = run_company(row, today=today, deadline=deadline)
            except Exception:  # noqa: BLE001 — one company must not stop the others
                logger.exception('finanzas: falló la pasada de recordatorios de la empresa %s', row.pk)
                totals['errors'] += 1
                continue
            for name in ('considered', 'emitted', 'snooze_fired', 'errors', 'deferred'):
                totals[name] += result[name]
    finally:
        _unlock()
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
