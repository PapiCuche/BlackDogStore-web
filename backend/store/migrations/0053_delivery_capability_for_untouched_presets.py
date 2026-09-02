"""
M12 — give the newly-assignable delivery capability to the presets that never
diverged.

Sixth time this pattern appears, and the third that also reaches the technician
preset. The reasoning has not changed since 0033.

THE DISCRIMINATORS. Two, one per preset, and they are not the same strength
because they do not need to be.

  · `Administrador` — exact set equality against every assignable capability
    minus the new one. ~37 codes; a tenant reproducing all of them by hand has
    rebuilt the preset.

  · `Servicio Técnico` — the four-field rule H1B established, because this
    preset's set is small enough to collide with an ordinary hand-built role.
    Slug, name, one of the platform's own descriptions, AND the exact set
    `_TECHNICIAN_CAPS` held before this phase.

EVERY OTHER ROLE IS LEFT ALONE. A shop that wants reception to release devices
and the workshop only to repair them grants this to its own role; nothing here
overrides that, which is the whole point of the preset being a default.
"""

from django.db import migrations

NEW_CAPABILITIES = ('service.delivery.manage',)

TECHNICIAN_NAME = 'Servicio Técnico'
TECHNICIAN_SLUG = 'servicio-tecnico'
#: Every description the platform has ever written for this preset. Three,
#: because H1B granted capabilities WITHOUT rewriting the description — matching
#: only the current sentence would skip the roles it repaired.
TECHNICIAN_DESCRIPTIONS = (
    'Recepción de equipos, órdenes de servicio y su seguimiento.',
    'Equivalente al rol legacy "technician".',
    'Servicio técnico. El módulo aún no existe; el rol reserva la autoridad.',
)
#: What `_TECHNICIAN_CAPS` held immediately before M12. Frozen.
TECHNICIAN_PREVIOUS = frozenset({
    'company.view',
    'service.manage',
    'service.customers.view',
    'service.devices.view',
    'service.devices.manage',
    'service.orders.view',
    'service.orders.create',
    'service.orders.manage',
    'service.diagnostic.manage',
    'service.repair.manage',
    'service.quality.manage',
})


def grant(apps, schema_editor):
    CompanyRole = apps.get_model('store', 'CompanyRole')

    from store.capabilities import ASSIGNABLE_CAPABILITY_CODES

    previous_admin_preset = frozenset(ASSIGNABLE_CAPABILITY_CODES) - set(NEW_CAPABILITIES)
    admins = 0
    technicians = 0

    for role in CompanyRole.objects.all().iterator():
        held = frozenset(role.capabilities or [])

        if previous_admin_preset and held == previous_admin_preset:
            role.capabilities = sorted(previous_admin_preset | set(NEW_CAPABILITIES))
            role.save(update_fields=['capabilities', 'updated_at'])
            admins += 1
            continue

        if (
            role.slug == TECHNICIAN_SLUG
            and role.name == TECHNICIAN_NAME
            and role.description in TECHNICIAN_DESCRIPTIONS
            and held == TECHNICIAN_PREVIOUS
        ):
            role.capabilities = sorted(held | set(NEW_CAPABILITIES))
            role.save(update_fields=['capabilities', 'updated_at'])
            technicians += 1

    if admins or technicians:
        print(
            f'\n  M12 — capacidad de entrega otorgada a {admins} rol(es) '
            f'administrador y {technicians} rol(es) técnico sin modificar'
        )


def revoke(apps, schema_editor):
    CompanyRole = apps.get_model('store', 'CompanyRole')
    for role in CompanyRole.objects.all().iterator():
        current = list(role.capabilities or [])
        remaining = [c for c in current if c not in NEW_CAPABILITIES]
        if len(remaining) != len(current):
            role.capabilities = remaining
            role.save(update_fields=['capabilities', 'updated_at'])


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0052_seed_delivered_status'),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
    ]
