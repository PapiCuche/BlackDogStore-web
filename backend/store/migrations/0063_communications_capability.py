"""
M12C — give the new communications capability to the `Administrador` presets
that never diverged.

THIS IS THE FIRST GRANT WRITTEN AFTER M12B.1, and it is written the way that
subphase concluded rather than the way the eight before it were.

WHAT THE PREVIOUS EIGHT DID, AND WHY IT WAS WRONG

    from store.capabilities import ASSIGNABLE_CAPABILITY_CODES
    previous_admin = frozenset(ASSIGNABLE_CAPABILITY_CODES) - set(NEW)

Correct in time, false out of it. On an upgrade the imported catalogue is the
catalogue of that release and the expression really does describe the previous
shape. On a fresh install every migration runs against today's catalogue, so it
describes a role that does not exist yet, matches nothing, and prints "otorgada
a 0 rol(es) administrador" while everybody reads past it.

WHAT THIS ONE DOES INSTEAD

`_PREVIOUS_ADMIN_PRESET` below is a literal: the thirty-eight assignable codes
of the release immediately before M12C, frozen at the moment this file was
written. Editing store/capabilities.py cannot change what it recognises, which
is the entire point — a migration reconstructs the past from its own source, not
from the code of the future.

The TARGET is still read live, and that difference is deliberate: `Administrador`
is DEFINED as every assignable capability of the release, so pointing at the
live catalogue is what makes this converge with
`provision_company_access_defaults` by construction. Frozen to identify, live to
aim.

WHO IT REACHES

  · `Administrador` — yes, by definition.
  · everybody else — no. Composing a message to the whole company is an
    authority a shop hands out on purpose. Ventas takes money and receives
    devices; Inventario counts stock; the technical presets work the bench.
    None of them needs to address the staff, and a tenant that wants one of
    them to say so grants it — which is what a capability catalogue is for.

WHERE THIS ACTUALLY FIRES, WHICH IS NOT WHERE YOU WOULD GUESS

On a FRESH install it never runs its grant, and that is correct. 0061 aims at
the live catalogue — `Administrador` means "everything this release has" — so by
the time this migration is reached the seeded admin already holds all 39 codes,
including this one. The discriminator looks for the frozen 38, finds 39, and
does nothing.

Where it DOES fire is an UPGRADE: a database that ran 0061 when the catalogue
was 38 has an admin sitting at exactly that shape, and this is the migration
that moves it. Two paths, one destination — which is what parity means, and why
both are tested rather than only the convenient one.

THE DISCRIMINATOR is the four-field conjunction 0053 established and 0061
repeated: slug, name, one of the platform's OWN descriptions, and exact set
equality. An `Administrador` a tenant narrowed is no longer an untouched preset
and is left exactly as the tenant left it.
"""

from django.db import migrations

NEW_CAPABILITIES = ('communications.manage',)

ADMIN_SLUG = 'administrador'
ADMIN_NAME = 'Administrador'
#: Every sentence the platform has ever written for this preset.
ADMIN_DESCRIPTIONS = (
    'Equivalente al rol legacy "admin": autoridad completa dentro de la empresa.',
    'Autoridad completa dentro de la empresa.',
)

#: FROZEN. The assignable catalogue as it stood immediately before M12C — the
#: shape `Administrador` has after 0061 and before this migration runs. A live
#: import here would be the defect M12B.1 spent a whole subphase removing.
_PREVIOUS_ADMIN_PRESET = frozenset({
    'areas.manage',  'company.manage',
    'company.view',  'inventory.adjust',
    'inventory.reports',  'inventory.view',
    'memberships.manage',  'memberships.view',
    'products.manage',  'products.view',
    'reports.view',  'roles.manage',
    'sales.analytics.view',  'sales.commissions.manage',
    'sales.commissions.view',  'sales.discounts.apply',
    'sales.notes.manage',  'sales.orders.manage',
    'sales.orders.view',  'sales.pos.assign_seller',
    'sales.pos.use',  'sales.promotions.manage',
    'sales.promotions.view',  'service.customers.manage',
    'service.customers.view',  'service.delivery.manage',
    'service.devices.manage',  'service.devices.view',
    'service.diagnostic.manage',  'service.manage',
    'service.orders.create',  'service.orders.manage',
    'service.orders.view',  'service.payments.manage',
    'service.quality.manage',  'service.repair.manage',
    'settings.manage',  'settings.view',
})


def grant(apps, schema_editor):
    CompanyRole = apps.get_model('store', 'CompanyRole')

    from store.capabilities import ASSIGNABLE_CAPABILITY_CODES

    everything = sorted(frozenset(ASSIGNABLE_CAPABILITY_CODES))
    granted = 0

    for role in CompanyRole.objects.all().iterator():
        held = frozenset(role.capabilities or [])
        if (
            role.slug == ADMIN_SLUG
            and role.name == ADMIN_NAME
            and role.description in ADMIN_DESCRIPTIONS
            and held == _PREVIOUS_ADMIN_PRESET
        ):
            role.capabilities = everything
            role.save(update_fields=['capabilities', 'updated_at'])
            granted += 1

    if granted:
        print(
            f'\n  M12C — capacidad de comunicados otorgada a {granted} '
            f'rol(es) administrador sin modificar'
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
        ('store', '0062_announcements'),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
    ]
