"""
Company access provisioning — SaaS Phase 2A.1 closure.

THE PROBLEM THIS SOLVES
-----------------------
Migration 0017 seeded preset areas and roles for the companies that existed the
moment it ran. A company created tomorrow — through the API, the Django admin, a
future onboarding command or a Platform Control screen — would arrive with no
areas and no roles at all.

This module is the SINGLE runtime source of those defaults. Every path that
creates a Company must call `provision_company_access_defaults()`; none may
re-declare presets of its own.

ON THE DUPLICATE LIST INSIDE MIGRATION 0017
-------------------------------------------
0017 carries its own frozen copy of these presets on purpose, and that is NOT
duplication to be refactored away. A migration must reproduce what it did when it
ran; importing this module would let a future catalogue change retroactively
rewrite history. The two are allowed to drift — 0017 is a historical record, this
module is the current default.

NEUTRALITY
----------
Nothing here is specific to any single tenant — not even the first one. These are
generic presets for a service business, and they are PRESETS: a company may
rename them, edit their capabilities, deactivate them or create entirely
different ones. A test scans this whole file (prose included) and fails if any
tenant name, legal name or tax id appears in it, so the neutrality is enforced
rather than merely intended.

WHAT IT NEVER DOES
------------------
  - assign a role to any user
  - create a Membership
  - touch UserProfile.role or Membership.role
  - touch User.is_superuser or User.is_staff
  - overwrite an area or role an operator already edited
"""

from __future__ import annotations

from django.db import transaction

from .capabilities import ASSIGNABLE_CAPABILITY_CODES

# --- Generic preset areas -----------------------------------------------------
# (name, slug, sort_order)
PRESET_AREAS: tuple[tuple[str, str, int], ...] = (
    ('Administración', 'administracion', 10),
    ('Ventas', 'ventas', 20),
    ('Inventario', 'inventario', 30),
    ('Servicio Técnico', 'servicio-tecnico', 40),
    ('Recepción', 'recepcion', 50),
    ('Control de Calidad', 'control-de-calidad', 60),
    ('Caja', 'caja', 70),
)

# --- Generic preset roles -----------------------------------------------------
_SALES_CAPS = (
    'company.view', 'products.view', 'reports.view',
    'sales.orders.view', 'sales.orders.manage', 'sales.notes.manage',
)
_INVENTORY_CAPS = (
    'company.view', 'products.view', 'reports.view',
    'inventory.view', 'inventory.adjust', 'inventory.reports',
)
_TECHNICIAN_CAPS = ('company.view', 'service.manage')

# (name, slug, description, capabilities)
PRESET_ROLES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ('Administrador', 'administrador',
     'Autoridad completa dentro de la empresa.',
     tuple(sorted(ASSIGNABLE_CAPABILITY_CODES))),
    ('Ventas', 'ventas',
     'Operación comercial: pedidos y notas de venta internas.',
     _SALES_CAPS),
    ('Inventario', 'inventario',
     'Control de stock, movimientos y Kardex.',
     _INVENTORY_CAPS),
    ('Servicio Técnico', 'servicio-tecnico',
     'Servicio técnico. El módulo aún no existe; el rol reserva la autoridad.',
     _TECHNICIAN_CAPS),
)

# A preset must never reference a capability the catalogue does not offer, or one
# that is reserved. Checked at import time so a bad edit fails loudly and early.
for _name, _slug, _desc, _caps in PRESET_ROLES:
    _unknown = set(_caps) - ASSIGNABLE_CAPABILITY_CODES
    assert not _unknown, f'preset "{_slug}" referencia capacidades inválidas: {_unknown}'


class ProvisioningError(Exception):
    """Raised when defaults cannot be provisioned for a company."""


@transaction.atomic
def provision_company_access_defaults(company, *, actor=None) -> dict:
    """
    Give `company` its default areas and roles. IDEMPOTENT.

    Matching is by (company, slug): an area or role that already exists is left
    exactly as it is, however the operator edited it. Nothing is overwritten and
    nothing is assigned to any user — the presets are offered, adopting them is
    an explicit act.

    Returns a summary dict of what was actually created, so callers can audit it.

    `actor` is accepted for symmetry with the rest of the admin surface and for
    future auditing; it is deliberately unused here because provisioning creates
    no user-linked record.
    """
    from .models import CompanyArea, CompanyRole

    if company is None or not company.pk:
        raise ProvisioningError('Se requiere una empresa guardada para aprovisionar.')

    areas_created: list[str] = []
    roles_created: list[str] = []

    for name, slug, sort_order in PRESET_AREAS:
        _, created = CompanyArea.objects.get_or_create(
            company=company, slug=slug,
            defaults={'name': name, 'sort_order': sort_order, 'is_active': True},
        )
        if created:
            areas_created.append(slug)

    for name, slug, description, capabilities in PRESET_ROLES:
        _, created = CompanyRole.objects.get_or_create(
            company=company, slug=slug,
            defaults={
                'name': name,
                'description': description,
                'capabilities': sorted(capabilities),
                'is_active': True,
            },
        )
        if created:
            roles_created.append(slug)

    return {
        'company_id': company.pk,
        'areas_created': areas_created,
        'roles_created': roles_created,
        'areas_total': company.areas.count(),
        'roles_total': company.roles.count(),
    }
