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
    """The caller's role inside `company`, or None if they have no access."""
    membership = get_membership(user, company)
    return membership.role if membership else None


def is_company_admin(user, company) -> bool:
    """Company-level administrator (not necessarily a platform administrator)."""
    return company_role(user, company) in (
        UserProfile.ROLE_ADMIN, UserProfile.ROLE_SUPERADMIN,
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
