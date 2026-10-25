"""
ACCOUNTING-02 — `accounting.view` and `accounting.export` for the roles that manage the company.

PROVISIONAL (owner's P3, `docs/adr-accounting.md` §15): until the owner decides,
the coordinator set that both capabilities go to every role that already holds
`company.manage`. That is wider than the «untouched preset» rule of 0094, 0104
and 0126 on purpose: a tenant whose administrator role was edited still has an
administrator, and the person who runs the company is the one who reads its
books. A role WITHOUT `company.manage` gets nothing — selling or repairing does
not imply seeing the company's turnover or its buyers' documents.

The rule is FROZEN here (`HOLDERS_OF`), as a migration must be: importing the
live default would make this node mean something else in a later version.
`store.accounting.defaults.GRANT_TO_HOLDERS_OF` states the same rule and a test
keeps the two equal.

A new company gets both from `company_provisioning` (Administrador = every
assignable capability), and a legacy admin/superadmin membership from
`tenancy.LEGACY_ROLE_CAPABILITIES`; neither needs this migration.

Data only, no schema: the previous release keeps working while it runs and
ignores a code it does not know. Not reversed on rollback: a tenant may since
have granted them on purpose.
"""
from django.db import migrations

NEW_CAPABILITIES = ('accounting.export', 'accounting.view')

#: The rule P3 (provisional): who receives them.
HOLDERS_OF = 'company.manage'

#: `Administrador` immediately before this migration: where 0126 left it, plus
#: what 0148 (MIGRATION-C) gave every holder of `inventory.adjust`. Kept for the
#: chain the preset tests walk (each node starts where the last ended).
ADMIN_PREVIOUS = frozenset({
    'areas.manage', 'communications.manage', 'company.manage', 'company.view', 'costs.view',
    'inventory.adjust', 'inventory.counts.approve', 'inventory.reports', 'inventory.view', 'memberships.manage',
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
        if HOLDERS_OF not in held or held >= frozenset(NEW_CAPABILITIES):
            continue
        role.capabilities = sorted(held | frozenset(NEW_CAPABILITIES))
        role.save(update_fields=['capabilities', 'updated_at'])
        extended += 1

    if extended:
        print(f'\n  ACCOUNTING-02 — centro contable otorgado a {extended} rol(es) con company.manage')


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0148_inventory_counts_approve_capability'),
    ]

    operations = [
        migrations.RunPython(grant, migrations.RunPython.noop),
    ]
