"""
LABEL-02 — `inventory.labels.print` and `inventory.labels.sensitive` (docs/adr-inventory-labels.md D6).

PROVISIONAL NUMBER (after 0151, LABEL-02's schema): renumbered with the merge order.

  inventory.labels.print       to EVERY role holding `inventory.view`. LABEL-03
                               opened the labels screen with `inventory.view`;
                               splitting it off must not take it from anybody,
                               edited roles included (as 0148 did for counts).
  inventory.labels.sensitive   ONLY to an `Administrador` that never diverged
                               (as 0126 did for costs). An IMEI on paper is a new
                               power, implied by nothing; a role a tenant edited
                               is theirs. Which other roles get it is the owner's
                               decision (ADR P11).

A new company gets both from `company_provisioning` (Inventario: print;
Administrador = every assignable capability); a legacy membership from
`tenancy.LEGACY_ROLE_CAPABILITIES`.

Data only. Not reversed on rollback: the previous release ignores capability
codes it does not know, and the tenant may have granted them on purpose since.
"""
from django.db import migrations

PRINT = 'inventory.labels.print'
SENSITIVE = 'inventory.labels.sensitive'
NEW_CAPABILITIES = (PRINT, SENSITIVE)
PRINT_GRANTED_WITH = 'inventory.view'

#: `Administrador` immediately before this migration (the catalogue after 0148), FROZEN.
ADMIN_PREVIOUS = frozenset({
    'areas.manage', 'communications.manage', 'company.manage', 'company.view', 'costs.view',
    'inventory.adjust', 'inventory.counts.approve', 'inventory.reports', 'inventory.view',
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

    printing = sensitive = 0
    for role in CompanyRole.objects.all().iterator():
        held = frozenset(role.capabilities or [])
        added = set()
        if PRINT_GRANTED_WITH in held and PRINT not in held:
            added.add(PRINT)
        if held == ADMIN_PREVIOUS:
            added.add(SENSITIVE)
        if not added:
            continue
        role.capabilities = sorted(held | added)
        role.save(update_fields=['capabilities', 'updated_at'])
        printing += PRINT in added
        sensitive += SENSITIVE in added

    if printing or sensitive:
        print(f'\n  LABEL-02 — imprimir etiquetas a {printing} rol(es); IMEI y series a {sensitive} '
              'administrador(es) sin modificar')


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0151_unit_label_code_and_label_settings'),
    ]

    operations = [
        migrations.RunPython(grant, migrations.RunPython.noop),
    ]
