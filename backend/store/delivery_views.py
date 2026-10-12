"""
DELIVERY-01, the base — the panel's side: a company's carriers and the delivery
record of an order. The buyer's address book is in `account_views` (web) and
`v1_customer_views` (app); the rules of all of it are in `delivery_services`.
"""
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import delivery_services as delivery
from .admin_views import (
    CAP_ORDERS_VIEW, _LEGACY_MANAGE_CATALOG_ROLES, _LEGACY_VIEW_ORDERS_ROLES, _company_context, _holds,
)
from .models import Carrier
from .tenancy import has_company_wide_scope
from .throttles import AdminOrdersThrottle, AdminOrderStatusChangeThrottle

CAP_SETTINGS_MANAGE = 'settings.manage'
_NOT_FOUND = 'Transportista no encontrado.'
_NEEDS_WHOLE_COMPANY = 'Los transportistas son de toda la empresa: tu acceso está limitado a algunas sucursales.'


def _may_manage_carriers(user, company) -> bool:
    """
    Who keeps a company's list of carriers: `settings.manage`, with the whole
    company in scope — the list is every branch's (WRITE-SCOPE-01). On the
    legacy bridge, the administrators.
    """
    return _holds(user, company, CAP_SETTINGS_MANAGE, _LEGACY_MANAGE_CATALOG_ROLES) and has_company_wide_scope(user, company)


class _CarrierWriteMixin:
    def writer(self, request):
        """`(company, None)` for whoever may change the list, or `(None, refusal)`."""
        company, error = _company_context(request, CAP_SETTINGS_MANAGE, _LEGACY_MANAGE_CATALOG_ROLES)
        if error:
            return None, error
        if not has_company_wide_scope(request.user, company):
            return None, Response({'detail': _NEEDS_WHOLE_COMPANY}, status=status.HTTP_403_FORBIDDEN)
        return company, None


class AdminCarrierListView(_CarrierWriteMixin, APIView):
    """
    GET  /api/admin/carriers/ — this company's carriers, the switched-off ones
         too. `sales.orders.view`: whoever looks at an order has to be able to
         name its carrier.
    POST — add one. `settings.manage`.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        return [AdminOrderStatusChangeThrottle()] if self.request.method == 'POST' else [AdminOrdersThrottle()]

    def get(self, request):
        company, error = _company_context(request, CAP_ORDERS_VIEW, _LEGACY_VIEW_ORDERS_ROLES)
        if error:
            return error
        return Response({
            'results': [delivery.carrier_payload(carrier) for carrier in Carrier.objects.filter(company=company)],
            'can_manage': _may_manage_carriers(request.user, company),
        })

    def post(self, request):
        company, error = self.writer(request)
        if error:
            return error
        try:
            carrier = delivery.create_carrier(company=company, actor=request.user, data=request.data, request=request)
        except delivery.DeliveryError as refused:
            return Response(refused.errors, status=status.HTTP_400_BAD_REQUEST)
        return Response(delivery.carrier_payload(carrier), status=status.HTTP_201_CREATED)


class AdminCarrierDetailView(_CarrierWriteMixin, APIView):
    """PATCH /api/admin/carriers/<pk>/ — rename, change or switch off. Never deleted."""
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminOrderStatusChangeThrottle]

    def patch(self, request, pk):
        company, error = self.writer(request)
        if error:
            return error
        carrier = Carrier.objects.filter(company=company, pk=pk).first()
        if carrier is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        try:
            carrier = delivery.update_carrier(
                carrier=carrier, company=company, actor=request.user, data=request.data, request=request,
            )
        except delivery.DeliveryError as refused:
            return Response(refused.errors, status=status.HTTP_400_BAD_REQUEST)
        return Response(delivery.carrier_payload(carrier))
