"""
SUPPLY-MARGIN-01 — the company's margin over a supplier offer's cost, for a SUGGESTED price (0190, after
0189_sales_quote).

Schema, all additive: `CompanySettings.supplier_margin_percent` (0–500, two decimals) and
`CompanySettings.supplier_price_round_to` (1–100000, whole currency units), both NULLABLE with no default — the previous
release keeps inserting settings rows without naming them — and one CHECK per column (NULL passes). Every row
existing when this runs is NULL, so both CHECKs validate at once on a small table.

No data. Nothing reads the columns to write a price: the suggestion is computed when a payload is built
(`store.supplier_pricing`) and only shown to `costs.view`.

CODE-ONLY ROLLBACK: the previous release ignores both columns. Reverse (`migrate store 0189_sales_quote`) drops
the CHECKs and the columns, and with them the margin each company configured.
"""

import django.core.validators
from decimal import Decimal
from django.db import migrations, models



def refuse_reverse_with_quote_tickets(apps, schema_editor):
    """Reverse guard: the pre-0189 print-job check refuses any `sales_quote_ticket` row, so going back with one
    would stop halfway (rehearsal R13). Every node from 0189 to the leaf carries it LAST, so a reverse from ANY
    of them refuses before anything is undone. A future node after 0192 must carry it too."""
    PrintJob = apps.get_model('store', 'PrintJob')
    tickets = PrintJob.objects.filter(kind='sales_quote_ticket').count()
    if tickets:
        raise RuntimeError(
            f'0190: hay {tickets} trabajo(s) de impresión de cotizaciones (incluidos los cancelados). '
            f"Antes de volver atrás: DELETE FROM store_printjob WHERE kind='sales_quote_ticket'; "
            f'(docs/despliegue-produccion.md §7).')


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0189_sales_quote'),
    ]

    operations = [
        migrations.AddField(
            model_name='companysettings',
            name='supplier_margin_percent',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True, validators=[django.core.validators.MinValueValidator(Decimal('0')), django.core.validators.MaxValueValidator(Decimal('500'))]),
        ),
        migrations.AddField(
            model_name='companysettings',
            name='supplier_price_round_to',
            field=models.PositiveIntegerField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(100000)]),
        ),
        migrations.AddConstraint(
            model_name='companysettings',
            constraint=models.CheckConstraint(condition=models.Q(('supplier_margin_percent__gte', Decimal('0')), ('supplier_margin_percent__lte', Decimal('500'))), name='companysettings_supplier_margin_0_500'),
        ),
        migrations.AddConstraint(
            model_name='companysettings',
            constraint=models.CheckConstraint(condition=models.Q(('supplier_price_round_to__gte', 1), ('supplier_price_round_to__lte', 100000)), name='companysettings_supplier_round_to_positive'),
        ),
        # Last: on reverse it runs FIRST, before any data is undone.
        migrations.RunPython(migrations.RunPython.noop, refuse_reverse_with_quote_tickets),
    ]
