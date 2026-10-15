"""
DEVICE-COMMERCE-01 · DC2 review — where a web hold came from, and since when.

On `store_stockunit`: `hold_origin` (`db_default` '') and `hold_started_at`
(nullable). Both are metadata only: no row is rewritten beyond the
constant default, and the previous release keeps writing units — every
column defaults in the database. Reversible.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0132_dc2_unit_sale_lines'),
    ]

    operations = [
        migrations.AddField(
            model_name='stockunit',
            name='hold_origin',
            field=models.CharField(blank=True, db_default='', default='', max_length=64),
        ),
        migrations.AddField(
            model_name='stockunit',
            name='hold_started_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
