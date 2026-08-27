"""
Make Category.company and Product.company mandatory — SaaS Phase 2B.

Written by hand rather than generated: `makemigrations` prompts interactively
when a nullable field becomes non-nullable, and the answer here is not a default
value but "0019 already filled every row". Splitting the change into
0018 (nullable) → 0019 (backfill) → 0020 (required) is what makes that true, and
what keeps the upgrade safe on a database that already holds a catalogue.

Reverting this migration simply makes the columns nullable again; no data is
touched, so 0020 → 0019 → 0018 is a lossless path back.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0019_backfill_catalog_company'),
    ]

    operations = [
        migrations.AlterField(
            model_name='category',
            name='company',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='categories',
                to='store.company',
            ),
        ),
        migrations.AlterField(
            model_name='product',
            name='company',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='products',
                to='store.company',
            ),
        ),
    ]
