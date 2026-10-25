"""
THE STATE OF A FISCAL DOCUMENT, AS VIEW B SAYS IT (ADR §5).

DERIVED, NEVER READ FROM ONE COLUMN. A boleta stays `signed` for ever: whether
SUNAT knows about it is the state of the daily summary that carries it
(ADR-26 of `adr-fiscal-c22a1.md`). An invoice that was voided keeps its own
status too: the void is a separate communication (RA). So the state shown is a
function of the document, its live summary inclusion and its live void
inclusion.

EXHAUSTIVE ON PURPOSE. Each status enum has its own table, and a test fails the
day FISCAL-PROD-01 adds a value nobody mapped here: a status without a name
would otherwise fall through to a guess.

WHAT IS NEVER SAID. «Declarado», «presentado», «conciliado». Having an XML or a
CDR is not a filed declaration, and reconciliation with SUNAT does not exist
yet (ADR §10). A test reads every label for those words.
"""
from __future__ import annotations

from store.models import (
    FiscalDocumentStatus as DS, FiscalDocumentType, FiscalSummaryStatus as SS,
    FiscalVoidStatus as VS,
)

LABELS = {
    'generated_unsigned': 'Generado, sin firmar',
    'signed_not_sent': 'Firmado, no enviado',
    'sent_no_cdr': 'Enviado, sin constancia (pendiente de consultar)',
    'submission_error': 'Error de envío: se reintenta el mismo documento',
    'accepted': 'Aceptado por SUNAT (con CDR)',
    'accepted_observed': 'Aceptado con observaciones',
    'rejected': 'Rechazado por SUNAT',
    'pending_summary': 'Firmada, pendiente de resumen diario',
    'pending_summary_after_rejection': 'Pendiente de resumen (el anterior fue rechazado)',
    'in_summary_not_sent': 'En un resumen todavía no enviado',
    'summary_sent': 'Resumen enviado (ticket), sin veredicto',
    'summary_uncertain': 'Resumen con envío incierto: consultar el ticket',
    'reported_by_summary': 'Informada por un resumen aceptado',
    'void_in_progress': 'Comunicación de baja en curso',
    'voided': 'Dado de baja (comunicación aceptada)',
}

#: Final states: nothing left to do in the SUNAT circuit for this document.
SETTLED = frozenset({'accepted', 'accepted_observed', 'reported_by_summary', 'voided'})

#: A document's own status → state, when it travels on its own (`sendBill`).
DOCUMENT_STATUS = {
    DS.PENDING: 'generated_unsigned',
    DS.GENERATED: 'generated_unsigned',
    DS.SIGNED: 'signed_not_sent',
    DS.SUBMITTED: 'sent_no_cdr',
    DS.ACCEPTED: 'accepted',
    DS.ACCEPTED_WITH_OBSERVATION: 'accepted_observed',
    DS.REJECTED: 'rejected',
    DS.SUBMISSION_ERROR: 'submission_error',
}

#: A live summary inclusion → the boleta's state. A REJECTED summary releases its
#: lines (`superseded`); one still live in a rejected summary is read the same way.
SUMMARY_STATUS = {
    SS.GENERATED: 'in_summary_not_sent',
    SS.SIGNED: 'in_summary_not_sent',
    SS.SUBMISSION_ERROR: 'in_summary_not_sent',
    SS.SUBMITTED: 'summary_sent',
    SS.SUBMISSION_UNKNOWN: 'summary_uncertain',
    SS.ACCEPTED: 'reported_by_summary',
    SS.ACCEPTED_WITH_OBSERVATION: 'reported_by_summary',
    SS.REJECTED: 'pending_summary_after_rejection',
}

#: A live void inclusion → state. `None`: the void does not change anything.
VOID_STATUS = {
    VS.GENERATED: 'void_in_progress',
    VS.SIGNED: 'void_in_progress',
    VS.SUBMITTED: 'void_in_progress',
    VS.SUBMISSION_ERROR: 'void_in_progress',
    VS.SUBMISSION_UNKNOWN: 'void_in_progress',
    VS.ACCEPTED: 'voided',
    VS.ACCEPTED_WITH_OBSERVATION: 'voided',
    VS.REJECTED: None,
}

assert set(DOCUMENT_STATUS.values()) | set(SUMMARY_STATUS.values()) | {
    v for v in VOID_STATUS.values() if v} <= set(LABELS), 'un estado sin etiqueta'


def travels_by_summary(document) -> bool:
    """A boleta, or a note on a boleta (series B), is reported by the daily summary."""
    if document.document_type == FiscalDocumentType.RECEIPT:
        return True
    return document.document_type in ('07', '08') and (document.series or '').startswith('B')


def live(rows):
    return [row for row in rows if not row.superseded]


def state(document) -> dict:
    """
    `{code, label}` for `document`. Expects `summary_inclusions` and
    `void_inclusions` prefetched (every row; the live ones are picked here) with
    their headers, so a list never asks the database per document.
    """
    code = None
    voids = live(document.void_inclusions.all())
    if voids:
        code = VOID_STATUS[voids[0].void_communication.status]
    if code is None:
        if document.status == DS.REJECTED or not travels_by_summary(document):
            code = DOCUMENT_STATUS[document.status]
        elif document.status in (DS.PENDING, DS.GENERATED):
            code = DOCUMENT_STATUS[document.status]
        else:
            inclusions = list(document.summary_inclusions.all())
            current = live(inclusions)
            if current:
                code = SUMMARY_STATUS[current[0].summary.status]
            elif inclusions:
                code = 'pending_summary_after_rejection'
            else:
                code = 'pending_summary'
    return {'code': code, 'label': LABELS[code], 'settled': code in SETTLED}
