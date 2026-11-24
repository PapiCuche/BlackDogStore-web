"""
USED-READY-01 — a used device is sold as THAT device, and only once somebody checked it. Additive.

SCHEMA (on `StockUnit`, every column nullable or with a constant `db_default`: no table rewrite, and the previous
release keeps inserting devices while this migrates — what it inserts is `pending` and unpublished):

- `inspection_status` (pending · approved · rejected), `inspection_note`, `inspected_at`, `inspected_by`;
- `published_online`, `published_at`, `published_by` — the device itself on the web;
- `warranty_days` (NULL = the company's policy, 0 = no warranty), `functional_state`, `defects`, `accessories`;
- five cheap CHECKs: published ⇒ approved, a decided inspection has a date, and the closed vocabularies;
- `CompanySettings.used_warranty_days` (NULL): the company's default warranty for a used device. No platform value.

Inspection is NOT a `StockUnit.Status`: `BranchStock.quantity == count(AVAILABLE)` never reads it
(docs/adr-trade-in.md §3.2).

DATA: `inventory.units.inspect` for every role that holds `company.manage` (the rule of 0161), nobody else.

NO GRANDFATHERING (owner, 2026-11-23: «No publicar automáticamente unidades históricas»). Every existing device takes
the column defaults: `pending` and unpublished. After this release EVERY used device leaves the web and the till until
somebody inspects it (and, for the web, prices, photographs and publishes it). How many is unknown here: the release
rehearsal counts them on the copy of production. No stock moves: `BranchStock` and the AVAILABLE count are the same
before and after («No reducir stock por introducir nuevos estados»).

VUELTA ATRÁS. The reverse drops the columns (inspections and publications decided since are lost; the previous code
offers a used device by its price alone again). The grant is NOT taken back (as 0161: removing authority in a
rollback is the riskier direction). In production, run with `PGOPTIONS='-c lock_timeout=5s -c statement_timeout=30s'`.
"""

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

NEW_CAPABILITIES = ('inventory.units.inspect',)

HOLDERS_OF = 'company.manage'

#: `Administrador` immediately before this migration, FROZEN: where 0161 (SALES-EMAIL-03) left it.
ADMIN_PREVIOUS = frozenset({
    'accounting.export', 'accounting.view',
    'areas.manage', 'communications.manage', 'company.manage', 'company.view', 'costs.view',
    'finance.manage', 'finance.payments.record', 'finance.view',
    'inventory.adjust', 'inventory.counts.approve', 'inventory.labels.print', 'inventory.labels.sensitive',
    'inventory.reports', 'inventory.view',
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
        if HOLDERS_OF not in held or held >= frozenset(NEW_CAPABILITIES):
            continue
        role.capabilities = sorted(held | frozenset(NEW_CAPABILITIES))
        role.save(update_fields=['capabilities', 'updated_at'])
        extended += 1

    if extended:
        print(f'\n  USED-READY-01 — inspeccionar seminuevos otorgado a {extended} rol(es) con company.manage')


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0186_label_printing'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='stockunit',
            name='accessories',
            field=models.CharField(blank=True, db_default='', default='', max_length=300),
        ),
        migrations.AddField(
            model_name='stockunit',
            name='defects',
            field=models.CharField(blank=True, db_default='', default='', max_length=500),
        ),
        migrations.AddField(
            model_name='stockunit',
            name='functional_state',
            field=models.CharField(blank=True, choices=[('full', 'Sin fallas declaradas'), ('with_faults', 'Con fallas declaradas')], db_default='', default='', max_length=12),
        ),
        migrations.AddField(
            model_name='stockunit',
            name='inspected_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='stockunit',
            name='inspected_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='stockunit',
            name='inspection_note',
            field=models.CharField(blank=True, db_default='', default='', max_length=300),
        ),
        migrations.AddField(
            model_name='stockunit',
            name='inspection_status',
            field=models.CharField(choices=[('pending', 'Pendiente de inspección'), ('approved', 'Inspección aprobada'), ('rejected', 'Inspección rechazada')], db_default='pending', default='pending', max_length=10),
        ),
        migrations.AddField(
            model_name='stockunit',
            name='published_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='stockunit',
            name='published_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='stockunit',
            name='published_online',
            field=models.BooleanField(db_default=False, default=False),
        ),
        migrations.AddField(
            model_name='stockunit',
            name='warranty_days',
            field=models.PositiveSmallIntegerField(blank=True, null=True, validators=[django.core.validators.MaxValueValidator(3650)]),
        ),
        migrations.AddConstraint(
            model_name='stockunit',
            constraint=models.CheckConstraint(condition=models.Q(('published_online', False), ('inspection_status', 'approved'), _connector='OR'), name='stock_unit_published_is_inspected'),
        ),
        migrations.AddConstraint(
            model_name='stockunit',
            constraint=models.CheckConstraint(condition=models.Q(('inspection_status__in', ['pending', 'approved', 'rejected'])), name='stock_unit_inspection_status_valid'),
        ),
        migrations.AddConstraint(
            model_name='stockunit',
            constraint=models.CheckConstraint(condition=models.Q(('inspection_status', 'pending'), ('inspected_at__isnull', False), _connector='OR'), name='stock_unit_inspection_decided_has_date'),
        ),
        migrations.AddConstraint(
            model_name='stockunit',
            constraint=models.CheckConstraint(condition=models.Q(('functional_state__in', ['', 'full', 'with_faults'])), name='stock_unit_functional_state_valid'),
        ),
        migrations.AddConstraint(
            model_name='stockunit',
            constraint=models.CheckConstraint(condition=models.Q(('warranty_days__isnull', True), ('warranty_days__lte', 3650), _connector='OR'), name='stock_unit_warranty_days_range'),
        ),
        migrations.AddField(
            model_name='companysettings',
            name='used_warranty_days',
            field=models.PositiveSmallIntegerField(blank=True, null=True, validators=[django.core.validators.MaxValueValidator(3650)]),
        ),
        migrations.RunPython(grant, migrations.RunPython.noop),
    ]
