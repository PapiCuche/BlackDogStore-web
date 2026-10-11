"""
Asking the gateway about payments whose notification never came (PAY-RECONCILE).

    https://secure.micuentaweb.pe/doc/es-PE/rest/V4.0/api/playground/Order/Get
    https://secure.micuentaweb.pe/doc/es-PE/rest/V4.0/api/kb/ipn_usage.html  (§5)

The notification is still what pays an order. But the gateway gives one up as
failed when the shop does not answer it with a 2xx or a 3xx, replays it at most
four times and only if the merchant ticked that box — and then the money is
taken and the order waits for ever. «Mi Cuenta Web» has a web service for that
case; the other product («SDK web / Checkout») is not asked anything here.

WHAT THIS DOES, AND ONLY THIS
-----------------------------
For each attempt of the ACTIVE product that is still waiting, old enough for
its notification to have had its chance and recent enough to be worth a call:
ask `Order/Get`, read the answer narrowly (`parse_order_transactions`) and hand
the result to the very code that judges a notification. That code compares it
with what the shop opened, takes the row lock and pays once.

WHAT IT NEVER DOES
------------------
It never believes a browser, never takes an identifier from anybody, never
decides what the answer leaves open (the attempt keeps waiting, and the first
time that happens it is written to the audit trail: money may be involved),
and never insists: one call per attempt per run, one after another, and the
run ends at the first sign that the gateway is not answering or is refusing
the caller. The documentation recommends one call a minute for a reference and
waiting for each answer; a run every five minutes stays well inside that.

WHO IS ASKED FIRST
------------------
Whoever has waited longest for a question: never asked before any that was,
then the one asked longest ago. An attempt whose form can no longer charge and
of which the gateway holds nothing is CLOSED (`abandoned`): it stops taking a
turn from the ones that may hide a payment.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from django.db.models import F
from django.utils import timezone

from ..models import AdminAuditLog, PaymentTransaction
from . import micuentaweb

logger = logging.getLogger(__name__)

#: The notification is instant. Nobody is asked before it had this long.
MIN_AGE = timedelta(minutes=3)
#: A run looks this far back unless somebody widens it for a claim.
WINDOW = timedelta(hours=2)
#: See `micuentaweb.FORM_LIFETIME`: until then a refusal is not an answer.
FORM_LIFETIME = micuentaweb.FORM_LIFETIME
#: How many attempts one run asks about.
LIMIT = 20

#: What one question about one attempt came to.
PAID = 'paid'                   # the order is paid, by this question
DUPLICATE = 'duplicate'         # the gateway took money for an order that was already paid
REJECTED = 'rejected'           # refused, and the form can no longer charge
MISMATCHED = 'mismatch'         # the gateway and the shop disagree: integrity failure
ABANDONED = 'abandoned'         # the form is gone and the gateway holds nothing: closed
WAITING = 'waiting'             # nothing there yet, or a refusal that is not final yet
RUNNING = 'running'             # the gateway holds something still open
UNDECIDED = 'undecided'         # an answer this cannot read on its own
ERROR = 'error'                 # the gateway answered THIS question with an error
ALREADY = 'already'             # settled meanwhile by somebody else (its notification)
UNREACHABLE = 'unreachable'     # the gateway did not answer
REFUSED = 'refused'             # the gateway refuses the caller, whatever is asked

#: Outcomes after which the attempt is still open and nobody can say why not.
_UNRESOLVED = (RUNNING, UNDECIDED, ERROR)
#: How an undecided answer is marked on the attempt, for the health check to count.
UNDECIDED_MARK = 'undecided: '


@dataclass
class Summary:
    asked: int = 0
    paid: int = 0
    duplicates: int = 0     # money taken for an order that was already paid
    rejected: int = 0
    mismatched: int = 0     # the gateway and the shop disagree: integrity failure
    abandoned: int = 0      # closed: nothing was ever tried and the form is gone
    waiting: int = 0        # nothing at the gateway yet, or still open there
    undecided: int = 0      # an answer this cannot read on its own
    errors: int = 0         # the gateway answered the question with an error
    left: int = 0           # waiting, inside the window, and not asked about in this run
    unreachable: bool = False
    refused: bool = False   # the gateway refuses the caller: the run stopped
    unconfigured: bool = False
    #: Attempts the gateway DOES hold something for, or would not answer about,
    #: and that are not settled. Kept by id for whoever must wait for them.
    unresolved: list = field(default_factory=list)

    def line(self) -> str:
        text = (f'consultados: {self.asked} · pagados: {self.paid} · rechazados: {self.rejected} · '
                f'no coinciden: {self.mismatched} · sin decidir: {self.undecided} · siguen esperando: {self.waiting}')
        if self.duplicates:
            text += f' · COBROS DUPLICADOS: {self.duplicates}'
        if self.abandoned:
            text += f' · cerrados sin intento: {self.abandoned}'
        if self.errors:
            text += f' · errores de la pasarela: {self.errors}'
        if self.left:
            text += f' · sin consultar: {self.left}'
        if self.refused:
            text += ' · la pasarela rechazó la consulta (credenciales o límite de llamadas): se detuvo la pasada'
        return text + (' · la pasarela no respondió' if self.unreachable else '')


def reconcile_micuentaweb(*, now=None, window: timedelta = WINDOW, limit: int = LIMIT,
                          reference: str = '') -> Summary:
    """
    Ask about the waiting attempts of «Mi Cuenta Web», if it is the product that charges.

    `reference`: ONE attempt, by the reference the shop gave it, whatever its
    age and even if it was closed as refused or abandoned — for the customer who
    says they paid. An attempt that already has its payment is not asked about.
    """
    from ..integrations import payments as payment_adapters

    summary = Summary()
    if payment_adapters.active_code() != micuentaweb.PROVIDER:
        # One product per installation: the other one's keys are not this
        # installation's, and its attempts are not ours to ask about.
        return summary

    now = now or timezone.now()
    Status = PaymentTransaction.Status
    if reference:
        due = PaymentTransaction.objects.filter(
            provider=micuentaweb.PROVIDER, transaction_id=reference,
        ).exclude(status__in=(Status.AUTHORIZED, Status.DUPLICATE))
    else:
        due = PaymentTransaction.objects.filter(
            provider=micuentaweb.PROVIDER, status=Status.PENDING,
            created_at__lte=now - MIN_AGE, created_at__gte=now - window,
        )
    # Never asked first, then whoever was asked longest ago: nobody is starved.
    attempts = list(due.select_related('order__company').order_by(
        F('gateway_checked_at').asc(nulls_first=True), 'created_at')[:max(0, limit)])
    if not attempts:
        return summary
    summary.left = due.count() - len(attempts)

    try:
        credentials = micuentaweb.load_credentials()
    except micuentaweb.MiCuentaWebError as exc:
        # Names what is missing, never a value.
        logger.error('Conciliación de pagos: pasarela mal configurada: %s', exc)
        summary.unconfigured = True
        return summary

    for attempt in attempts:
        summary.asked += 1
        outcome = ask(attempt, credentials, now=now)
        if outcome in (UNREACHABLE, REFUSED):
            # No point in asking about the next one: the answer is about us.
            summary.unreachable = outcome == UNREACHABLE
            summary.refused = outcome == REFUSED
            summary.left += len(attempts) - summary.asked
            break
        _count(summary, attempt, outcome)
    return summary


def _count(summary: Summary, attempt, outcome: str) -> None:
    if outcome in _UNRESOLVED:
        summary.unresolved.append(attempt.pk)
    if outcome == PAID:
        summary.paid += 1
    elif outcome == DUPLICATE:
        summary.duplicates += 1
    elif outcome == REJECTED:
        summary.rejected += 1
    elif outcome == MISMATCHED:
        summary.mismatched += 1
    elif outcome == ABANDONED:
        summary.abandoned += 1
    elif outcome == UNDECIDED:
        summary.undecided += 1
    elif outcome == ERROR:
        summary.errors += 1
    elif outcome in (WAITING, RUNNING):
        summary.waiting += 1
    # ALREADY: its notification got there first and settled it. Nothing of ours.


def ask(attempt, credentials, *, now=None) -> str:
    """
    One question to the gateway about ONE attempt, and what came of it.

    Whatever the answer says is handed to the code that judges a notification
    (`settle_answer`): this decides nothing about money by itself. What it does
    decide is what the answer cannot: an attempt of which the gateway holds
    nothing once its form is gone is closed, and an answer nobody can read is
    written down the first time.
    """
    from ..views import MiCuentaWebNotificationView

    now = now or timezone.now()
    try:
        answer = micuentaweb.get_order_transactions(credentials=credentials, order_id=attempt.transaction_id)
    except micuentaweb.MiCuentaWebUnreachable:
        return UNREACHABLE
    except micuentaweb.MiCuentaWebRefused:
        return REFUSED
    except micuentaweb.MiCuentaWebError:
        _asked(attempt, now)
        return ERROR
    _asked(attempt, now)

    try:
        result = micuentaweb.parse_order_transactions(answer, credentials, attempt.transaction_id)
    except micuentaweb.MiCuentaWebError as exc:
        _undecided(attempt, exc)
        return UNDECIDED

    if result is None:
        if attempt.created_at > now - FORM_LIFETIME:
            return WAITING
        # The form can no longer charge and nothing was ever tried: nobody is
        # coming back to this one. A notification that did arrive later would
        # still be judged on its own.
        closed = PaymentTransaction.objects.filter(
            pk=attempt.pk, status=PaymentTransaction.Status.PENDING,
        ).update(status=PaymentTransaction.Status.ABANDONED, failure_reason='sin intento de pago en la pasarela')
        return ABANDONED if closed else WAITING

    outcome = MiCuentaWebNotificationView.settle_answer(result, credentials)
    if outcome == WAITING and not result.settled:
        return RUNNING
    return outcome


def _asked(attempt, now) -> None:
    """The gateway answered about this one: it goes to the back of the queue."""
    PaymentTransaction.objects.filter(pk=attempt.pk).update(gateway_checked_at=now)


def _undecided(attempt, exc) -> None:
    """
    An answer that says money may have moved and that this cannot decide: two
    paid debits, a refund beside the debit, a partial amount. The attempt keeps
    waiting; the FIRST time, it is written where an owner looks, under the
    order's company. The reason is one of this adapter's own sentences — nothing
    the gateway wrote.
    """
    logger.warning('Conciliación de pagos: el intento %s sigue esperando: %s', attempt.pk, exc)
    first = PaymentTransaction.objects.filter(
        pk=attempt.pk, status=PaymentTransaction.Status.PENDING,
    ).exclude(failure_reason__startswith=UNDECIDED_MARK).update(failure_reason=(UNDECIDED_MARK + str(exc))[:200])
    if first:
        AdminAuditLog.log(
            actor=None, action='payment_undecided', target_type='order', target_id=attempt.order_id,
            metadata={'provider': attempt.provider, 'attempt': attempt.pk, 'reason': str(exc)[:200]},
            company=attempt.order.company,
        )


def earlier_attempts(order, credentials, *, now=None) -> str:
    """
    Before ANOTHER form is opened for the same order: what became of the ones
    whose form can still charge. Asked now, without waiting for a notification.

        'paid'      one of them was paid (and the order now is): no second form
        'unknown'   the gateway could not be asked, or holds something unsettled
        'clear'     nothing paid under any of them
    """
    now = now or timezone.now()
    earlier = PaymentTransaction.objects.filter(
        order=order, provider=micuentaweb.PROVIDER, status=PaymentTransaction.Status.PENDING,
        created_at__gt=now - FORM_LIFETIME,
    ).select_related('order__company').order_by('created_at')
    unknown = False
    for attempt in earlier:
        outcome = ask(attempt, credentials, now=now)
        if outcome in (PAID, DUPLICATE, ALREADY):
            return 'paid'
        unknown = unknown or outcome in (UNREACHABLE, REFUSED, *_UNRESOLVED)
    return 'unknown' if unknown else 'clear'
