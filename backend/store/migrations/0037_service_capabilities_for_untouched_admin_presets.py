"""
M8 — give the newly-assignable service capabilities to roles that never diverged.

Same problem, same discriminator and same deliberate narrowness as 0033.

THE PROBLEM
-----------
Five `service.*` capabilities stopped being RESERVED in this phase. Every company
provisioned from now on gets them in its `Administrador` preset automatically,
because that preset is defined as "every assignable capability" and is evaluated
when the code loads. Companies provisioned BEFORE this phase do not: their roles
are rows with a capability list frozen when they were created, so their
administrators would see a Servicio Técnico module they cannot open.

THE DISCRIMINATOR
-----------------
A role is treated as an untouched preset only if its capability set is EXACTLY
what the `Administrador` preset granted before this phase — every assignable
capability minus the five new ones. Exact equality, both directions. One
capability added or removed and the role belongs to the tenant, not to the
platform, and it is left alone. Authority arriving because software shipped is
not a decision the company made.

EVERY OTHER ROLE IS LEFT ALONE — INCLUDING `Servicio Técnico`
-------------------------------------------------------------
This is the same call 0033 made, and it is worth restating because it is
counter-intuitive: the new provisioning code DOES grant these capabilities to the
technical-service preset, so a company registered tomorrow gets a technician role
that can receive devices, and a company registered last year does not.

`Administrador` is a special case precisely because its definition is "all of
it": extending it does not change what the role means. `Servicio Técnico` is
defined as a specific list, and rewriting that list in a migration would change
what a tenant's technicians may do without anybody deciding it. Existing tenants
grant it themselves — it is a checkbox on a role they already own — and the
asymmetry is recorded as debt in docs/saas-multiempresa.md.
"""

from django.db import migrations

NEW_CAPABILITIES = (
    'service.devices.manage',
    'service.devices.view',
    'service.orders.create',
    'service.orders.manage',
    'service.orders.view',
)


def grant(apps, schema_editor):
    CompanyRole = apps.get_model('store', 'CompanyRole')

    # Imported from the live catalogue rather than frozen: this migration must
    # compare against what "all assignable capabilities" means today, and a
    # frozen copy would go stale the next time the catalogue grows. See 0033.
    from store.capabilities import ASSIGNABLE_CAPABILITY_CODES

    previous_admin_preset = frozenset(ASSIGNABLE_CAPABILITY_CODES) - set(NEW_CAPABILITIES)
    if not previous_admin_preset:
        return

    updated = 0
    for role in CompanyRole.objects.all().iterator():
        if frozenset(role.capabilities or []) != previous_admin_preset:
            continue
        role.capabilities = sorted(previous_admin_preset | set(NEW_CAPABILITIES))
        role.save(update_fields=['capabilities', 'updated_at'])
        updated += 1

    if updated:
        print(
            f'\n  M8 — capacidades de servicio técnico otorgadas a {updated} '
            f'rol(es) preset de administrador sin modificar'
        )


def revoke(apps, schema_editor):
    """
    Remove the five capabilities from any role that holds them.

    Safe as a reverse: rolling the code back makes them non-assignable again, and
    a stored list containing a reserved code would fail catalogue validation on
    that role's next save.
    """
    CompanyRole = apps.get_model('store', 'CompanyRole')
    for role in CompanyRole.objects.all().iterator():
        current = list(role.capabilities or [])
        remaining = [c for c in current if c not in NEW_CAPABILITIES]
        if len(remaining) != len(current):
            role.capabilities = remaining
            role.save(update_fields=['capabilities', 'updated_at'])


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0036_seed_repair_statuses_and_series'),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
    ]
