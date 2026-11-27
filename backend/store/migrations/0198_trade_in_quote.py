"""
TRADE-IN VALUATION PRO · Phase 5a — the trade-in quote, its states and its exact expiry (docs/adr-trade-in-valuation.md
§3.5, §5.3, §10).

Schema, all additive: ONE new table (`TradeInQuote`) with its checks (one live revision per number, revisions >= 1,
offer within the frozen maximum, an issued quote has an issue instant and a later expiry instant) and two columns on
`TradeInPolicy` (`quote_validity_hours` 96, `quote_conditions` ''), both with `db_default` so the previous release keeps
inserting policies, and one on `TradeInAssessment` (`approval_kind`, `db_default` '': how it was approved, carried by
its quote). No `PrintJob` change (that is 5b) and no column on any existing sales table.

FRESH-INSTALL REPAIR (data), as 0157/0163/0194/0196/0197: the new explicit grant `sales.tradein.approve_own` is taken
off a fresh install's seeded `Administrador` (0061 reads the live catalogue), only without any `Membership`.

NOTHING HERE BUYS, PAYS, CREDITS OR RECEIVES (D1-D12). CODE-ONLY ROLLBACK: the previous release ignores the table and
the columns; remove the `expire_trade_in_quotes` line from the crontab first. Like every node since 0189, it runs the
quote-ticket guard first. In production, run with `PGOPTIONS='-c lock_timeout=5s -c statement_timeout=30s'`.
"""

import django.db.models.deletion
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


#: The explicit grant this node introduces (FROZEN).
CODES = frozenset({'sales.tradein.approve_own'})
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
        print(f'\n  TRADE-IN VALUATION PRO — {stripped} rol(es) de instalación nueva sin aprobar valorizaciones propias')


def refuse_reverse_with_quote_tickets(apps, schema_editor):
    """Reverse guard: the pre-0189 print-job check refuses any `sales_quote_ticket` row, so going back with one
    would stop halfway (rehearsal R13). Every node from 0189 to the leaf carries it LAST, so a reverse from ANY
    of them refuses before anything is undone. A future node after 0198 must carry it too."""
    PrintJob = apps.get_model('store', 'PrintJob')
    tickets = PrintJob.objects.filter(kind='sales_quote_ticket').count()
    if tickets:
        raise RuntimeError(
            f'0198: hay {tickets} trabajo(s) de impresión de cotizaciones (incluidos los cancelados). '
            f"Antes de volver atrás: DELETE FROM store_printjob WHERE kind='sales_quote_ticket'; "
            f'(docs/despliegue-produccion.md §7).')


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0197_trade_in_assessment'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='TradeInQuote',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('number', models.PositiveIntegerField()),
                ('revision', models.PositiveIntegerField(default=1)),
                ('origin', models.CharField(default='new', max_length=10)),
                ('revision_reason', models.CharField(blank=True, default='', max_length=300)),
                ('status', models.CharField(choices=[('pending_approval', 'Pendiente de aprobación'), ('approved', 'Aprobada'), ('issued', 'Emitida'), ('accepted', 'Aceptada'), ('declined', 'Rechazada por el cliente'), ('expired', 'Vencida'), ('rejected', 'No aprobada'), ('cancelled', 'Anulada'), ('superseded', 'Reemplazada')], max_length=18)),
                ('calculation', models.JSONField(blank=True, default=dict)),
                ('eligibility', models.CharField(max_length=24)),
                ('printable_eligibility', models.CharField(max_length=24)),
                ('needs_approval', models.BooleanField(default=False)),
                ('computed_value', models.DecimalField(decimal_places=2, max_digits=12)),
                ('offered_value', models.DecimalField(decimal_places=2, max_digits=12)),
                ('adjustment_reason', models.CharField(blank=True, default='', max_length=300)),
                ('max_reception_value', models.DecimalField(decimal_places=2, max_digits=12)),
                ('currency', models.CharField(default='PEN', max_length=3)),
                ('target_price', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('difference', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('refurbishment_cost', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('resale_value', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('target_margin', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('conditions', models.TextField(blank=True, default='', max_length=2000)),
                ('public_uid', models.CharField(max_length=32, unique=True)),
                ('approval_kind', models.CharField(blank=True, choices=[('approver', 'Aprobación de otra persona'), ('self', 'Aprobación propia'), ('assessment', 'Aprobada en la evaluación'), ('not_needed', 'No necesitaba aprobación')], default='', max_length=12)),
                ('approved_at', models.DateTimeField(blank=True, null=True)),
                ('approval_note', models.CharField(blank=True, default='', max_length=300)),
                ('issued_at', models.DateTimeField(blank=True, null=True)),
                ('valid_until', models.DateTimeField(blank=True, null=True)),
                ('decided_at', models.DateTimeField(blank=True, null=True)),
                ('decision_note', models.CharField(blank=True, default='', max_length=300)),
                ('expired_at', models.DateTimeField(blank=True, null=True)),
                ('superseded_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['-created_at', '-pk'],
            },
        ),
        migrations.AddField(
            model_name='tradeinassessment',
            name='approval_kind',
            field=models.CharField(blank=True, db_default='', default='', max_length=10),
        ),
        migrations.AddField(
            model_name='tradeinpolicy',
            name='quote_conditions',
            field=models.TextField(blank=True, db_default='', default='', max_length=2000),
        ),
        migrations.AddField(
            model_name='tradeinpolicy',
            name='quote_validity_hours',
            field=models.PositiveIntegerField(db_default=96, default=96),
        ),
        migrations.AddConstraint(
            model_name='tradeinpolicy',
            constraint=models.CheckConstraint(condition=models.Q(('quote_validity_hours__gte', 1), ('quote_validity_hours__lte', 720)), name='trade_in_policy_quote_validity_hours'),
        ),
        migrations.AddField(
            model_name='tradeinquote',
            name='approved_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='tradeinquote',
            name='assessment',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='quotes', to='store.tradeinassessment'),
        ),
        migrations.AddField(
            model_name='tradeinquote',
            name='branch',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='trade_in_quotes', to='store.branch'),
        ),
        migrations.AddField(
            model_name='tradeinquote',
            name='company',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='trade_in_quotes', to='store.company'),
        ),
        migrations.AddField(
            model_name='tradeinquote',
            name='created_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='tradeinquote',
            name='decided_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='tradeinquote',
            name='issued_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='tradeinquote',
            name='rule_set_version',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='store.tradeinrulesetversion'),
        ),
        migrations.AddField(
            model_name='tradeinquote',
            name='source_quote',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='store.tradeinquote'),
        ),
        migrations.AddField(
            model_name='tradeinquote',
            name='tariff_version',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='store.tradeintariffversion'),
        ),
        migrations.AddIndex(
            model_name='tradeinquote',
            index=models.Index(fields=['company', 'status'], name='store_trade_company_0ab498_idx'),
        ),
        migrations.AddIndex(
            model_name='tradeinquote',
            index=models.Index(fields=['status', 'valid_until'], name='store_trade_status_252723_idx'),
        ),
        migrations.AddConstraint(
            model_name='tradeinquote',
            constraint=models.UniqueConstraint(fields=('company', 'number', 'revision'), name='trade_in_quote_revision_unique'),
        ),
        migrations.AddConstraint(
            model_name='tradeinquote',
            constraint=models.UniqueConstraint(condition=models.Q(('status', 'superseded'), _negated=True), fields=('company', 'number'), name='trade_in_quote_one_current_revision'),
        ),
        migrations.AddConstraint(
            model_name='tradeinquote',
            constraint=models.CheckConstraint(condition=models.Q(('revision__gte', 1)), name='trade_in_quote_revision_positive'),
        ),
        migrations.AddConstraint(
            model_name='tradeinquote',
            constraint=models.CheckConstraint(condition=models.Q(('offered_value__gte', Decimal('0.00')), ('computed_value__gte', Decimal('0.00')), ('offered_value__lte', models.F('max_reception_value'))), name='trade_in_quote_offer_within_maximum'),
        ),
        migrations.AddConstraint(
            model_name='tradeinquote',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('status__in', ['issued', 'accepted', 'declined', 'expired']), _negated=True), models.Q(('issued_at__isnull', False), ('valid_until__isnull', False)), _connector='OR'), name='trade_in_quote_issued_has_validity'),
        ),
        migrations.AddConstraint(
            model_name='tradeinquote',
            constraint=models.CheckConstraint(condition=models.Q(('valid_until__isnull', True), ('valid_until__gt', models.F('issued_at')), _connector='OR'), name='trade_in_quote_validity_after_issue'),
        ),
        migrations.RunPython(strip, migrations.RunPython.noop),
        # Last: on reverse it runs FIRST, before any data is undone.
        migrations.RunPython(migrations.RunPython.noop, refuse_reverse_with_quote_tickets),
    ]
