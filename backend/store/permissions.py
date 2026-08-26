from rest_framework.permissions import BasePermission

from .models import UserProfile
from .tenancy import (
    CAP_MANAGE_COMPANY,
    CAP_MANAGE_INVENTORY,
    CAP_MANAGE_MEMBERSHIPS,
    CAP_MANAGE_SALES,
    CAP_MANAGE_TECHNICAL_SERVICE,
)

_ADMIN_ROLES = frozenset([UserProfile.ROLE_ADMIN, UserProfile.ROLE_SUPERADMIN])
_MANAGE_ORDERS_ROLES = frozenset([
    UserProfile.ROLE_SALES,
    UserProfile.ROLE_ADMIN,
    UserProfile.ROLE_SUPERADMIN,
])
_VIEW_ADMIN_PRODUCTS_ROLES = frozenset([
    UserProfile.ROLE_INVENTORY,
    UserProfile.ROLE_SALES,
    UserProfile.ROLE_ADMIN,
    UserProfile.ROLE_SUPERADMIN,
])
_MANAGE_PRODUCTS_ROLES = frozenset([UserProfile.ROLE_ADMIN, UserProfile.ROLE_SUPERADMIN])
_MANAGE_INVENTORY_ROLES = frozenset([
    UserProfile.ROLE_INVENTORY,
    UserProfile.ROLE_ADMIN,
    UserProfile.ROLE_SUPERADMIN,
])
_VIEW_ADMIN_ORDERS_ROLES = frozenset([
    UserProfile.ROLE_INVENTORY,
    UserProfile.ROLE_SALES,
    UserProfile.ROLE_ADMIN,
    UserProfile.ROLE_SUPERADMIN,
])
# Phase 6.0 — inventory / sales reporting
_VIEW_INVENTORY_REPORTS_ROLES = frozenset([
    UserProfile.ROLE_INVENTORY,
    UserProfile.ROLE_ADMIN,
    UserProfile.ROLE_SUPERADMIN,
])
_MANAGE_STOCK_MOVEMENTS_ROLES = frozenset([
    UserProfile.ROLE_INVENTORY,
    UserProfile.ROLE_ADMIN,
    UserProfile.ROLE_SUPERADMIN,
])
_VIEW_SALES_REPORTS_ROLES = frozenset([
    UserProfile.ROLE_SALES,
    UserProfile.ROLE_INVENTORY,
    UserProfile.ROLE_ADMIN,
    UserProfile.ROLE_SUPERADMIN,
])
_MANAGE_SALES_NOTES_ROLES = frozenset([
    UserProfile.ROLE_SALES,
    UserProfile.ROLE_ADMIN,
    UserProfile.ROLE_SUPERADMIN,
])
_MANAGE_ORDER_FULFILLMENT_ROLES = frozenset([
    UserProfile.ROLE_INVENTORY,
    UserProfile.ROLE_SALES,
    UserProfile.ROLE_ADMIN,
    UserProfile.ROLE_SUPERADMIN,
])


def get_user_role(user):
    """Return the effective business role string for a user, or None if unauthenticated."""
    if not user or not user.is_authenticated:
        return None
    if user.is_superuser:
        return UserProfile.ROLE_SUPERADMIN
    try:
        return user.profile.role
    except UserProfile.DoesNotExist:
        return UserProfile.ROLE_CUSTOMER


class IsAdminRole(BasePermission):
    message = 'Se requiere rol de administrador o superior.'

    def has_permission(self, request, view):
        return get_user_role(request.user) in _ADMIN_ROLES


class IsSuperAdminRole(BasePermission):
    message = 'Se requiere rol de superadministrador.'

    def has_permission(self, request, view):
        return get_user_role(request.user) == UserProfile.ROLE_SUPERADMIN


class CanManageOrders(BasePermission):
    message = 'Se requiere rol de vendedor, administrador o superior.'

    def has_permission(self, request, view):
        return get_user_role(request.user) in _MANAGE_ORDERS_ROLES


class CanViewAdminProducts(BasePermission):
    message = 'Se requiere rol de inventario, ventas, administrador o superior.'

    def has_permission(self, request, view):
        return get_user_role(request.user) in _VIEW_ADMIN_PRODUCTS_ROLES


class CanManageProducts(BasePermission):
    message = 'Se requiere rol de administrador o superior para gestionar productos.'

    def has_permission(self, request, view):
        return get_user_role(request.user) in _MANAGE_PRODUCTS_ROLES


class CanManageInventory(BasePermission):
    message = 'Se requiere rol de inventario, administrador o superior.'

    def has_permission(self, request, view):
        return get_user_role(request.user) in _MANAGE_INVENTORY_ROLES


class CanViewAdminOrders(BasePermission):
    message = 'Se requiere rol de inventario, ventas, administrador o superior.'

    def has_permission(self, request, view):
        return get_user_role(request.user) in _VIEW_ADMIN_ORDERS_ROLES


class CanManageOrderFulfillment(BasePermission):
    message = 'Se requiere rol de inventario, ventas, administrador o superior para gestionar despacho.'

    def has_permission(self, request, view):
        return get_user_role(request.user) in _MANAGE_ORDER_FULFILLMENT_ROLES


# ---------------------------------------------------------------------------
# Phase 6.0 — inventory, reports and internal sales notes
# ---------------------------------------------------------------------------

class CanViewInventoryReports(BasePermission):
    """Read inventory dashboards, Kardex and stock reports."""
    message = 'Se requiere rol de inventario, administrador o superior.'

    def has_permission(self, request, view):
        return get_user_role(request.user) in _VIEW_INVENTORY_REPORTS_ROLES


class CanManageStockMovements(BasePermission):
    """Create manual stock entries and exits. Sales/technician are excluded."""
    message = 'Se requiere rol de inventario, administrador o superior para mover stock.'

    def has_permission(self, request, view):
        return get_user_role(request.user) in _MANAGE_STOCK_MOVEMENTS_ROLES


class CanViewSalesReports(BasePermission):
    """Read best-selling / revenue reports."""
    message = 'Se requiere rol de ventas, inventario, administrador o superior.'

    def has_permission(self, request, view):
        return get_user_role(request.user) in _VIEW_SALES_REPORTS_ROLES


class CanManageSalesNotes(BasePermission):
    """Issue, view and download INTERNAL sales notes. Inventory is excluded."""
    message = 'Se requiere rol de ventas, administrador o superior.'

    def has_permission(self, request, view):
        return get_user_role(request.user) in _MANAGE_SALES_NOTES_ROLES


# ---------------------------------------------------------------------------
# SaaS Phase 1 — multi-tenant administration
# ---------------------------------------------------------------------------
#
# These are ADDITIVE. None of the permission classes above changed, and
# get_user_role() is untouched, so existing RBAC behaves exactly as before.

class IsPlatformAdmin(BasePermission):
    """
    SaaS PLATFORM administrator — operates the platform itself, across tenants.

    During the transition this is `User.is_superuser`, which get_user_role()
    already maps to `superadmin`, so no existing behaviour shifts.
    """
    message = 'Se requiere administrador de plataforma.'

    def has_permission(self, request, view):
        from .tenancy import is_platform_admin
        return is_platform_admin(request.user)


class HasCompanyMembership(BasePermission):
    """
    Caller belongs to at least one active company (or is a platform admin).

    A user with no membership — or whose only membership is inactive, or whose
    company is deactivated — is rejected. Business access is never implied by
    merely being authenticated, and NEVER by UserProfile.role: the legacy global
    role grants nothing in the SaaS surface.
    """
    message = 'El usuario no pertenece a ninguna empresa activa.'

    def has_permission(self, request, view):
        from .tenancy import active_memberships, is_platform_admin
        if is_platform_admin(request.user):
            return True
        return active_memberships(request.user).exists()


class _CompanyCapabilityPermission(BasePermission):
    """
    Coarse view-level gate: the caller holds `capability` in AT LEAST ONE company.

    This exists so a request that could not succeed in any company is rejected
    before its payload is parsed. It is NEVER a substitute for the per-company
    check the view performs against the company named in the request — the
    company only becomes known once the body/URL is read.

    Role sets live in tenancy.COMPANY_CAPABILITIES; nothing is redeclared here.
    """

    capability: str = ''

    def has_permission(self, request, view):
        from .tenancy import holds_any_capability
        return holds_any_capability(request.user, self.capability)


class CanManageCompanyMemberships(_CompanyCapabilityPermission):
    """Company administrators (in some company) and platform administrators."""
    capability = CAP_MANAGE_MEMBERSHIPS
    message = 'Se requiere rol de administrador de la empresa.'


class CanManageCompanySettings(_CompanyCapabilityPermission):
    """Company administrators. Reserved for company configuration endpoints."""
    capability = CAP_MANAGE_COMPANY
    message = 'Se requiere rol de administrador de la empresa.'


class CanManageCompanyInventory(_CompanyCapabilityPermission):
    """
    Company-scoped inventory authority.

    NOT wired to /api/admin/inventory/ yet: StockMovement has no company column,
    so switching those endpoints now would grant tenant-shaped permissions over
    globally-shared data — a false sense of isolation. See docs/saas-multiempresa.md.
    """
    capability = CAP_MANAGE_INVENTORY
    message = 'Se requiere rol de inventario de la empresa.'


class CanManageCompanySales(_CompanyCapabilityPermission):
    """Company-scoped sales authority. Not wired to legacy sales endpoints yet."""
    capability = CAP_MANAGE_SALES
    message = 'Se requiere rol de ventas de la empresa.'


class CanManageCompanyTechnicalService(_CompanyCapabilityPermission):
    """Company-scoped technical-service authority. No endpoint uses it yet."""
    capability = CAP_MANAGE_TECHNICAL_SERVICE
    message = 'Se requiere rol de servicio técnico de la empresa.'
