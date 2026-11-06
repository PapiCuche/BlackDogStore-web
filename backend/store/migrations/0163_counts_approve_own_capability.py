"""
DEC-INV-06 — `inventory.counts.approve_own` is held by NO role when this node runs.

The owner's rule: whoever performs a physical count does not approve it. The small-team way out is the
capability `inventory.counts.approve_own` («Aprobar recuentos propios»), an EXPLICIT GRANT
(`capabilities.EXPLICIT_GRANT_CODES`): no preset, no legacy role and not the `Administrador` preset hold it;
a company gives it to a role on purpose from Roles, and only somebody who holds it may delegate it.

FRESH-INSTALL REPAIR, the same one 0157 does for the finance explicit grants. 0061 builds the seeded
`Administrador` from the LIVE catalogue, so on a fresh install it already holds this code when this node runs.
No role of a real database can hold it before this node: the code did not exist in any earlier release. So
stripping it here only ever undoes that live read; on an upgraded database it finds nothing to do.

ONLY THAT, ON PURPOSE (review #247). The repair touches the seeded `Administrador` (slug `administrador`) and
only on a database nobody works in yet (no `Membership`): a fresh install, where nobody could have granted the
code. A database in use that is rolled back past this node and migrated again keeps every grant a tenant made
on purpose — a role «Administrador» given «Aprobar recuentos propios» deliberately included.

Data only, no schema. The REVERSE IS A NO-OP on purpose: a tenant may since have granted it, and a rollback
that changes who may approve their own count is not what anybody rolling back wants.
"""
from django.db import migrations

CODE = 'inventory.counts.approve_own'
#: The preset 0061 rebuilds from the live catalogue (FROZEN, as in 0061).
ADMIN_SLUG = 'administrador'


def strip(apps, schema_editor):
    CompanyRole = apps.get_model('store', 'CompanyRole')
    Membership = apps.get_model('store', 'Membership')

    if Membership.objects.exists():
        # Somebody works in this database: whoever holds the code was given it on purpose.
        return

    stripped = 0
    for role in CompanyRole.objects.filter(slug=ADMIN_SLUG).iterator():
        held = list(role.capabilities or [])
        if CODE not in held:
            continue
        role.capabilities = sorted(frozenset(held) - {CODE})
        role.save(update_fields=['capabilities', 'updated_at'])
        stripped += 1

    if stripped:
        print(f'\n  DEC-INV-06 — {stripped} rol(es) de instalación nueva sin «Aprobar recuentos propios»')


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0162_company_document_design'),
    ]

    operations = [
        migrations.RunPython(strip, migrations.RunPython.noop),
    ]
