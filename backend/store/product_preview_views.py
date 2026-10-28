"""
ECOM-READY-D1 — GET /api/admin/products/<id>/storefront-preview/  (products.view)

See `product_preview`. The company is the caller's; a product of another company
answers 404 exactly like one that does not exist. Read only.
"""
from django.shortcuts import get_object_or_404
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from . import product_preview
from .admin_views import CAP_PRODUCTS_VIEW, _LEGACY_VIEW_CATALOG_ROLES, _company_context
from .models import Product
from .throttles import AdminProductsThrottle


class AdminProductStorefrontPreviewView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminProductsThrottle]
    http_method_names = ['get', 'head', 'options']

    def get(self, request, pk):
        company, error = _company_context(request, CAP_PRODUCTS_VIEW, _LEGACY_VIEW_CATALOG_ROLES)
        if error:
            return error
        product = get_object_or_404(
            Product.objects.select_related('category', 'company', 'product_model')
            .prefetch_related('reviews', 'images').filter(company=company),
            pk=pk,
        )
        return Response(product_preview.build(product))
