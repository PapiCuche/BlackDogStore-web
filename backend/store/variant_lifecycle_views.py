"""
VARIANT-SAFE-01 — the HTTP surface of `variant_lifecycle`.

GET    /api/admin/products/<pk>/deletion-check/   products.view — what deleting or archiving would meet
DELETE /api/admin/products/<pk>/                  products.manage + products.delete — {reason, confirm_name}
POST   /api/admin/products/<pk>/archive/          products.manage — {reason}
POST   /api/admin/products/<pk>/unarchive/        products.manage — {reason}

The company is the caller's (`_company_context`); a product of another company answers 404, like one that does not
exist. On the legacy bridge (a pre-SaaS operator of the pilot) the legacy admin roles decide, as for every catalogue
write: they hold the administration preset, `products.delete` included.
"""
from __future__ import annotations

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import variant_lifecycle
from .admin_views import (
    CAP_PRODUCTS_MANAGE, CAP_PRODUCTS_VIEW, _LEGACY_MANAGE_CATALOG_ROLES, _LEGACY_VIEW_CATALOG_ROLES,
    _company_context,
)
from .models import Product
from .tenancy import has_capability, uses_legacy_bridge
from .throttles import AdminProductWriteThrottle, AdminProductsThrottle

CAP_PRODUCTS_DELETE = 'products.delete'

_NO_DELETE = {
    'detail': 'Necesitas el permiso «Eliminar variantes» para eliminar una variante. Puedes archivarla.',
    'code': 'delete_forbidden',
}


def _body(request) -> dict:
    return request.data if isinstance(request.data, dict) else {}


def _error(exc: variant_lifecycle.LifecycleError) -> Response:
    return Response(exc.body(), status=exc.status)


def may_delete(user, company) -> bool:
    if uses_legacy_bridge(user, company):
        return True     # `_company_context` already required a legacy admin role
    return has_capability(user, company, CAP_PRODUCTS_DELETE)


def delete_product(request, pk) -> Response:
    """The DELETE of `AdminProductDetailView`."""
    company, error = _company_context(request, CAP_PRODUCTS_MANAGE, _LEGACY_MANAGE_CATALOG_ROLES)
    if error:
        return error
    if not may_delete(request.user, company):
        return Response(_NO_DELETE, status=status.HTTP_403_FORBIDDEN)
    data = _body(request)
    try:
        deleted = variant_lifecycle.delete_variant(
            company=company, pk=pk, reason=data.get('reason'), confirm_name=data.get('confirm_name'),
            actor=request.user, request=request)
    except variant_lifecycle.LifecycleError as exc:
        return _error(exc)
    return Response({'deleted': True, 'product_id': deleted['product_id'], 'name': deleted['name']})


class AdminProductDeletionCheckView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminProductsThrottle]

    def get(self, request, pk):
        company, error = _company_context(request, CAP_PRODUCTS_VIEW, _LEGACY_VIEW_CATALOG_ROLES)
        if error:
            return error
        product = Product.objects.filter(company=company, pk=pk).first()
        if product is None:
            return Response({'detail': 'No encontrado.'}, status=status.HTTP_404_NOT_FOUND)
        answer = variant_lifecycle.check(product)
        # What the caller may do here, so the dialog offers nothing the server would refuse.
        manage = has_capability(request.user, company, CAP_PRODUCTS_MANAGE) or (
            uses_legacy_bridge(request.user, company) and _legacy_admin(request.user))
        answer['can_archive'] = bool(manage)
        answer['can_delete'] = bool(manage and (
            _legacy_admin(request.user) if uses_legacy_bridge(request.user, company)
            else has_capability(request.user, company, CAP_PRODUCTS_DELETE)))
        return Response(answer)


def _legacy_admin(user) -> bool:
    from .permissions import get_user_role

    return get_user_role(user) in _LEGACY_MANAGE_CATALOG_ROLES


class _ArchiveBase(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminProductWriteThrottle]
    http_method_names = ['post', 'options']
    act = None

    def post(self, request, pk):
        company, error = _company_context(request, CAP_PRODUCTS_MANAGE, _LEGACY_MANAGE_CATALOG_ROLES)
        if error:
            return error
        try:
            product = type(self).act(company=company, pk=pk, reason=_body(request).get('reason'),
                                     actor=request.user, request=request)
        except variant_lifecycle.LifecycleError as exc:
            return _error(exc)
        return Response(variant_lifecycle.check(product))


class AdminProductArchiveView(_ArchiveBase):
    act = staticmethod(variant_lifecycle.archive_variant)


class AdminProductUnarchiveView(_ArchiveBase):
    act = staticmethod(variant_lifecycle.unarchive_variant)
