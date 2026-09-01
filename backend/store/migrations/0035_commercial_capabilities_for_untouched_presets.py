"""
Commercial Phase C1 — grant the new sales capabilities to untouched presets.

Same mechanism, and the same reasoning, as migration 0033.

`sales.pos.use` and `sales.analytics.view` became assignable in this phase, so
every company provisioned from now on gets them through the preset definitions
in `company_provisioning`. Companies provisioned BEFORE this phase hold their
roles as rows in the database, frozen at the moment they were created, and would
see a POS module they cannot open.

THE DISCRIMINATOR IS EXACT EQUALITY, IN BOTH DIRECTIONS
-------------------------------------------------------
A role is treated as an untouched preset only when its capability set is
EXACTLY what this project's preset granted before this phase. One capability
added or removed and the role belongs to the tenant, not to the platform, and it
is left alone. Writing new authority into a role somebody deliberately shaped —
silently, in a migration — would widen access that was chosen to be narrow.

WHICH PRESETS EVOLVE, AND WHY ONLY THESE
-----------------------------------------
  Administrador  its definition IS "every assignable capability", so extending
                 it does not change what the role means.

  Ventas         its definition is a specific list, and this phase adds the one
                 capability that is unambiguously part of the job the role
                 already names: operating the till. It does NOT receive
                 `sales.analytics.view` — turnover and branch performance are a
                 separate decision, and one this migration has no standing to
                 make on a tenant's behalf.

Every other role, including untouched `Inventario` and `Servicio Técnico`, is
untouched. Existing tenants grant what they want from the roles screen.
"""

from django.db import migrations

POS = 'sales.pos.use'
ANALYTICS = 'sales.analytics.view'
NEW_CAPABILITIES = (POS, ANALYTICS)

# What the `Ventas` preset granted before this phase.
PREVIOUS_SALES_PRESET = frozenset({
    'company.view', 'products.view', 'reports.view',
    'sales.orders.view', 'sales.orders.manage', 'sales.notes.manage',
})


def grant(apps, schema_editor):
    CompanyRole = apps.get_model('store', 'CompanyRole')

    # Read from the live catalogue rather than a frozen copy: this must compare
    # against what "all assignable capabilities" means today, and a hard-coded
    # list would go stale the next time the catalogue grows.
    from store.capabilities import ASSIGNABLE_CAPABILITY_CODES

    previous_admin = frozenset(ASSIGNABLE_CAPABILITY_CODES) - set(NEW_CAPABILITIES)
    if not previous_admin:
        return

    admins = sales = 0
    for role in CompanyRole.objects.all().iterator():
        current = frozenset(role.capabilities or [])

        if current == previous_admin:
            role.capabilities = sorted(previous_admin | set(NEW_CAPABILITIES))
            role.save(update_fields=['capabilities', 'updated_at'])
            admins += 1
        elif current == PREVIOUS_SALES_PRESET:
            # The till only. Analytics is a separate decision.
            role.capabilities = sorted(PREVIOUS_SALES_PRESET | {POS})
            role.save(update_fields=['capabilities', 'updated_at'])
            sales += 1

    if admins or sales:
        print(
            f'\n  Fase C1 — capacidades comerciales otorgadas a {admins} rol(es) '
            f'de administrador y {sales} rol(es) de ventas sin modificar'
        )


def revoke(apps, schema_editor):
    """
    Remove both capabilities wherever they are held.

    Safe as a reverse because rolling the code back also removes them from the
    catalogue; leaving them in a stored list would make those roles fail
    catalogue validation the next time they are saved.
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
        ('store', '0034_commercial_pos_barcode'),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
    ]
