"""
TRADE-IN VALUATION PRO · Phase 5b — the trade-in quote's roll through the print queue (docs/adr-trade-in-valuation.md
§3.5, §9, §10).

Schema, additive: `PrintJob.trade_in_quote` (nullable FK, NULL for every row the previous release writes), the
`trade_in_quote_ticket` kind (choices only) and the print-job target constraint rewritten as `_v3` (every row the
previous release writes satisfies it: no trade-in quote, the same targets as `_v2`). No data.

CODE-ONLY ROLLBACK — ONE STEP FIRST, as 0189. The previous release's queue (`printing.services.claim_next`) cancels a
ticket kind it does not know only when it has no order, which a trade-in ticket never has: it is cancelled, never
crashing the queue. Even so, run `manage.py cancel_trade_in_quote_print_jobs --apply` before switching the containers
back, so no ticket is left half-way.

SCHEMA REVERSE: `_v2` refuses any `trade_in_quote_ticket` row, so going back with one would stop halfway. This node
ends with TWO guards: `refuse_reverse_with_trade_in_tickets` (second to last) and `refuse_reverse_with_quote_tickets`
(last, as every node since 0189). Every later node carries both, in that order. In production, run with
`PGOPTIONS='-c lock_timeout=5s -c statement_timeout=30s'`.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def refuse_reverse_with_trade_in_tickets(apps, schema_editor):
    """Reverse guard: `_v2` refuses any `trade_in_quote_ticket` row. Every node from 0200 to the leaf carries this
    second to last (before the quote-ticket guard), so a reverse refuses before anything is undone."""
    PrintJob = apps.get_model('store', 'PrintJob')
    tickets = PrintJob.objects.filter(kind='trade_in_quote_ticket').count()
    if tickets:
        raise RuntimeError(
            f'0200: hay {tickets} trabajo(s) de impresión de cotizaciones de recepción (incluidos los cancelados). '
            f"Antes de volver atrás: DELETE FROM store_printjob WHERE kind='trade_in_quote_ticket'; "
            f'(docs/despliegue-produccion.md §7).')


def refuse_reverse_with_quote_tickets(apps, schema_editor):
    """Reverse guard: the pre-0189 print-job check refuses any `sales_quote_ticket` row, so going back with one
    would stop halfway (rehearsal R13). Every node from 0189 to the leaf carries it LAST, so a reverse from ANY
    of them refuses before anything is undone. A future node after 0200 must carry it too."""
    PrintJob = apps.get_model('store', 'PrintJob')
    tickets = PrintJob.objects.filter(kind='sales_quote_ticket').count()
    if tickets:
        raise RuntimeError(
            f'0200: hay {tickets} trabajo(s) de impresión de cotizaciones (incluidos los cancelados). '
            f"Antes de volver atrás: DELETE FROM store_printjob WHERE kind='sales_quote_ticket'; "
            f'(docs/despliegue-produccion.md §7).')


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0198_trade_in_quote'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='printjob',
            name='print_job_target_matches_kind_v2',
        ),
        migrations.AddField(
            model_name='printjob',
            name='trade_in_quote',
            field=models.ForeignKey(blank=True, db_default=None, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='print_jobs', to='store.tradeinquote'),
        ),
        migrations.AlterField(
            model_name='printjob',
            name='kind',
            field=models.CharField(choices=[('fiscal_ticket', 'Comprobante electrónico (80 mm)'), ('sales_note_ticket', 'Nota de venta (80 mm)'), ('label_batch', 'Lote de etiquetas'), ('sales_quote_ticket', 'Cotización (80 mm)'), ('trade_in_quote_ticket', 'Cotización de recepción (80 mm)')], max_length=30),
        ),
        migrations.AddConstraint(
            model_name='printjob',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('kind', 'label_batch'), ('label_batch__isnull', False), ('order__isnull', True), ('sales_quote__isnull', True), ('trade_in_quote__isnull', True)), models.Q(('kind', 'sales_quote_ticket'), ('sales_quote__isnull', False), ('order__isnull', True), ('label_batch__isnull', True), ('trade_in_quote__isnull', True)), models.Q(('kind', 'trade_in_quote_ticket'), ('trade_in_quote__isnull', False), ('order__isnull', True), ('label_batch__isnull', True), ('sales_quote__isnull', True)), models.Q(models.Q(('kind__in', ['label_batch', 'sales_quote_ticket', 'trade_in_quote_ticket']), _negated=True), ('order__isnull', False), ('label_batch__isnull', True), ('sales_quote__isnull', True), ('trade_in_quote__isnull', True)), _connector='OR'), name='print_job_target_matches_kind_v3'),
        ),
        # Second to last: on reverse it runs right after the quote-ticket guard, before anything is undone.
        migrations.RunPython(migrations.RunPython.noop, refuse_reverse_with_trade_in_tickets),
        # Last: on reverse it runs FIRST, before any data is undone.
        migrations.RunPython(migrations.RunPython.noop, refuse_reverse_with_quote_tickets),
    ]
