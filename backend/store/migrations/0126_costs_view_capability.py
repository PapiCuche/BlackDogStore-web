"""
DEVICE-COMMERCE-01 · DC1 — `costs.view` for the `Administrador` that never diverged.

Seeing what a product or a device cost, the inventory valued at cost and the
commercial margin of the sales is a new capability. Owner's decision
(2026-10-14): it belongs, by default, to the company's administration and to
nobody else. So only `Administrador` is extended — it holds everything by
definition. `Inventario`, `Ventas` and the technical presets are NOT: moving
stock or ringing up a sale does not imply seeing what the shop paid.

ONLY AN UNTOUCHED PRESET, as in 0094, 0095 and 0104. A role is extended when
its capability set is exactly what the platform shipped immediately before this
migration. A role a tenant edited is theirs and is left as it is. The previous
set is FROZEN here: importing it live would compare the database against what
the preset means in some later version of the code.

A new company gets it from `company_provisioning` (Administrador = every
assignable capability), and a legacy admin/superadmin membership from
`tenancy.LEGACY_ROLE_CAPABILITIES`; neither needs this migration.

Data only, no schema: the previous release keeps working while it runs, and it
simply ignores a capability code it does not know. Not reversed on rollback:
the capability may since have been granted by the tenant on purpose.
"""
from django.db import migrations

NEW_CAPABILITIES = ('costs.view',)

#: `Administrador` immediately before this migration: 0104's set plus what 0104 added.
ADMIN_PREVIOUS = frozenset({
    'areas.manage', 'communications.manage', 'company.manage', 'company.view',
    'inventory.adjust', 'inventory.reports', 'inventory.view', 'memberships.manage',
    'memberships.view', 'products.manage', 'products.view', 'reports.view',
    'roles.manage', 'sales.analytics.view', 'sales.commissions.manage',
    'sales.commissions.view', 'sales.discounts.apply', 'sales.fiscal.issue',
    'sales.fiscal.view', 'sales.notes.manage', 'sales.orders.manage',
    'sales.orders.view', 'sales.pos.assign_seller', 'sales.pos.use',
    'sales.promotions.manage', 'sales.promotions.view', 'service.customers.manage',
    'service.customers.view', 'service.delivery.manage', 'service.devices.manage',
    'service.devices.view', 'service.diagnostic.manage', 'service.manage',
    'service.orders.assign', 'service.orders.create', 'service.orders.manage',
    'service.orders.view', 'service.payments.collect', 'service.payments.manage',
    'service.quality.manage', 'service.quotes.record_decision',
    'service.repair.manage', 'settings.manage', 'settings.view',
})


def grant(apps, schema_editor):
    CompanyRole = apps.get_model('store', 'CompanyRole')

    extended = 0
    for role in CompanyRole.objects.all().iterator():
        held = frozenset(role.capabilities or [])
        if held != ADMIN_PREVIOUS:
            continue
        role.capabilities = sorted(held | frozenset(NEW_CAPABILITIES))
        role.save(update_fields=['capabilities', 'updated_at'])
        extended += 1

    if extended:
        print(f'\n  DC1 — ver costos y márgenes otorgado a {extended} rol(es) administrador sin modificar')


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0123_company_publication_requires_image'),
    ]

    operations = [
        migrations.RunPython(grant, migrations.RunPython.noop),
    ]
