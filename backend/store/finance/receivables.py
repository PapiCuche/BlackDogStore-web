"""
FIN-E — receivables: a repair's balance (derived) and receivables entered by hand.

docs/adr-finance-obligations.md §6. The rules this module enforces:

- A REPAIR RECEIVABLE STORES IDENTITY ONLY: the repair, the customer, the
  branch, the due date and the dispute. The amount, what was paid and the
  balance are READ from `RepairQuote` + `RepairPayment` every time
  (`service_payment_summary`'s arithmetic; `services.with_figures` annotates the
  same numbers for lists). There is no second ledger and nothing to drift.
- IT EXISTS WHEN MONEY IS OWED: the quote is approved (the hook in
  `service_services.record_quote_decision`, same transaction), the order is not
  cancelled and the approved total is above zero. A cancelled order drops out
  of the pending lists; the history keeps it as «origen cancelado».
- ONE LOCK ORDER: RepairOrder, then Obligation. Everything here that writes
  takes the order lock first, so an approval, a status change and the backfill
  cannot interleave.
- PAYMENTS NEVER GO THROUGH FINANCE: a repair is collected with
  `record_service_payment` (service capabilities). `finance.*` writes nothing
  in that ledger.
- THE DUE DATE IS THE LOCAL DATE the order first reaches «Listo para recoger»
  (or, failing that, is delivered) plus `REPAIR_DUE_DAYS`. Until then the
  receivable has no due date and is never overdue.
- A RECEIVABLE BY HAND IS NEVER A REPAIR OR A SALE: it needs a reason and a
  customer of the company, and it cannot restate the balance of an open repair
  of that customer.

Nothing here sends a message to anybody.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import date, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction

from ..models import (
    Customer, FinanceDirection, Obligation, RepairOrder, RepairQuote, RepairStatusCode, RepairStatusHistory,
)
from . import services

logger = logging.getLogger(__name__)

#: Owner question 5 (open): a repair falls due this many local days after it
#: reaches «Listo para recoger». 0 = the same day. Changing it moves only the
#: receivables whose due date is set from now on; one already set never moves.
REPAIR_DUE_DAYS = 0

ZERO = services.ZERO
CENT = services.CENT

AUDIT_DERIVED = 'finance_receivable_derived'
#: What a hand-made receivable may NOT carry: a link to the repair or the sale it would restate.
FORBIDDEN_LINKS = (
    'repair_order', 'repair_order_id', 'repair', 'repair_id', 'order', 'order_id', 'sale', 'sale_id',
    'origin', 'origin_type', 'origin_key', 'credit_sale',
)


# ---------------------------------------------------------------------------
# The arithmetic of a repair's balance (the same as `service_payment_summary`)
# ---------------------------------------------------------------------------

def derive(total, paid) -> dict:
    """Balance and credit of a repair: `outstanding` is floored at zero, the excess is `credit`."""
    total = Decimal(total or ZERO).quantize(CENT)
    paid = Decimal(paid or ZERO).quantize(CENT)
    return {
        'effective': total, 'paid': paid,
        'balance': max(total - paid, ZERO).quantize(CENT),
        'credit': max(paid - total, ZERO).quantize(CENT),
    }


def last_approved_quote(order):
    """The approved revision in force, else the last one that was approved and then reopened."""
    from .. import service_services as svc

    current = svc.financial_quote(order)
    if current is not None:
        return current, True
    reopened = (order.quotes.filter(status=RepairQuote.STATUS_SUPERSEDED, approved_at__isnull=False)
                .order_by('-revision', '-pk').first())
    return reopened, False


def repair_figures(obligation) -> dict:
    """
    Figures of a repair receivable read from the repair itself. An obligation
    fetched through `services.with_figures` carries the same numbers as
    annotations (one query for a whole list); otherwise the repair's own
    `service_payment_summary` answers.
    """
    if hasattr(obligation, 'fin_rq_paid'):
        numbers = derive(obligation.fin_rq_total, obligation.fin_rq_paid)
        has_basis = obligation.fin_rq_total is not None
        current = bool(obligation.fin_rq_current)
        status = obligation.fin_order_status
        currency = obligation.fin_rq_currency or obligation.currency
    else:
        from .. import service_services as svc

        order = obligation.repair_order
        quote, current = last_approved_quote(order)
        summary = svc.service_payment_summary(order)
        has_basis = quote is not None
        if current:
            numbers = {'effective': summary['quoted_total'], 'paid': summary['confirmed_paid'],
                       'balance': summary['outstanding'], 'credit': summary['credit']}
        else:
            numbers = derive(quote.total if quote else None, summary['confirmed_paid'])
        status = order.status
        currency = (quote.currency if quote else '') or summary['currency']
    return {
        **numbers, 'has_payments': numbers['paid'] > 0,
        'partially_paid': numbers['paid'] > 0 and numbers['balance'] > 0,
        'currency': currency, 'quote_current': current, 'order_cancelled': status == RepairStatusCode.CANCELLED,
        'has_basis': has_basis, 'order_status': status,
    }


# ---------------------------------------------------------------------------
# Due date
# ---------------------------------------------------------------------------

def _local_date(company, instant) -> date:
    from django.utils import timezone

    from ..sales_analytics_views import _company_tz

    return timezone.localtime(instant, _company_tz(company)).date()


def due_date_for(order):
    """
    The due date a repair has earned, or None. The FIRST time the order reached
    «Listo para recoger» (its history is the evidence, so a backfill and the live
    hook agree), else the first delivery, as a local date of the company plus N.
    """
    for code in (RepairStatusCode.READY_FOR_PICKUP, RepairStatusCode.DELIVERED):
        event = (RepairStatusHistory.objects.filter(repair_order=order, to_status=code)
                 .order_by('created_at', 'pk').first())
        if event is not None:
            return _local_date(order.company, event.created_at) + timedelta(days=REPAIR_DUE_DAYS)
    return None


def sync_due_date(order, obligation=None) -> bool:
    """Set the due date once. The order is locked by the caller. True if it was written."""
    obligation = obligation or Obligation.objects.filter(repair_order=order).first()
    if obligation is None or obligation.due_date is not None:
        return False
    due = due_date_for(order)
    if due is None:
        return False
    obligation.due_date = due
    obligation.save(update_fields=['due_date'])
    return True


# ---------------------------------------------------------------------------
# Creation (under the order lock)
# ---------------------------------------------------------------------------

def is_eligible(order) -> bool:
    """Money is owed on a repair: not cancelled, with an approved quote in force whose total is above zero."""
    from .. import service_services as svc

    if order.status == RepairStatusCode.CANCELLED:
        return False
    quote = svc.financial_quote(order)
    return quote is not None and quote.total > 0


def ensure_repair_receivable(order, *, actor=None, request=None):
    """
    The receivable of `order`, created if money is owed. The CALLER holds the
    order's lock (RepairOrder → Obligation). Idempotent: one per order.
    """
    existing = Obligation.objects.select_for_update().filter(repair_order=order).first()
    if existing is not None:
        sync_due_date(order, existing)
        return existing
    if not is_eligible(order):
        return None
    from .. import service_services as svc

    quote = svc.financial_quote(order)
    company = order.company
    row = Obligation(
        company=company, branch=order.branch, direction=FinanceDirection.RECEIVABLE,
        origin_type=Obligation.ORIGIN_REPAIR, origin_key=f'repair:{order.pk}', repair_order=order,
        customer=order.customer, concept=f'Reparación {order.number}'[:140], currency=quote.currency,
        issue_date=services.company_today(company), created_by=actor,
    )
    try:
        with transaction.atomic():
            row.reference = services._allocate_reference(company)
            row.save()
    except IntegrityError:
        winner = Obligation.objects.filter(repair_order=order).first()
        if winner is None:
            raise
        return winner
    services._audit(actor=actor, action=AUDIT_DERIVED, target_type=services.AUDIT_OBLIGATION, target_id=row.pk,
                    company=company, request=request,
                    metadata={'repair_order_id': order.pk, 'repair_number': order.number,
                              'revision': quote.revision})
    sync_due_date(order, row)
    return row


def ensure_repair_receivable_for(order_pk, *, actor=None, request=None):
    """Lock the order, then make sure its receivable exists. For callers that do not hold the lock."""
    with transaction.atomic():
        order = RepairOrder.objects.select_for_update().select_related('company', 'branch', 'customer').get(
            pk=order_pk)
        return ensure_repair_receivable(order, actor=actor, request=request)


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------

def backfill_repair_receivables(*, company=None, dry_run=False) -> dict:
    """
    Create the receivables of the repairs approved before FIN-E (and the due
    date of those that already reached «Listo para recoger»). Idempotent. One
    company at a time, one repair at a time: a failure never touches another.
    """
    from ..models import Company

    companies = [company] if company is not None else list(Company.objects.order_by('pk'))
    counts = {'created': 0, 'skipped_zero_total': 0, 'due_dates_set': 0, 'errors': 0,
              'companies': len(companies), 'dry_run': dry_run}
    for current in companies:
        pending = (RepairOrder.objects.filter(company=current, quotes__status=RepairQuote.STATUS_APPROVED,
                                              receivable__isnull=True)
                   .exclude(status=RepairStatusCode.CANCELLED).distinct().order_by('pk')
                   .values_list('pk', flat=True))
        for order_pk in list(pending):
            try:
                with transaction.atomic():
                    order = RepairOrder.objects.select_for_update().select_related(
                        'company', 'branch', 'customer').get(pk=order_pk)
                    if Obligation.objects.filter(repair_order=order).exists():
                        continue  # created meanwhile by the approval
                    if not is_eligible(order):
                        counts['skipped_zero_total'] += 1
                    elif dry_run:
                        counts['created'] += 1
                    else:
                        ensure_repair_receivable(order)
                        counts['created'] += 1
            except Exception:  # noqa: BLE001 - one repair never stops the rest
                counts['errors'] += 1
                logger.exception('backfill_repair_receivables: repair %s failed', order_pk)
        undated = (Obligation.objects.filter(company=current, origin_type=Obligation.ORIGIN_REPAIR,
                                             due_date__isnull=True).order_by('pk')
                   .values_list('repair_order_id', flat=True))
        for order_pk in list(undated):
            try:
                with transaction.atomic():
                    order = RepairOrder.objects.select_for_update().select_related('company').get(pk=order_pk)
                    if dry_run:
                        counts['due_dates_set'] += due_date_for(order) is not None
                    elif sync_due_date(order):
                        counts['due_dates_set'] += 1
            except Exception:  # noqa: BLE001
                counts['errors'] += 1
                logger.exception('backfill_repair_receivables: due date of repair %s failed', order_pk)
    return counts


# ---------------------------------------------------------------------------
# Receivables by hand
# ---------------------------------------------------------------------------

def _plain(text: str) -> str:
    """Lower-case text with look-alikes folded (NFKC), invisible characters dropped and every separator a space."""
    folded = unicodedata.normalize('NFKC', text or '')
    folded = ''.join(ch for ch in folded if unicodedata.category(ch) != 'Cf')
    return re.sub(r'[\W_]+', ' ', folded.casefold()).strip()


def _mentions(text: str, token: str) -> bool:
    """`token` appears in `text` as a whole reference (not as the start of a longer one)."""
    wanted = _plain(token)
    return bool(wanted) and bool(re.search(rf'(?<![a-z0-9]){re.escape(wanted)}(?![a-z0-9])', _plain(text)))


def _names_repair(text: str, number: str) -> bool:
    """
    `text` carries the repair `number` («SRV-000001») as a whole reference after normalisation: any
    separators or leading zeros, look-alike and invisible characters folded. Exact, not fuzzy: a longer
    number, letters glued to it, a bare number or another word do not match.
    """
    prefix = re.sub(r'[^A-Za-z]', '', number).casefold()
    digits = re.sub(r'\D', '', number).lstrip('0')
    if not prefix or not digits:
        return False
    return bool(re.search(rf'(?<![a-z0-9]){re.escape(prefix)}\s*0*{digits}(?![a-z0-9])', _plain(text)))


def _reject_restated_repair(company, customer, *texts):
    """
    A hand-made receivable must not re-express what a repair already owes: refuse
    one that names an OPEN repair receivable of the same customer in any of its
    free texts (concept, external reference, notes, reason). This is a guard on
    wording, not an accounting identity: the amount of a repair is never typed.
    """
    rows = services.with_figures(
        Obligation.objects.filter(company=company, customer=customer, origin_type=Obligation.ORIGIN_REPAIR)
        .select_related('repair_order'))
    for row in rows:
        numbers = services.figures(row)
        if numbers['order_cancelled'] or numbers['balance'] <= 0:
            continue
        number = row.repair_order.number
        if any(_mentions(text, number) or _names_repair(text, number) or _mentions(text, row.origin_key)
               or _mentions(text, row.reference) for text in texts):
            raise services.FinanceConflict(
                f'La reparación {number} ya tiene su saldo en cuentas por cobrar ({row.reference}): '
                'cóbrala desde su orden.')


def create_receivable(company, *, actor, customer, concept, amount, due_date, reason, branch=None, currency=None,
                      category=None, owner=None, planned_method='', external_reference='', notes='',
                      issue_date=None, idempotency_key='', today: date | None = None, request=None):
    """
    A receivable entered by hand: reason required, a customer of this company,
    never a link to a repair or a sale. The capability is checked by the view.
    """
    if not isinstance(customer, Customer):
        raise services.FinanceError('Elige el cliente.')
    services._same_company(company, customer, 'El cliente')
    why = services._reason(reason, what='el motivo del cobro')
    key = services._key(idempotency_key)
    # A replay of an attempt that was accepted is answered from its key, not re-judged against repairs
    # that appeared afterwards.
    if not (key and Obligation.objects.filter(company=company, idempotency_key=key).exists()):
        _reject_restated_repair(company, customer, services._text(concept), services._text(external_reference),
                                services._text(notes), services._text(why))
    return services.create_obligation(
        company, actor=actor, concept=concept, amount=amount, due_date=due_date, branch=branch,
        currency=currency, category=category, owner=owner, planned_method=planned_method,
        external_reference=external_reference, notes=notes, issue_date=issue_date,
        idempotency_key=idempotency_key, today=today, request=request,
        direction=FinanceDirection.RECEIVABLE, customer=customer, creation_reason=why,
    )
