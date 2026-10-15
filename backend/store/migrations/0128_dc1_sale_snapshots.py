"""
DEVICE-COMMERCE-01 · DC1 — the snapshots of a sale line.

`OrderItem.unit_cost`, `cost_source`, `unit_price_net`, `discount_net`,
`net_amount`: what the line cost and what it was worth without IGV, frozen when
its exit is written (`margin_services`).

SAFE FOR THE RELEASE BEFORE IT. Four nullable columns and one NOT NULL column
with a database default ('unknown'), so the previous release keeps writing
order lines while this runs and after a rollback — and each line it writes is
honestly «costo pendiente». On PostgreSQL every ADD COLUMN here is a catalogue
change, without a table rewrite.

NO BACKFILL (owner's decision): history is not valued from today's catalogue.
Existing lines read NULL / 'unknown'.

Reversible: reversing drops the five columns.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0127_dc1_cost_model'),
    ]

    operations = [
        migrations.AddField(
            model_name='orderitem',
            name='cost_source',
            field=models.CharField(choices=[('average', 'Costo promedio de la sucursal'), ('unit', 'Costo específico del equipo'), ('reference', 'Costo de referencia del catálogo'), ('unknown', 'Costo pendiente')], db_default='unknown', default='unknown', max_length=16),
        ),
        migrations.AddField(
            model_name='orderitem',
            name='discount_net',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name='orderitem',
            name='net_amount',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name='orderitem',
            name='unit_cost',
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name='orderitem',
            name='unit_price_net',
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=14, null=True),
        ),
    ]
