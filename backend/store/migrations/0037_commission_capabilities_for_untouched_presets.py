"""
Commercial Phase C1.2 — grant the new commercial capabilities, carefully.

Same mechanism as migrations 0033 and 0035, and the same reasoning.

Four capabilities became assignable in this phase:

    sales.pos.assign_seller     attribute a sale to a colleague
    sales.discounts.apply       type a discount that no promotion configured
    sales.commissions.view      see what sellers have earned
    sales.commissions.manage    set the rate

ONLY `Administrador` RECEIVES THEM
----------------------------------
Its definition is "every assignable capability", so extending it does not change
what the role means. `Ventas` is deliberately NOT extended: every one of these
four is a decision ABOUT a salesperson rather than part of doing the job.
Someone who can credit a sale to a colleague can credit it to themselves;
someone who can type a discount can price a friend's purchase; and turnover per
seller is a management view. A shop that wants any of that grants it — that is
one checkbox and a decision the business gets to make.

THE DISCRIMINATOR IS EXACT EQUALITY, IN BOTH DIRECTIONS
-------------------------------------------------------
A role is treated as an untouched preset only when its capability set is
EXACTLY what the preset granted before this phase. One capability added or
removed and the role belongs to the tenant, not to the platform, and it is left
alone. Writing new authority into a role somebody deliberately shaped — silently,
in a migration — would widen access that was chosen to be narrow.
"""

from django.db import migrations

NEW_CAPABILITIES = (
    'sales.commissions.manage',
    'sales.commissions.view',
    'sales.discounts.apply',
    'sales.pos.assign_seller',
)


def grant(apps, schema_editor):
    CompanyRole = apps.get_model('store', 'CompanyRole')

    # Read from the live catalogue rather than a frozen copy: this must compare
    # against what "all assignable capabilities" means today, and a hard-coded
    # list would go stale the next time the catalogue grows.
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
            f'\n  Fase C1.2 — capacidades comerciales otorgadas a {updated} '
            f'rol(es) preset de administrador sin modificar'
        )


def revoke(apps, schema_editor):
    """
    Remove the four capabilities wherever they are held.

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
        ('store', '0036_pos_customers_discounts_commissions'),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
    ]
