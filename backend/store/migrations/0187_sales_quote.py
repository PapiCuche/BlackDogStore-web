"""
SALES-QUOTE-01 — commercial quotes (`SalesQuote`, `SalesQuoteItem`), in ONE self-contained node.

Schema, all additive: the two quote tables; `Order.source_quote` and `PrintJob.sales_quote` (nullable, `db_default`
NULL, so the previous release keeps inserting orders and print jobs while this migrates); the `sales_quote` kind of
`InternalSequence` and of `PrintJob` (choices only); and the print-job target constraint rewritten to admit a quote
ticket (renamed `_v2`; every row the previous release writes satisfies it, so a code-only rollback stays valid).

Data: the three `sales.quotes.*` capabilities, by the rule of 0157/0161 — every role holding `company.manage` (an
administrator, edited or not) gets all three; every other role holding `sales.pos.use` (the counter, `Ventas`) gets
`view` and `manage`, never `approve`. A new company gets them from `company_provisioning`, a legacy membership from
`tenancy.LEGACY_ROLE_CAPABILITIES`. The rule and the preset before this node are FROZEN here. The REVERSE IS A NO-OP
for the data (taking authority away in a rollback is the riskier direction).

RENUMBERING: nothing outside this file names it except the capability-chain tests, which find it by its suffix
(`store.test_sales_quotes.sales_quote_migration`). Rename the file and its `dependencies` and nothing else changes.
"""

import django.db.models.deletion
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


NEW_CAPABILITIES = ('sales.quotes.view', 'sales.quotes.manage', 'sales.quotes.approve')
#: What the counter (holders of `sales.pos.use` without `company.manage`) receives.
COUNTER_CAPABILITIES = ('sales.quotes.view', 'sales.quotes.manage')
HOLDERS_OF = 'company.manage'
COUNTER_HOLDERS_OF = 'sales.pos.use'

#: `Administrador` immediately before this migration, FROZEN: where 0187_used_unit_readiness (USED-READY-01) left it.
ADMIN_PREVIOUS = frozenset({
    'accounting.export', 'accounting.view',
    'areas.manage', 'communications.manage', 'company.manage', 'company.view', 'costs.view',
    'finance.manage', 'finance.payments.record', 'finance.view',
    'inventory.adjust', 'inventory.counts.approve', 'inventory.labels.print', 'inventory.labels.sensitive',
    'inventory.reports', 'inventory.units.inspect', 'inventory.view',
    'memberships.manage', 'memberships.view', 'products.manage', 'products.view', 'reports.view',
    'roles.manage', 'sales.analytics.view', 'sales.commissions.manage', 'sales.commissions.view',
    'sales.discounts.apply', 'sales.documents.send_copy', 'sales.fiscal.issue', 'sales.fiscal.view',
    'sales.notes.manage', 'sales.orders.manage', 'sales.orders.view', 'sales.pos.assign_seller', 'sales.pos.use',
    'sales.promotions.manage', 'sales.promotions.view', 'service.customers.manage',
    'service.customers.view', 'service.delivery.manage', 'service.devices.manage',
    'service.devices.view', 'service.diagnostic.manage', 'service.manage', 'service.orders.assign',
    'service.orders.create', 'service.orders.manage', 'service.orders.view',
    'service.payments.collect', 'service.payments.manage', 'service.quality.manage',
    'service.quotes.record_decision', 'service.repair.manage', 'settings.manage', 'settings.view',
})


def grant(apps, schema_editor):
    CompanyRole = apps.get_model('store', 'CompanyRole')

    extended = 0
    for role in CompanyRole.objects.all().iterator():
        held = frozenset(role.capabilities or [])
        if HOLDERS_OF in held:
            wanted = frozenset(NEW_CAPABILITIES)
        elif COUNTER_HOLDERS_OF in held:
            wanted = frozenset(COUNTER_CAPABILITIES)
        else:
            continue
        if held >= wanted:
            continue
        role.capabilities = sorted(held | wanted)
        role.save(update_fields=['capabilities', 'updated_at'])
        extended += 1

    if extended:
        print(f'\n  SALES-QUOTE-01 — cotizaciones otorgadas a {extended} rol(es)')


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0187_used_unit_readiness'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SalesQuote',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('customer_name', models.CharField(blank=True, default='', max_length=255)),
                ('customer_document_type', models.CharField(blank=True, default='', max_length=10)),
                ('customer_document_number', models.CharField(blank=True, default='', max_length=20)),
                ('customer_phone', models.CharField(blank=True, default='', max_length=30)),
                ('customer_email', models.EmailField(blank=True, default='', max_length=254)),
                ('seller_name_snapshot', models.CharField(blank=True, default='', max_length=150)),
                ('number', models.CharField(max_length=40)),
                ('sequence_value', models.PositiveBigIntegerField()),
                ('revision', models.PositiveIntegerField(default=1)),
                ('origin', models.CharField(choices=[('new', 'Nueva'), ('revision', 'Revisión'), ('duplicate', 'Copia')], default='new', max_length=10)),
                ('status', models.CharField(choices=[('draft', 'Borrador'), ('approved', 'Aprobada'), ('sent', 'Enviada'), ('accepted', 'Aceptada'), ('rejected', 'Rechazada'), ('expired', 'Vencida'), ('converted', 'Convertida en venta'), ('superseded', 'Reemplazada')], db_index=True, default='draft', max_length=12)),
                ('valid_until', models.DateField()),
                ('conditions', models.TextField(blank=True, default='', max_length=2000)),
                ('notes', models.TextField(blank=True, default='', max_length=2000)),
                ('currency', models.CharField(default='PEN', max_length=3)),
                ('subtotal', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
                ('discount_amount', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
                ('discount_source', models.CharField(choices=[('none', 'Sin descuento'), ('coupon', 'Código promocional'), ('manual', 'Descuento manual'), ('promotion', 'Promoción automática')], default='none', max_length=10)),
                ('discount_type', models.CharField(blank=True, choices=[('', 'Sin descuento manual'), ('amount', 'Monto'), ('percent', 'Porcentaje')], default='', max_length=8)),
                ('discount_value', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('discount_reason', models.CharField(blank=True, default='', max_length=200)),
                ('taxable_amount', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
                ('tax_amount', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
                ('tax_rate', models.DecimalField(decimal_places=4, default=Decimal('0.0000'), max_digits=6)),
                ('tax_treatment', models.CharField(blank=True, default='taxed', max_length=16)),
                ('total', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('approved_at', models.DateTimeField(blank=True, null=True)),
                ('sent_at', models.DateTimeField(blank=True, null=True)),
                ('decided_at', models.DateTimeField(blank=True, null=True)),
                ('decision_note', models.CharField(blank=True, default='', max_length=300)),
                ('expired_at', models.DateTimeField(blank=True, null=True)),
                ('superseded_at', models.DateTimeField(blank=True, null=True)),
                ('converted_at', models.DateTimeField(blank=True, null=True)),
                ('conversion_key', models.CharField(blank=True, default='', max_length=64)),
            ],
            options={
                'ordering': ['-created_at', '-pk'],
            },
        ),
        migrations.CreateModel(
            name='SalesQuoteItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('on_order', models.BooleanField(default=False)),
                ('supply_reason', models.CharField(blank=True, default='', max_length=200)),
                ('priced_by', models.CharField(choices=[('product', 'Precio del producto'), ('offer', 'Oferta de proveedor'), ('unit', 'Precio del equipo')], default='product', max_length=8)),
                ('description', models.CharField(max_length=300)),
                ('variant_snapshot', models.JSONField(blank=True, default=dict)),
                ('code', models.CharField(blank=True, default='', max_length=64)),
                ('quantity', models.PositiveIntegerField()),
                ('unit_price', models.DecimalField(decimal_places=2, max_digits=12)),
                ('line_total', models.DecimalField(decimal_places=2, max_digits=12)),
                ('sort_order', models.PositiveSmallIntegerField(default=0)),
            ],
            options={
                'ordering': ['sort_order', 'pk'],
            },
        ),
        migrations.RemoveConstraint(
            model_name='printjob',
            name='print_job_target_matches_kind',
        ),
        migrations.AlterField(
            model_name='internalsequence',
            name='document_type',
            field=models.CharField(choices=[('sales_note', 'Nota de venta interna'), ('repair_order', 'Orden de servicio técnico'), ('product_code', 'Código de producto'), ('finance_obligation', 'Obligación financiera'), ('sales_quote', 'Cotización comercial')], db_index=True, max_length=32),
        ),
        migrations.AlterField(
            model_name='printjob',
            name='kind',
            field=models.CharField(choices=[('fiscal_ticket', 'Comprobante electrónico (80 mm)'), ('sales_note_ticket', 'Nota de venta (80 mm)'), ('label_batch', 'Lote de etiquetas'), ('sales_quote_ticket', 'Cotización (80 mm)')], max_length=30),
        ),
        migrations.AddField(
            model_name='salesquote',
            name='approved_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='salesquote',
            name='branch',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='sales_quotes', to='store.branch'),
        ),
        migrations.AddField(
            model_name='salesquote',
            name='company',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='sales_quotes', to='store.company'),
        ),
        migrations.AddField(
            model_name='salesquote',
            name='converted_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='salesquote',
            name='converted_order',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='converted_quotes', to='store.order'),
        ),
        migrations.AddField(
            model_name='salesquote',
            name='created_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='salesquote',
            name='customer',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sales_quotes', to='store.customer'),
        ),
        migrations.AddField(
            model_name='salesquote',
            name='decided_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='salesquote',
            name='discount_authorized_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='salesquote',
            name='seller',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sales_quotes_sold', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='salesquote',
            name='source_quote',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='derived_quotes', to='store.salesquote'),
        ),
        migrations.AddField(
            model_name='salesquote',
            name='updated_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='order',
            name='source_quote',
            field=models.ForeignKey(blank=True, db_default=None, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='orders', to='store.salesquote'),
        ),
        migrations.AddField(
            model_name='printjob',
            name='sales_quote',
            field=models.ForeignKey(blank=True, db_default=None, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='print_jobs', to='store.salesquote'),
        ),
        migrations.AddConstraint(
            model_name='printjob',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('kind', 'label_batch'), ('label_batch__isnull', False), ('order__isnull', True), ('sales_quote__isnull', True)), models.Q(('kind', 'sales_quote_ticket'), ('sales_quote__isnull', False), ('order__isnull', True), ('label_batch__isnull', True)), models.Q(models.Q(('kind__in', ['label_batch', 'sales_quote_ticket']), _negated=True), ('order__isnull', False), ('label_batch__isnull', True), ('sales_quote__isnull', True)), _connector='OR'), name='print_job_target_matches_kind_v2'),
        ),
        migrations.AddField(
            model_name='salesquoteitem',
            name='product',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='sales_quote_items', to='store.product'),
        ),
        migrations.AddField(
            model_name='salesquoteitem',
            name='quote',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='items', to='store.salesquote'),
        ),
        migrations.AddField(
            model_name='salesquoteitem',
            name='stock_unit',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='sales_quote_items', to='store.stockunit'),
        ),
        migrations.AddIndex(
            model_name='salesquote',
            index=models.Index(fields=['company', 'status'], name='store_sales_company_9846b4_idx'),
        ),
        migrations.AddIndex(
            model_name='salesquote',
            index=models.Index(fields=['company', 'number'], name='store_sales_company_8a62d0_idx'),
        ),
        migrations.AddIndex(
            model_name='salesquote',
            index=models.Index(fields=['branch', 'created_at'], name='store_sales_branch__3eccfa_idx'),
        ),
        migrations.AddConstraint(
            model_name='salesquote',
            constraint=models.UniqueConstraint(fields=('company', 'number', 'revision'), name='sales_quote_revision_unique'),
        ),
        migrations.AddConstraint(
            model_name='salesquote',
            constraint=models.UniqueConstraint(condition=models.Q(('status', 'superseded'), _negated=True), fields=('company', 'number'), name='sales_quote_one_current_revision'),
        ),
        migrations.AddConstraint(
            model_name='salesquote',
            constraint=models.CheckConstraint(condition=models.Q(('total__gte', Decimal('0.00'))), name='sales_quote_total_non_negative'),
        ),
        migrations.AddConstraint(
            model_name='salesquote',
            constraint=models.CheckConstraint(condition=models.Q(('discount_amount__gte', Decimal('0.00'))), name='sales_quote_discount_non_negative'),
        ),
        migrations.AddConstraint(
            model_name='salesquote',
            constraint=models.CheckConstraint(condition=models.Q(('revision__gte', 1)), name='sales_quote_revision_positive'),
        ),
        migrations.AddIndex(
            model_name='salesquoteitem',
            index=models.Index(fields=['quote', 'sort_order'], name='store_sales_quote_i_96d9d2_idx'),
        ),
        migrations.AddConstraint(
            model_name='salesquoteitem',
            constraint=models.CheckConstraint(condition=models.Q(('quantity__gte', 1)), name='sales_quote_item_quantity_positive'),
        ),
        migrations.AddConstraint(
            model_name='salesquoteitem',
            constraint=models.CheckConstraint(condition=models.Q(('unit_price__gte', Decimal('0.00'))), name='sales_quote_item_price_non_negative'),
        ),
        migrations.RunPython(grant, migrations.RunPython.noop),
    ]
