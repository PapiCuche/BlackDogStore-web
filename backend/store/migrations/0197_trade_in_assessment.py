"""
TRADE-IN VALUATION PRO · Phase 4 — the assessment and its calculation (docs/adr-trade-in-valuation.md §3.4, §5, §6,
§10).

Schema, all additive: two NEW tables (`TradeInAssessment`, `TradeInAssessmentAnswer`) with their checks (values never
negative, a target only when trading towards another device, a calculated assessment names its tariff and rule
versions, one live assessment per device check) and one column on `TradeInPolicy` (`require_identified_customer`,
`db_default` true: D3's recommendation (a), the previous release keeps inserting).

FRESH-INSTALL REPAIR (data), as 0157/0163/0194/0196: the new explicit grant `sales.tradein.approve` is taken off a
fresh install's seeded `Administrador` (0061 reads the live catalogue), only without any `Membership`. Reverse: no-op.

NOTHING HERE BUYS, PAYS, CREDITS OR RECEIVES (D1-D12): no stock, payable, payment or fiscal table is touched.
CODE-ONLY ROLLBACK: the previous release ignores the tables and the column. Like every node since 0189, it runs the
quote-ticket guard first. In production, run with `PGOPTIONS='-c lock_timeout=5s -c statement_timeout=30s'`.
"""

import django.db.models.deletion
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


#: The explicit grant this node introduces (FROZEN).
CODES = frozenset({'sales.tradein.approve'})
#: The preset 0061 rebuilds from the live catalogue (FROZEN, as in 0061).
ADMIN_SLUG = 'administrador'


def strip(apps, schema_editor):
    """A fresh install's seeded Administrador loses the code 0061 gave it by reading the live catalogue."""
    CompanyRole = apps.get_model('store', 'CompanyRole')
    Membership = apps.get_model('store', 'Membership')

    if Membership.objects.exists():
        return
    stripped = 0
    for role in CompanyRole.objects.filter(slug=ADMIN_SLUG).iterator():
        held = frozenset(role.capabilities or [])
        if not held & CODES:
            continue
        role.capabilities = sorted(held - CODES)
        role.save(update_fields=['capabilities', 'updated_at'])
        stripped += 1
    if stripped:
        print(f'\n  TRADE-IN VALUATION PRO — {stripped} rol(es) de instalación nueva sin aprobar valorizaciones')


def refuse_reverse_with_quote_tickets(apps, schema_editor):
    """Reverse guard: the pre-0189 print-job check refuses any `sales_quote_ticket` row, so going back with one
    would stop halfway (rehearsal R13). Every node from 0189 to the leaf carries it LAST, so a reverse from ANY
    of them refuses before anything is undone. A future node after 0197 must carry it too."""
    PrintJob = apps.get_model('store', 'PrintJob')
    tickets = PrintJob.objects.filter(kind='sales_quote_ticket').count()
    if tickets:
        raise RuntimeError(
            f'0197: hay {tickets} trabajo(s) de impresión de cotizaciones (incluidos los cancelados). '
            f"Antes de volver atrás: DELETE FROM store_printjob WHERE kind='sales_quote_ticket'; "
            f'(docs/despliegue-produccion.md §7).')


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0196_trade_in_eligibility'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='tradeinpolicy',
            name='require_identified_customer',
            field=models.BooleanField(db_default=True, default=True),
        ),
        migrations.CreateModel(
            name='TradeInAssessment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('number', models.PositiveIntegerField()),
                ('status', models.CharField(choices=[('draft', 'Borrador'), ('calculated', 'Calculada'), ('approved', 'Aprobada'), ('quoted', 'Cotizada'), ('cancelled', 'Anulada')], default='draft', max_length=10)),
                ('purpose', models.CharField(choices=[('valuation_only', 'Sólo valorización'), ('trade_towards', 'Parte de pago')], default='valuation_only', max_length=14)),
                ('brand', models.CharField(max_length=80)),
                ('model_name', models.CharField(max_length=160)),
                ('capacity', models.CharField(blank=True, default='', max_length=40)),
                ('sim', models.CharField(blank=True, default='', max_length=40)),
                ('region', models.CharField(blank=True, default='', max_length=40)),
                ('calculation', models.JSONField(blank=True, default=dict)),
                ('eligibility', models.CharField(blank=True, default='', max_length=24)),
                ('computed_value', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('offered_value', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('adjustment_reason', models.CharField(blank=True, default='', max_length=300)),
                ('needs_approval', models.BooleanField(default=False)),
                ('target_price', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('difference', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('calculated_at', models.DateTimeField(blank=True, null=True)),
                ('approved_at', models.DateTimeField(blank=True, null=True)),
                ('approval_note', models.CharField(blank=True, default='', max_length=300)),
                ('contributor_ids', models.JSONField(blank=True, default=list)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('cancelled_at', models.DateTimeField(blank=True, null=True)),
                ('cancel_reason', models.CharField(blank=True, default='', max_length=300)),
                ('approved_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('branch', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='trade_in_assessments', to='store.branch')),
                ('calculated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='trade_in_assessments', to='store.company')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('customer', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='store.customer')),
                ('device_check', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='assessments', to='store.tradeindevicecheck')),
                ('rule_set_version', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='store.tradeinrulesetversion')),
                ('target_product', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='store.product')),
                ('target_sales_quote', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='store.salesquote')),
                ('tariff_entry', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='assessments', to='store.tradeintariffentry')),
                ('tariff_version', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='store.tradeintariffversion')),
            ],
            options={
                'ordering': ['-created_at', '-pk'],
            },
        ),
        migrations.CreateModel(
            name='TradeInAssessmentAnswer',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('value', models.CharField(max_length=60)),
                ('answered_at', models.DateTimeField(auto_now=True)),
                ('answered_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('assessment', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='answers', to='store.tradeinassessment')),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='store.company')),
                ('factor', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='store.tradeinfactor')),
            ],
            options={
                'ordering': ['factor_id'],
            },
        ),
        migrations.AddIndex(
            model_name='tradeinassessment',
            index=models.Index(fields=['company', 'status'], name='store_trade_company_33fff9_idx'),
        ),
        migrations.AddConstraint(
            model_name='tradeinassessment',
            constraint=models.UniqueConstraint(fields=('company', 'number'), name='trade_in_assessment_number_unique'),
        ),
        migrations.AddConstraint(
            model_name='tradeinassessment',
            constraint=models.UniqueConstraint(condition=models.Q(('status', 'cancelled'), _negated=True), fields=('device_check',), name='trade_in_assessment_one_live_per_check'),
        ),
        migrations.AddConstraint(
            model_name='tradeinassessment',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('computed_value__isnull', True), ('computed_value__gte', Decimal('0.00')), _connector='OR'), models.Q(('offered_value__isnull', True), ('offered_value__gte', Decimal('0.00')), _connector='OR'), models.Q(('difference__isnull', True), ('difference__gte', Decimal('0.00')), _connector='OR')), name='trade_in_assessment_values_non_negative'),
        ),
        migrations.AddConstraint(
            model_name='tradeinassessment',
            constraint=models.CheckConstraint(condition=models.Q(('purpose', 'trade_towards'), models.Q(('target_product__isnull', True), ('target_sales_quote__isnull', True)), _connector='OR'), name='trade_in_assessment_target_only_when_trading'),
        ),
        migrations.AddConstraint(
            model_name='tradeinassessment',
            constraint=models.CheckConstraint(condition=models.Q(('status', 'draft'), ('status', 'cancelled'), models.Q(('tariff_version__isnull', False), ('rule_set_version__isnull', False), ('calculated_at__isnull', False)), _connector='OR'), name='trade_in_assessment_calculated_has_versions'),
        ),
        migrations.AddConstraint(
            model_name='tradeinassessmentanswer',
            constraint=models.UniqueConstraint(fields=('assessment', 'factor'), name='trade_in_assessment_answer_unique'),
        ),
        migrations.RunPython(strip, migrations.RunPython.noop),
        # Last: on reverse it runs FIRST, before any data is undone.
        migrations.RunPython(migrations.RunPython.noop, refuse_reverse_with_quote_tickets),
    ]
