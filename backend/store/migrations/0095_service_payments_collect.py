"""
SVC-PAY-01 — `service.payments.collect` for the presets that never diverged.

Recording money received was part of `service.payments.manage`, which also
reverses payments. It is now its own capability, so a technician can take a
payment without being able to declare one a mistake.

WHICH PRESETS GET IT, a product decision:

  · `Administrador`      — it holds everything by definition.
  · `Servicio Técnico`   — the technician may record what the customer paid.
  · `Supervisor Técnico` — for the same reason.

Neither technical preset receives `service.payments.manage`: reversing stays
with whoever already had it. `Ventas` is NOT extended: it already collects
through `service.payments.manage`, which keeps implying it.

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

NEW_CAPABILITIES = ('service.payments.collect',)

#: `Administrador` is "every assignable capability". This is what that meant
#: immediately before this migration — written out, not read from the catalogue.
#: It includes `service.orders.assign`: 0094 runs first.
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
    'service.orders.view', 'service.payments.manage', 'service.quality.manage',
    'service.repair.manage', 'settings.manage', 'settings.view',
})

TECHNICIAN_PREVIOUS = frozenset({
    'company.view', 'service.manage',
    'service.customers.view',
    'service.devices.view', 'service.devices.manage',
    'service.orders.view', 'service.orders.create', 'service.orders.manage',
    'service.diagnostic.manage', 'service.repair.manage',
    'service.quality.manage', 'service.delivery.manage',
})

SUPERVISOR_PREVIOUS = TECHNICIAN_PREVIOUS | frozenset({
    'reports.view', 'service.customers.manage',
})

#: slug → (the set the platform shipped before this migration, what to add).
PRESETS = {
    'servicio-tecnico': (TECHNICIAN_PREVIOUS, frozenset(NEW_CAPABILITIES)),
    'supervisor-tecnico': (SUPERVISOR_PREVIOUS, frozenset(NEW_CAPABILITIES)),
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
        print(f'\n  SVC-PAY-01 — cobro del servicio otorgado a {extended} rol(es) estándar sin modificar')


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0094_service_orders_assign'),
    ]

    operations = [
        migrations.RunPython(grant, migrations.RunPython.noop),
    ]
