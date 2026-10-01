"""
SVC-ASSIGN-01 — `service.orders.assign` for the presets that never diverged.

Choosing the technician was part of `service.orders.manage`. It is now its own
capability, so reception and the till can say who will work on a device without
being able to move orders through the workshop.

WHICH PRESETS GET IT, a product decision:

  · `Administrador` — it holds everything by definition.
  · `Ventas`        — the counter takes the device in and says who gets it.

`Servicio Técnico` and `Supervisor Técnico` are NOT extended: they already
assign through `service.orders.manage`, which keeps implying it. Nothing is
taken from anybody.

ONLY UNTOUCHED PRESETS. A role is extended when its capability set is exactly
what the platform shipped immediately before this migration (and, for the named
presets, its slug is the preset's). A role a tenant edited — one capability more
or less — is theirs, and is left exactly as it is. So is any role of their own.

The previous sets are FROZEN here, `Administrador` included. Importing them live
would compare the database against what the preset means today, in a process
whose code is always newer than its data: the day a later phase adds a
capability, a tenant crossing both nodes in one `migrate` would have this one
skip their role and blame them for editing it.

Not reversed on rollback: the capability may since have been granted by the
tenant on purpose, and reverting application code needs no data rollback.
"""

from django.db import migrations

NEW_CAPABILITIES = ('service.orders.assign',)

#: `Administrador` is "every assignable capability". This is what that meant
#: immediately before this migration — written out, not read from the catalogue.
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
    'service.orders.create', 'service.orders.manage', 'service.orders.view',
    'service.payments.manage', 'service.quality.manage', 'service.repair.manage',
    'settings.manage', 'settings.view',
})

SALES_PREVIOUS = frozenset({
    'company.view', 'products.view', 'reports.view',
    'sales.orders.view', 'sales.orders.manage', 'sales.notes.manage',
    'sales.pos.use', 'sales.fiscal.view',
    'service.customers.view', 'service.customers.manage',
    'service.devices.view', 'service.devices.manage',
    'service.orders.create', 'service.orders.view',
    'service.payments.manage', 'service.delivery.manage',
})

#: slug → (the set the platform shipped before this migration, what to add).
PRESETS = {
    'ventas': (SALES_PREVIOUS, frozenset(NEW_CAPABILITIES)),
}


def grant(apps, schema_editor):
    CompanyRole = apps.get_model('store', 'CompanyRole')

    extended = 0
    for role in CompanyRole.objects.all().iterator():
        held = frozenset(role.capabilities or [])

        if held == ADMIN_PREVIOUS:
            addition = frozenset(NEW_CAPABILITIES)
        elif role.slug in PRESETS and held == PRESETS[role.slug][0]:
            addition = PRESETS[role.slug][1]
        else:
            continue

        role.capabilities = sorted(held | addition)
        role.save(update_fields=['capabilities', 'updated_at'])
        extended += 1

    if extended:
        print(f'\n  SVC-ASSIGN-01 — asignación de técnico otorgada a {extended} rol(es) estándar sin modificar')


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0093_sales_service_delivery'),
    ]

    operations = [
        migrations.RunPython(grant, migrations.RunPython.noop),
    ]
