"""
VIEW B — THE FISCAL DOCUMENTS OF THE PERIOD AND THEIR REAL SUNAT CIRCUIT (ADR §4.2).

One row per `FiscalDocument` of the company, by its LEGAL issue date
(`issued_at`, local day). Production by default: a BETA document has no tax
validity and is listed only on request, marked as such, never summed with
production.

A credit note carries a NEGATIVE total and names its original; a debit note a
positive one. The amounts are the document's own (copied from the sale when it
was issued), never recomputed.

THE TOTALS ADD WHAT STANDS. Per type: the live originals (not rejected, not
voided) and the SETTLED credit and debit notes. A rejected boleta and its
reissue are one sale, so the rejected one is not added; it is reported in
`excluded_totals` with the voided documents and the notes not yet settled.
Every row is still listed: excluded from a sum is not hidden.

Paged over a list bounded by the period (`periods.MAX_DAYS`) and by
`MAX_DOCUMENTS`; above it the answer is 400.

No XML or CDR body travels in a list: only whether the CDR is there, its
response code, whether its stored text still gives its stored hash, and that
its signature's authenticity is NOT verified (ADR-23).
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.utils import timezone

from django.db.models import Q

from store.models import FiscalDocument, FiscalDocumentStatus, FiscalDocumentType

from . import fiscal_states
from .operations import DOCUMENT_KIND, ZERO, TooManyRows, cdr_hash_ok, fiscal_queryset, mask, money

TYPE_LABELS = dict(FiscalDocumentType.choices)
NOTE_TYPES = (FiscalDocumentType.CREDIT_NOTE, FiscalDocumentType.DEBIT_NOTE)

#: How many documents one answer may assemble.
MAX_DOCUMENTS = 5000
SIGN = {FiscalDocumentType.CREDIT_NOTE: Decimal('-1')}


def _tags(document, state_code) -> frozenset:
    tags = {DOCUMENT_KIND[document.document_type]}
    if state_code == 'rejected':
        tags.add('rechazado')
    elif state_code == 'voided':
        tags.add('anulado')
    elif state_code not in fiscal_states.SETTLED:
        tags.add('pendiente')
    return frozenset(tags)


def _row(document, unmask: bool, tz) -> tuple[dict, frozenset]:
    state = fiscal_states.state(document)
    sign = SIGN.get(document.document_type, Decimal('1'))
    live_summary = fiscal_states.live(document.summary_inclusions.all())
    present = bool(document.cdr_sha256)
    row = {
        'id': document.pk, 'key': f'sale:{document.order_id}', 'order_id': document.order_id,
        'type': document.document_type, 'type_label': TYPE_LABELS.get(document.document_type, ''),
        'document_id': document.document_id,
        'issued_at': timezone.localtime(document.issued_at, tz).isoformat(),
        'environment': document.environment, 'test_environment': document.environment != 'production',
        'customer': {
            'name': document.customer_legal_name, 'document_type': document.customer_doc_type,
            'document_number': mask(document.customer_doc_number, unmask),
        },
        'currency': document.currency,
        'taxable': money(document.taxable_amount * sign), 'tax': money(document.tax_amount * sign),
        'total': money(document.total * sign), 'tax_rate': f'{document.tax_rate:.4f}',
        'state': state,
        'sunat': {'code': document.sunat_response_code, 'message': document.sunat_response_message},
        'original': document.original_document.document_id if document.original_document_id else None,
        'granted': {
            'granted_at': timezone.localtime(document.granted_at, tz).isoformat() if document.granted_at else None,
            'method': document.granted_method,
            'not_granted': document.not_granted_at is not None,
        },
        'cdr': {
            'present': present, 'response_code': document.sunat_response_code if present else '',
            'hash_ok': cdr_hash_ok(document), 'authenticity': 'unverified',
        },
        'summary': ({'identifier': live_summary[0].summary.identifier, 'status': live_summary[0].summary.status}
                    if live_summary else None),
    }
    return row, _tags(document, state['code'])


def _bucket(document, state) -> str:
    """Where a document's amount goes: its type, or one of the excluded buckets."""
    if state['code'] == 'rejected':
        return 'rejected'
    if state['code'] == 'voided':
        return 'voided'
    if document.document_type in NOTE_TYPES and not state['settled']:
        return 'notes_not_settled'
    return document.document_type


def _document_condition(documents: frozenset) -> Q:
    """
    A NECESSARY condition, in SQL, for a document to carry any of these tags.

    The exact tags derive from the summary and the void (`_tags`), which is
    Python; this only has to keep every document that MIGHT match, so the cap
    counts the narrowed set and the exact check still runs on each row.
    """
    live_void = Q(void_inclusions__superseded=False)
    accepted = Q(status__in=(FiscalDocumentStatus.ACCEPTED, FiscalDocumentStatus.ACCEPTED_WITH_OBSERVATION))
    conditions = {
        'factura': Q(document_type=FiscalDocumentType.INVOICE),
        'boleta': Q(document_type=FiscalDocumentType.RECEIPT),
        'nc': Q(document_type=FiscalDocumentType.CREDIT_NOTE),
        'nd': Q(document_type=FiscalDocumentType.DEBIT_NOTE),
        'rechazado': Q(status=FiscalDocumentStatus.REJECTED),
        'anulado': live_void,
        # Not accepted on its own (a boleta reported by summary is settled later,
        # in Python), or accepted with a void still in flight.
        'pendiente': ~accepted | live_void,
    }
    found = Q(pk__in=[])
    for tag in documents:
        found |= conditions.get(tag, Q(pk__in=[]))   # internal notes are never fiscal documents
    return found


def rows(scope, period, filters, unmask: bool) -> tuple[list, dict, dict]:
    from store.sales_analytics_views import _company_tz

    tz = _company_tz(scope.company)
    documents = (
        fiscal_queryset()
        .filter(company=scope.company, order__in=scope.orders, environment=filters.environment,
                issued_at__gte=period.start, issued_at__lt=period.end)
        .select_related('order')
        .order_by('issued_at', 'pk')
    )
    # THE FILTERS GO IN THE QUERY, BEFORE THE CAP: narrowing is what the 400
    # asks the person to do, so it has to bring the count down.
    if filters.kinds and 'sale' not in filters.kinds:
        documents = documents.none()
    if filters.channels:
        documents = documents.filter(order__sales_channel__in=filters.channels)
    if filters.seller_id is not None:
        documents = documents.filter(order__sold_by_id=filters.seller_id)
    if filters.documents:
        matching = FiscalDocument.objects.filter(_document_condition(filters.documents)).values('pk')
        documents = documents.filter(pk__in=matching)
    if documents[:MAX_DOCUMENTS + 1].count() > MAX_DOCUMENTS:
        raise TooManyRows(MAX_DOCUMENTS)
    out = []
    sums = defaultdict(lambda: ZERO)
    counts = defaultdict(int)
    for document in documents:
        row, tags = _row(document, unmask, tz)
        if filters.documents and not (filters.documents & tags):
            continue
        out.append(row)
        bucket = _bucket(document, row['state'])
        counts[bucket] += 1
        sums[bucket] += Decimal(row['total'])
    totals = {code: {'count': counts[code], 'total': money(sums[code])} for code in FiscalDocumentType.values}
    excluded = {name: {'count': counts[name], 'total': money(sums[name])}
                for name in ('rejected', 'voided', 'notes_not_settled')}
    return out, totals, excluded
