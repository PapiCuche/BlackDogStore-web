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

PHASE 2E — INTERNAL NUMBERING
-----------------------------
Provisioning also creates the company-level `InternalSequence` for sales notes.
It always exists, whatever scope the company uses: under company scope it is the
counter, and under branch scope it is the template each branch series copies its
prefix and padding from.

PHASE 3 — SETTINGS
------------------
Provisioning also creates the company's `CompanySettings` row, with the NEUTRAL
platform theme and every commercial field EMPTY. It deliberately copies nothing
from any existing tenant: a new business must start blank, not wearing somebody
else's name, address and colours until it notices.

PHASE 2D — THE FIRST BRANCH
---------------------------
Provisioning also gives a new company ONE branch and points its storefront at
it. Stock lives in branches now, so a company with none cannot hold inventory,
cannot check out and cannot be counted — it is not a usable tenant, it is a row.
Creating the first location is part of creating a business, not a decision to
defer to whoever notices the errors first.

Exactly one, named generically, and only when the company has none. Guessing at
a SECOND branch would be inventing a shop that does not exist.

WHAT IT NEVER DOES
------------------
  - assign a role to any user
  - create a Membership
  - touch UserProfile.role or Membership.role
  - touch User.is_superuser or User.is_staff
  - overwrite an area or role an operator already edited
  - create a second branch, or rename/move an existing one
  - copy identity, branding or contact details from another company
"""

from __future__ import annotations

from django.db import transaction

from .capabilities import ASSIGNABLE_CAPABILITY_CODES

# --- Generic preset areas -----------------------------------------------------
# (name, slug, sort_order)
# The first location of a new tenant. Generic on purpose — the operator renames
# it to whatever the shop is actually called.
PRESET_BRANCH_NAME = 'Sucursal principal'

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
# PHASE 4: `service.customers.view`, and deliberately NOT `.manage`.
#
# A technician needs to know whose device is on the bench. Deciding what the
# client file says is a different job, and giving it away by default because the
# module happened to ship is how a preset stops meaning anything. A company that
# wants its technicians editing customers grants it — that is one checkbox, and
# it is a decision the business gets to make rather than inherit.
#
# `service.manage` is NOT treated as an umbrella over this. It is the Phase 2A
# membership-in-technical-service concept; letting it silently absorb every
# capability the service module ever adds would make it exactly the kind of
# implicit super-permission the capability catalogue exists to replace.
_TECHNICIAN_CAPS = ('company.view', 'service.manage', 'service.customers.view')

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
    from .company_settings import NEUTRAL_THEME
    from .models import Branch, CompanyArea, CompanyRole, CompanySettings, InternalSequence
    from .sequences import DEFAULT_PADDING, DEFAULT_PREFIX

    if company is None or not company.pk:
        raise ProvisioningError('Se requiere una empresa guardada para aprovisionar.')

    areas_created: list[str] = []
    roles_created: list[str] = []

    # The sales-note series. `get_or_create` leaves an existing one alone, so a
    # company that has already renamed its prefix or advanced its counter is
    # never reset by a second provisioning run.
    _sequence, sequence_created = InternalSequence.objects.get_or_create(
        company=company,
        branch=None,
        document_type=InternalSequence.DOCUMENT_SALES_NOTE,
        defaults={
            'prefix': DEFAULT_PREFIX,
            'padding': DEFAULT_PADDING,
            'next_value': 1,
        },
    )

    # Settings first: everything else in the tenant assumes they exist. Created
    # with the neutral theme and NOTHING else — no name, no address, no phone
    # borrowed from anywhere. `get_or_create` leaves an existing row untouched,
    # however the operator edited it.
    _settings, settings_created = CompanySettings.objects.get_or_create(
        company=company,
        defaults={'currency': 'PEN', **NEUTRAL_THEME},
    )

    # The first branch, and the storefront pointed at it. Both only when absent:
    # a company that already has locations has already been set up by somebody
    # who knows more about it than this function does.
    branch_created = False
    if not Branch.objects.filter(company=company).exists():
        Branch.objects.create(
            company=company, name=PRESET_BRANCH_NAME, is_active=True,
        )
        branch_created = True

    if company.default_inventory_branch_id is None:
        first = Branch.objects.filter(
            company=company, is_active=True,
        ).order_by('pk').first()
        if first is not None:
            company.default_inventory_branch = first
            company.save(update_fields=['default_inventory_branch', 'updated_at'])

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
        'settings_created': settings_created,
        'sequence_created': sequence_created,
        'branch_created': branch_created,
        'areas_created': areas_created,
        'roles_created': roles_created,
        'areas_total': company.areas.count(),
        'roles_total': company.roles.count(),
        'branches_total': company.branches.count(),
    }
