"""
Tenant resolution — SaaS Phase 1.

THE RULE
--------
The active tenant is NEVER taken from client input. A `company_id` in a request
body, query string, header or cookie is treated as *data to be validated*, never
as the answer to "which company is this?". Anything else lets a caller pivot into
another tenant by editing one number.

RESOLUTION STRATEGY
-------------------
1. Authenticated staff request
   The tenant comes from the caller's own active `Membership` rows:
     - exactly one active membership  → that company (no client input involved)
     - several active memberships     → the caller must name one, and the value is
       validated against their own memberships before use
       (`resolve_company_for_user`)
     - platform administrator (`is_superuser`) → may act across tenants, but the
       target company must still be named explicitly and is audited

2. Public storefront request  (DESIGNED, NOT YET APPLIED)
   The tenant comes from the request host: `blackdog.example.com` →
   `Company.slug = "blackdog"`. Host is set by DNS and the reverse proxy, not by
   page JavaScript, so it is not attacker-controlled in the way a body field is.
   `resolve_company_from_host` implements the lookup; no public view calls it yet
   because catalogue/cart/checkout are not tenantised in this phase.

3. Django admin / management commands
   Explicit; the operator is trusted.

CURRENT SCOPE
-------------
Phase 1 is the FOUNDATION only. `Product`, `Order`, `CartItem`, `StockMovement`,
`SalesNote`, `Coupon` and `Review` are NOT yet tenantised, so the public
storefront behaves exactly as before. The helpers below are what the next phase
builds on, and they are already used to enforce isolation on the multi-tenant
admin endpoints introduced here.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Branch, Company, Membership, UserProfile


class TenantError(Exception):
    """Base class for tenant-resolution failures. Views map these to 400/403."""


class NoTenantError(TenantError):
    """The caller has no usable company context."""


class CrossTenantError(TenantError):
    """The caller referenced a company/branch they have no access to."""


# ---------------------------------------------------------------------------
# Platform vs company administration
# ---------------------------------------------------------------------------

def is_platform_admin(user) -> bool:
    """
    True for a SaaS PLATFORM administrator — someone who operates the platform
    itself and may act across tenants.

    During the transition this is `User.is_superuser`, which is exactly what
    `permissions.get_user_role()` already treats as `superadmin`. Keeping the two
    aligned means this phase introduces no behaviour change.
    """
    return bool(user and user.is_authenticated and user.is_superuser)


def active_memberships(user):
    """Active memberships of a user whose company is also active."""
    if not user or not user.is_authenticated:
        return Membership.objects.none()
    return (
        Membership.objects
        .filter(user=user, is_active=True, company__is_active=True)
        .select_related('company', 'branch')
    )


def get_membership(user, company) -> Membership | None:
    """Return the caller's active membership in `company`, or None."""
    if company is None:
        return None
    company_id = getattr(company, 'pk', company)
    return active_memberships(user).filter(company_id=company_id).first()


def has_company_access(user, company) -> bool:
    """
    Whether `user` may operate inside `company`.

    A platform administrator may. Everyone else needs an ACTIVE membership in an
    ACTIVE company — an inactive membership grants nothing, and neither does
    having no membership at all.
    """
    if is_platform_admin(user):
        return True
    return get_membership(user, company) is not None


def company_role(user, company) -> str | None:
    """Deprecated alias of get_company_role(), kept so Phase 1 imports keep working."""
    return get_company_role(user, company)


def is_company_admin(user, company) -> bool:
    """
    Company-level administrator (NOT a platform administrator).

    Kept as the readable shorthand for the manage_company capability; the role
    set lives in COMPANY_CAPABILITIES, not here.
    """
    return has_company_role(user, company, COMPANY_CAPABILITIES[CAP_MANAGE_COMPANY])


# ---------------------------------------------------------------------------
# Company capability matrix — SINGLE SOURCE OF TRUTH
# ---------------------------------------------------------------------------
#
# Which company roles may do what INSIDE their own company. Views must never
# re-declare role sets; they ask the helpers below.
#
# Scope note: every capability here is COMPANY-scoped. Holding one in company A
# says nothing about company B, and nothing at all about platform authority —
# that is `User.is_superuser` and only that.
#
# `superadmin` is a LEGACY role value kept for backwards compatibility. As a
# Membership role it behaves like a company administrator and stays company
# scoped; it never implies `is_platform_admin`. See can_grant_company_role().

CAP_VIEW_COMPANY = 'view_company'
CAP_MANAGE_COMPANY = 'manage_company'
CAP_MANAGE_MEMBERSHIPS = 'manage_memberships'
CAP_MANAGE_INVENTORY = 'manage_inventory'
CAP_MANAGE_SALES = 'manage_sales'
CAP_MANAGE_TECHNICAL_SERVICE = 'manage_technical_service'

_ADMIN_ROLES = frozenset([UserProfile.ROLE_ADMIN, UserProfile.ROLE_SUPERADMIN])

COMPANY_CAPABILITIES: dict[str, frozenset[str]] = {
    CAP_VIEW_COMPANY: frozenset([
        UserProfile.ROLE_SALES, UserProfile.ROLE_INVENTORY,
        UserProfile.ROLE_TECHNICIAN, *_ADMIN_ROLES,
    ]),
    CAP_MANAGE_COMPANY: _ADMIN_ROLES,
    CAP_MANAGE_MEMBERSHIPS: _ADMIN_ROLES,
    CAP_MANAGE_INVENTORY: frozenset([UserProfile.ROLE_INVENTORY, *_ADMIN_ROLES]),
    CAP_MANAGE_SALES: frozenset([UserProfile.ROLE_SALES, *_ADMIN_ROLES]),
    CAP_MANAGE_TECHNICAL_SERVICE: frozenset([UserProfile.ROLE_TECHNICIAN, *_ADMIN_ROLES]),
}

# `customer` is a buyer, never operational staff: it appears in no capability.
assert all(
    UserProfile.ROLE_CUSTOMER not in roles for roles in COMPANY_CAPABILITIES.values()
), 'customer must never hold a company capability'

# Roles a COMPANY administrator may hand out. `superadmin` is withheld: it is a
# legacy value whose meaning is still being normalised, so only a platform
# administrator may assign it (needed for migration/administration).
GRANTABLE_BY_COMPANY_ADMIN = frozenset([
    UserProfile.ROLE_CUSTOMER, UserProfile.ROLE_SALES,
    UserProfile.ROLE_INVENTORY, UserProfile.ROLE_TECHNICIAN,
    UserProfile.ROLE_ADMIN,
])


def get_company_role(user, company) -> str | None:
    """
    The caller's role INSIDE `company`, or None.

    Returns None for a platform administrator with no membership: platform
    authority is not a company role, and conflating the two is exactly the bug
    this separation exists to prevent.
    """
    membership = get_membership(user, company)
    return membership.role if membership else None


def has_company_role(user, company, allowed_roles) -> bool:
    """Whether the caller holds one of `allowed_roles` inside `company`."""
    role = get_company_role(user, company)
    return role is not None and role in set(allowed_roles)


def has_company_capability(user, company, capability: str) -> bool:
    """
    Whether the caller may perform `capability` inside `company`.

    A platform administrator may do anything, in any company. Everyone else needs
    an active membership, in an active company, whose role holds the capability.
    """
    if capability not in COMPANY_CAPABILITIES:
        raise ValueError(f'Capacidad desconocida: {capability}')
    if is_platform_admin(user):
        return True
    return has_company_role(user, company, COMPANY_CAPABILITIES[capability])


def can_view_company(user, company) -> bool:
    return has_company_capability(user, company, CAP_VIEW_COMPANY)


def can_manage_company(user, company) -> bool:
    return has_company_capability(user, company, CAP_MANAGE_COMPANY)


def can_manage_company_memberships(user, company) -> bool:
    return has_company_capability(user, company, CAP_MANAGE_MEMBERSHIPS)


def can_manage_company_inventory(user, company) -> bool:
    return has_company_capability(user, company, CAP_MANAGE_INVENTORY)


def can_manage_company_sales(user, company) -> bool:
    return has_company_capability(user, company, CAP_MANAGE_SALES)


def can_manage_company_technical_service(user, company) -> bool:
    return has_company_capability(user, company, CAP_MANAGE_TECHNICAL_SERVICE)


def can_grant_company_role(user, company, role: str) -> bool:
    """
    Whether the caller may assign `role` inside `company`.

    A platform administrator may assign any role. A company administrator may
    assign anything except the legacy `superadmin` value — see
    GRANTABLE_BY_COMPANY_ADMIN. Assigning `superadmin` as a Membership role
    NEVER touches `User.is_superuser`, so it is not a platform escalation either
    way; the restriction exists because the value's semantics are still legacy.
    """
    if is_platform_admin(user):
        return True
    if not can_manage_company_memberships(user, company):
        return False
    return role in GRANTABLE_BY_COMPANY_ADMIN


def holds_any_capability(user, capability: str) -> bool:
    """
    Whether the caller holds `capability` in AT LEAST ONE company.

    Used as a coarse view-level gate so a request that could not succeed in any
    company is rejected before the payload is parsed. It is never a substitute
    for the per-company check.
    """
    if capability not in COMPANY_CAPABILITIES:
        raise ValueError(f'Capacidad desconocida: {capability}')
    if is_platform_admin(user):
        return True
    allowed = COMPANY_CAPABILITIES[capability]
    return active_memberships(user).filter(role__in=allowed).exists()


# ---------------------------------------------------------------------------
# Company context
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CompanyContext:
    """
    Everything a request needs to know about the tenant it is acting on.

    Built only through build_company_context(), which resolves the company from
    the caller's own memberships — never from raw client input.
    """

    user: object
    company: Company
    membership: Membership | None
    role: str | None
    is_platform_admin: bool

    def has_role(self, *roles) -> bool:
        return self.role is not None and self.role in set(roles)

    def can(self, capability: str) -> bool:
        """Capability check that reuses the shared matrix — no local role sets."""
        if capability not in COMPANY_CAPABILITIES:
            raise ValueError(f'Capacidad desconocida: {capability}')
        if self.is_platform_admin:
            return True
        return self.role is not None and self.role in COMPANY_CAPABILITIES[capability]


def build_company_context(user, requested_company_id=None) -> CompanyContext:
    """
    Resolve the tenant and package the caller's authority inside it.

    Raises NoTenantError / CrossTenantError exactly like resolve_company_for_user.
    """
    company = resolve_company_for_user(user, requested_company_id)
    membership = get_membership(user, company)
    return CompanyContext(
        user=user,
        company=company,
        membership=membership,
        role=membership.role if membership else None,
        is_platform_admin=is_platform_admin(user),
    )


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def resolve_company_for_user(user, requested_company_id=None) -> Company:
    """
    Resolve the company a staff request should act on.

    `requested_company_id` is UNTRUSTED. It is only ever used to *select* among
    companies the caller already has access to; it can never widen access.

    Raises NoTenantError / CrossTenantError; views map both to 400/403.
    """
    if not user or not user.is_authenticated:
        raise NoTenantError('Se requiere autenticación para resolver la empresa.')

    if requested_company_id is not None:
        try:
            company = Company.objects.get(pk=requested_company_id)
        except (Company.DoesNotExist, ValueError, TypeError):
            # Same error for "does not exist" and "not yours" — do not leak
            # whether an id belongs to another tenant.
            raise CrossTenantError('Empresa no encontrada o sin acceso.')
        if not has_company_access(user, company):
            raise CrossTenantError('Empresa no encontrada o sin acceso.')
        if not company.is_active and not is_platform_admin(user):
            raise CrossTenantError('La empresa está desactivada.')
        return company

    memberships = list(active_memberships(user)[:2])
    if len(memberships) == 1:
        return memberships[0].company
    if not memberships:
        raise NoTenantError('El usuario no pertenece a ninguna empresa activa.')
    raise NoTenantError(
        'El usuario pertenece a varias empresas: indique cuál usar.'
    )


def resolve_company_from_host(host: str) -> Company | None:
    """
    Map a request host to a Company via its slug (DESIGNED, not yet wired up).

    `blackdog.example.com` → Company.slug == "blackdog".
    Returns None when the host carries no usable subdomain or no company matches.
    """
    if not host:
        return None
    hostname = host.split(':')[0].strip().lower()
    labels = hostname.split('.')
    if len(labels) < 3:
        return None
    subdomain = labels[0]
    if subdomain in ('www', 'api', 'admin', 'app'):
        return None
    return Company.objects.filter(slug=subdomain, is_active=True).first()


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

def assert_branch_in_company(branch, company) -> None:
    """
    Reject a branch id belonging to a different company.

    The FK already prevents a Branch from having two companies, but a caller can
    still *pass* another tenant's branch id — this is the check that stops it.
    """
    if branch is None:
        return
    company_id = getattr(company, 'pk', company)
    if branch.company_id != company_id:
        raise CrossTenantError('La sucursal no pertenece a esta empresa.')


def scope_queryset(queryset, user, *, company_field='company'):
    """
    Restrict `queryset` to the companies the caller may see.

    Platform administrators see everything; everyone else sees only companies
    where they hold an active membership. A caller with no membership gets an
    EMPTY queryset — never the unfiltered one.
    """
    if is_platform_admin(user):
        return queryset
    company_ids = list(active_memberships(user).values_list('company_id', flat=True))
    if not company_ids:
        return queryset.none()
    return queryset.filter(**{f'{company_field}__in': company_ids})


def visible_companies(user):
    """Companies the caller may see. Empty for users without membership."""
    if is_platform_admin(user):
        return Company.objects.all()
    company_ids = list(active_memberships(user).values_list('company_id', flat=True))
    if not company_ids:
        return Company.objects.none()
    return Company.objects.filter(pk__in=company_ids)
