"""
CATALOG-REFERENCE-01 · PR B — the reference catalogue, seen from ONE company (`catalog_reference_import`).

    GET  /api/admin/catalog/reference/models/    the reference models and the company's model linked to each
    POST /api/admin/catalog/reference/preview/   {keys}: what importing them would do; writes nothing
    POST /api/admin/catalog/reference/apply/     {keys, approve}: imports them; `Idempotency-Key` supported

Reading is `products.view`; applying is `products.manage` (the capabilities of the pasted-list proposal: apply
creates no variant, so `inventory.adjust` is not asked). The company is the request's context
(`_company_context`); a company id in the body is never read.
"""
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from . import catalog_reference_import as importer
from . import device_catalog
from .admin_views import (
    CAP_PRODUCTS_MANAGE, CAP_PRODUCTS_VIEW, _LEGACY_MANAGE_CATALOG_ROLES, _LEGACY_VIEW_CATALOG_ROLES, _company_context,
)
from .catalog_proposal import BadProposal
from .throttles import AdminOrdersThrottle, AdminProductWriteThrottle


class AdminReferenceModelListView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminOrdersThrottle]

    def get(self, request):
        company, error = _company_context(request, CAP_PRODUCTS_VIEW, _LEGACY_VIEW_CATALOG_ROLES)
        if error:
            return error
        return Response({'results': importer.models_list(company)})


class AdminReferencePreviewView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminOrdersThrottle]

    def post(self, request):
        company, error = _company_context(request, CAP_PRODUCTS_VIEW, _LEGACY_VIEW_CATALOG_ROLES)
        if error:
            return error
        body = request.data if isinstance(request.data, dict) else {}
        try:
            return Response(importer.preview(company, body.get('keys')))
        except BadProposal as exc:
            return Response(exc.body(), status=exc.status)


class AdminReferenceApplyView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminProductWriteThrottle]

    def post(self, request):
        company, error = _company_context(request, CAP_PRODUCTS_MANAGE, _LEGACY_MANAGE_CATALOG_ROLES)
        if error:
            return error
        body = request.data if isinstance(request.data, dict) else {}
        try:
            result = importer.apply(
                company=company, keys=body.get('keys'), approve=body.get('approve'),
                idempotency_key=request.headers.get('Idempotency-Key') or body.get('idempotency_key') or '',
                actor=request.user, request=request)
        except BadProposal as exc:
            return Response(exc.body(), status=exc.status)
        except device_catalog.CatalogError as exc:
            return Response(exc.body(), status=404 if exc.code == 'not_found' else 400)
        return Response(result)
