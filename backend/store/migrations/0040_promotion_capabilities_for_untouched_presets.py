"""
Commercial Phase C1.3 — grant the promotion capabilities, carefully.

Same mechanism as 0033, 0035 and 0037.

`sales.promotions.view` and `sales.promotions.manage` became assignable in this
phase. Only an `Administrador` preset that has never been edited receives them:
its definition is "every assignable capability", so extending it does not change
what the role means.

`Ventas` is NOT extended, and not even with `view`. A promotion fires
automatically at the till — a salesperson does not need to read the rules to
benefit from them, and the list of what the company is giving away is management
information. A shop that wants its sellers to see it grants one checkbox.

Exact equality, both directions. One capability added or removed and the role is
the tenant's, not the platform's, and it is left alone.
"""

from django.db import migrations

NEW_CAPABILITIES = ('sales.promotions.manage', 'sales.promotions.view')


def grant(apps, schema_editor):
    CompanyRole = apps.get_model('store', 'CompanyRole')
    from store.capabilities import ASSIGNABLE_CAPABILITY_CODES

    previous_admin = frozenset(ASSIGNABLE_CAPABILITY_CODES) - set(NEW_CAPABILITIES)
    if not previous_admin:
        return

    updated = 0
    for role in CompanyRole.objects.all().iterator():
        if frozenset(role.capabilities or []) != previous_admin:
            continue
        role.capabilities = sorted(previous_admin | set(NEW_CAPABILITIES))
        role.save(update_fields=['capabilities', 'updated_at'])
        updated += 1

    if updated:
        print(
            f'\n  Fase C1.3 — capacidades de promociones otorgadas a {updated} '
            f'rol(es) preset de administrador sin modificar'
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
        ('store', '0039_promotions_and_combos'),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
    ]
