"""
Capability catalogue — SaaS Phase 2A.1.

DESIGN DECISION (alternative B, catalogue in code)
--------------------------------------------------
Capabilities are defined HERE, in code, and roles store their `code` strings.
The rejected alternative was a `PermissionDefinition` table.

Why B:
  - Security: the PLATFORM owns what capabilities exist. With a table, anything
    able to write it could invent a capability; tenants would be defining the
    vocabulary of their own authority. Here a tenant only chooses WHICH of the
    platform's capabilities its roles hold.
  - Migrations: adding or renaming a capability is a code change plus a test,
    not a schema migration and a data migration per environment.
  - One source of truth: Phase 2A already expressed capabilities as code
    constants. A table would have created a second, divergent authority.
  - Integrity: enforced by validation against this catalogue (model `clean()`
    plus serializer), which is where the meaning lives anyway.
  - UI: the catalogue is served read-only by /api/admin/capabilities/, so a
    front-end can render checkboxes without duplicating the list.

Django's `auth.Permission` is deliberately NOT used: it is global, model-bound
and has no tenant dimension, so it cannot express "this capability, inside this
company".

STATUS — honest about what is actually enforced
-----------------------------------------------
  ACTIVE    the platform enforces it today
  AVAILABLE the module exists but its endpoints are still authorised by the
            legacy RBAC (UserProfile.role); assignable, not yet enforced
  RESERVED  the module does not exist yet. Listed for design only and NOT
            assignable to a role — no fake permissions for absent features.
"""

from __future__ import annotations

from dataclasses import dataclass

STATUS_ACTIVE = 'active'
STATUS_AVAILABLE = 'available'
STATUS_RESERVED = 'reserved'


@dataclass(frozen=True)
class Capability:
    code: str
    module: str
    name: str
    description: str
    status: str

    @property
    def is_assignable(self) -> bool:
        """Reserved capabilities describe features that do not exist yet."""
        return self.status != STATUS_RESERVED


def _cap(code, module, name, description, status):
    return Capability(code=code, module=module, name=name,
                      description=description, status=status)


CAPABILITY_LIST: tuple[Capability, ...] = (
    # --- company (enforced today on the SaaS surface) ---
    _cap('company.view', 'company', 'Ver la empresa',
         'Acceder al panel interno de la empresa.', STATUS_ACTIVE),
    _cap('company.manage', 'company', 'Administrar la empresa',
         'Editar datos y sucursales de la empresa.', STATUS_ACTIVE),

    # --- memberships (enforced today) ---
    _cap('memberships.view', 'memberships', 'Ver personal',
         'Consultar el personal interno de la empresa.', STATUS_ACTIVE),
    _cap('memberships.manage', 'memberships', 'Administrar personal',
         'Dar de alta, editar y desactivar personal interno.', STATUS_ACTIVE),

    # --- areas and roles (enforced today) ---
    _cap('areas.manage', 'areas', 'Administrar áreas',
         'Crear y editar las áreas internas de la empresa.', STATUS_ACTIVE),
    _cap('roles.manage', 'roles', 'Administrar roles',
         'Crear y editar los roles internos y sus permisos.', STATUS_ACTIVE),

    # --- catalogue (module exists; endpoints still legacy-authorised) ---
    _cap('products.view', 'products', 'Ver productos',
         'Consultar el catálogo interno.', STATUS_AVAILABLE),
    _cap('products.manage', 'products', 'Administrar productos',
         'Crear y editar productos y categorías.', STATUS_AVAILABLE),

    # --- inventory (module exists; endpoints still legacy-authorised) ---
    _cap('inventory.view', 'inventory', 'Ver inventario',
         'Consultar stock y Kardex.', STATUS_AVAILABLE),
    _cap('inventory.adjust', 'inventory', 'Mover inventario',
         'Registrar entradas y salidas de stock.', STATUS_AVAILABLE),
    _cap('inventory.reports', 'inventory', 'Reportes de inventario',
         'Ver reportes de stock y rotación.', STATUS_AVAILABLE),

    # --- sales (module exists; endpoints still legacy-authorised) ---
    _cap('sales.orders.view', 'sales', 'Ver pedidos',
         'Consultar pedidos de la empresa.', STATUS_AVAILABLE),
    _cap('sales.orders.manage', 'sales', 'Administrar pedidos',
         'Gestionar el despacho de pedidos.', STATUS_AVAILABLE),
    _cap('sales.notes.manage', 'sales', 'Notas de venta internas',
         'Emitir y descargar notas de venta internas.', STATUS_AVAILABLE),

    # --- cross-cutting (module exists; endpoints still legacy-authorised) ---
    _cap('reports.view', 'reports', 'Ver reportes',
         'Acceder a los reportes operativos.', STATUS_AVAILABLE),
    _cap('settings.view', 'settings', 'Ver configuración',
         'Consultar la configuración de la empresa.', STATUS_AVAILABLE),
    _cap('settings.manage', 'settings', 'Administrar configuración',
         'Editar la configuración de la empresa.', STATUS_AVAILABLE),

    # --- technical service ---
    # `service.manage` mirrors the Phase 2A technical-service role, which exists
    # as an authority concept even though no endpoint consumes it yet.
    _cap('service.manage', 'service', 'Servicio técnico',
         'Pertenecer a la autoridad de servicio técnico.', STATUS_AVAILABLE),
    # Everything below describes a module that DOES NOT EXIST. Reserved for
    # design; not assignable, so no role can claim authority over absent code.
    _cap('service.customers.view', 'service', 'Ver clientes de servicio',
         'RESERVADO — módulo de servicio técnico no implementado.', STATUS_RESERVED),
    _cap('service.customers.manage', 'service', 'Administrar clientes de servicio',
         'RESERVADO — módulo de servicio técnico no implementado.', STATUS_RESERVED),
    _cap('service.devices.view', 'service', 'Ver equipos',
         'RESERVADO — módulo de servicio técnico no implementado.', STATUS_RESERVED),
    _cap('service.devices.manage', 'service', 'Administrar equipos',
         'RESERVADO — módulo de servicio técnico no implementado.', STATUS_RESERVED),
    _cap('service.orders.view', 'service', 'Ver órdenes de servicio',
         'RESERVADO — módulo de servicio técnico no implementado.', STATUS_RESERVED),
    _cap('service.orders.create', 'service', 'Crear órdenes de servicio',
         'RESERVADO — módulo de servicio técnico no implementado.', STATUS_RESERVED),
    _cap('service.orders.manage', 'service', 'Administrar órdenes de servicio',
         'RESERVADO — módulo de servicio técnico no implementado.', STATUS_RESERVED),
    _cap('service.diagnostic.manage', 'service', 'Diagnóstico',
         'RESERVADO — módulo de servicio técnico no implementado.', STATUS_RESERVED),
    _cap('service.repair.manage', 'service', 'Reparación',
         'RESERVADO — módulo de servicio técnico no implementado.', STATUS_RESERVED),
    _cap('service.quality.manage', 'service', 'Control de calidad',
         'RESERVADO — módulo de servicio técnico no implementado.', STATUS_RESERVED),
)

CAPABILITIES: dict[str, Capability] = {c.code: c for c in CAPABILITY_LIST}
ALL_CAPABILITY_CODES: frozenset[str] = frozenset(CAPABILITIES)
ASSIGNABLE_CAPABILITY_CODES: frozenset[str] = frozenset(
    c.code for c in CAPABILITY_LIST if c.is_assignable
)
RESERVED_CAPABILITY_CODES: frozenset[str] = ALL_CAPABILITY_CODES - ASSIGNABLE_CAPABILITY_CODES

assert len(CAPABILITIES) == len(CAPABILITY_LIST), 'códigos de capability duplicados'


def is_valid_capability(code: str) -> bool:
    return code in CAPABILITIES


def is_assignable_capability(code: str) -> bool:
    return code in ASSIGNABLE_CAPABILITY_CODES


def normalise_capabilities(codes) -> list[str]:
    """
    Validate and canonicalise a capability list coming from an API payload.

    Raises ValueError on an unknown or reserved code. Returns a sorted,
    duplicate-free list so a role's stored set is stable and diffable.
    """
    if codes is None:
        return []
    if isinstance(codes, str) or not hasattr(codes, '__iter__'):
        raise ValueError('Las capacidades deben enviarse como una lista de códigos.')

    cleaned: set[str] = set()
    for raw in codes:
        if not isinstance(raw, str):
            raise ValueError('Cada capacidad debe ser una cadena de texto.')
        code = raw.strip()
        if not is_valid_capability(code):
            raise ValueError(f'Capacidad desconocida: {code}')
        if not is_assignable_capability(code):
            raise ValueError(
                f'La capacidad "{code}" está reservada para un módulo que todavía '
                f'no existe y no puede asignarse.'
            )
        cleaned.add(code)
    return sorted(cleaned)


def serialise_catalog(include_reserved: bool = True) -> list[dict]:
    """Catalogue as plain data for the read-only API."""
    return [
        {
            'code': c.code,
            'module': c.module,
            'name': c.name,
            'description': c.description,
            'status': c.status,
            'assignable': c.is_assignable,
        }
        for c in CAPABILITY_LIST
        if include_reserved or c.is_assignable
    ]
