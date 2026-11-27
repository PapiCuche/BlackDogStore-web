"""
SUNAT-PROD-C3 — where this installation stands with SUNAT PRODUCTION, as booleans and counts.

    python manage.py fiscal_production_state [--json]

A marker for operators and for the release kit: before deploying or rolling back a release,
it says whether production is chosen, allowed and running, and whether anything was issued
there that a rollback would leave without a way to be sent, consulted or reconciled.

READ-ONLY. It writes nothing, opens no socket and prints no secret, no RUC, no SOL user and
no document identifier: only booleans, counts by status and the earliest legal deadline.
Its exit status is always 0: it informs; `fiscal_preflight` is the one that says GO/NO-GO.
"""
import json
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from store import fiscal_config
from store.fiscal_preflight import INVOICE_DEADLINE_DAYS, RECEIPT_DEADLINE_DAYS, SUMMARY_REACHED_SUNAT
from store.models import (
    FISCAL_NOTE_TYPES, FiscalDailySummary, FiscalDailySummaryDocument, FiscalDocument, FiscalDocumentStatus,
    FiscalDocumentType, FiscalEnvironment, FiscalVoidCommunication,
)

#: Not yet with a verdict from SUNAT, and still owed to it.
_UNSENT = (FiscalDocumentStatus.PENDING, FiscalDocumentStatus.GENERATED, FiscalDocumentStatus.SIGNED,
           FiscalDocumentStatus.SUBMISSION_ERROR)


def _by_status(queryset) -> dict:
    counts = {}
    for status in queryset.values_list('status', flat=True):
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def _earliest_deadline(documents):
    """`(date, kind)` of the first legal deadline among what is still owed to SUNAT, or `(None, '')`."""
    candidates = []
    individual = documents.filter(status__in=_UNSENT).exclude(document_type=FiscalDocumentType.RECEIPT).exclude(
        document_type__in=FISCAL_NOTE_TYPES, series__startswith='B')
    first = individual.order_by('issued_at').values_list('issued_at', flat=True).first()
    if first is not None:
        candidates.append((timezone.localdate(first) + timedelta(days=INVOICE_DEADLINE_DAYS), 'factura_o_nota'))
    informed = FiscalDailySummaryDocument.objects.filter(
        superseded=False, summary__status__in=SUMMARY_REACHED_SUNAT).values('document_id')
    boletas = documents.filter(
        document_type=FiscalDocumentType.RECEIPT,
        status__in=(FiscalDocumentStatus.PENDING, FiscalDocumentStatus.GENERATED, FiscalDocumentStatus.SIGNED),
    ).exclude(pk__in=informed)
    first = boletas.order_by('issued_at').values_list('issued_at', flat=True).first()
    if first is not None:
        candidates.append((timezone.localdate(first) + timedelta(days=RECEIPT_DEADLINE_DAYS), 'boleta_sin_resumen'))
    return min(candidates) if candidates else (None, '')


def production_state() -> dict:
    active = fiscal_config._console_row()
    production = FiscalEnvironment.PRODUCTION
    documents = FiscalDocument.objects.filter(environment=production)
    deadline, kind = _earliest_deadline(documents)
    return {
        'console_configured': active is not None,
        'console_environment': fiscal_config.console_environment(active.public) if active is not None else '',
        'console_enabled': bool(active is not None and active.enabled),
        'console_paused': bool(active is not None and active.enabled and active.paused),
        'fiscal_production_enabled': fiscal_config.production_enabled(),
        'fiscal_consult_enabled': fiscal_config.fiscal_consult_enabled(),
        'fiscal_environment_setting_is_beta': getattr(settings, 'FISCAL_ENVIRONMENT', 'beta') == 'beta',
        'production_documents': _by_status(documents),
        'production_documents_owed': documents.filter(status__in=_UNSENT).count(),
        'production_summaries': _by_status(FiscalDailySummary.objects.filter(environment=production)),
        'production_voids': _by_status(FiscalVoidCommunication.objects.filter(environment=production)),
        'earliest_deadline': deadline.isoformat() if deadline else None,
        'earliest_deadline_kind': kind,
    }


class Command(BaseCommand):
    help = ('Estado de SUNAT PRODUCCIÓN en esta instalación, sólo con booleanos y conteos: modo de la consola, '
            'interruptores, comprobantes/RC/RA de producción por estado y el primer plazo legal. Sólo lee.')

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true', help='Salida en JSON.')

    def handle(self, *args, **options):
        state = production_state()
        if options['json']:
            self.stdout.write(json.dumps(state, ensure_ascii=False, indent=2))
            return
        for key, value in state.items():
            if isinstance(value, dict):
                value = ', '.join(f'{k}={v}' for k, v in value.items()) or '0'
            self.stdout.write(f'{key}: {value}')
