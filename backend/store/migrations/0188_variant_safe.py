"""
VARIANT-SAFE-01 — a variant is deleted only when nothing depends on it; with history it is archived. Additive.

SCHEMA
- `Product.archived_at` / `archived_by` (SET_NULL) / `archive_reason`: NULL or a constant `db_default`, no table
  rewrite; the previous release never writes them. CHECK `archived_product_is_inactive` (archived ⇒ inactive): every
  existing row has `archived_at` NULL, so it holds from the first second; it is what keeps an importer, a form or a bulk
  update from reactivating an archived variant without the explicit, audited unarchive.
- `ProductModel.is_active` (db_default True): every model stays as it was.
- `on_delete` CASCADE → PROTECT for `ProductImage`, `CartItem`, `Review`, `ProductBarcode`,
  `AccessoryCompatibility.accessory` and `SupplierProductRef`; `Product.product_model` SET_NULL → PROTECT. Django
  enforces `on_delete` itself (the database FK stays NO ACTION, DEFERRABLE): these operations change no SQL, only what
  a `delete()` is allowed to take along. `ProductVariantOption` stays CASCADE on purpose: those rows ARE the variant,
  and `variant_lifecycle.delete_variant` deletes them explicitly inside its transaction.

DATA: `products.delete` for every role that holds `company.manage` (the rule of 0161 and 0187), nobody else.

VUELTA ATRÁS. The reverse drops the columns and the CHECK (archived variants stay inactive, but lose «archivada» and
its reason; the previous code lets them be reactivated) and puts the CASCADE / SET_NULL back. The grant is NOT taken
back (as 0161 and 0187). In production, run with `PGOPTIONS='-c lock_timeout=5s -c statement_timeout=30s'`.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

NEW_CAPABILITIES = ('products.delete',)

HOLDERS_OF = 'company.manage'

#: `Administrador` immediately before this migration, FROZEN: where 0187 (USED-READY-01) left it.
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
        if HOLDERS_OF not in held or held >= frozenset(NEW_CAPABILITIES):
            continue
        role.capabilities = sorted(held | frozenset(NEW_CAPABILITIES))
        role.save(update_fields=['capabilities', 'updated_at'])
        extended += 1

    if extended:
        print(f'\n  VARIANT-SAFE-01 — eliminar variantes otorgado a {extended} rol(es) con company.manage')


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0187_used_unit_readiness'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='archive_reason',
            field=models.CharField(blank=True, db_default='', default='', max_length=500),
        ),
        migrations.AddField(
            model_name='product',
            name='archived_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='product',
            name='archived_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='productmodel',
            name='is_active',
            field=models.BooleanField(db_default=True, default=True),
        ),
        migrations.AlterField(
            model_name='accessorycompatibility',
            name='accessory',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='compatible_model_links', to='store.product'),
        ),
        migrations.AlterField(
            model_name='cartitem',
            name='product',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='store.product'),
        ),
        migrations.AlterField(
            model_name='product',
            name='product_model',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='variants', to='store.productmodel'),
        ),
        migrations.AlterField(
            model_name='productbarcode',
            name='product',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='barcodes', to='store.product'),
        ),
        migrations.AlterField(
            model_name='productimage',
            name='product',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='images', to='store.product'),
        ),
        migrations.AlterField(
            model_name='review',
            name='product',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='reviews', to='store.product'),
        ),
        migrations.AlterField(
            model_name='supplierproductref',
            name='product',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='supplier_refs', to='store.product'),
        ),
        migrations.AddConstraint(
            model_name='product',
            constraint=models.CheckConstraint(condition=models.Q(('archived_at__isnull', True), ('is_active', False), _connector='OR'), name='archived_product_is_inactive'),
        ),
        migrations.RunPython(grant, migrations.RunPython.noop),
    ]
