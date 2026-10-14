"""
DEVICE-COMMERCE-01 · DC1 — where costs live.

  · `BranchStock.average_cost`   weighted average per branch (quantity products)
  · `Product.reference_cost`     the catalogue's reference cost (an estimate)
  · `StockMovement.unit_cost`    the cost each Kardex line moved at
  · `StockMovement.cost_source`  where that cost came from

SAFE FOR THE RELEASE BEFORE IT. Three nullable columns and one NOT NULL column
with a database default (`''`), so the previous release keeps inserting stock
rows, products and Kardex lines while this runs and after a rollback. On
PostgreSQL each ADD COLUMN is a catalogue change: no table rewrite.

NO BACKFILL, on purpose (owner's decision): history is not recalculated from
today's catalogue. Existing lines stay NULL with an empty source.

Reversible: reversing drops the four columns.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0126_costs_view_capability'),
    ]

    operations = [
        migrations.AddField(
            model_name='branchstock',
            name='average_cost',
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name='product',
            name='reference_cost',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name='stockmovement',
            name='cost_source',
            field=models.CharField(blank=True, choices=[('', 'Sin registro'), ('entry', 'Costo de la entrada'), ('average', 'Costo promedio'), ('unit', 'Costo específico del equipo'), ('reference', 'Costo de referencia'), ('unknown', 'Costo desconocido')], db_default='', default='', max_length=16),
        ),
        migrations.AddField(
            model_name='stockmovement',
            name='unit_cost',
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=14, null=True),
        ),
    ]
