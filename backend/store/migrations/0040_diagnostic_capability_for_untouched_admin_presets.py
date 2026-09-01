"""
M9 — give the newly-assignable diagnostic capability to roles that never diverged.

Third time this pattern appears (0033, M8's 0037, now this), and the reasoning is
unchanged.

THE PROBLEM. `service.diagnostic.manage` stopped being RESERVED in this phase.
Every company provisioned from now on gets it in its `Administrador` preset
automatically, because that preset is defined as "every assignable capability"
and is evaluated when the code loads. Companies provisioned BEFORE do not: their
roles are rows with a capability list frozen when they were created, so their
administrators would see a Diagnóstico module they cannot open.

THE DISCRIMINATOR. A role is treated as an untouched preset only if its
capability set is EXACTLY what `Administrador` granted before this phase — every
assignable capability minus the new one. Exact equality, both directions. One
capability added or removed and the role belongs to the tenant, and authority
arriving because software shipped is not a decision the company made.

EVERY OTHER ROLE IS LEFT ALONE, including untouched `Servicio Técnico` presets,
which the new provisioning code does grant it. That asymmetry is the same one M8
recorded: `Administrador` is a special case precisely because its definition is
"all of it", and extending a role defined as a specific list would change what a
tenant's technicians may do without anybody deciding it.
"""

from django.db import migrations

NEW_CAPABILITIES = ('service.diagnostic.manage',)


def grant(apps, schema_editor):
    CompanyRole = apps.get_model('store', 'CompanyRole')

    # Imported from the live catalogue rather than frozen: this migration must
    # compare against what "all assignable capabilities" means today.
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
            f'\n  M9 — capacidad de diagnóstico otorgada a {updated} '
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
        ('store', '0039_seed_approval_statuses'),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
    ]
