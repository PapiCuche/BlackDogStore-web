"""
M12B.1 — repair the standard presets that a FRESH INSTALL never finished
building, and normalise the one preset that stored a capability twice.

THE DEFECT, STATED PRECISELY
----------------------------
Nine migrations extend `Administrador`, and every one of them identifies the
role it means to extend like this:

    from store.capabilities import ASSIGNABLE_CAPABILITY_CODES
    previous_admin_preset = frozenset(ASSIGNABLE_CAPABILITY_CODES) - set(NEW)

That expression is CORRECT IN TIME and WRONG OUT OF TIME.

On an UPGRADE it is right: when 0033 actually ran, on a real database, the
imported catalogue was the catalogue of that release, so `live - NEW` really did
describe the shape the previous release had written. The admin matched, grew by
one, and matched the next migration too.

On a FRESH INSTALL every migration runs today, against today's catalogue. 0017
seeds the admin with its own frozen list of 18 codes — correctly frozen, and the
comment there says why. Then 0033 asks "does this role hold the 37 codes that
today's catalogue minus one describes?" It holds 18. No match. Nor for 0035,
0037, 0040, 0045, 0050, 0053 or 0059.

Verified by stepping a fresh database through history one migration at a time:
the seeded `Administrador` is 18 after 0017 and still 18 after 0059. Nine grants,
nine silent no-ops. The number in the console output was there all along —
"capacidad otorgada a 0 rol(es) administrador" — saying exactly what happened to
anybody who read it.

A migration must not reconstruct the past by consulting the catalogue of the
future. The seed knew that in 0017. The grants forgot.

WHY THIS IS A FORWARD REPAIR
----------------------------
0033 through 0059 are published. Rewriting them would change history for every
database that already ran them correctly, which is a worse bug than the one being
fixed. This migration only moves forward, and it is idempotent: run it twice and
the second run finds nothing to do.

FROZEN TO IDENTIFY, LIVE TO TARGET
----------------------------------
The two halves of the question have different answers, and conflating them is
the original defect.

  · WHAT THE ROLE WAS  → frozen literal. `_FRESH_INSTALL_ADMIN` below is a copy
    of 0017's `_ALL_ASSIGNABLE`. Editing store/capabilities.py must never change
    what this migration recognises.

  · WHAT THE ROLE MEANS → live catalogue. `Administrador` is DEFINED as every
    assignable capability of the release; that is what the preset means and what
    `provision_company_access_defaults` writes today. Reading the same source is
    what makes the two paths converge by construction rather than by luck.

THE DISCRIMINATOR
-----------------
Four fields, the full conjunction that 0053 established and 0059 repeated:
slug, name, one of the platform's OWN descriptions, and exact set equality.

A tenant that hand-built a role would have to have chosen the platform's slug,
the platform's name, one of the two sentences the platform has literally written
for it, and precisely the eighteen codes the 2017 seed used — not seventeen, not
nineteen. That is not a customisation anybody performs by accident. Anything
else is left alone, including an `Administrador` from which a tenant deliberately
removed one capability: that removal is a decision, and this migration is not
entitled to overrule it.

THE SUPERVISOR IS A DIFFERENT PROBLEM WITH THE SAME SYMPTOM
-----------------------------------------------------------
`Supervisor Técnico` was never short of authority. `_SERVICE_SUPERVISOR_CAPS`
listed `service.orders.manage` twice — once inherited from `_TECHNICIAN_CAPS`,
once re-added — so 0054 stored fifteen elements describing fourteen
capabilities, while provisioning deduplicated on write and stored fourteen.

Identical authority, different numbers. Nothing misbehaved, so nothing was found
until the counts were compared. Deduplicating changes no authority whatsoever,
which is why it can safely be applied to EVERY role rather than only to untouched
presets: removing a repetition does not take anything away from anybody.
"""

from django.db import migrations

#: Verbatim copy of `_ALL_ASSIGNABLE` in 0017_seed_company_presets. FROZEN: this
#: is what the platform wrote, not what it would write today.
_FRESH_INSTALL_ADMIN = frozenset({
    'company.view', 'company.manage',
    'memberships.view', 'memberships.manage',
    'areas.manage', 'roles.manage',
    'products.view', 'products.manage',
    'inventory.view', 'inventory.adjust', 'inventory.reports',
    'sales.orders.view', 'sales.orders.manage', 'sales.notes.manage',
    'reports.view', 'settings.view', 'settings.manage',
    'service.manage',
})

ADMIN_SLUG = 'administrador'
ADMIN_NAME = 'Administrador'
#: Every sentence the platform has ever written for this preset. Matching only
#: the current one would skip exactly the roles that need repairing, because the
#: broken ones are the ones still carrying the 2017 wording.
ADMIN_DESCRIPTIONS = (
    'Equivalente al rol legacy "admin": autoridad completa dentro de la empresa.',
    'Autoridad completa dentro de la empresa.',
)


def repair(apps, schema_editor):
    CompanyRole = apps.get_model('store', 'CompanyRole')

    from store.capabilities import ASSIGNABLE_CAPABILITY_CODES

    # What `Administrador` MEANS, read live and deliberately: the preset is
    # defined as the whole assignable catalogue of the release, and this is the
    # same expression `provision_company_access_defaults` uses.
    everything = sorted(frozenset(ASSIGNABLE_CAPABILITY_CODES))

    admins = 0
    deduped = 0

    for role in CompanyRole.objects.all().iterator():
        stored = list(role.capabilities or [])
        held = frozenset(stored)

        if (
            role.slug == ADMIN_SLUG
            and role.name == ADMIN_NAME
            and role.description in ADMIN_DESCRIPTIONS
            and held == _FRESH_INSTALL_ADMIN
        ):
            role.capabilities = everything
            role.save(update_fields=['capabilities', 'updated_at'])
            admins += 1
            continue

        # Applies to every role, untouched preset or not. Collapsing a repeated
        # code removes no authority, so there is no tenant decision to respect.
        if len(stored) != len(held):
            role.capabilities = sorted(held)
            role.save(update_fields=['capabilities', 'updated_at'])
            deduped += 1

    if admins or deduped:
        print(
            f'\n  M12B.1 — preset Administrador completado en {admins} rol(es); '
            f'capacidades repetidas normalizadas en {deduped} rol(es)'
        )


def unrepair(apps, schema_editor):
    """
    Deliberately not an inverse.

    Reversing this would mean putting a duplicate back and taking authority away
    from an administrator, guessing which of the two it was. Both are worse than
    leaving a correct role correct, and neither is what anybody rolling back
    wants. The migration is reversible so the graph can be walked backwards; it
    just has nothing to undo.
    """
    return None


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0060_notification_center'),
    ]

    operations = [
        migrations.RunPython(repair, unrepair),
    ]
