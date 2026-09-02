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

from .capabilities import (
    ALL_CAPABILITY_CODES,
    ASSIGNABLE_CAPABILITY_CODES,
)
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

    Phase 2A.1: this now delegates to resolve_capabilities(), so a custom role
    genuinely governs. Behaviour is unchanged for a membership with no custom
    role assignments — LEGACY_ROLE_CAPABILITIES reproduces the Phase 2A matrix
    exactly, and there is a test asserting the two agree for every role.

    Delegating (rather than OR-ing the two systems) is the whole point: a company
    that restricts someone with a custom role must not have the legacy matrix
    hand the withheld authority back.
    """
    if capability not in COMPANY_CAPABILITIES:
        raise ValueError(f'Capacidad desconocida: {capability}')
    if is_platform_admin(user):
        return True
    return has_capability(user, company, LEGACY_CAP_TO_CODE[capability])


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

    Coarse view-level gate: it rejects a request that could not succeed in any
    company before the payload is parsed. NEVER a substitute for the per-company
    check — holding something somewhere is not holding it everywhere.

    Phase 2A.1: walks each membership through resolve_capabilities(), so a user
    whose authority comes from a custom role is not rejected here just because
    their legacy Membership.role would not have qualified.
    """
    if capability not in COMPANY_CAPABILITIES:
        raise ValueError(f'Capacidad desconocida: {capability}')
    if is_platform_admin(user):
        return True
    return holds_any_capability_code(user, LEGACY_CAP_TO_CODE[capability])


def holds_any_capability_code(user, code: str) -> bool:
    """Same coarse gate, addressed by catalogue code instead of legacy name."""
    if code not in ALL_CAPABILITY_CODES:
        raise ValueError(f'Capacidad desconocida: {code}')
    if is_platform_admin(user):
        return True
    for membership in active_memberships(user):
        if code in resolve_capabilities(user, membership.company):
            return True
    return False


# ---------------------------------------------------------------------------
# Phase 2A.1 — capability resolution (custom roles + legacy fallback)
# ---------------------------------------------------------------------------
#
# Legacy Phase 2A roles are expressed as capability codes so BOTH systems answer
# in the same vocabulary. This mapping is what lets a custom role fully replace
# a legacy role instead of merely adding to it.

LEGACY_CAP_TO_CODE: dict[str, str] = {
    CAP_VIEW_COMPANY: 'company.view',
    CAP_MANAGE_COMPANY: 'company.manage',
    CAP_MANAGE_MEMBERSHIPS: 'memberships.manage',
    CAP_MANAGE_INVENTORY: 'inventory.adjust',
    CAP_MANAGE_SALES: 'sales.orders.manage',
    CAP_MANAGE_TECHNICAL_SERVICE: 'service.manage',
}

# What each legacy Membership.role is worth in catalogue terms. Used both for the
# fallback below and to seed the preset roles of a company.
LEGACY_ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    UserProfile.ROLE_CUSTOMER: frozenset(),
    UserProfile.ROLE_SALES: frozenset([
        'company.view', 'products.view', 'reports.view',
        'sales.orders.view', 'sales.orders.manage', 'sales.notes.manage',
        # PHASE C1: operating the till IS the commercial job, so the legacy role
        # gets it for the same reason the `Ventas` preset does. Kept in step
        # with that preset deliberately — a test asserts the two agree, because
        # two descriptions of the same authority that drift apart mean somebody
        # is granted different things depending on which one is consulted.
        #
        # `sales.analytics.view` is NOT here, also matching the preset: ringing
        # up a cable does not require seeing the company's turnover.
        'sales.pos.use',
    ]),
    UserProfile.ROLE_INVENTORY: frozenset([
        'company.view', 'products.view', 'reports.view',
        'inventory.view', 'inventory.adjust', 'inventory.reports',
    ]),
    UserProfile.ROLE_TECHNICIAN: frozenset([
        'company.view', 'service.manage',
    ]),
    UserProfile.ROLE_ADMIN: ASSIGNABLE_CAPABILITY_CODES,
    # Legacy value: company-scoped authority, never platform authority.
    UserProfile.ROLE_SUPERADMIN: ASSIGNABLE_CAPABILITY_CODES,
}

assert all(
    caps <= ASSIGNABLE_CAPABILITY_CODES for caps in LEGACY_ROLE_CAPABILITIES.values()
), 'un rol legacy referencia una capacidad inexistente o reservada'
assert set(LEGACY_CAP_TO_CODE.values()) <= ALL_CAPABILITY_CODES, \
    'LEGACY_CAP_TO_CODE apunta a una capacidad inexistente'


def active_role_assignments(membership):
    """Assignments that currently count: active, with an active role."""
    if membership is None:
        return []
    return list(
        membership.role_assignments
        .filter(is_active=True, role__is_active=True)
        .select_related('role', 'area')
    )


def has_custom_role_history(membership) -> bool:
    """
    Has this membership EVER been given a custom role?

    ANY row, active or not, and with an active role or not. That is the whole
    point: the question is not "does it have authority now" — that is
    `active_role_assignments()` — but "has this company ever expressed this
    person's authority through RBAC". Once it has, the legacy matrix stops
    being an answer about them.

    Reliable because REVOKING is soft: `is_active=False`, and the row stays for
    the audit trail. A row's existence is therefore a fact about the past, which
    is exactly the kind of fact this question needs.

    WHAT ACTUALLY DELETES ONE, stated precisely rather than claimed away — an
    earlier version of this docstring asserted "nothing in this project deletes
    a MembershipRoleAssignment", and that was false:

      · `seed_demo_users` purges rows outright, but it is DEBUG-only and deletes
        the `User` too, so nobody survives to be revived.
      · Migration 0048 removes redundant DUPLICATES of one fact; the survivor
        keeps the history, so the marker is preserved by construction.
      · `membership` is `on_delete=CASCADE`, so deleting a `Membership` takes its
        assignments with it. That does not revive anybody either — with no
        membership, `resolve_capabilities` returns nothing at all. The residual
        risk is a membership DELETED and then RECREATED with a legacy `role`,
        which is an act only somebody who could already grant that authority can
        perform.

    So the invariant holds, but it rests on discipline rather than on a column.
    A `Membership.adopted_rbac_at` stamp would make it structural and survive
    the cascade; it is recorded as debt rather than smuggled into this phase.
    """
    if membership is None:
        return False
    return membership.role_assignments.exists()


def resolve_capabilities(user, company) -> frozenset[str]:
    """
    Every capability the caller holds inside `company`.

    Resolution order — deliberately EXCLUSIVE, not additive:

      1. Platform master  → every assignable capability, in every company.
      2. Custom roles     → the UNION of the capabilities of the membership's
                            active role assignments. The legacy Membership.role
                            is IGNORED here. This is the important part: once a
                            company models a user with custom roles, restricting
                            them actually restricts them. Falling back to the
                            legacy matrix as well would silently re-grant what
                            the custom role withheld.
      3. Migrated, but with nothing active → NOTHING. See below.
      4. Legacy fallback  → the capabilities of Membership.role, but ONLY for a
                            membership that never adopted RBAC, so a company
                            that has not configured any custom role keeps
                            working exactly as in Phase 2A.

    WHY STEP 3 EXISTS — THE BUG IT CLOSES
    -------------------------------------
    This used to read "if there are active assignments use them, otherwise fall
    back to the legacy role". That is safe only while somebody HAS an active
    assignment, and assignments are revoked by setting `is_active=False`.

    So: take a membership whose legacy `role` is `admin`, give it one narrow
    custom role, then revoke that role. Active assignments drop to zero, the old
    code reached the last line, and the person was handed
    `ASSIGNABLE_CAPABILITY_CODES` again — every capability in the tenant. The
    act of TAKING AWAY their only role made them an administrator. The same
    happens if the role itself is merely deactivated.

    The distinction that fixes it is not "how many roles are active" but "has
    this company ever expressed this person's authority through RBAC". Once it
    has, the legacy matrix is no longer an answer about them, and zero active
    roles honestly means zero capabilities. That is a valid state — a person
    between jobs, or suspended — and it must stay reachable, because a system
    where revoking the last role is impossible is a system where nobody is ever
    really revoked.

    A membership that is inactive, or whose company is inactive, holds nothing.
    """
    if is_platform_admin(user):
        return ASSIGNABLE_CAPABILITY_CODES

    membership = get_membership(user, company)
    if membership is None:
        return frozenset()

    assignments = active_role_assignments(membership)
    if assignments:
        granted: set[str] = set()
        for assignment in assignments:
            granted |= set(assignment.role.capability_set)
        return frozenset(granted)

    if has_custom_role_history(membership):
        # Migrated to RBAC and currently holds no active role. NOT a legacy
        # user, so the legacy matrix says nothing about them.
        return frozenset()

    return LEGACY_ROLE_CAPABILITIES.get(membership.role, frozenset())


def has_capability(user, company, code: str) -> bool:
    """Whether the caller holds capability `code` inside `company`."""
    if code not in ALL_CAPABILITY_CODES:
        raise ValueError(f'Capacidad desconocida: {code}')
    return code in resolve_capabilities(user, company)


def user_areas(user, company):
    """
    Areas the caller is assigned to inside `company`.

    Organisational only — this never affects authority. See CompanyArea.
    """
    membership = get_membership(user, company)
    return [
        a.area for a in active_role_assignments(membership)
        if a.area is not None and a.area.is_active
    ]


def can_delegate_capabilities(user, company, codes) -> bool:
    """
    Whether the caller may put `codes` into a role of `company`.

    Anti-escalation rule: a company administrator may only delegate capabilities
    they themselves hold. Otherwise a limited admin could author a powerful role,
    assign it to themselves and escalate. A platform master is exempt.
    """
    if is_platform_admin(user):
        return True
    return set(codes) <= set(resolve_capabilities(user, company))


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


def resolve_storefront_company(request) -> Company | None:
    """
    Resolve the tenant whose catalogue this PUBLIC request should see.

    Order, most specific first:

      1. The request HOST. Set by DNS and the reverse proxy, not by page
         JavaScript, so it is not attacker-controlled the way a body field is.
      2. settings.DEFAULT_STOREFRONT_COMPANY_SLUG — an explicit single-store
         deployment naming its own tenant.
      3. DEBUG only, and only when the database holds exactly ONE active company.

    Returns None when nothing resolves, and the public views then serve an EMPTY
    catalogue. An empty storefront is the safe failure: serving somebody else's
    products is not.

    There is deliberately no "first company in the database" fallback. On a
    multi-tenant install that would quietly hand one tenant's catalogue to
    another tenant's domain, and nothing in the response would reveal it.

    A `company` id in the request body is NEVER consulted here: the public
    surface has no authenticated identity to validate it against.
    """
    from django.conf import settings

    company = resolve_company_from_host(request.get_host() if request else '')
    if company is not None:
        return company

    slug = getattr(settings, 'DEFAULT_STOREFRONT_COMPANY_SLUG', '') or ''
    if slug:
        return Company.objects.filter(slug=slug.strip().lower(), is_active=True).first()

    # Exactly ONE active company: unambiguous by construction, so there is
    # nothing to leak — it is not "the first of many", it is "the only one".
    #
    # This is not a convenience: without it, every existing single-store
    # deployment would serve an EMPTY catalogue the moment it applied these
    # migrations, until someone noticed and set the env var. The fallback stops
    # firing the instant a second company exists, which is precisely when the
    # operator must configure per-tenant hosts or the slug above.
    active = Company.objects.filter(is_active=True)
    if active.count() == 1:
        return active.first()

    return None


def storefront_products(request):
    """
    Active products of the storefront's tenant, annotated with SELLABLE stock.

    PHASE 2D — WHAT THE STOREFRONT SHOWS IS WHAT CHECKOUT CAN DELIVER.

    `available_stock` is the quantity in the company's FULFILLMENT branch, not
    the sum across every shop. Those are different numbers the moment a company
    opens a second location, and showing the sum would promise units the online
    order cannot take: a customer sees 20, checkout finds 2 in the branch that
    actually ships, and the sale fails at the last step for no reason the
    customer can understand.

    A company with no resolvable fulfillment branch annotates ZERO everywhere —
    consistent with checkout, which refuses. An empty shelf is the honest
    failure; a full one that cannot ship is not.

    Empty queryset when the storefront itself does not resolve.
    """
    return company_storefront_products(resolve_storefront_company(request))


def company_storefront_products(company):
    """
    The same queryset as `storefront_products`, for a Company already resolved.

    Extracted so a surface that names its tenant EXPLICITLY — the `/api/v1/`
    catalogue Mobile calls, which reaches a shared API host and therefore cannot
    be identified by Host header — reuses this scoping instead of restating it.
    One definition of "what this storefront sells" is the point: a second copy
    would drift, and the drift would be a cross-tenant leak nobody sees in a
    diff.

    `None` yields an empty queryset, exactly as an unresolved Host does.
    """
    from django.db.models import IntegerField, OuterRef, Subquery, Value
    from django.db.models.functions import Coalesce

    from .models import BranchStock, Product

    if company is None:
        return Product.objects.none()

    qs = Product.objects.filter(company=company, is_active=True)
    branch = company_fulfillment_branch(company)
    if branch is None:
        return qs.annotate(available_stock=Value(0, output_field=IntegerField()))

    in_branch = BranchStock.objects.filter(
        branch=branch, product_id=OuterRef('pk'),
    ).values('quantity')[:1]
    return qs.annotate(
        available_stock=Coalesce(
            Subquery(in_branch, output_field=IntegerField()),
            Value(0, output_field=IntegerField()),
        ),
    )


def storefront_available_stock(request, product) -> int:
    """
    Sellable units of one product on this storefront. Zero without a branch.

    The single-object counterpart of the `available_stock` annotation above, for
    the cart and checkout paths, which hold a handful of products rather than a
    page of them.
    """
    from .models import BranchStock

    branch = storefront_fulfillment_branch(request)
    if branch is None:
        return 0
    row = BranchStock.objects.filter(branch=branch, product=product).first()
    return row.quantity if row else 0


def storefront_cart_items(request, session_key):
    """
    The cart of this browser session ON THIS STOREFRONT.

    CART TENANCY WITHOUT A CART MODEL (Phase 2C)
    --------------------------------------------
    A cart is identified by `session_key` + the storefront's company, derived
    through `CartItem.product.company`. No `Cart` model and no `CartItem.company`
    column were added, because neither would tell us anything the product does
    not already say — and a duplicated company field is a second source of truth
    that can drift out of sync with the product it points at.

    A consequence worth stating: one browser can hold SEVERAL logical carts at
    once, one per storefront, sharing a session key. That is correct — the same
    person shopping at two tenants has two carts, and emptying one must not touch
    the other.

    Returns an EMPTY queryset when the storefront does not resolve, matching the
    catalogue's safe failure.
    """
    from .models import CartItem

    company = resolve_storefront_company(request)
    if company is None or not session_key:
        return CartItem.objects.none()
    return CartItem.objects.filter(
        session_key=session_key, product__company=company,
    )


def storefront_coupon(request, code):
    """
    A coupon of THIS storefront's tenant, or None.

    Never a global lookup: two tenants may run the same code, and honouring
    another company's discount is both a leak and a financial error.
    """
    from .models import Coupon

    company = resolve_storefront_company(request)
    if company is None or not code:
        return None
    return Coupon.objects.filter(
        company=company, code=code, is_active=True,
    ).first()


def storefront_orders(request, user):
    """
    The authenticated customer's orders ON THIS STOREFRONT.

    The same User may buy from several tenants — that is one identity, not
    several. But inside storefront A they must see only their orders from A:
    listing B's would leak what they bought elsewhere into an unrelated business's
    account page.
    """
    from .models import Order

    company = resolve_storefront_company(request)
    if company is None or not user or not user.is_authenticated:
        return Order.objects.none()
    return Order.objects.filter(user=user, company=company)


def storefront_categories(request):
    """Categories of the storefront's tenant. Empty when unresolved."""
    return company_storefront_categories(resolve_storefront_company(request))


def company_storefront_categories(company):
    """Categories of an already-resolved Company. Empty for `None`."""
    from .models import Category

    if company is None:
        return Category.objects.none()
    return Category.objects.filter(company=company)


def customer_owned_orders(user, company):
    """
    The orders `user` OWNS inside `company`. The security boundary of the
    customer surface.

    OWNERSHIP IS TWO FKs, AND BOTH ARE NEEDED:

      `Order.user`            — set at checkout when the buyer was signed in.
                                Unambiguous: an account authenticated for it.

      `Order.customer.user`   — the CRM record this sale belongs to, linked to
                                an account. This covers the real case that
                                `Order.user` alone misses: someone bought
                                anonymously, the business matched them to their
                                CRM file by DOCUMENT, and that file was later
                                linked to their login (see
                                `customer_services.link_order_to_customer`).
                                Those purchases are genuinely theirs.

    EMAIL IS NEVER OWNERSHIP. `Order.customer_email` is a snapshot of what was
    typed at checkout, it carries no uniqueness, and `find_possible_duplicates`
    exists precisely because a household or a small office share one address.
    Matching on it would hand one person another person's purchase history.
    Nor is the document consulted here: matching documents is the CRM's job at
    checkout time, under the business's own eyes, not an access rule.

    A null company yields nothing rather than everything.
    """
    from django.db.models import Q

    from .models import Order

    if company is None or user is None or not user.is_authenticated:
        return Order.objects.none()

    return Order.objects.filter(
        Q(user=user) | Q(customer__user=user),
        company=company,
    ).distinct()


def has_customer_relation(user, company) -> bool:
    """
    Whether `user` is a client OF `company`, as the server can prove.

    Two ways, either of which is a fact already in the database:

      1. an ACTIVE `Customer` row — the business keeps a file on them;
      2. they own at least one order here — they bought something.

    The second exists so that archiving a CRM record cannot lock someone out of
    their own purchase history. "We closed your file" is a commercial decision;
    "you may no longer see what you bought" is not one it should be able to make
    by accident.

    A MEMBERSHIP IS NOT A CUSTOMER RELATION. Working for a company does not make
    its clients' orders yours, and this surface must never become a staff view
    by way of an employee who happens to be signed in. Internal access to
    company-wide orders is a DIFFERENT surface with a different permission
    (`sales.orders.view`) — see DEC-API-001.
    """
    from .models import Customer

    if company is None or user is None or not user.is_authenticated:
        return False

    if Customer.objects.filter(company=company, user=user, is_active=True).exists():
        return True

    return customer_owned_orders(user, company).exists()


def access_contexts(user):
    """
    Everything the CLIENT may know about where this user can act.

    One entry per company the user has any verified relation with, saying which
    relations hold and — for members — which capabilities the server resolved.

    ⚠️  THIS IS FOR PRESENTATION. Capabilities travel so the app can decide
    which tab to draw, not whether an operation is allowed. Every internal
    endpoint re-resolves them server-side; a client that lies about holding
    `inventory.view` gets a 403 from the endpoint, not inventory. See
    DEC-MOBILE-008.

    Platform master is reported SEPARATELY and grants nothing here: it does not
    enumerate every tenant into a phone. Operating on a company as platform
    master stays an explicit, audited act on the internal surface.
    """
    from .models import Customer

    if user is None or not user.is_authenticated:
        return []

    contexts: dict[int, dict] = {}

    def slot(company):
        if company.pk not in contexts:
            contexts[company.pk] = {
                'company': {'slug': company.slug, 'name': company.name},
                'customer': False,
                'member': False,
                'capabilities': [],
            }
        return contexts[company.pk]

    for membership in (
        Membership.objects
        .filter(user=user, is_active=True, company__is_active=True)
        .select_related('company')
    ):
        entry = slot(membership.company)
        entry['member'] = True
        entry['capabilities'] = sorted(resolve_capabilities(user, membership.company))

    for record in (
        Customer.objects
        .filter(user=user, is_active=True, company__is_active=True)
        .select_related('company')
    ):
        slot(record.company)['customer'] = True

    return sorted(contexts.values(), key=lambda row: row['company']['name'])


RELATION_MEMBER = 'member'      # staff of the company
RELATION_CUSTOMER = 'customer'  # buys from the company


def verified_company_relations(user):
    """
    Every company this AUTHENTICATED user has a server-verified relation with.

    WHY THIS IS NOT JUST MEMBERSHIPS

    Migration 0015 deliberately gave customers no `Membership`: a shopper is not
    staff, and turning buyers into company members the day multi-tenant
    permissions went live would have been a quiet privilege escalation. That
    decision is correct and this function does not revisit it.

    But it means memberships alone answer the wrong question for a mobile app
    whose entire audience is shoppers. A customer signing in would get an empty
    list, and the app would conclude it has no company at all.

    So both relations are reported, and LABELLED, because they are not the same
    thing and the client must never flatten them:

      member    → `Membership`, active, company active. This person is staff.
      customer  → active `Customer` CRM row of an active company. An ARCHIVED
                  customer is excluded: the business has closed that file, and a
                  relation the company considers over is not one to advertise.

    WHAT THIS IS NOT

    Not authorization. It is a statement of fact about relations that already
    exist in the database, computed from the authenticated user's own rows and
    nothing the client sent. A private endpoint must still re-check membership
    for itself: this list is what the app may DISPLAY and SELECT from, never a
    grant it may present back as proof.

    `is_superuser` is deliberately ignored. A platform administrator does not
    silently receive every tenant on a phone; if that is ever needed it will be
    an explicit, audited feature rather than a side effect of a boolean.

    Returns a list of dicts ordered by name, one entry per company, with
    `member` winning when a user is both staff and customer of the same company.
    """
    from .models import Customer

    if user is None or not user.is_authenticated:
        return []

    relations: dict[int, dict] = {}

    for membership in (
        Membership.objects
        .filter(user=user, is_active=True, company__is_active=True)
        .select_related('company')
    ):
        company = membership.company
        relations[company.pk] = {
            'slug': company.slug,
            'name': company.name,
            'relation': RELATION_MEMBER,
        }

    for record in (
        Customer.objects
        .filter(user=user, is_active=True, company__is_active=True)
        .select_related('company')
    ):
        company = record.company
        # Staff wins: being both is not a demotion.
        if company.pk in relations:
            continue
        relations[company.pk] = {
            'slug': company.slug,
            'name': company.name,
            'relation': RELATION_CUSTOMER,
        }

    return sorted(relations.values(), key=lambda row: row['name'])


def resolve_public_storefront_company(slug) -> Company | None:
    """
    Resolve the tenant NAMED BY THE CLIENT for a public catalogue request.

    This is the `/api/v1/` counterpart of `resolve_storefront_company`, and the
    difference matters. The web storefront identifies its tenant by Host,
    because DNS and the reverse proxy set it and page JavaScript cannot. A
    mobile app has no such signal: it reaches ONE shared API host, so it must
    say which storefront it wants.

    WHAT THIS SLUG IS: a SELECTOR of a public catalogue.
    WHAT IT IS NOT: authorization, identity, or a grant of any kind.

    Naming a tenant selects which PUBLIC shelf to read. It cannot reach private
    data, because this resolver is only ever used by public catalogue views —
    every private surface keeps deriving its company from the authenticated
    user's membership (BR-001/BR-002), never from a path segment. Anyone can
    type any slug; that is fine when the answer is a shop window and fatal when
    it is an order history, which is why these two paths never converge.

    Only an ACTIVE company resolves. Unknown, inactive, malformed and blank all
    return None, and the views turn None into a 404 — indistinguishable from
    each other on purpose, so the endpoint cannot be walked to enumerate which
    companies exist.
    """
    if not isinstance(slug, str):
        return None
    normalized = slug.strip().lower()
    if not normalized or len(normalized) > 100:
        return None
    return Company.objects.filter(slug=normalized, is_active=True).first()


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

def pilot_company() -> Company | None:
    """
    The installation's first tenant — the one migration 0015 created.

    Identified by signature (oldest row), not by name, so an installation whose
    first tenant is a different business resolves to ITS own first tenant.
    """
    return Company.objects.order_by('pk').first()


def legacy_catalog_company(user) -> Company | None:
    """
    LEGACY BRIDGE — Phase 2B, temporary and deliberately narrow.

    An operator who holds a legacy staff role (UserProfile.role) but no
    Membership is the state of every pre-SaaS operator. Before Phase 2B the
    catalogue was global, so they managed all of it; now it has owners.

    The dangerous reading would be "legacy admin sees everything", which on a
    multi-tenant install means one company's staff administering another's
    catalogue. So this bridge returns EXACTLY ONE company — the pilot — and
    never any other. A legacy operator therefore keeps working on the catalogue
    they always managed, and gains access to nothing new.

    Returns None for customers, for technicians (never had catalogue access) and
    for anyone who does have a Membership: they go through normal resolution.

    DEBT: this disappears once every operator holds a Membership. Tracked in
    docs/saas-multiempresa.md.
    """
    from .permissions import get_user_role

    if not user or not user.is_authenticated:
        return None
    if is_platform_admin(user):
        # A platform master is not a pre-SaaS operator. Bridging them to the
        # pilot would silently pick a tenant for someone whose whole role is to
        # act across tenants — they must name the company explicitly.
        return None
    if active_memberships(user).exists():
        return None  # has real company context; the bridge is not for them

    role = get_user_role(user)
    if role not in (
        UserProfile.ROLE_SALES, UserProfile.ROLE_INVENTORY,
        UserProfile.ROLE_ADMIN, UserProfile.ROLE_SUPERADMIN,
    ):
        return None

    pilot = pilot_company()
    return pilot if pilot is not None and pilot.is_active else None


# How a catalogue request obtained its company. The caller needs to know,
# because the two sources carry different authority.
CATALOG_SOURCE_TENANT = 'tenant'          # membership or platform master
CATALOG_SOURCE_LEGACY = 'legacy_bridge'   # pre-SaaS operator, pilot tenant only


def resolve_catalog_company(user, requested_company_id=None):
    """
    Resolve the company an INTERNAL catalogue request acts on.

    Returns `(company, source)`; `(None, None)` when nothing applies.

    Two sources, and the distinction matters:

      CATALOG_SOURCE_TENANT — the caller has a Membership (or is a platform
        master naming a company). Authority is the company CAPABILITY:
        products.view / products.manage. This is the real model.

      CATALOG_SOURCE_LEGACY — the caller has no company context at all, only a
        legacy staff role. They get the PILOT tenant and nothing else, and their
        authority remains the legacy DRF permission class the view already
        applied. Giving them capabilities they never had would be inventing
        authority; refusing them outright would lock every pre-SaaS operator out
        of the catalogue they have always managed.
    """
    try:
        return resolve_company_for_user(user, requested_company_id), CATALOG_SOURCE_TENANT
    except (NoTenantError, CrossTenantError):
        # A caller naming a company they cannot reach never falls back: the
        # bridge is for callers with no context, not for rejected requests.
        if requested_company_id is None:
            bridged = legacy_catalog_company(user)
            if bridged is not None:
                return bridged, CATALOG_SOURCE_LEGACY
        return None, None


def resolve_catalog_company_only(user, requested_company_id=None) -> Company | None:
    """Company alone, for callers that do not care how it was resolved."""
    company, _source = resolve_catalog_company(user, requested_company_id)
    return company


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


# ---------------------------------------------------------------------------
# SaaS Phase 2D — branch authority
# ---------------------------------------------------------------------------
#
# TWO QUESTIONS, ASKED SEPARATELY, BOTH REQUIRED
#
#   has_capability(user, company, 'inventory.adjust')   MAY THEY move stock?
#   has_branch_access(user, branch)                     WHERE may they move it?
#
# Collapsing these into one check is the mistake this section exists to prevent.
# "Can adjust inventory" is a company-wide statement about a person's job;
# "may operate branch Cayma" is a statement about where they work. A warehouse
# clerk in Cayma holds the first and must not thereby hold the second for the
# downtown shop.
#
# BRANCH IDS FROM CLIENTS ARE NEVER TRUSTED
# Exactly as with `company`, a `branch` id arriving in a query string is data to
# be validated against the caller's own grants, never the answer to "which
# branch is this?". A branch the caller cannot reach answers like one that does
# not exist — see BranchAccessError and how the views map it.

class NoBranchError(TenantError):
    """The caller has no branch they may operate in this company."""


class BranchAccessError(TenantError):
    """The named branch is not one the caller may operate."""


def company_branches(company):
    """Active branches of `company`, oldest first. Never crosses tenants."""
    from .models import Branch

    if company is None:
        return Branch.objects.none()
    return Branch.objects.filter(
        company=company, is_active=True,
    ).order_by('pk')


def visible_branches(user, company):
    """
    Every ACTIVE branch of `company` the caller may operate in.

    Resolution, in order:

      1. Platform master → every active branch of the company they selected.
         Their authority is platform-wide, but it is still exercised inside ONE
         company at a time: this never mixes tenants.
      2. Mode ALL        → every active branch, including ones created after the
         membership was granted. That automatic inclusion is the point of ALL.
      3. Mode SELECTED   → exactly the active grants, and nothing else. A branch
         opened tomorrow is NOT included; somebody has to grant it.

    An inactive company, an inactive membership or an inactive branch yields
    nothing: a deactivated location is not somewhere work happens.

    Returns an EMPTY queryset rather than raising, so callers can compose it into
    a larger query. Use assert_branch_access() when you need the refusal.
    """
    from .models import Branch, Membership

    if company is None:
        return Branch.objects.none()

    active = company_branches(company)

    if is_platform_admin(user):
        return active

    if not company.is_active:
        return Branch.objects.none()

    membership = get_membership(user, company)
    if membership is None:
        # LEGACY BRIDGE, same one the catalogue and the Kardex use.
        #
        # A pre-SaaS operator has a staff role and no Membership. Phase 2B gave
        # them the pilot company; without this they would reach that company and
        # then find it has no branches THEY can operate, which is a 403 dressed
        # up as an empty list. The bridge is company-wide by construction — it
        # predates branches entirely — so it grants the pilot's active branches
        # and nothing else, and never fires for anyone who has a real Membership.
        bridged = legacy_catalog_company(user)
        if bridged is not None and bridged.pk == company.pk:
            return active
        return Branch.objects.none()

    if not membership.grants_business_access:
        return Branch.objects.none()

    if membership.branch_access_mode == Membership.ACCESS_MODE_ALL:
        return active

    return active.filter(
        membership_access__membership=membership,
        membership_access__is_active=True,
    )


def visible_branch_ids(user, company) -> list[int]:
    """Primary keys of visible_branches(), for building `branch__in` filters."""
    return list(visible_branches(user, company).values_list('pk', flat=True))


def has_branch_access(user, branch) -> bool:
    """Whether the caller may operate in `branch`, tenant included."""
    if branch is None:
        return False
    return visible_branches(user, branch.company).filter(pk=branch.pk).exists()


def assert_branch_access(user, branch) -> None:
    """Raise BranchAccessError unless the caller may operate in `branch`."""
    if not has_branch_access(user, branch):
        raise BranchAccessError('Sucursal no encontrada o sin acceso.')


def default_branch_for_user(user, company):
    """
    Which branch the internal control should open on, or None.

    Preference order: the membership's own default (`Membership.branch`, kept
    from Phase 1 for exactly this), then the company's fulfillment branch, then
    the oldest branch they can reach. Every candidate is filtered through
    visible_branches(), so a stale default that was later revoked degrades to
    the next option instead of granting access it no longer carries.
    """
    visible = visible_branches(user, company)
    if not visible.exists():
        return None

    membership = get_membership(user, company)
    if membership is not None and membership.branch_id:
        preferred = visible.filter(pk=membership.branch_id).first()
        if preferred is not None:
            return preferred

    if company is not None and company.default_inventory_branch_id:
        preferred = visible.filter(pk=company.default_inventory_branch_id).first()
        if preferred is not None:
            return preferred

    return visible.first()


# Sentinel for "the caller asked for every branch they can see", which is a
# different request from "the caller did not say". A report may honour it; a
# stock movement may not, because units are added to a place, not to a set.
BRANCH_SCOPE_ALL = 'all'


def resolve_branch_for_user(user, company, requested_branch_id=None, *, allow_all=False):
    """
    Resolve the branch a request acts on. Returns a Branch, or None for aggregate.

    `requested_branch_id` is UNTRUSTED input. It can only ever SELECT among the
    branches the caller already reaches; it can never widen them. A value naming
    another tenant's branch, an inactive branch or one they were not granted
    raises BranchAccessError — the same error as a branch id that does not
    exist, so ids cannot be probed.

    `allow_all` lets a READ endpoint accept the literal string "all" and answer
    with the aggregate of the caller's visible branches (returned as None).
    Write endpoints leave it False: there is no such place as "all branches" to
    put units into.

    Raises NoBranchError when the caller can reach no branch at all — which is a
    real, expressible state (SELECTED mode with no grants), not a bug.
    """
    visible = visible_branches(user, company)

    raw = '' if requested_branch_id is None else str(requested_branch_id).strip()

    if raw and raw.lower() == BRANCH_SCOPE_ALL:
        if not allow_all:
            raise BranchAccessError('Esta operación requiere una sucursal concreta.')
        if not visible.exists():
            raise NoBranchError('No tienes acceso a ninguna sucursal de esta empresa.')
        return None

    if raw:
        try:
            branch_id = int(raw)
        except (TypeError, ValueError):
            raise BranchAccessError('Sucursal no encontrada o sin acceso.')
        branch = visible.filter(pk=branch_id).first()
        if branch is None:
            raise BranchAccessError('Sucursal no encontrada o sin acceso.')
        return branch

    branch = default_branch_for_user(user, company)
    if branch is None:
        raise NoBranchError('No tienes acceso a ninguna sucursal de esta empresa.')
    return branch


# ---------------------------------------------------------------------------
# Fulfillment branch — where the online store sells from
# ---------------------------------------------------------------------------

def company_fulfillment_branch(company):
    """
    The branch a company's e-commerce sells from, or None.

    Order, and there is no third option:

      1. `Company.default_inventory_branch`, if set and still active. An
         explicit choice by the tenant.
      2. The company's ONLY active branch. Unambiguous by construction — not
         "the first of several", but "the only one there is". Without this rule
         every single-branch installation would have to configure a field before
         it could sell, which is a migration that breaks working shops.

    With two or more active branches and no explicit default: None. The store
    then refuses to check out and says so. Picking one silently would ship from
    a shop that does not know it sold anything.
    """
    if company is None or not company.is_active:
        return None

    explicit = company.default_inventory_branch
    if explicit is not None and explicit.is_active and explicit.company_id == company.pk:
        return explicit

    active = company_branches(company)
    if active.count() == 1:
        return active.first()

    return None


def storefront_fulfillment_branch(request):
    """The fulfillment branch of the storefront this public request is on."""
    return company_fulfillment_branch(resolve_storefront_company(request))
