"""
Seed per-company area and role presets — SaaS Phase 2A.1.

Runs for EVERY existing company, not just the pilot: these presets belong to
each tenant, they are not Black Dog Store constants. A company created later
gets its presets from the API/admin, not from here.

What it does, idempotently:
  1. Creates the preset areas of each company (skipping any that already exist).
  2. Creates one CompanyRole per legacy role value, carrying the capability set
     that legacy role is worth (see tenancy.LEGACY_ROLE_CAPABILITIES).
  3. Does NOT assign those roles to anybody.

Why step 3 is deliberate: the legacy fallback in resolve_capabilities() already
gives every existing membership exactly its Phase 2A authority. Creating
assignments here would flip those memberships onto the custom-role path — a
behaviour change disguised as a backfill. The presets are offered; adopting them
is an explicit act by each company.

What it does NOT touch:
  - Membership.role — untouched, still the legacy fallback source.
  - UserProfile.role — untouched.
  - Any e-commerce model.
  - User.is_superuser — nothing here can create a platform master.
"""

from django.db import migrations

# Capability codes are duplicated here on purpose: a migration must keep working
# even if the code catalogue evolves later. Editing store/capabilities.py must
# never retroactively change what this migration wrote.
_PRESET_AREAS = [
    ('Administración', 'administracion', 10),
    ('Ventas', 'ventas', 20),
    ('Inventario', 'inventario', 30),
    ('Servicio Técnico', 'servicio-tecnico', 40),
    ('Recepción', 'recepcion', 50),
    ('Control de Calidad', 'control-de-calidad', 60),
    ('Caja', 'caja', 70),
]

_ALL_ASSIGNABLE = [
    'company.view', 'company.manage',
    'memberships.view', 'memberships.manage',
    'areas.manage', 'roles.manage',
    'products.view', 'products.manage',
    'inventory.view', 'inventory.adjust', 'inventory.reports',
    'sales.orders.view', 'sales.orders.manage', 'sales.notes.manage',
    'reports.view', 'settings.view', 'settings.manage',
    'service.manage',
]

_PRESET_ROLES = [
    ('Administrador', 'administrador',
     'Equivalente al rol legacy "admin": autoridad completa dentro de la empresa.',
     _ALL_ASSIGNABLE),
    ('Ventas', 'ventas',
     'Equivalente al rol legacy "sales".',
     ['company.view', 'products.view', 'reports.view',
      'sales.orders.view', 'sales.orders.manage', 'sales.notes.manage']),
    ('Inventario', 'inventario',
     'Equivalente al rol legacy "inventory".',
     ['company.view', 'products.view', 'reports.view',
      'inventory.view', 'inventory.adjust', 'inventory.reports']),
    ('Servicio Técnico', 'servicio-tecnico',
     'Equivalente al rol legacy "technician".',
     ['company.view', 'service.manage']),
]


def seed_presets(apps, schema_editor):
    Company = apps.get_model('store', 'Company')
    CompanyArea = apps.get_model('store', 'CompanyArea')
    CompanyRole = apps.get_model('store', 'CompanyRole')

    for company in Company.objects.all():
        for name, slug, order in _PRESET_AREAS:
            CompanyArea.objects.get_or_create(
                company=company, slug=slug,
                defaults={'name': name, 'sort_order': order, 'is_active': True},
            )
        for name, slug, description, capabilities in _PRESET_ROLES:
            CompanyRole.objects.get_or_create(
                company=company, slug=slug,
                defaults={
                    'name': name,
                    'description': description,
                    'capabilities': sorted(capabilities),
                    'is_active': True,
                },
            )


def unseed_presets(apps, schema_editor):
    """
    Reverse: remove ONLY the untouched presets this migration created.

    A preset whose capabilities or name were edited by an operator is theirs now,
    not ours. A role that is assigned to somebody is never removed — PROTECT on
    MembershipRoleAssignment.role would refuse anyway, and silently disabling it
    would be worse.
    """
    Company = apps.get_model('store', 'Company')
    CompanyArea = apps.get_model('store', 'CompanyArea')
    CompanyRole = apps.get_model('store', 'CompanyRole')
    Assignment = apps.get_model('store', 'MembershipRoleAssignment')

    preset_role_by_slug = {slug: (name, sorted(caps)) for name, slug, _d, caps in _PRESET_ROLES}
    preset_area_by_slug = {slug: name for name, slug, _o in _PRESET_AREAS}

    for company in Company.objects.all():
        for role in CompanyRole.objects.filter(
            company=company, slug__in=preset_role_by_slug,
        ):
            name, caps = preset_role_by_slug[role.slug]
            untouched = (
                role.name == name
                and sorted(role.capabilities or []) == caps
                and role.is_active
            )
            if untouched and not Assignment.objects.filter(role=role).exists():
                role.delete()

        for area in CompanyArea.objects.filter(
            company=company, slug__in=preset_area_by_slug,
        ):
            if (
                area.name == preset_area_by_slug[area.slug]
                and area.is_active
                and not Assignment.objects.filter(area=area).exists()
            ):
                area.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0016_saas_company_areas_roles'),
    ]

    operations = [
        migrations.RunPython(seed_presets, unseed_presets),
    ]
