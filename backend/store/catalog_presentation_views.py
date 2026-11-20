"""
CATALOG-REFERENCE-01 · PR E — how one company presents a model, from the panel.

    GET  /api/admin/catalog/models/<id>/option-order/   the model's options per attribute, in its order  products.view
    PUT  /api/admin/catalog/models/<id>/option-order/   {attribute_id, option_ids}: that attribute's     products.manage
                                                         palette options, each once, in the model's order
    GET  /api/admin/catalog/models/<id>/commercial/      «Configuración comercial»: what the existing      products.view
                                                         editors keep (costs only with `costs.view`,
                                                         promotions only with `sales.promotions.view`)

«Destacado» is a field of the model's own form (`PATCH /api/admin/catalog/models/<id>/`, `is_featured`).

THE COMPANY COMES FROM THE RESOLVED CONTEXT, never from the body. A model of another company answers like one that
does not exist, and so does an attribute of another company.
"""
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import catalog_presentation, device_catalog, model_palette
from .admin_views import (
    CAP_PRODUCTS_MANAGE, CAP_PRODUCTS_VIEW, _LEGACY_MANAGE_CATALOG_ROLES, _LEGACY_VIEW_CATALOG_ROLES, _company_context,
)
from .models import ProductModel
from .tenancy import has_capability
from .throttles import AdminProductsThrottle, AdminProductWriteThrottle

_NOT_FOUND = {'detail': 'No encontrado.'}

_STATUS = {'not_found': status.HTTP_404_NOT_FOUND, 'no_palette': status.HTTP_409_CONFLICT}


class _ModelView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [AdminProductsThrottle()]
        return [AdminProductWriteThrottle()]

    def model(self, request, pk, *, write=False):
        company, error = (
            _company_context(request, CAP_PRODUCTS_MANAGE, _LEGACY_MANAGE_CATALOG_ROLES) if write else
            _company_context(request, CAP_PRODUCTS_VIEW, _LEGACY_VIEW_CATALOG_ROLES)
        )
        if error:
            return None, error
        model = ProductModel.objects.filter(company=company, pk=pk).first()
        if model is None:
            return None, Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        return model, None


class AdminModelOptionOrderView(_ModelView):
    def get(self, request, pk):
        model, error = self.model(request, pk)
        if error:
            return error
        return Response(model_palette.option_order(model))

    def put(self, request, pk):
        model, error = self.model(request, pk, write=True)
        if error:
            return error
        data = request.data if isinstance(request.data, dict) else {}
        try:
            body = model_palette.set_option_order(model, attribute_id=data.get('attribute_id'),
                                                  option_ids=data.get('option_ids'), actor=request.user,
                                                  request=request)
        except device_catalog.CatalogError as exc:
            return Response(exc.body(), status=_STATUS.get(exc.code, status.HTTP_400_BAD_REQUEST))
        return Response(body)


class AdminModelCommercialView(_ModelView):
    def get(self, request, pk):
        from .inventory_views import may_view_costs

        model, error = self.model(request, pk)
        if error:
            return error
        return Response(catalog_presentation.commercial_summary(
            model,
            may_view_costs=may_view_costs(request),
            may_view_promotions=has_capability(request.user, model.company, 'sales.promotions.view'),
        ))
