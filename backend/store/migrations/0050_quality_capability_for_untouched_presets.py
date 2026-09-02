"""
M11 — give the newly-assignable quality capability to the presets that never
diverged.

Fifth time this pattern appears (0033, M8's 0037, M9's 0040, M10's 0045, now
this) — and the SECOND time it also reaches the technician preset, following
H1B's 0047.

THE PROBLEM. `service.quality.manage` stopped being RESERVED in this phase.
Every company provisioned from now on gets it: in `Administrador`, because that
preset is defined as "every assignable capability", and in `Servicio Técnico`,
because M11 added it to `_TECHNICIAN_CAPS` on the owner's explicit decision that
the standard technical preset works the lifecycle the platform implemented.
Companies provisioned BEFORE do not, because their roles are rows with a
capability list frozen when they were created.

THE DISCRIMINATORS. Two, one per preset, and they are not the same strength
because they do not need to be.

  · `Administrador` — exact set equality against every assignable capability
    minus the new one. ~35 codes; a tenant reproducing all of them by hand has
    rebuilt the preset.

  · `Servicio Técnico` — H1B's four-field rule, because this preset's set is
    small enough to collide with an ordinary hand-built role. The row must match
    the platform's slug, name AND description, and hold exactly the ten
    capabilities `_TECHNICIAN_CAPS` held before this phase. One field edited and
    it is the tenant's.

EVERY OTHER ROLE IS LEFT ALONE. A tenant that built a narrow technical role
without quality authority keeps it that way — which is the entire point of the
preset being a default rather than a hardcoded permission. A shop that wants a
second pair of eyes on finished work grants `service.repair.manage` to one role
and `service.quality.manage` to another, and nothing here overrides that.
"""

from django.db import migrations

NEW_CAPABILITIES = ('service.quality.manage',)

TECHNICIAN_NAME = 'Servicio Técnico'
TECHNICIAN_SLUG = 'servicio-tecnico'

#: EVERY description the platform has ever written for this preset.
#:
#: Three, not one, and the reason is H1B: that migration granted the service
#: capabilities to historical presets WITHOUT rewriting their description, so a
#: role it just fixed still carries the wording of 0017 or of the pre-M8
#: provisioning code. Matching only the current string would skip exactly the
#: roles the previous phase went to the trouble of repairing — the asymmetry
#: would close for the lifecycle and reopen for quality.
#:
#: The conjunction is unchanged in strength: slug AND name AND one of these
#: platform-authored sentences AND the exact capability set.
TECHNICIAN_DESCRIPTIONS = (
    'Recepción de equipos, órdenes de servicio y su seguimiento.',
    'Equivalente al rol legacy "technician".',
    'Servicio técnico. El módulo aún no existe; el rol reserva la autoridad.',
)
#: What `_TECHNICIAN_CAPS` held immediately before M11. Frozen.
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
})


def grant(apps, schema_editor):
    CompanyRole = apps.get_model('store', 'CompanyRole')

    # Imported from the live catalogue rather than frozen: this migration must
    # compare against what "all assignable capabilities" means today.
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
            f'\n  M11 — capacidad de calidad otorgada a {admins} rol(es) '
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
        ('store', '0049_seed_quality_statuses_and_checklists'),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
    ]
