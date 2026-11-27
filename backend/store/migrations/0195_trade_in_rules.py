"""
TRADE-IN VALUATION PRO · Phase 2 — the valuation rules (docs/adr-trade-in-valuation.md §3.2, §6, §10).

Schema only, all additive: four NEW tables (`TradeInFactorGroup`, `TradeInFactor`, `TradeInRuleSetVersion`,
`TradeInRule`) with their checks (group cap 0-100 %, one draft and one pending rule set per company, a proposed set has
a start, window ordered, a rule has a condition, each effect carries exactly its own amount or percentage). No column
is added to an existing table, nothing is written, no capability is created: the rules use the four
`sales.tradein.tariff.*` explicit grants of 0194, so no fresh-install repair is needed here.

NOTHING HERE BUYS, PAYS, CREDITS OR RECEIVES (D1-D12): no `Order`, payment, stock, unit, payable or fiscal table.

CODE-ONLY ROLLBACK: the previous release ignores the four tables. The reverse drops them and loses every rule: do not
unapply it unless that is intended. Like every node since 0189, it runs the quote-ticket guard first. In production,
run with `PGOPTIONS='-c lock_timeout=5s -c statement_timeout=30s'`.
"""

import django.db.models.deletion
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


def refuse_reverse_with_quote_tickets(apps, schema_editor):
    """Reverse guard: the pre-0189 print-job check refuses any `sales_quote_ticket` row, so going back with one
    would stop halfway (rehearsal R13). Every node from 0189 to the leaf carries it LAST, so a reverse from ANY
    of them refuses before anything is undone. A future node after 0195 must carry it too."""
    PrintJob = apps.get_model('store', 'PrintJob')
    tickets = PrintJob.objects.filter(kind='sales_quote_ticket').count()
    if tickets:
        raise RuntimeError(
            f'0195: hay {tickets} trabajo(s) de impresión de cotizaciones (incluidos los cancelados). '
            f"Antes de volver atrás: DELETE FROM store_printjob WHERE kind='sales_quote_ticket'; "
            f'(docs/despliegue-produccion.md §7).')


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0194_trade_in_tariff'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='TradeInFactorGroup',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('battery', 'Batería'), ('cosmetic', 'Estética'), ('functional', 'Funcionamiento'), ('repairs', 'Reparaciones y reemplazos'), ('accessories', 'Accesorios y caja'), ('custom', 'Otro')], max_length=12)),
                ('name', models.CharField(max_length=80)),
                ('combine', models.CharField(choices=[('max', 'Sólo la mayor deducción del grupo'), ('sum', 'Suma de las deducciones')], default='max', max_length=4)),
                ('group_cap_percent', models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True)),
                ('sort_order', models.PositiveIntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='trade_in_factor_groups', to='store.company')),
            ],
            options={
                'ordering': ['sort_order', 'name', 'pk'],
            },
        ),
        migrations.CreateModel(
            name='TradeInFactor',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.SlugField(max_length=60)),
                ('label', models.CharField(max_length=120)),
                ('input', models.CharField(choices=[('check', 'Resultado de prueba'), ('percent', 'Porcentaje'), ('choice', 'Opciones de la empresa'), ('boolean', 'Sí / No')], max_length=8)),
                ('options', models.JSONField(blank=True, default=list)),
                ('component', models.CharField(blank=True, default='', max_length=60)),
                ('applies_brand', models.CharField(blank=True, default='', max_length=80)),
                ('applies_model_name', models.CharField(blank=True, default='', max_length=160)),
                ('requires_photo', models.BooleanField(default=False)),
                ('sort_order', models.PositiveIntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('applies_product_model', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='store.productmodel')),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='trade_in_factors', to='store.company')),
                ('group', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='factors', to='store.tradeinfactorgroup')),
            ],
            options={
                'ordering': ['sort_order', 'label', 'pk'],
            },
        ),
        migrations.CreateModel(
            name='TradeInRuleSetVersion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('number', models.PositiveIntegerField()),
                ('status', models.CharField(choices=[('draft', 'Borrador'), ('pending', 'Pendiente de aprobación'), ('approved', 'Aprobada'), ('rejected', 'Rechazada'), ('withdrawn', 'Retirada')], default='draft', max_length=10)),
                ('valid_from', models.DateField(blank=True, null=True)),
                ('valid_until', models.DateField(blank=True, null=True)),
                ('reason', models.CharField(blank=True, default='', max_length=300)),
                ('snapshot', models.JSONField(blank=True, default=dict)),
                ('contributor_ids', models.JSONField(blank=True, default=list)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('proposed_at', models.DateTimeField(blank=True, null=True)),
                ('approval_kind', models.CharField(blank=True, choices=[('approver', 'Aprobación de otra persona'), ('self', 'Aprobación propia')], default='', max_length=10)),
                ('decided_at', models.DateTimeField(blank=True, null=True)),
                ('decision_note', models.CharField(blank=True, default='', max_length=300)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='trade_in_rule_set_versions', to='store.company')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('decided_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('proposed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('source_version', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='store.tradeinrulesetversion')),
            ],
            options={
                'ordering': ['company_id', '-number'],
            },
        ),
        migrations.CreateModel(
            name='TradeInRule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('when_outcome', models.CharField(blank=True, default='', max_length=60)),
                ('when_percent_gte', models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True)),
                ('when_percent_lt', models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True)),
                ('effect', models.CharField(choices=[('none', 'Sin penalización'), ('fixed', 'Importe fijo'), ('percent', 'Porcentaje del valor base'), ('repair_cost', 'Costo de reparación'), ('requires_approval', 'Requiere aprobación'), ('pending_verification', 'Pendiente de verificación'), ('not_eligible', 'No elegible')], max_length=24)),
                ('amount', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('percent', models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True)),
                ('priority', models.IntegerField(default=0)),
                ('notes', models.CharField(blank=True, default='', max_length=300)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='store.company')),
                ('factor', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='rules', to='store.tradeinfactor')),
                ('owner', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('version', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='rules', to='store.tradeinrulesetversion')),
            ],
            options={
                'ordering': ['version_id', 'factor_id', '-priority', 'pk'],
            },
        ),
        migrations.AddConstraint(
            model_name='tradeinfactorgroup',
            constraint=models.UniqueConstraint(fields=('company', 'name'), name='trade_in_factor_group_name_unique'),
        ),
        migrations.AddConstraint(
            model_name='tradeinfactorgroup',
            constraint=models.CheckConstraint(condition=models.Q(('group_cap_percent__isnull', True), models.Q(('group_cap_percent__gte', Decimal('0.00')), ('group_cap_percent__lte', Decimal('100.00'))), _connector='OR'), name='trade_in_factor_group_cap_percent'),
        ),
        migrations.AddConstraint(
            model_name='tradeinfactor',
            constraint=models.UniqueConstraint(fields=('company', 'code'), name='trade_in_factor_code_unique'),
        ),
        migrations.AddConstraint(
            model_name='tradeinrulesetversion',
            constraint=models.UniqueConstraint(fields=('company', 'number'), name='trade_in_rule_set_number_unique'),
        ),
        migrations.AddConstraint(
            model_name='tradeinrulesetversion',
            constraint=models.UniqueConstraint(condition=models.Q(('status', 'draft')), fields=('company',), name='trade_in_rule_set_one_draft'),
        ),
        migrations.AddConstraint(
            model_name='tradeinrulesetversion',
            constraint=models.UniqueConstraint(condition=models.Q(('status', 'pending')), fields=('company',), name='trade_in_rule_set_one_pending'),
        ),
        migrations.AddConstraint(
            model_name='tradeinrulesetversion',
            constraint=models.CheckConstraint(condition=models.Q(('number__gte', 1)), name='trade_in_rule_set_number_positive'),
        ),
        migrations.AddConstraint(
            model_name='tradeinrulesetversion',
            constraint=models.CheckConstraint(condition=models.Q(('status', 'draft'), ('valid_from__isnull', False), _connector='OR'), name='trade_in_rule_set_proposed_has_start'),
        ),
        migrations.AddConstraint(
            model_name='tradeinrulesetversion',
            constraint=models.CheckConstraint(condition=models.Q(('valid_until__isnull', True), ('valid_from__isnull', True), ('valid_until__gte', models.F('valid_from')), _connector='OR'), name='trade_in_rule_set_window_ordered'),
        ),
        migrations.AddIndex(
            model_name='tradeinrule',
            index=models.Index(fields=['version', 'factor'], name='store_trade_version_a7c07b_idx'),
        ),
        migrations.AddConstraint(
            model_name='tradeinrule',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('when_outcome', ''), _negated=True), ('when_percent_gte__isnull', False), ('when_percent_lt__isnull', False), _connector='OR'), name='trade_in_rule_has_a_condition'),
        ),
        migrations.AddConstraint(
            model_name='tradeinrule',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('effect__in', ['fixed', 'repair_cost']), ('amount__isnull', False), ('amount__gte', Decimal('0.00')), ('percent__isnull', True)), models.Q(('effect', 'percent'), ('percent__isnull', False), ('percent__gte', Decimal('0.00')), ('percent__lte', Decimal('100.00')), ('amount__isnull', True)), models.Q(('effect__in', ['none', 'requires_approval', 'pending_verification', 'not_eligible']), ('amount__isnull', True), ('percent__isnull', True)), _connector='OR'), name='trade_in_rule_effect_fields'),
        ),
        # Last: on reverse it runs FIRST, before any data is undone.
        migrations.RunPython(migrations.RunPython.noop, refuse_reverse_with_quote_tickets),
    ]
