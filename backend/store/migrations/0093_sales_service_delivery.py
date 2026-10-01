"""Extend only untouched Sales presets; tenant-customized roles remain intact."""
from django.db import migrations

PREVIOUS = frozenset({
    'company.view', 'products.view', 'reports.view',
    'sales.orders.view', 'sales.orders.manage', 'sales.notes.manage', 'sales.pos.use',
    'sales.fiscal.view', 'service.customers.view', 'service.customers.manage',
    'service.devices.view', 'service.devices.manage', 'service.orders.create',
    'service.orders.view', 'service.payments.manage',
})


def extend_sales(apps, schema_editor):
    Role = apps.get_model('store', 'CompanyRole')
    for role in Role.objects.using(schema_editor.connection.alias).filter(slug='ventas').iterator():
        if frozenset(role.capabilities or []) == PREVIOUS:
            role.capabilities = sorted(PREVIOUS | {'service.delivery.manage'})
            role.save(update_fields=['capabilities'])


class Migration(migrations.Migration):
    dependencies = [('store', '0092_fiscal_not_granted_attestation')]
    # Do not revoke a permission on rollback: it may since have been granted by
    # the tenant. Reverting application code needs no data/schema rollback.
    operations = [migrations.RunPython(extend_sales, migrations.RunPython.noop)]
