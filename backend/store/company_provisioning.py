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
# PHASE C1: the sales role can now work the till.
#
# `sales.pos.use` is included because selling at a counter IS the sales job.
# `sales.analytics.view` is NOT: a salesperson ringing up a cable does not
# thereby need to see the company's turnover, its best branches or how their
# colleagues are performing. A business that wants that grants it — one
# checkbox, and a decision they get to make rather than inherit.
# M11 — VENTAS ES EL MOSTRADOR: vender, cobrar y RECIBIR.
#
# The product decision is that one counter role covers sales, cashier and
# reception, rather than three presets a small shop would have to combine by
# hand. The capability catalogue stays granular underneath, so splitting them
# later is a preset change and not a redesign.
#
# THE PART THAT REQUIRED CARE. Receiving a device into the workshop must NOT
# arrive by handing over `service.manage`, which is the whole technical-service
# module. It does not have to: every endpoint reception needs is already behind
# its own capability, and none of them asks for `service.manage`.
#
#   search the client        service.customers.view
#   register a new client    service.customers.manage
#   record the device        service.devices.view / .manage
#   open the intake order    service.orders.create
#   follow it for the client service.orders.view
#
# What Ventas deliberately does NOT get, and why:
#
#   service.orders.manage     moving an order through the workshop, assigning
#                             technicians — that is running the bench, not
#                             receiving at the counter
#   service.diagnostic.manage saying what is wrong with a device
#   service.repair.manage     fixing it, and spending stock doing so
#   service.manage            the blanket permission this list exists to avoid
#   inventory.*               a counter that can sell is not a counter that can
#                             correct the shelf
#   sales.discounts.apply     deciding what a thing costs is a supervisor's
#                             call; ringing it up is not
#   sales.analytics.view      ringing up a cable does not require seeing the
#                             company's turnover
#
# `service.customers.manage` IS granted here while the technician preset still
# only gets `.view`, and the asymmetry is deliberate: the person at the counter
# is the one who takes a new customer's details; the person at the bench should
# not be editing client files.
_SALES_CAPS = (
    'company.view', 'products.view', 'reports.view',
    # Comercial y caja
    'sales.orders.view', 'sales.orders.manage', 'sales.notes.manage',
    'sales.pos.use',
    # Recepción técnica
    'service.customers.view', 'service.customers.manage',
    'service.devices.view', 'service.devices.manage',
    'service.orders.create', 'service.orders.view',
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
#
# M8 — THE MODULE EXISTS NOW, so the role that reserved its authority receives
# it. A technical-service role that cannot receive a device into the workshop,
# read the order it opened or move that order forward is not a technical-service
# role; it is a label. What it still does NOT get is `service.customers.manage`,
# for the reason stated above, and none of the reserved capabilities, which name
# modules that do not exist yet.
_TECHNICIAN_CAPS = (
    'company.view',
    'service.manage',
    'service.customers.view',
    'service.devices.view',
    'service.devices.manage',
    'service.orders.view',
    'service.orders.create',
    'service.orders.manage',
    # M9. Diagnosing and quoting is the job the role is named after; a
    # technical-service role that can receive a device and not say what is wrong
    # with it is half a role.
    'service.diagnostic.manage',
    # M10. And repairing it is the other half. This is the first capability in
    # the preset that spends stock — but only inside a repair whose quote a
    # customer approved, only from that repair's own branch, and only against a
    # line somebody was quoted. It is emphatically NOT `inventory.adjust`, which
    # this role still does not have and must not acquire by implication: a
    # technician may fit an approved part, not correct a shelf.
    'service.repair.manage',
    # M11. Testing the work is part of the trade, and the owner's product
    # decision is that the STANDARD technical preset works the lifecycle the
    # platform implemented. A tenant that wants a second pair of eyes builds a
    # narrower role and withholds this — the preset is a default, never a
    # hardcoded authorization.
    'service.quality.manage',
    # M12. In most shops the technician who finished the job is also the person
    # who hands it back. A shop that wants reception to release devices instead
    # grants this to its own role and narrows the technical one — the preset is
    # a default, never a hardcoded authorization.
    'service.delivery.manage',
)

# M11 — SUPERVISOR TÉCNICO. Everything the technician preset has, plus running
# the bench rather than only working at it.
#
# Modelled ENTIRELY from capabilities that already exist: this role needed no
# new capability, no new endpoint and no new concept, which is the test the
# phase set for whether it should exist at all. `service.orders.manage` is the
# difference that matters — transitions and assignment, i.e. deciding who works
# on what and when an order moves on.
#
# It gets `service.customers.manage` because a supervisor corrects the intake
# record when the counter got it wrong.
#
# It does NOT get SaaS administration: no `roles.manage`, no
# `memberships.manage`, no `settings.manage`, no `company.manage`. Supervising
# a workshop is not administering a company, and a supervisor who could rewrite
# roles could give themselves anything.
#
# `inventory.adjust` is also withheld. A supervisor approves the repair that
# spends a part; correcting the shelf is the inventory role's job.
_SERVICE_SUPERVISOR_CAPS = _TECHNICIAN_CAPS + (
    'reports.view',
    'service.customers.manage',
    'service.orders.manage',
)

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
     'Recepción de equipos, órdenes de servicio y su seguimiento.',
     _TECHNICIAN_CAPS),
    ('Supervisor Técnico', 'supervisor-tecnico',
     'Supervisión del taller: órdenes, asignación, diagnóstico y reparación.',
     _SERVICE_SUPERVISOR_CAPS),
)

# A preset must never reference a capability the catalogue does not offer, or one
# that is reserved. Checked at import time so a bad edit fails loudly and early.
for _name, _slug, _desc, _caps in PRESET_ROLES:
    _unknown = set(_caps) - ASSIGNABLE_CAPABILITY_CODES
    assert not _unknown, f'preset "{_slug}" referencia capacidades inválidas: {_unknown}'


# M8 — how a company presents the repair lifecycle.
#
# THE CODES ARE THE PLATFORM'S; ONLY THESE THREE COLUMNS ARE THE TENANT'S. A
# company renames "Recibido" to "En recepción" and its reports keep working,
# because every query is written against `received`. What it may not do is
# change what `received` means, which is why there is no such field.
#
# Seeded for every company, not just the ones that repair things: a row costs
# nothing, and the alternative is a module that half-works for whoever enables
# it later. Order matches the lifecycle rather than the alphabet.
PRESET_REPAIR_STATUSES: tuple[tuple[str, str, bool, int], ...] = (
    # (code, label, is_customer_visible, sort_order)
    ('received', 'Recibido', True, 10),
    ('diagnosing', 'En diagnóstico', True, 20),
    ('waiting_approval', 'Esperando aprobación', True, 30),
    # M9 — the two outcomes of a customer deciding on a quote. Visible by
    # default: the answer is theirs, and hiding their own decision from them
    # would be a strange default for a shop to inherit.
    ('approved', 'Aprobado', True, 40),
    ('rejected', 'Rechazado', True, 50),
    # M10 — the bench. All three visible by default: somebody whose device is
    # in pieces wants to know it is being worked on, and a shop that goes quiet
    # for a week because a part is on order looks idle rather than blocked.
    # `repaired` says the technician finished and NOTHING ELSE — not checked,
    # not ready to collect, not paid — so the default label says exactly that.
    ('in_repair', 'En reparación', True, 60),
    ('waiting_parts', 'Esperando repuestos', True, 70),
    ('repaired', 'Reparado', True, 80),
    # M11. `quality_control` is visible: "we are testing it" is exactly what a
    # customer wants to hear at that point, and it explains the delay honestly.
    # What they do NOT see is the checklist, which is internal — that separation
    # lives in the serializers, not here.
    #
    # `ready_for_pickup` says the device passed its tests and may go to handover.
    # It does NOT say anybody was told: this platform has no notification
    # channel, and a default label claiming otherwise would ship a promise the
    # product does not keep.
    ('quality_control', 'En control de calidad', True, 85),
    ('ready_for_pickup', 'Listo para recoger', True, 88),
    # M12. Visible: the customer collected their device, and seeing that recorded
    # is the natural close of the story they have been following. The label says
    # what happened and nothing about payment, because this platform cannot
    # charge for a repair.
    ('delivered', 'Entregado', True, 89),
    ('cancelled', 'Cancelado', True, 90),
)

#: The checklist a company starts with. DEVICE-NEUTRAL ON PURPOSE.
#:
#: Every code here is a question you can ask a phone, a laptop, a tablet, a
#: console or a thing nobody has a word for yet. Nothing names a vendor, a
#: connector or a feature that belongs to one manufacturer — a platform whose
#: default checklist asked about Face ID would be a platform that fits one shop.
#:
#: A tenant edits these labels, adds items, or creates a template for a specific
#: device type. This is the floor, not the ceiling.
PRESET_QUALITY_TEMPLATE_NAME = 'Control general'
PRESET_QUALITY_ITEMS: tuple[tuple[str, str, bool, int], ...] = (
    # (code, label, is_required, sort_order)
    ('power', 'Enciende y arranca correctamente', True, 10),
    ('repaired_function', 'La falla reportada quedó resuelta', True, 20),
    ('charging', 'Carga y alimentación', True, 30),
    ('audio', 'Audio (altavoz y micrófono)', False, 40),
    ('connectivity', 'Conectividad (red inalámbrica y puertos)', False, 50),
    ('physical', 'Estado físico y cierre del equipo', True, 60),
)


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
    from .models import (
        Branch, CompanyArea, CompanyRole, CompanySettings, InternalSequence,
        QualityChecklistTemplate, QualityChecklistTemplateItem,
        RepairStatusSetting,
    )
    from .sequences import (
        DEFAULT_PADDING, DEFAULT_PREFIX,
        DEFAULT_REPAIR_PADDING, DEFAULT_REPAIR_PREFIX,
    )

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

    # M8 — the repair-order series. Its own row, its own counter and its own
    # prefix: a company's service orders and its sales notes are different
    # documents and must not share a number. `SRV-` is the seed value written
    # into a row the tenant owns, not a constant any allocation reads.
    _repair_sequence, repair_sequence_created = InternalSequence.objects.get_or_create(
        company=company,
        branch=None,
        document_type=InternalSequence.DOCUMENT_REPAIR_ORDER,
        defaults={
            'prefix': DEFAULT_REPAIR_PREFIX,
            'padding': DEFAULT_REPAIR_PADDING,
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

    # M8 — the lifecycle's presentation. `get_or_create` by (company, code), so a
    # company that renamed a state or hid it from customers is never reset.
    repair_statuses_created: list[str] = []
    for code, label, customer_visible, sort_order in PRESET_REPAIR_STATUSES:
        _, created = RepairStatusSetting.objects.get_or_create(
            company=company, code=code,
            defaults={
                'label': label,
                'is_customer_visible': customer_visible,
                'sort_order': sort_order,
            },
        )
        if created:
            repair_statuses_created.append(code)

    template, template_created = QualityChecklistTemplate.objects.get_or_create(
        company=company, device_type='',
        defaults={'name': PRESET_QUALITY_TEMPLATE_NAME, 'is_active': True},
    )
    if template_created:
        QualityChecklistTemplateItem.objects.bulk_create([
            QualityChecklistTemplateItem(
                template=template, code=code, label=label,
                is_required=required, sort_order=order,
            )
            for code, label, required, order in PRESET_QUALITY_ITEMS
        ])

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
        'repair_sequence_created': repair_sequence_created,
        'repair_statuses_created': repair_statuses_created,
        'branch_created': branch_created,
        'areas_created': areas_created,
        'roles_created': roles_created,
        'areas_total': company.areas.count(),
        'roles_total': company.roles.count(),
        'branches_total': company.branches.count(),
    }
