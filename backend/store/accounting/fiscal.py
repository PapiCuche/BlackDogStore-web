"""
VIEW B — THE FISCAL DOCUMENTS OF THE PERIOD AND THEIR REAL SUNAT CIRCUIT (ADR §4.2).

One row per `FiscalDocument` of the company, by its LEGAL issue date
(`issued_at`, local day). Production by default: a BETA document has no tax
validity and is listed only on request, marked as such, never summed with
production.

A credit note carries a NEGATIVE total and names its original; a debit note a
positive one. The amounts are the document's own (copied from the sale when it
was issued), never recomputed.

No XML or CDR body travels in a list: only whether the CDR is there, its
response code, whether its stored text still gives its stored hash, and that
its signature's authenticity is NOT verified (ADR-23).
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.utils import timezone

from store.models import FiscalDocumentType

from . import fiscal_states
from .operations import DOCUMENT_KIND, ZERO, cdr_hash_ok, fiscal_queryset, mask, money

TYPE_LABELS = dict(FiscalDocumentType.choices)
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


def _row(document, unmask: bool) -> tuple[dict, frozenset]:
    state = fiscal_states.state(document)
    sign = SIGN.get(document.document_type, Decimal('1'))
    live_summary = fiscal_states.live(document.summary_inclusions.all())
    present = bool(document.cdr_sha256)
    row = {
        'id': document.pk, 'key': f'sale:{document.order_id}', 'order_id': document.order_id,
        'type': document.document_type, 'type_label': TYPE_LABELS.get(document.document_type, ''),
        'document_id': document.document_id,
        'issued_at': timezone.localtime(document.issued_at).isoformat(),
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
            'granted_at': timezone.localtime(document.granted_at).isoformat() if document.granted_at else None,
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


def rows(scope, period, filters, unmask: bool) -> tuple[list, dict]:
    documents = (
        fiscal_queryset()
        .filter(company=scope.company, order__in=scope.orders, environment=filters.environment,
                issued_at__gte=period.start, issued_at__lt=period.end)
        .select_related('order')
        .order_by('issued_at', 'pk')
    )
    out = []
    sums = defaultdict(lambda: ZERO)
    counts = defaultdict(int)
    for document in documents:
        order = document.order
        if filters.channels and order.sales_channel not in filters.channels:
            continue
        if filters.kinds and 'sale' not in filters.kinds:
            continue
        if filters.seller_id is not None and order.sold_by_id != filters.seller_id:
            continue
        row, tags = _row(document, unmask)
        if filters.documents and not (filters.documents & tags):
            continue
        out.append(row)
        counts[document.document_type] += 1
        sums[document.document_type] += Decimal(row['total'])
    totals = {code: {'count': counts[code], 'total': money(sums[code])} for code in FiscalDocumentType.values}
    return out, totals
