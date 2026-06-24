from rest_framework.permissions import BasePermission

from .models import UserProfile

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
