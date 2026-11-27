"""
TRADE-IN VALUATION PRO · Phase 3 — eligibility and the assisted identity check (docs/adr-trade-in-valuation.md §3.3,
§10).

Schema, all additive: four NEW tables (`TradeInVerificationSource`, `TradeInDeviceCheck`, `TradeInBlockerAnswer`,
`TradeInIdentityRecord`) and three columns on `TradeInPolicy` (0194), each with a `db_default` so the previous release
keeps inserting policies while this migrates. Identifiers are stored only as a keyed digest and their last four
characters; no evidence file is stored (D6 pending).

FRESH-INSTALL REPAIR (data), as 0157/0163/0194: 0061 builds the seeded `Administrador` from the LIVE catalogue, so on a
fresh install it already holds the two new explicit grants (`sales.tradein.view`, `sales.tradein.value`) when this
node runs. `strip` takes them off, only on a database nobody works in yet (no `Membership`). Reverse: no-op.

NOTHING HERE BUYS, PAYS, CREDITS OR RECEIVES (D1-D12). CODE-ONLY ROLLBACK: the previous release ignores the tables and
columns. The reverse drops them: do not unapply it unless that is intended. Like every node since 0189, it runs the
quote-ticket guard first. In production, run with `PGOPTIONS='-c lock_timeout=5s -c statement_timeout=30s'`.
"""

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


#: The explicit grants this node introduces (FROZEN: `capabilities.TRADE_IN_ASSESSMENT_CODES` today).
CODES = frozenset({'sales.tradein.view', 'sales.tradein.value'})
#: The preset 0061 rebuilds from the live catalogue (FROZEN, as in 0061).
ADMIN_SLUG = 'administrador'


def strip(apps, schema_editor):
    """A fresh install's seeded Administrador loses the two codes 0061 gave it by reading the live catalogue."""
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
        print(f'\n  TRADE-IN VALUATION PRO — {stripped} rol(es) de instalación nueva sin las comprobaciones de parte de pago')


def refuse_reverse_with_quote_tickets(apps, schema_editor):
    """Reverse guard: the pre-0189 print-job check refuses any `sales_quote_ticket` row, so going back with one
    would stop halfway (rehearsal R13). Every node from 0189 to the leaf carries it LAST, so a reverse from ANY
    of them refuses before anything is undone. A future node after 0196 must carry it too."""
    PrintJob = apps.get_model('store', 'PrintJob')
    tickets = PrintJob.objects.filter(kind='sales_quote_ticket').count()
    if tickets:
        raise RuntimeError(
            f'0196: hay {tickets} trabajo(s) de impresión de cotizaciones (incluidos los cancelados). '
            f"Antes de volver atrás: DELETE FROM store_printjob WHERE kind='sales_quote_ticket'; "
            f'(docs/despliegue-produccion.md §7).')


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0195_trade_in_rules'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='TradeInBlockerAnswer',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(max_length=60)),
                ('answer', models.CharField(choices=[('clear', 'Sin problema'), ('blocked', 'Bloqueado'), ('not_verified', 'Sin verificar')], max_length=14)),
                ('note', models.CharField(blank=True, default='', max_length=300)),
                ('answered_at', models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={
                'ordering': ['code'],
            },
        ),
        migrations.CreateModel(
            name='TradeInDeviceCheck',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('open', 'Abierta'), ('closed', 'Cerrada')], default='open', max_length=8)),
                ('description', models.CharField(blank=True, default='', max_length=200)),
                ('has_imei', models.BooleanField(default=True)),
                ('imei_last4', models.CharField(blank=True, default='', max_length=4)),
                ('imei_digest', models.CharField(blank=True, default='', max_length=64)),
                ('imei_valid', models.BooleanField(default=True)),
                ('imei2_last4', models.CharField(blank=True, default='', max_length=4)),
                ('imei2_digest', models.CharField(blank=True, default='', max_length=64)),
                ('imei2_valid', models.BooleanField(default=True)),
                ('serial_last4', models.CharField(blank=True, default='', max_length=4)),
                ('serial_digest', models.CharField(blank=True, default='', max_length=64)),
                ('duplicate_of', models.CharField(blank=True, default='', max_length=20)),
                ('known_to_company', models.BooleanField(default=False)),
                ('required_blockers', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('closed_at', models.DateTimeField(blank=True, null=True)),
                ('close_reason', models.CharField(blank=True, default='', max_length=300)),
            ],
            options={
                'ordering': ['-created_at', '-pk'],
            },
        ),
        migrations.CreateModel(
            name='TradeInIdentityRecord',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('slot', models.CharField(choices=[('imei', 'IMEI 1'), ('imei2', 'IMEI 2'), ('serial', 'Número de serie')], max_length=8)),
                ('result', models.CharField(choices=[('no_report', 'Sin reporte'), ('reported', 'Reportado (robo o pérdida)'), ('invalid', 'Identificador inválido'), ('not_found', 'No encontrado'), ('unavailable', 'Fuente no disponible'), ('declared_clear', 'Declarado sin bloqueos')], max_length=16)),
                ('checked_at', models.DateTimeField()),
                ('valid_until', models.DateTimeField()),
                ('note', models.CharField(blank=True, default='', max_length=300)),
                ('recorded_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['-checked_at', '-pk'],
            },
        ),
        migrations.CreateModel(
            name='TradeInVerificationSource',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120)),
                ('url', models.URLField(max_length=300)),
                ('identifier', models.CharField(choices=[('imei', 'IMEI'), ('serial', 'Número de serie')], default='imei', max_length=8)),
                ('checks', models.JSONField(blank=True, default=list)),
                ('instructions', models.TextField(blank=True, default='', max_length=1000)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['name', 'pk'],
            },
        ),
        migrations.AddField(
            model_name='tradeinpolicy',
            name='extra_blockers',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='tradeinpolicy',
            name='identity_check_validity_hours',
            field=models.PositiveIntegerField(db_default=24, default=24),
        ),
        migrations.AddField(
            model_name='tradeinpolicy',
            name='optional_blockers',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddConstraint(
            model_name='tradeinpolicy',
            constraint=models.CheckConstraint(condition=models.Q(('identity_check_validity_hours__gte', 1), ('identity_check_validity_hours__lte', 720)), name='trade_in_policy_identity_validity_hours'),
        ),
        migrations.AddField(
            model_name='tradeinblockeranswer',
            name='answered_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='tradeinblockeranswer',
            name='company',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='store.company'),
        ),
        migrations.AddField(
            model_name='tradeindevicecheck',
            name='branch',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='trade_in_device_checks', to='store.branch'),
        ),
        migrations.AddField(
            model_name='tradeindevicecheck',
            name='closed_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='tradeindevicecheck',
            name='company',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='trade_in_device_checks', to='store.company'),
        ),
        migrations.AddField(
            model_name='tradeindevicecheck',
            name='created_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='tradeinblockeranswer',
            name='device_check',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='blocker_answers', to='store.tradeindevicecheck'),
        ),
        migrations.AddField(
            model_name='tradeinidentityrecord',
            name='company',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='store.company'),
        ),
        migrations.AddField(
            model_name='tradeinidentityrecord',
            name='device_check',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='identity_records', to='store.tradeindevicecheck'),
        ),
        migrations.AddField(
            model_name='tradeinidentityrecord',
            name='recorded_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='tradeinverificationsource',
            name='company',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='trade_in_verification_sources', to='store.company'),
        ),
        migrations.AddField(
            model_name='tradeinidentityrecord',
            name='source',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='records', to='store.tradeinverificationsource'),
        ),
        migrations.AddIndex(
            model_name='tradeindevicecheck',
            index=models.Index(fields=['company', 'status'], name='store_trade_company_2f617e_idx'),
        ),
        migrations.AddIndex(
            model_name='tradeindevicecheck',
            index=models.Index(fields=['company', 'imei_digest'], name='store_trade_company_ef2492_idx'),
        ),
        migrations.AddConstraint(
            model_name='tradeindevicecheck',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('has_imei', True), models.Q(('imei_digest', ''), _negated=True)), models.Q(('has_imei', False), ('imei_digest', ''), ('imei2_digest', ''), models.Q(('serial_digest', ''), _negated=True)), _connector='OR'), name='trade_in_device_check_identified'),
        ),
        migrations.AddConstraint(
            model_name='tradeindevicecheck',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('imei_last4', ''), ('imei_last4__regex', '^[0-9]{4}$'), _connector='OR'), models.Q(('imei2_last4', ''), ('imei2_last4__regex', '^[0-9]{4}$'), _connector='OR')), name='trade_in_device_check_imei_last4_only'),
        ),
        migrations.AddConstraint(
            model_name='tradeinblockeranswer',
            constraint=models.UniqueConstraint(fields=('device_check', 'code'), name='trade_in_blocker_answer_unique'),
        ),
        migrations.AddConstraint(
            model_name='tradeinverificationsource',
            constraint=models.UniqueConstraint(fields=('company', 'name'), name='trade_in_verification_source_name_unique'),
        ),
        migrations.AddConstraint(
            model_name='tradeinverificationsource',
            constraint=models.CheckConstraint(condition=models.Q(('url__startswith', 'https://')), name='trade_in_verification_source_https'),
        ),
        migrations.AddIndex(
            model_name='tradeinidentityrecord',
            index=models.Index(fields=['device_check', 'slot'], name='store_trade_device__08828c_idx'),
        ),
        migrations.AddConstraint(
            model_name='tradeinidentityrecord',
            constraint=models.CheckConstraint(condition=models.Q(('valid_until__gt', models.F('checked_at'))), name='trade_in_identity_record_validity'),
        ),
        migrations.AddConstraint(
            model_name='tradeinidentityrecord',
            constraint=models.CheckConstraint(condition=models.Q(('source__isnull', False), ('result', 'declared_clear'), _connector='OR'), name='trade_in_identity_record_source_or_declaration'),
        ),
        migrations.RunPython(strip, migrations.RunPython.noop),
        # Last: on reverse it runs FIRST, before any data is undone.
        migrations.RunPython(migrations.RunPython.noop, refuse_reverse_with_quote_tickets),
    ]
