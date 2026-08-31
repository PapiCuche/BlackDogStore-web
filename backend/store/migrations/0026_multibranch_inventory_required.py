"""
Phase 2D, step 3 of 3 — make a Kardex line's company and branch REQUIRED.

Hand-written rather than generated: `makemigrations` asks interactively what to
do with the existing rows when a column becomes NOT NULL, and the answer is
"0025 already filled them" — which is not one of the options it offers.

From here on, a stock movement without a company and a branch cannot exist. The
rule the whole phase rests on ("units are somewhere") is now enforced by the
database and not only by the service layer.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0025_backfill_multibranch_inventory'),
    ]

    operations = [
        migrations.AlterField(
            model_name='stockmovement',
            name='company',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='stock_movements',
                to='store.company',
            ),
        ),
        migrations.AlterField(
            model_name='stockmovement',
            name='branch',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='stock_movements',
                to='store.branch',
            ),
        ),
    ]
