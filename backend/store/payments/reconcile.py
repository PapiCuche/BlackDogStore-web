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
decides what the answer leaves open (the attempt keeps waiting and the log says
why), and never insists: one call per attempt per run, one after another, and
the run ends at the first sign that the gateway is not answering. The
documentation recommends one call a minute for a reference and waiting for
each answer; a run every five minutes stays well inside that.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone

from ..models import AdminAuditLog, PaymentTransaction
from . import micuentaweb

logger = logging.getLogger(__name__)

#: The notification is instant. Nobody is asked before it had this long.
MIN_AGE = timedelta(minutes=3)
#: A run looks this far back unless somebody widens it for a claim.
WINDOW = timedelta(hours=2)
#: After this, the form of an attempt cannot charge any more: the formToken
#: lives 15 minutes and a payment session 10. Until then a refused card is not
#: an answer — the form lets the buyer try again under the same reference.
FORM_LIFETIME = timedelta(minutes=30)
#: How many attempts one run asks about. The newest first: that is where a
#: buyer is still looking at «verificando el pago».
LIMIT = 20


@dataclass
class Summary:
    asked: int = 0
    paid: int = 0
    rejected: int = 0
    mismatched: int = 0     # the gateway and the shop disagree: integrity failure
    waiting: int = 0        # nothing at the gateway yet, or still open there
    undecided: int = 0      # an answer this cannot read on its own
    left: int = 0           # waiting, inside the window, and not asked about in this run
    unreachable: bool = False
    unconfigured: bool = False

    def line(self) -> str:
        text = (f'consultados: {self.asked} · pagados: {self.paid} · rechazados: {self.rejected} · '
                f'no coinciden: {self.mismatched} · sin decidir: {self.undecided} · siguen esperando: {self.waiting}')
        if self.left:
            text += f' · sin consultar: {self.left}'
        return text + (' · la pasarela no respondió' if self.unreachable else '')


def reconcile_micuentaweb(*, now=None, window: timedelta = WINDOW, limit: int = LIMIT) -> Summary:
    """Ask about the waiting attempts of «Mi Cuenta Web», if it is the product that charges."""
    from ..integrations import payments as payment_adapters
    from ..views import MiCuentaWebNotificationView

    summary = Summary()
    if payment_adapters.active_code() != micuentaweb.PROVIDER:
        # One product per installation: the other one's keys are not this
        # installation's, and its attempts are not ours to ask about.
        return summary

    now = now or timezone.now()
    due = PaymentTransaction.objects.filter(
        provider=micuentaweb.PROVIDER, status=PaymentTransaction.Status.PENDING,
        created_at__lte=now - MIN_AGE, created_at__gte=now - window,
    )
    attempts = list(due.order_by('-created_at')[:max(0, limit)])
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
        try:
            answer = micuentaweb.get_order_transactions(credentials=credentials, order_id=attempt.transaction_id)
            result = micuentaweb.parse_order_transactions(answer, credentials, attempt.transaction_id)
        except micuentaweb.MiCuentaWebUnreachable:
            summary.unreachable = True
            summary.left += len(attempts) - summary.asked
            break
        except micuentaweb.MiCuentaWebError as exc:
            summary.undecided += 1
            logger.warning('Conciliación de pagos: el intento %s sigue esperando: %s', attempt.pk, exc)
            continue

        final = result is not None and (
            result.authorized or (result.settled and attempt.created_at <= now - FORM_LIFETIME))
        if not final:
            summary.waiting += 1
            continue

        MiCuentaWebNotificationView.settle_answer(result, credentials)
        _count(summary, attempt, result)
    return summary


def _count(summary: Summary, attempt, result) -> None:
    """What became of the attempt, read back from the database rather than assumed."""
    fresh = PaymentTransaction.objects.select_related('order').get(pk=attempt.pk)
    Status = PaymentTransaction.Status
    if fresh.status == Status.AUTHORIZED:
        if fresh.signature_verified:
            # Its notification arrived while the gateway was being asked, and
            # paid it. Nothing was reconciled here.
            return
        summary.paid += 1
        # A payment confirmed without a signature leaves its own record, under
        # the company of the order: who was asked, and which transaction said so.
        AdminAuditLog.log(
            actor=None, action='payment_reconciled', target_type='order', target_id=fresh.order_id,
            metadata={
                'provider': fresh.provider, 'attempt': fresh.pk, 'source': 'Order/Get',
                'gateway_transaction': fresh.provider_unique_id, 'mode': result.mode,
            },
            company=fresh.order.company,
        )
        logger.info('Conciliación de pagos: el pedido %s quedó pagado por consulta (intento %s).',
                    fresh.order_id, fresh.pk)
    elif fresh.status == Status.REJECTED:
        summary.rejected += 1
    elif fresh.status == Status.INTEGRITY_FAILED:
        summary.mismatched += 1
    else:
        summary.waiting += 1
