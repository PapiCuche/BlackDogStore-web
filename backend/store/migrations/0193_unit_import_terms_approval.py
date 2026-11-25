"""
IMPORT-TERMS-APPROVAL — the explicit approval of the devices' own sale prices and warranties that a device load
stages (owner's decision 2026-11-25). Additive, ONE node (0193, after 0192_unit_photo_provenance).

SCHEMA (on `BulkImportJob`; every column nullable or with a constant `db_default`: no table rewrite, and the previous
release keeps staging and applying jobs while this migrates — what it writes is «no approval», as before):

- `terms_approved_by` / `terms_approved_at` — who approved and when;
- `terms_approved_digest` — sha256 of what was approved (`unit_import_services.terms_digest`).

No data. VUELTA ATRÁS: the reverse drops the three columns (approvals given since are lost; a job still previewed
with terms goes back to needing whoever applies it to hold `products.manage`). It runs the quote-ticket guard first,
like every node since 0189. In production, run with `PGOPTIONS='-c lock_timeout=5s -c statement_timeout=30s'`.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def refuse_reverse_with_quote_tickets(apps, schema_editor):
    """Reverse guard: the pre-0189 print-job check refuses any `sales_quote_ticket` row, so going back with one
    would stop halfway (rehearsal R13). Every node from 0189 to the leaf carries it LAST, so a reverse from ANY
    of them refuses before anything is undone. A future node after 0193 must carry it too."""
    PrintJob = apps.get_model('store', 'PrintJob')
    tickets = PrintJob.objects.filter(kind='sales_quote_ticket').count()
    if tickets:
        raise RuntimeError(
            f'0193: hay {tickets} trabajo(s) de impresión de cotizaciones (incluidos los cancelados). '
            f"Antes de volver atrás: DELETE FROM store_printjob WHERE kind='sales_quote_ticket'; "
            f'(docs/despliegue-produccion.md §7).')

class Migration(migrations.Migration):

    dependencies = [
        ('store', '0192_unit_photo_provenance'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='bulkimportjob',
            name='terms_approved_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='bulkimportjob',
            name='terms_approved_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='bulkimportjob',
            name='terms_approved_digest',
            field=models.CharField(blank=True, db_default='', default='', max_length=64),
        ),
        # Last: on reverse it runs FIRST, before any data is undone.
        migrations.RunPython(migrations.RunPython.noop, refuse_reverse_with_quote_tickets),
    ]
