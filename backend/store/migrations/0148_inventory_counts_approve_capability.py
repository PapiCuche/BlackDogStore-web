"""
MIGRATION-C — `inventory.counts.approve` for every role that could approve a count.

Until now creating, filling, APPROVING and cancelling a count all needed the same
`inventory.adjust` (docs/adr-migration-count.md §1.3). Approval gets its own
capability (§6), and so that nobody loses what they could do the day before,
EVERY role that holds `inventory.adjust` receives it — including the roles a
tenant edited: here, unlike 0126, the new capability splits an existing one
instead of adding a new power, and leaving an edited role out would take away an
approval it had. The owner removes it afterwards where four eyes are wanted.

A new company gets it from `company_provisioning` (the Inventario preset, and
Administrador = every assignable capability); a legacy membership from
`tenancy.LEGACY_ROLE_CAPABILITIES`.

Data only. Not reversed on rollback: the previous release ignores a capability
code it does not know, and the tenant may have granted it on purpose since.
"""
from django.db import migrations

GRANTED_WITH = 'inventory.adjust'
NEW_CAPABILITY = 'inventory.counts.approve'


def grant(apps, schema_editor):
    CompanyRole = apps.get_model('store', 'CompanyRole')

    extended = 0
    for role in CompanyRole.objects.all().iterator():
        held = set(role.capabilities or [])
        if GRANTED_WITH not in held or NEW_CAPABILITY in held:
            continue
        role.capabilities = sorted(held | {NEW_CAPABILITY})
        role.save(update_fields=['capabilities', 'updated_at'])
        extended += 1

    if extended:
        print(f'\n  MIGRATION-C — aprobar recuentos otorgado a {extended} rol(es) que mueven inventario')


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0147_inventory_count_migration'),
    ]

    operations = [
        migrations.RunPython(grant, migrations.RunPython.noop),
    ]
