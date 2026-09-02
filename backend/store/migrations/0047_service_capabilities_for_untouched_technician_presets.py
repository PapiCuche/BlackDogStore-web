"""
H1B — give the UNTOUCHED historical `Servicio Técnico` preset the service
capabilities the platform has shipped since.

THE ASYMMETRY THIS CLOSES
-------------------------
Seven capability-grant migrations exist and every one of them targets
`Administrador` (0035 also extends `Ventas`). Not one has ever touched
`servicio-tecnico`. So a company provisioned today gets a technical-service role
that can receive a device, diagnose it, quote it and repair it — and a company
provisioned last year gets one that can do none of those, because its row was
written when none of those modules existed and nothing has updated it since.

`provision_company_access_defaults()` cannot fix it either: it is `get_or_create`
keyed on (company, slug), so re-running it on an existing tenant creates nothing
and backfills nothing.

The debt was recorded deliberately in `docs/saas-multiempresa.md`. It is being
paid now because the owner made an explicit product decision: the STANDARD
technician preset must be able to work the lifecycle the platform implemented.

WHY THIS IS HARDER THAN THE ADMINISTRADOR MIGRATIONS
----------------------------------------------------
`CompanyRole` carries NO `is_preset` / `is_system` marker — verified across the
whole model and the whole migration history. So a preset can only be recognised
by what it CONTAINS, and for `Administrador` that is easy: its capability set is
~35 codes, and a tenant reproducing all 35 by hand has, for practical purposes,
rebuilt the preset.

The historical technician preset holds TWO or THREE codes. `{company.view,
service.manage}` is an entirely plausible thing for a tenant to have built by
hand for a limited role. Capability-set equality ALONE is therefore NOT a safe
discriminator here, and using it would risk handing device, order, diagnosis and
repair authority to a role somebody deliberately kept small.

THE DISCRIMINATOR ACTUALLY USED
-------------------------------
FOUR fields must match, all four of them platform-authored, against one of the
two shapes this preset has ever had:

    slug          == 'servicio-tecnico'
    name          == 'Servicio Técnico'
    description   == the exact string that era's code or migration wrote
    capabilities  == exactly that era's set

The description is what makes this sound. It is a specific Spanish sentence the
platform wrote — "Equivalente al rol legacy \"technician\"." or "Servicio
técnico. El módulo aún no existe; el rol reserva la autoridad." — and a tenant
who independently created a role reproducing the platform's own wording,
verbatim, alongside the platform's exact slug, name and capability set has not
created a different role. They have the preset.

ONE CHANGED FIELD AND THE ROW IS LEFT ALONE. Renamed, re-described, one
capability added or removed: it belongs to the tenant, and authority arriving
because software shipped is not a decision the company made.

WHAT IS DELIBERATELY NOT DONE HERE
-----------------------------------
· Roles created from M8 onward are NOT touched. Their description is the current
  one and provisioning already gave them the service capabilities; there is
  nothing to backfill.
· `LEGACY_ROLE_CAPABILITIES[technician]` is NOT widened. That fallback exists so
  a company with no configured roles keeps behaving as it did in Phase 2A, and
  broadening it would grant the whole service lifecycle to every legacy
  technician in every such tenant without anybody choosing it. The platform's
  stated direction is to move onto per-company RBAC, not to keep growing the
  system it is replacing.
· `service.quality.manage` is NOT granted. It is still RESERVED at this commit.
  M11 promotes it and M11 grants it.
"""

from django.db import migrations

#: The capabilities the CURRENT preset holds that a historical one does not.
#: Frozen here on purpose: a migration must reproduce what it did on the day it
#: ran, and importing `_TECHNICIAN_CAPS` would let a future edit rewrite history.
NEW_CAPABILITIES = (
    'service.customers.view',
    'service.devices.manage',
    'service.devices.view',
    'service.diagnostic.manage',
    'service.orders.create',
    'service.orders.manage',
    'service.orders.view',
    'service.repair.manage',
)

#: Every shape the untouched preset has ever had, as (description, capabilities).
#: Both were written by the platform — the first by migration 0017, the second by
#: `provision_company_access_defaults` before M8 — and both are matched exactly.
HISTORICAL_SHAPES = (
    (
        'Equivalente al rol legacy "technician".',
        frozenset({'company.view', 'service.manage'}),
    ),
    (
        'Servicio técnico. El módulo aún no existe; el rol reserva la autoridad.',
        frozenset({'company.view', 'service.manage', 'service.customers.view'}),
    ),
)

PRESET_NAME = 'Servicio Técnico'
PRESET_SLUG = 'servicio-tecnico'


def grant(apps, schema_editor):
    CompanyRole = apps.get_model('store', 'CompanyRole')

    updated = 0
    for role in CompanyRole.objects.filter(slug=PRESET_SLUG).iterator():
        if role.name != PRESET_NAME:
            continue
        held = frozenset(role.capabilities or [])
        for description, shape in HISTORICAL_SHAPES:
            if role.description == description and held == shape:
                role.capabilities = sorted(held | set(NEW_CAPABILITIES))
                role.save(update_fields=['capabilities', 'updated_at'])
                updated += 1
                break

    if updated:
        print(
            f'\n  H1B — capacidades de servicio otorgadas a {updated} '
            f'rol(es) preset "Servicio Técnico" sin modificar'
        )


def revoke(apps, schema_editor):
    """
    Put back exactly the shape each row had, and touch nothing else.

    The forward pass only ever produced `historical ∪ NEW_CAPABILITIES`, so a row
    holding precisely that is one this migration wrote. A row holding anything
    else was edited by the tenant afterwards and is left alone — stripping the
    capabilities off it would undo somebody's decision rather than this
    migration's.
    """
    CompanyRole = apps.get_model('store', 'CompanyRole')

    for role in CompanyRole.objects.filter(slug=PRESET_SLUG).iterator():
        if role.name != PRESET_NAME:
            continue
        held = frozenset(role.capabilities or [])
        for description, shape in HISTORICAL_SHAPES:
            if role.description == description and held == (shape | set(NEW_CAPABILITIES)):
                role.capabilities = sorted(shape)
                role.save(update_fields=['capabilities', 'updated_at'])
                break


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0046_merge_payments_and_service_execution'),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
    ]
