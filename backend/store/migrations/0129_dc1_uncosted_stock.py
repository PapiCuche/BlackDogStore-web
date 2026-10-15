"""
DC1 review — the part of a shelf whose cost is not known.

`store_branchstock.uncosted_quantity` integer NOT NULL with `db_default` 0
(PostgreSQL 11+ adds it without rewriting the table). Then ONE data step,
bounded by the table: a shelf of a product counted by quantity, with stock
and no average (every shelf before DC1 — `0127` added the average empty), is
entirely of unknown cost, so `uncosted_quantity = quantity`. Without it, the
first purchase with a cost after the deployment would value the whole old
shelf at that cost.

The previous release does not know the column: it keeps writing quantities
and the column keeps its value; the Kardex writer of this release clamps it
to the quantity. Reverse: the column goes (the data step has nothing to undo).
"""
from django.db import migrations, models
from django.db.models import F


def mark_unknown_shelves(apps, schema_editor):
    BranchStock = apps.get_model('store', 'BranchStock')
    BranchStock.objects.filter(
        average_cost__isnull=True, quantity__gt=0, product__is_serialized=False,
    ).update(uncosted_quantity=F('quantity'))


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0128_dc1_sale_snapshots'),
    ]

    operations = [
        migrations.AddField(
            model_name='branchstock',
            name='uncosted_quantity',
            field=models.PositiveIntegerField(db_default=0, default=0),
        ),
        migrations.RunPython(mark_unknown_shelves, migrations.RunPython.noop),
    ]
