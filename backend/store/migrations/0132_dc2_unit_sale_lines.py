"""
DEVICE-COMMERCE-01 · DC2 — a sale line can be ONE device.

`store_orderitem.stock_unit_id` (nullable FK). The unique index
(order, product) becomes partial — WHERE stock_unit_id IS NULL — and two
constraints arrive: unique (order, stock_unit) WHERE NOT NULL, and CHECK
stock_unit_id IS NULL OR quantity = 1. Every existing line has no unit, so
every existing order satisfies all three; the previous release writes lines
without a unit and keeps meeting the old rule. `OrderUnit.method` gains the
choice 'buyer' (no SQL). Reversible while no order has two device lines of
one product; afterwards the reverse refuses rather than lose a line.
"""
import django.contrib.postgres.functions
import django.core.validators
import django.db.models.deletion
import store.models
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0131_dc2_units'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='orderitem',
            name='unique_order_line_per_product',
        ),
        migrations.AddField(
            model_name='orderitem',
            name='stock_unit',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='store.stockunit'),
        ),
        migrations.AlterField(
            model_name='orderunit',
            name='method',
            field=models.CharField(choices=[('scanner', 'Lector'), ('manual', 'Ingreso manual'), ('legacy', 'Asignado por el sistema (regla anterior)'), ('buyer', 'Elegido por el comprador')], max_length=12),
        ),
        migrations.AddConstraint(
            model_name='orderitem',
            constraint=models.UniqueConstraint(condition=models.Q(('stock_unit__isnull', True)), fields=('order', 'product'), name='unique_order_line_per_product'),
        ),
        migrations.AddConstraint(
            model_name='orderitem',
            constraint=models.UniqueConstraint(condition=models.Q(('stock_unit__isnull', False)), fields=('order', 'stock_unit'), name='unique_order_line_per_unit'),
        ),
        migrations.AddConstraint(
            model_name='orderitem',
            constraint=models.CheckConstraint(condition=models.Q(('stock_unit__isnull', True), ('quantity', 1), _connector='OR'), name='order_line_of_a_unit_is_one'),
        ),
    ]
