"""
TRADE-IN VALUATION PRO · Phase 1 — the per-company reception tariff (docs/adr-trade-in-valuation.md §3.1, §10).

Schema, all additive: four NEW tables (`TradeInPolicy`, `TradeInTariffImport`, `TradeInTariffEntry`,
`TradeInTariffVersion`) with their checks (amounts >= 0, base <= maximum, window ordered, one pending change per
device, one device key per company). No column is added to an existing table and no capability is granted: the four
`sales.tradein.tariff.*` capabilities are explicit grants (nobody holds them until somebody grants them from Roles;
the platform master resolves to everything).

FRESH-INSTALL REPAIR (data), the one 0157 and 0163 do: 0061 builds the seeded `Administrador` from the LIVE
catalogue, so on a fresh install it already holds the four codes when this node runs. No role of a real database can
hold them before this node (the codes did not exist), so `strip` only undoes that live read, and only on a database
nobody works in yet (no `Membership`). The reverse of the repair is a no-op.

NOTHING HERE BUYS, PAYS, CREDITS OR RECEIVES: no `Order`, `PaymentMethod`, `StockMovement`, `StockUnit`, payable or
fiscal table is touched (D1-D12, docs/decisiones-propietario-trade-in.md).

CODE-ONLY ROLLBACK: the previous release ignores the four tables. The reverse drops them and loses every tariff,
version and import: do not unapply it unless that is intended. Like every node since 0189, it runs the quote-ticket
guard first. In production, run with `PGOPTIONS='-c lock_timeout=5s -c statement_timeout=30s'`.
"""

import django.db.models.deletion
import django.utils.timezone
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models

#: The explicit grants this node introduces (FROZEN: `capabilities.TRADE_IN_TARIFF_CODES` today).
CODES = frozenset({'sales.tradein.tariff.view', 'sales.tradein.tariff.manage', 'sales.tradein.tariff.approve',
                   'sales.tradein.tariff.approve_own'})
#: The preset 0061 rebuilds from the live catalogue (FROZEN, as in 0061).
ADMIN_SLUG = 'administrador'


def strip(apps, schema_editor):
    """A fresh install's seeded Administrador loses the four codes 0061 gave it by reading the live catalogue."""
    CompanyRole = apps.get_model('store', 'CompanyRole')
    Membership = apps.get_model('store', 'Membership')

    if Membership.objects.exists():
        # Somebody works in this database: whoever holds a code was given it on purpose.
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
        print(f'\n  TRADE-IN VALUATION PRO — {stripped} rol(es) de instalación nueva sin el tarifario de parte de pago')


def refuse_reverse_with_quote_tickets(apps, schema_editor):
    """Reverse guard: the pre-0189 print-job check refuses any `sales_quote_ticket` row, so going back with one
    would stop halfway (rehearsal R13). Every node from 0189 to the leaf carries it LAST, so a reverse from ANY
    of them refuses before anything is undone. A future node after 0194 must carry it too."""
    PrintJob = apps.get_model('store', 'PrintJob')
    tickets = PrintJob.objects.filter(kind='sales_quote_ticket').count()
    if tickets:
        raise RuntimeError(
            f'0194: hay {tickets} trabajo(s) de impresión de cotizaciones (incluidos los cancelados). '
            f"Antes de volver atrás: DELETE FROM store_printjob WHERE kind='sales_quote_ticket'; "
            f'(docs/despliegue-produccion.md §7).')


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0193_unit_import_terms_approval'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='TradeInTariffEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('brand', models.CharField(max_length=80)),
                ('model_name', models.CharField(max_length=160)),
                ('capacity', models.CharField(blank=True, default='', max_length=40)),
                ('sim', models.CharField(blank=True, default='', max_length=40)),
                ('region', models.CharField(blank=True, default='', max_length=40)),
                ('device_key', models.CharField(max_length=400)),
                ('is_active', models.BooleanField(default=True)),
                ('deactivated_at', models.DateTimeField(blank=True, null=True)),
                ('deactivation_reason', models.CharField(blank=True, default='', max_length=300)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='trade_in_tariff_entries', to='store.company')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('deactivated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('product_model', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='trade_in_tariff_entries', to='store.productmodel')),
            ],
            options={
                'ordering': ['brand', 'model_name', 'capacity', 'sim', 'region', 'pk'],
            },
        ),
        migrations.CreateModel(
            name='TradeInTariffImport',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('previewed', 'Previsualizado'), ('applied', 'Aplicado')], default='previewed', max_length=10)),
                ('original_filename', models.CharField(blank=True, default='', max_length=255)),
                ('file_sha256', models.CharField(max_length=64)),
                ('rows', models.JSONField(blank=True, default=list)),
                ('summary', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('applied_at', models.DateTimeField(blank=True, null=True)),
                ('applied_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='trade_in_tariff_imports', to='store.company')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at', '-pk'],
            },
        ),
        migrations.CreateModel(
            name='TradeInTariffVersion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('number', models.PositiveIntegerField()),
                ('status', models.CharField(choices=[('pending', 'Pendiente de aprobación'), ('approved', 'Aprobada'), ('rejected', 'Rechazada'), ('withdrawn', 'Retirada')], default='pending', max_length=10)),
                ('currency', models.CharField(default='PEN', max_length=3)),
                ('base_reception_value', models.DecimalField(decimal_places=2, max_digits=12)),
                ('refurbishment_cost', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('resale_value', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('target_margin', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('max_reception_value', models.DecimalField(decimal_places=2, max_digits=12)),
                ('valid_from', models.DateField()),
                ('valid_until', models.DateField(blank=True, null=True)),
                ('reason', models.CharField(blank=True, default='', max_length=300)),
                ('source', models.CharField(choices=[('manual', 'Panel'), ('import', 'Carga de Excel')], default='manual', max_length=8)),
                ('approval_reasons', models.JSONField(blank=True, default=list)),
                ('proposed_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('approval_kind', models.CharField(blank=True, choices=[('approver', 'Aprobación de otra persona'), ('self', 'Aprobación propia'), ('policy', 'Aprobada por política')], default='', max_length=10)),
                ('decided_at', models.DateTimeField(blank=True, null=True)),
                ('decision_note', models.CharField(blank=True, default='', max_length=300)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='store.company')),
                ('decided_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('entry', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='versions', to='store.tradeintariffentry')),
                ('import_batch', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='versions', to='store.tradeintariffimport')),
                ('previous', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='store.tradeintariffversion')),
                ('proposed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['entry_id', '-number'],
            },
        ),
        migrations.CreateModel(
            name='TradeInPolicy',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tariff_approval_mode', models.CharField(choices=[('relevant', 'Sólo los cambios relevantes'), ('all', 'Todos los cambios')], default='relevant', max_length=10)),
                ('tariff_increase_tolerance_percent', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=5)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('company', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='trade_in_policy', to='store.company')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'constraints': [models.CheckConstraint(condition=models.Q(('tariff_increase_tolerance_percent__gte', Decimal('0.00')), ('tariff_increase_tolerance_percent__lte', Decimal('100.00'))), name='trade_in_policy_tolerance_percent')],
            },
        ),
        migrations.AddIndex(
            model_name='tradeintariffentry',
            index=models.Index(fields=['company', 'brand', 'model_name'], name='store_trade_company_728960_idx'),
        ),
        migrations.AddIndex(
            model_name='tradeintariffentry',
            index=models.Index(fields=['company', 'is_active'], name='store_trade_company_7e61c3_idx'),
        ),
        migrations.AddConstraint(
            model_name='tradeintariffentry',
            constraint=models.UniqueConstraint(fields=('company', 'device_key'), name='trade_in_tariff_entry_device_unique'),
        ),
        migrations.AddConstraint(
            model_name='tradeintariffentry',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('brand', ''), _negated=True), models.Q(('model_name', ''), _negated=True)), name='trade_in_tariff_entry_names_present'),
        ),
        migrations.AddIndex(
            model_name='tradeintariffimport',
            index=models.Index(fields=['company', 'created_at'], name='store_trade_company_27995d_idx'),
        ),
        migrations.AddIndex(
            model_name='tradeintariffversion',
            index=models.Index(fields=['company', 'status'], name='store_trade_company_fefb4d_idx'),
        ),
        migrations.AddConstraint(
            model_name='tradeintariffversion',
            constraint=models.UniqueConstraint(fields=('entry', 'number'), name='trade_in_tariff_version_number_unique'),
        ),
        migrations.AddConstraint(
            model_name='tradeintariffversion',
            constraint=models.UniqueConstraint(condition=models.Q(('status', 'pending')), fields=('entry',), name='trade_in_tariff_version_one_pending'),
        ),
        migrations.AddConstraint(
            model_name='tradeintariffversion',
            constraint=models.CheckConstraint(condition=models.Q(('number__gte', 1)), name='trade_in_tariff_version_number_positive'),
        ),
        migrations.AddConstraint(
            model_name='tradeintariffversion',
            constraint=models.CheckConstraint(condition=models.Q(('base_reception_value__gte', Decimal('0.00')), ('max_reception_value__gte', Decimal('0.00'))), name='trade_in_tariff_version_values_non_negative'),
        ),
        migrations.AddConstraint(
            model_name='tradeintariffversion',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('refurbishment_cost__isnull', True), ('refurbishment_cost__gte', Decimal('0.00')), _connector='OR'), models.Q(('resale_value__isnull', True), ('resale_value__gte', Decimal('0.00')), _connector='OR'), models.Q(('target_margin__isnull', True), ('target_margin__gte', Decimal('0.00')), _connector='OR')), name='trade_in_tariff_version_estimates_non_negative'),
        ),
        migrations.AddConstraint(
            model_name='tradeintariffversion',
            constraint=models.CheckConstraint(condition=models.Q(('base_reception_value__lte', models.F('max_reception_value'))), name='trade_in_tariff_version_base_within_max'),
        ),
        migrations.AddConstraint(
            model_name='tradeintariffversion',
            constraint=models.CheckConstraint(condition=models.Q(('valid_until__isnull', True), ('valid_until__gte', models.F('valid_from')), _connector='OR'), name='trade_in_tariff_version_window_ordered'),
        ),
        migrations.RunPython(strip, migrations.RunPython.noop),
        # Last: on reverse it runs FIRST, before any data is undone.
        migrations.RunPython(migrations.RunPython.noop, refuse_reverse_with_quote_tickets),
    ]
