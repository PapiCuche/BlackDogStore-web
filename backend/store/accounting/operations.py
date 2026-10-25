"""
VIEW A — EVERY ECONOMIC OPERATION OF THE PERIOD, ONCE (ADR §2, §4.1).

THE KEY. `sale:<order.id>` for every sale, online or at the till, and
`repair:<repair_order.id>` for every repair that collected money. Nothing else
opens an operation.

THE AMOUNT HAS ONE SOURCE. A sale's money is `Order.total` and the breakdown the
sale froze (C2.1). Its internal note, its receipt, its credit notes and its
payment attempts are attached to the same row as STATE and EVIDENCE; none of
them adds a cent. That is what keeps a sale with a note, a boleta and an
authorised payment from being counted three times.

ADJUSTMENTS ARE EVENTS OF THE SAME KEY, ON THEIR OWN DATE — the rules of
`sales_analytics_views._collections`, so both screens add up the same money:

    sale          by `paid_at`             (P2, provisional)
    refund        CONFIRMED by `confirmed_at` → leaves `net`
                  PENDING by `created_at`     → said, not subtracted
    late charge   a DUPLICATE charge on a cancelled order, by `confirmed_at`,
                  never one taken with TEST keys
    repair        every payment, by `received_at` (P1, provisional)
    reversal      a reversed repair payment, NEGATIVE, by `reversed_at` — so
                  reversing a payment months later never rewrites the month
                  it was received in

THE LEGACY STATUS `REFUNDED`. No current flow writes it (refunds keep the order
CANCELLED), but history may hold it. Such an order was paid, so it is an
operation here, flagged `REFUNDED_STATUS`; the sales dashboard leaves it out.
That, and the unbranched history a company-wide caller sees, are the only
differences from `_collections`, and a test states them.

A credit note does NOT subtract income: the money that went back does (the
refund). Subtracting both would subtract twice; reconciling the two is view C's
job (`REFUND_WITHOUT_NOTE`, `NOTE_WITHOUT_REFUND`).

TEST PAYMENTS (P4, provisional). An online order whose every authorised attempt
was taken with TEST keys is kept OUT of income — it, its refunds and its late
charges — and reported under `excluded_test`. So `gross + excluded_test.gross`
is exactly the dashboard's gross.

READ ONLY. One query for the orders and a fixed number of prefetches, whatever
the number of rows. ASSEMBLED IN MEMORY until the views aggregate in the
database (ADR §9): `periods.LEDGER_MAX_DAYS` bounds the window and
`MAX_LEDGER_ROWS` the rows, and a question over either answers 400 instead of
holding the single server process.

DATES are written in the company's timezone, the same zone the period is cut in.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import CharField, Func, Prefetch
from django.db.models.functions import SHA256
from django.utils import timezone

from store.models import (
    FiscalDailySummaryDocument, FiscalDocument, FiscalDocumentType, FiscalVoidCommunicationDocument,
    Order, OrderRefund, OrderReturn, PaymentTransaction, RepairPayment, SalesChannel, SalesNote,
)
from store.tax_services import breakdown_for_order

from . import defaults, fiscal_states, pending

ZERO = Decimal('0.00')
CENT = Decimal('0.01')

#: How many rows (sales, adjustments, repairs) one answer may assemble.
MAX_LEDGER_ROWS = 5000


class TooManyRows(ValueError):
    """The question covers more rows than one answer assembles. A person reads it."""

    def __init__(self, limit: int):
        super().__init__(
            f'El período tiene más de {limit} filas: estrecha el período o los filtros.')

DOCUMENT_KIND = {
    FiscalDocumentType.INVOICE: 'factura', FiscalDocumentType.RECEIPT: 'boleta',
    FiscalDocumentType.CREDIT_NOTE: 'nc', FiscalDocumentType.DEBIT_NOTE: 'nd',
}
ORIGINAL_TYPES = (FiscalDocumentType.INVOICE, FiscalDocumentType.RECEIPT)


def money(value) -> str:
    return str((value or ZERO).quantize(CENT))


def mask(number: str, unmask: bool) -> str:
    if unmask or not number:
        return number or ''
    return '****' + number[-4:] if len(number) > 4 else '****'


class Utf8Sha256(Func):
    """
    SHA-256 of a text column's UTF-8 bytes, in the database.

    Django's own `SHA256` needs `pgcrypto` on PostgreSQL, which production does
    not promise; PostgreSQL 11+ ships `sha256(bytea)` built in. Elsewhere (the
    SQLite development copy) Django's function does the same.
    """
    output_field = CharField()

    def as_postgresql(self, compiler, connection, **extra):
        return self.as_sql(compiler, connection,
                           template="ENCODE(SHA256(CONVERT_TO(%(expressions)s, 'UTF8')), 'hex')", **extra)

    def as_sqlite(self, compiler, connection, **extra):
        return SHA256(*self.get_source_expressions()).as_sqlite(compiler, connection, **extra)


def fiscal_queryset():
    """Documents without their XML bodies, with what the state is derived from."""
    return (
        FiscalDocument.objects.defer('signed_xml', 'cdr_xml')
        .annotate(cdr_sha256_now=Utf8Sha256('cdr_xml'))
        .select_related('original_document')
        .prefetch_related(
            Prefetch('summary_inclusions',
                     queryset=FiscalDailySummaryDocument.objects.select_related('summary').order_by('pk')),
            Prefetch('void_inclusions',
                     queryset=FiscalVoidCommunicationDocument.objects.select_related('void_communication')
                     .order_by('pk')),
        )
        .order_by('pk')
    )


def cdr_hash_ok(document):
    """None without a CDR; else whether its stored text still gives its stored hash."""
    if not document.cdr_sha256:
        return None
    return document.cdr_sha256_now == document.cdr_sha256


def _one(instance, name):
    try:
        return getattr(instance, name)
    except Exception:  # RelatedObjectDoesNotExist of a reverse one-to-one
        return None


@dataclass
class SaleFacts:
    """Everything known about one sale, read once and shared by views A and C."""

    order: Order
    documents: list
    states: dict                 # document pk → state dict
    live_original: object
    note: SalesNote | None
    cancellation: object
    refunds_confirmed: list
    refunds_pending: list
    closed_returns: list
    test_only: bool
    env_unknown: bool
    breakdown: object
    breakdown_source: str
    tags: frozenset = field(default_factory=frozenset)
    flags: list = field(default_factory=list)

    @property
    def key(self) -> str:
        return f'sale:{self.order.pk}'

    @property
    def excluded(self) -> bool:
        return defaults.EXCLUDE_TEST_PAYMENTS and self.test_only

    @property
    def note_issued(self) -> bool:
        return self.note is not None and self.note.status == SalesNote.STATUS_ISSUED

    @property
    def commercial_state(self) -> str:
        if self.closed_returns:
            return 'devuelta'
        if self.order.status == Order.Status.CANCELLED:
            return 'anulada'
        if self.order.status == Order.Status.REFUNDED:
            return 'reembolsada'
        return 'pagada'

    def settled_credit_notes(self):
        return [d for d in self.documents if d.document_type == FiscalDocumentType.CREDIT_NOTE
                and self.states[d.pk]['settled']]


def load_orders(ids):
    if not ids:
        return []
    return list(
        Order.objects.filter(pk__in=ids)
        .select_related('company', 'fulfillment_branch', 'sales_note', 'cancellation')
        .prefetch_related(
            Prefetch('fiscal_documents', queryset=fiscal_queryset()),
            Prefetch('refunds', queryset=OrderRefund.objects.order_by('pk')),
            Prefetch('returns', queryset=OrderReturn.objects.filter(status=OrderReturn.STATUS_CLOSED)),
            Prefetch('payment_transactions', queryset=PaymentTransaction.objects.filter(
                status__in=(PaymentTransaction.Status.AUTHORIZED, PaymentTransaction.Status.DUPLICATE),
            ).order_by('pk')),
        )
    )


def analyse(order) -> SaleFacts:
    documents = list(order.fiscal_documents.all())
    states = {d.pk: fiscal_states.state(d) for d in documents}
    originals = [d for d in documents if d.document_type in ORIGINAL_TYPES]
    live = [d for d in originals if d.status != 'rejected']
    authorised = [t for t in order.payment_transactions.all()
                  if t.status == PaymentTransaction.Status.AUTHORIZED]
    online = order.sales_channel == SalesChannel.ONLINE
    environments = {t.environment for t in authorised}
    refunds = list(order.refunds.all())
    frozen = order.tax_rate is not None and order.taxable_amount is not None
    facts = SaleFacts(
        order=order, documents=documents, states=states,
        live_original=live[-1] if live else None,
        note=_one(order, 'sales_note'), cancellation=_one(order, 'cancellation'),
        refunds_confirmed=[r for r in refunds if r.status == OrderRefund.STATUS_CONFIRMED],
        refunds_pending=[r for r in refunds if r.status == OrderRefund.STATUS_PENDING],
        closed_returns=list(order.returns.all()),
        test_only=online and bool(authorised) and environments == {defaults.PAYMENT_ENV_TEST},
        env_unknown=online and (not authorised or defaults.PAYMENT_ENV_UNKNOWN in environments),
        breakdown=breakdown_for_order(order), breakdown_source='frozen' if frozen else 'rebuilt',
    )
    facts.tags = _tags(facts)
    facts.flags = pending.sale_flags(facts, originals)
    return facts


def _tags(facts: SaleFacts) -> frozenset:
    """What the document filter matches: types and circuit states of every paper."""
    tags = set()
    for document in facts.documents:
        tags.add(DOCUMENT_KIND[document.document_type])
        code = facts.states[document.pk]['code']
        if code == 'rejected':
            tags.add('rechazado')
        elif code == 'voided':
            tags.add('anulado')
        elif code not in fiscal_states.SETTLED:
            tags.add('pendiente')
    if facts.note is not None:
        tags.add('nota_interna')
        if facts.note.status == SalesNote.STATUS_VOID:
            tags.add('anulado')
    if facts.live_original is None and not facts.note_issued:
        tags.add('sin_documento')
    return frozenset(tags)


def _local(moment, tz):
    return timezone.localtime(moment, tz).isoformat() if moment else None


def _customer(name, doc_type, number, unmask):
    return {'name': name or '', 'document_type': doc_type or '', 'document_number': mask(number, unmask)}


def _documents(facts: SaleFacts) -> list:
    out = [{
        'kind': DOCUMENT_KIND[d.document_type], 'number': d.document_id,
        'state': facts.states[d.pk]['code'], 'environment': d.environment,
    } for d in facts.documents]
    if facts.note is not None:
        out.append({'kind': 'nota_interna', 'number': facts.note.number, 'state': facts.note.status,
                    'environment': ''})
    return out


def _primary(facts: SaleFacts):
    if facts.live_original is not None:
        document = facts.live_original
        return {'kind': DOCUMENT_KIND[document.document_type], 'number': document.document_id,
                'environment': document.environment}
    if facts.note is not None:
        return {'kind': 'nota_interna', 'number': facts.note.number}
    return None


def _base_row(facts: SaleFacts, unmask: bool) -> dict:
    order = facts.order
    branch = order.fulfillment_branch
    return {
        'key': facts.key, 'kind': 'sale', 'order_id': order.pk,
        'branch': {'id': branch.pk, 'name': branch.name} if branch else None,
        'channel': order.sales_channel,
        'seller': order.seller_name_snapshot or None,
        'customer': _customer(order.customer_name, order.document_type, order.document_number, unmask),
        'currency': (order.currency or 'PEN').upper(),
        'commercial_state': facts.commercial_state,
        'excluded_from_income': facts.excluded,
        'primary_document': _primary(facts),
        'flags': list(facts.flags),
    }


def operation_row(facts: SaleFacts, unmask: bool, tz) -> dict:
    order, tax = facts.order, facts.breakdown
    confirmed = sum((r.amount for r in facts.refunds_confirmed), ZERO)
    kept = sum((r.deduction_amount or ZERO for r in facts.closed_returns), ZERO)
    payment = {'method': order.payment_method}
    if order.sales_channel == SalesChannel.POS:
        payment['reference'] = order.payment_reference
    else:
        authorised = [t for t in order.payment_transactions.all()
                      if t.status == PaymentTransaction.Status.AUTHORIZED]
        payment['attempts'] = [{'provider': t.provider, 'environment': t.environment or 'desconocido'}
                               for t in authorised]
    return {
        **_base_row(facts, unmask),
        'row_type': 'operation', 'event': 'sale', 'date': _local(order.paid_at, tz), '_at': order.paid_at,
        'gross': money(order.total), 'amount': money(order.total),
        'taxable': money(tax.taxable_amount), 'tax': money(tax.tax_amount), 'tax_rate': f'{tax.tax_rate:.4f}',
        'breakdown_source': facts.breakdown_source,
        'refunds_confirmed': money(confirmed), 'kept': money(kept),
        'documents': _documents(facts), 'payment': payment,
    }


def adjustment_row(facts: SaleFacts, event: str, amount: Decimal, moment, unmask: bool, tz) -> dict:
    return {
        **_base_row(facts, unmask),
        'row_type': 'adjustment', 'event': event, 'date': _local(moment, tz), '_at': moment,
        'amount': money(amount), 'affects_net': event != 'refund_pending',
        'documents': _documents(facts),
    }


def _repair_base(repair, currency: str, unmask: bool) -> dict:
    customer = repair.customer
    branch = repair.branch
    return {
        'key': f'repair:{repair.pk}', 'kind': 'repair', 'repair_order_id': repair.pk,
        'repair_number': repair.number,
        'branch': {'id': branch.pk, 'name': branch.name} if branch else None,
        'channel': 'service', 'seller': None,
        'customer': _customer(customer.display_name, customer.document_type, customer.document_number, unmask),
        'currency': currency.upper(),
        'commercial_state': 'servicio', 'excluded_from_income': False,
        'primary_document': None, 'flags': list(pending.REPAIR_FLAGS),
        'documents': [],
    }


def repair_row(repair, payments: list, unmask: bool, tz) -> dict:
    """Every payment received in the period, reversed later or not: it was received."""
    total = sum((p.amount for p in payments), ZERO)
    last = max(p.received_at for p in payments)
    return {
        **_repair_base(repair, payments[0].currency, unmask),
        'row_type': 'operation', 'event': 'service_collection', 'date': _local(last, tz), '_at': last,
        'gross': money(total), 'amount': money(total), 'payments': len(payments),
        'taxable': None, 'tax': None, 'tax_rate': None, 'breakdown_source': 'none',
    }


def reversal_row(repair, payment, unmask: bool, tz) -> dict:
    """A reversal is its own NEGATIVE event, on the day it was made."""
    return {
        **_repair_base(repair, payment.currency, unmask),
        'row_type': 'adjustment', 'event': 'service_reversal',
        'date': _local(payment.reversed_at, tz), '_at': payment.reversed_at,
        'amount': money(-payment.amount), 'affects_net': True,
    }


# ---------------------------------------------------------------------------
# The period
# ---------------------------------------------------------------------------

@dataclass
class Ledger:
    """The rows of a period (unfiltered), and the facts behind its sales."""

    rows: list
    facts: dict                  # order pk → SaleFacts, for every sale a row refers to
    unpaid: list                 # orders awaiting payment created in the period (P2 → view C)


def build(scope, period, unmask: bool) -> Ledger:
    from store.sales_analytics_views import _company_tz

    tz = _company_tz(scope.company)
    window = {f'{defaults.SALE_PERIOD_FIELD}__gte': period.start, f'{defaults.SALE_PERIOD_FIELD}__lt': period.end}
    sold_ids = set(scope.orders.filter(paid=True, **window).values_list('pk', flat=True)[:MAX_LEDGER_ROWS + 1])
    if len(sold_ids) > MAX_LEDGER_ROWS:
        raise TooManyRows(MAX_LEDGER_ROWS)

    refunds = list(OrderRefund.objects.filter(order__in=scope.orders).filter(
        status=OrderRefund.STATUS_CONFIRMED, confirmed_at__gte=period.start, confirmed_at__lt=period.end,
    ).order_by('confirmed_at', 'pk')) + list(OrderRefund.objects.filter(order__in=scope.orders).filter(
        status=OrderRefund.STATUS_PENDING, created_at__gte=period.start, created_at__lt=period.end,
    ).order_by('created_at', 'pk'))
    late = list(PaymentTransaction.objects.filter(
        order__in=scope.orders.filter(status=Order.Status.CANCELLED),
        status=PaymentTransaction.Status.DUPLICATE,
        confirmed_at__gte=period.start, confirmed_at__lt=period.end,
    ).exclude(environment=defaults.PAYMENT_ENV_TEST).order_by('confirmed_at', 'pk'))

    received = list(RepairPayment.objects.filter(
        company=scope.company, repair_order__in=scope.repairs,
        received_at__gte=period.start, received_at__lt=period.end).order_by('received_at', 'pk'))
    reversed_ = list(RepairPayment.objects.filter(
        company=scope.company, repair_order__in=scope.repairs,
        reversed_at__gte=period.start, reversed_at__lt=period.end).order_by('reversed_at', 'pk'))
    payments = defaultdict(list)
    for payment in received:
        payments[payment.repair_order_id].append(payment)
    if len(sold_ids) + len(refunds) + len(late) + len(payments) + len(reversed_) > MAX_LEDGER_ROWS:
        raise TooManyRows(MAX_LEDGER_ROWS)

    ids = sold_ids | {r.order_id for r in refunds} | {t.order_id for t in late}
    facts = {order.pk: analyse(order) for order in load_orders(ids)}

    rows = [operation_row(facts[pk], unmask, tz) for pk in sold_ids]
    for refund in refunds:
        confirmed = refund.status == OrderRefund.STATUS_CONFIRMED
        rows.append(adjustment_row(
            facts[refund.order_id], 'refund' if confirmed else 'refund_pending', -refund.amount,
            refund.confirmed_at if confirmed else refund.created_at, unmask, tz))
    for charge in late:
        rows.append(adjustment_row(facts[charge.order_id], 'late_charge', charge.amount, charge.confirmed_at,
                                   unmask, tz))

    repair_ids = set(payments) | {p.repair_order_id for p in reversed_}
    if repair_ids:
        repairs = {r.pk: r for r in scope.repairs.filter(pk__in=repair_ids).select_related('branch', 'customer')}
        for pk, received_here in payments.items():
            rows.append(repair_row(repairs[pk], received_here, unmask, tz))
        for payment in reversed_:
            rows.append(reversal_row(repairs[payment.repair_order_id], payment, unmask, tz))

    rows.sort(key=lambda r: (r['_at'], 0 if r['row_type'] == 'operation' else 1, r['key']))
    unpaid = list(scope.orders.filter(
        status=Order.Status.PENDING_PAYMENT, paid=False,
        created_at__gte=period.start, created_at__lt=period.end,
    ).select_related('fulfillment_branch').order_by('created_at', 'pk')[:MAX_LEDGER_ROWS + 1])
    if len(rows) + len(unpaid) > MAX_LEDGER_ROWS:
        raise TooManyRows(MAX_LEDGER_ROWS)
    return Ledger(rows=rows, facts=facts, unpaid=unpaid)


def matches(row, filters, tags_of) -> bool:
    """Whether a row of view A passes the filters. `tags_of(row)` gives its document tags."""
    if filters.channels and row['channel'] not in filters.channels:
        return False
    if filters.kinds and row['kind'] not in filters.kinds:
        return False
    if filters.operations and row['commercial_state'] not in filters.operations:
        return False
    if filters.seller_id is not None:
        if row['kind'] != 'sale' or row['_sold_by'] != filters.seller_id:
            return False
    if filters.documents and not (filters.documents & tags_of(row)):
        return False
    return True


def filtered(ledger: Ledger, filters) -> list:
    def tags_of(row):
        if row['kind'] == 'repair':
            return frozenset({'sin_documento'})
        return ledger.facts[row['order_id']].tags

    out = []
    for row in ledger.rows:
        if row['kind'] == 'sale':
            row['_sold_by'] = ledger.facts[row['order_id']].order.sold_by_id
        if matches(row, filters, tags_of):
            out.append(row)
    return out


def totals(rows) -> dict:
    sums = defaultdict(lambda: ZERO)
    counts = defaultdict(int)
    currencies = set()
    for row in rows:
        amount = Decimal(row['amount'])
        currencies.add(row['currency'])
        bucket = 'test' if row['excluded_from_income'] else 'in'
        if row['row_type'] == 'operation':
            counts[(bucket, 'operations')] += 1
            if row['kind'] == 'repair':
                sums[(bucket, 'service')] += amount
            else:
                sums[(bucket, 'sales')] += amount
                sums[(bucket, 'taxable')] += Decimal(row['taxable'])
                sums[(bucket, 'tax')] += Decimal(row['tax'])
        elif row['event'] == 'late_charge':
            sums[(bucket, 'late')] += amount
        elif row['event'] == 'service_reversal':
            sums[(bucket, 'reversals')] += -amount
        elif row['event'] == 'refund':
            sums[(bucket, 'refunds')] += -amount
        elif row['event'] == 'refund_pending':
            sums[(bucket, 'pending')] += -amount
    gross = sums[('in', 'sales')] + sums[('in', 'late')]
    net = gross - sums[('in', 'refunds')]
    return {
        'operations': counts[('in', 'operations')],
        'sales_gross': money(sums[('in', 'sales')]),
        'sales_taxable': money(sums[('in', 'taxable')]),
        'sales_tax': money(sums[('in', 'tax')]),
        'late_charges': money(sums[('in', 'late')]),
        'gross': money(gross),
        'refunds_confirmed': money(sums[('in', 'refunds')]),
        'refunds_pending': money(sums[('in', 'pending')]),
        'net': money(net),
        'service_collections': money(sums[('in', 'service')]),
        'service_reversals': money(sums[('in', 'reversals')]),
        'income': money(net + sums[('in', 'service')] - sums[('in', 'reversals')]),
        'excluded_test': {
            'operations': counts[('test', 'operations')],
            'gross': money(sums[('test', 'sales')] + sums[('test', 'late')]),
            'refunds_confirmed': money(sums[('test', 'refunds')]),
        },
        'currencies': sorted(currencies),
    }


def public(row) -> dict:
    return {k: v for k, v in row.items() if not k.startswith('_')}

