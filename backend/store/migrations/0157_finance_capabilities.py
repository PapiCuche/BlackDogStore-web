"""
FINANCE-OBLIGATIONS-01 · FIN-B1 — the three finance capabilities an administrator holds by default.

PROVISIONAL (owner's P3, docs/adr-finance-obligations.md §8.2): `finance.view`,
`finance.manage` and `finance.payments.record` go to every role that already
holds `company.manage`, the rule of 0149. The other nine finance capabilities
(reverse, adjust, void, dispute, manual receivables, export, messages,
templates, automation) are NOT granted by anybody: a company assigns them on
purpose from Roles. A new company's `Administrador` preset is built the same
way (`capabilities.DEFAULT_ADMIN_CAPABILITY_CODES`).

The rule and the preset before this node are FROZEN here, as a migration must
be. Data only, no schema; not reversed on rollback (the previous release
ignores codes it does not know, and a tenant may have kept them on purpose).
"""
from django.db import migrations

NEW_CAPABILITIES = ('finance.manage', 'finance.payments.record', 'finance.view')

HOLDERS_OF = 'company.manage'

#: `Administrador` immediately before this migration, FROZEN: where 0152 (LABEL-02) left it.
ADMIN_PREVIOUS = frozenset({
    'accounting.export', 'accounting.view',
    'areas.manage', 'communications.manage', 'company.manage', 'company.view', 'costs.view',
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
        role.capabilities = sorted(held | set(NEW_CAPABILITIES))
        role.save(update_fields=['capabilities', 'updated_at'])
        extended += 1

    if extended:
        print(f'\n  FIN-B1 — finanzas (ver, administrar y registrar pagos) a {extended} rol(es) con company.manage')


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0156_finance_core'),
    ]

    operations = [
        migrations.RunPython(grant, migrations.RunPython.noop),
    ]
