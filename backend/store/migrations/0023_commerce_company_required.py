"""
Make Order.company and Coupon.company mandatory — SaaS Phase 2C.

Hand-written for the same reason as 0020: `makemigrations` prompts interactively
when a nullable field becomes non-nullable, and the answer is not a default value
but "0022 already filled every row".

Reverting simply makes the columns nullable again; no data is touched, so
0023 → 0022 → 0021 is a lossless path back.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0022_backfill_commerce_company'),
    ]

    operations = [
        migrations.AlterField(
            model_name='coupon',
            name='company',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='coupons',
                to='store.company',
            ),
        ),
        migrations.AlterField(
            model_name='order',
            name='company',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='orders',
                to='store.company',
            ),
        ),
    ]
