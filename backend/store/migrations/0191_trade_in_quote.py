"""
TRADE-IN-QUOTE-00 — the part-exchange block of a commercial quote (`SalesQuoteTradeIn`), in ONE additive node.

Schema only: one new table, OneToOne to `SalesQuote` (CASCADE from the quote only), with its checks (valuation and
asking price >= 0, battery 0-100 %, `imei_last4` empty or exactly four digits). No column is added to `Order`,
`PaymentMethod`, `StockMovement` or any fiscal table: the block is a referential valuation, never credit, payment,
purchase or stock (D1-D12 pending, docs/decisiones-propietario-trade-in.md). No data, no capability.

NUMBERING: named 0191 because SUPPLY-MARGIN-01 takes 0190. While that PR is not merged this node depends on
`0189_sales_quote`; whoever merges the second of the two changes `dependencies` to the other's node. Nothing else
names this file.

CODE-ONLY ROLLBACK: the previous release ignores the table; nothing it writes references it. The reverse
(dropping the table) loses the valuations of every quote: do not unapply it unless that is intended.
"""

import django.db.models.deletion
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0189_sales_quote'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SalesQuoteTradeIn',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('device_description', models.CharField(max_length=200)),
                ('capacity', models.CharField(blank=True, default='', max_length=40)),
                ('color', models.CharField(blank=True, default='', max_length=40)),
                ('condition', models.CharField(choices=[('like_new', 'Como nuevo'), ('good', 'Buen estado'), ('fair', 'Con detalles de uso'), ('damaged', 'Con daños'), ('not_working', 'No funciona')], max_length=12)),
                ('battery_health', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('accessories', models.CharField(blank=True, default='', max_length=200)),
                ('imei_last4', models.CharField(blank=True, default='', max_length=4)),
                ('customer_asking_price', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('estimated_valuation', models.DecimalField(decimal_places=2, max_digits=12)),
                ('valuation_notes', models.TextField(blank=True, default='', max_length=1000)),
                ('valued_at', models.DateTimeField(blank=True, null=True)),
                ('quote', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='trade_in', to='store.salesquote')),
                ('valued_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'constraints': [models.CheckConstraint(condition=models.Q(('estimated_valuation__gte', Decimal('0.00'))), name='sales_quote_trade_in_valuation_non_negative'), models.CheckConstraint(condition=models.Q(('customer_asking_price__isnull', True), ('customer_asking_price__gte', Decimal('0.00')), _connector='OR'), name='sales_quote_trade_in_asking_non_negative'), models.CheckConstraint(condition=models.Q(('battery_health__isnull', True), ('battery_health__lte', 100), _connector='OR'), name='sales_quote_trade_in_battery_percent'), models.CheckConstraint(condition=models.Q(('imei_last4', ''), ('imei_last4__regex', '^[0-9]{4}$'), _connector='OR'), name='sales_quote_trade_in_imei_last4_only')],
            },
        ),
    ]
