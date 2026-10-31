"""
SALES-EMAIL-03 — `sales.documents.send_copy` for the roles that manage the company.

Sending a sale's document to an address the operator types is a new authority,
narrower than `sales.orders.manage`: the counter manages orders, but an
arbitrary destination is a way for a customer's personal data to leave the
company. It goes, by the rule of 0149 and 0157, to every role that already
holds `company.manage` — an administrator, whether or not the tenant edited the
preset — and to nobody else. `Ventas`, `Inventario` and the technical presets do
NOT receive it; a tenant grants it on purpose from Roles.

A new company gets it from `company_provisioning` (Administrador = every
assignable capability but the explicit grants), and a legacy admin/superadmin
membership from `tenancy.LEGACY_ROLE_CAPABILITIES`; neither needs this node.
On a fresh install 0061 has already built `Administrador` from the live
catalogue, so it holds the code when this runs: nothing to do there.

The rule and the preset before this node are FROZEN here, as a migration must
be. Data only, no schema: the previous release ignores a code it does not know.
The REVERSE IS A NO-OP on purpose: a tenant may since have granted or kept it,
and taking authority away in a rollback is the riskier direction.
"""
from django.db import migrations

NEW_CAPABILITIES = ('sales.documents.send_copy',)

HOLDERS_OF = 'company.manage'

#: `Administrador` immediately before this migration, FROZEN: where 0157 (FIN-B1) left it.
ADMIN_PREVIOUS = frozenset({
    'accounting.export', 'accounting.view',
    'areas.manage', 'communications.manage', 'company.manage', 'company.view', 'costs.view',
    'finance.manage', 'finance.payments.record', 'finance.view',
    'inventory.adjust', 'inventory.counts.approve', 'inventory.labels.print', 'inventory.labels.sensitive',
    'inventory.reports', 'inventory.view',
    'memberships.manage', 'memberships.view', 'products.manage', 'products.view', 'reports.view',
    'roles.manage', 'sales.analytics.view', 'sales.commissions.manage', 'sales.commissions.view',
    'sales.discounts.apply', 'sales.fiscal.issue', 'sales.fiscal.view', 'sales.notes.manage',
    'sales.orders.manage', 'sales.orders.view', 'sales.pos.assign_seller', 'sales.pos.use',
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
        print(f'\n  SALES-EMAIL-03 — enviar documentos a otro correo otorgado a {extended} rol(es) con company.manage')


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0160_finance_reminders'),
    ]

    operations = [
        migrations.RunPython(grant, migrations.RunPython.noop),
    ]
