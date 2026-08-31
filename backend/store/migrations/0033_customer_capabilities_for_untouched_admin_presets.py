"""
Phase 4 — give the new customer capabilities to roles that never diverged.

THE PROBLEM
-----------
`service.customers.view` and `service.customers.manage` became assignable in this
phase. Every company provisioned from now on gets them in its `Administrador`
preset automatically, because that preset is defined as "every assignable
capability" and is evaluated when the code loads.

Companies provisioned BEFORE this phase do not: their roles are rows in the
database with a capability list frozen at the moment they were created. Their
administrators would see a Clientes module they cannot open.

WHY NOT JUST ADD IT TO EVERY ADMIN ROLE
---------------------------------------
Because "Administrador" is a name, not a guarantee. A company may have edited
that role — removed capabilities, repurposed it, renamed what it means. Writing
new authority into a role a tenant has deliberately shaped would widen access
that somebody chose to narrow, silently, in a migration. A capability arriving
because software shipped is not a decision the company made.

THE DISCRIMINATOR
-----------------
A role is treated as an untouched preset only if its capability set is EXACTLY
what this project's `Administrador` preset granted before this phase — that is,
every assignable capability minus the two new ones. Exact equality, both
directions. One capability added or removed and the role is the tenant's, not
the platform's, and it is left alone.

That distinction is what makes this safe, and it is why the check is equality
rather than "contains most of them".

EVERY OTHER ROLE IS LEFT ALONE
------------------------------
Including untouched `Servicio Técnico` presets, which the new provisioning code
grants `service.customers.view`. Administrador is a special case precisely
because its definition is "all of it" — extending it does not change what the
role means. Extending a role whose definition is a specific list does. Existing
tenants grant their technicians that capability themselves; it is recorded as
debt in docs/saas-multiempresa.md.
"""

from django.db import migrations

NEW_CAPABILITIES = ('service.customers.manage', 'service.customers.view')


def grant(apps, schema_editor):
    CompanyRole = apps.get_model('store', 'CompanyRole')

    # Imported from the live catalogue rather than hard-coded: this migration
    # must compare against what "all assignable capabilities" means, and a
    # frozen copy would go stale the next time the catalogue grows.
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
            f'\n  Phase 4 — capacidades de clientes otorgadas a {updated} '
            f'rol(es) preset de administrador sin modificar'
        )


def revoke(apps, schema_editor):
    """
    Remove the two capabilities from any role that holds them.

    Safe as a reverse because the capabilities cease to exist as assignable when
    the code is rolled back too; leaving them in a stored list would make those
    roles fail catalogue validation on their next save.
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
        ('store', '0032_backfill_customers'),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
    ]
