"""
SUNAT G5 (docs/sunat-estado-2026-11.md) — an uncertain invoice send gets its own status, and a discarded CDR is
kept on its attempt.

Schema, all additive:
- `FiscalDocument.status` gains the choice `submission_unknown`. Choices only: no SQL.
- `FiscalSubmissionAttempt.discarded_cdr` (bytea, NULL) and `discarded_cdr_sha256` (`db_default` '', so the previous
  release keeps inserting attempts), with a CHECK bounding the bytes to 256 KiB.

Data: a document left in `submission_error` whose LAST attempt was uncertain (transport_unknown, unknown_response,
provider_exception, cdr_mismatch) is the uncertain send the code already treated as such, and becomes
`submission_unknown`. Reverse: back to `submission_error`; the previous release reads the uncertainty from that same
last attempt, so nothing is lost.

CODE-ONLY ROLLBACK is the normal way back: the previous release never writes `submission_unknown`, reads the
uncertainty from the attempts and ignores the two columns. Reversing this node refuses while a discarded CDR is kept
(its bytes would be dropped), and, like every node since 0189, runs the quote-ticket guard first. In production, run
with `PGOPTIONS='-c lock_timeout=5s -c statement_timeout=30s'`.
"""

import django.db.models.functions.text
import django.db.models.lookups
from django.db import migrations, models

#: Frozen copies: a migration does not import the code that will change after it.
UNCERTAIN_RESULTS = ('transport_unknown', 'unknown_response', 'provider_exception', 'cdr_mismatch')
SUBMISSION_ERROR = 'submission_error'
SUBMISSION_UNKNOWN = 'submission_unknown'


def mark_uncertain(apps, schema_editor):
    FiscalDocument = apps.get_model('store', 'FiscalDocument')
    FiscalSubmissionAttempt = apps.get_model('store', 'FiscalSubmissionAttempt')

    marked = 0
    for document_id in FiscalDocument.objects.filter(status=SUBMISSION_ERROR).values_list('pk', flat=True).iterator():
        last = (FiscalSubmissionAttempt.objects.filter(document_id=document_id)
                .order_by('-attempt_number').values_list('result', flat=True).first())
        if last in UNCERTAIN_RESULTS:
            marked += FiscalDocument.objects.filter(pk=document_id, status=SUBMISSION_ERROR).update(
                status=SUBMISSION_UNKNOWN)
    if marked:
        print(f'\n  SUNAT G5 — {marked} comprobante(s) con envío incierto pasan a «Envío con resultado incierto»')


def unmark_uncertain(apps, schema_editor):
    FiscalDocument = apps.get_model('store', 'FiscalDocument')
    FiscalDocument.objects.filter(status=SUBMISSION_UNKNOWN).update(status=SUBMISSION_ERROR)


def refuse_reverse_with_discarded_cdrs(apps, schema_editor):
    """Reverse guard: dropping the column would lose the bytes of every CDR kept as evidence."""
    FiscalSubmissionAttempt = apps.get_model('store', 'FiscalSubmissionAttempt')
    kept = FiscalSubmissionAttempt.objects.filter(discarded_cdr__isnull=False).count()
    if kept:
        raise RuntimeError(
            f'0199: hay {kept} CDR descartado(s) guardado(s) como evidencia en store_fiscalsubmissionattempt. '
            f'Vuelva atrás sólo con el código (la versión anterior ignora las columnas) o expórtelos antes.')


def refuse_reverse_with_quote_tickets(apps, schema_editor):
    """Reverse guard: the pre-0189 print-job check refuses any `sales_quote_ticket` row, so going back with one
    would stop halfway (rehearsal R13). Every node from 0189 to the leaf carries it LAST, so a reverse from ANY
    of them refuses before anything is undone. A future node after 0199 must carry it too."""
    PrintJob = apps.get_model('store', 'PrintJob')
    tickets = PrintJob.objects.filter(kind='sales_quote_ticket').count()
    if tickets:
        raise RuntimeError(
            f'0199: hay {tickets} trabajo(s) de impresión de cotizaciones (incluidos los cancelados). '
            f"Antes de volver atrás: DELETE FROM store_printjob WHERE kind='sales_quote_ticket'; "
            f'(docs/despliegue-produccion.md §7).')


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0198_trade_in_quote'),
    ]

    operations = [
        migrations.AlterField(
            model_name='fiscaldocument',
            name='status',
            field=models.CharField(choices=[('pending', 'Pendiente de generar'), ('generated', 'XML generado'), ('signed', 'Firmado'), ('submitted', 'Enviado'), ('accepted', 'Aceptado por SUNAT'), ('accepted_observed', 'Aceptado con observaciones'), ('rejected', 'Rechazado por SUNAT'), ('submission_error', 'Error de envío'), ('submission_unknown', 'Envío con resultado incierto')], db_index=True, default='pending', max_length=24),
        ),
        migrations.AddField(
            model_name='fiscalsubmissionattempt',
            name='discarded_cdr',
            field=models.BinaryField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='fiscalsubmissionattempt',
            name='discarded_cdr_sha256',
            field=models.CharField(blank=True, db_default='', default='', max_length=64),
        ),
        migrations.AddConstraint(
            model_name='fiscalsubmissionattempt',
            constraint=models.CheckConstraint(condition=django.db.models.lookups.LessThanOrEqual(django.db.models.functions.text.Length('discarded_cdr'), 262144), name='fiscal_attempt_discarded_cdr_bounded'),
        ),
        migrations.RunPython(mark_uncertain, unmark_uncertain),
        migrations.RunPython(migrations.RunPython.noop, refuse_reverse_with_discarded_cdrs),
        # Last: on reverse it runs FIRST, before any data is undone.
        migrations.RunPython(migrations.RunPython.noop, refuse_reverse_with_quote_tickets),
    ]
