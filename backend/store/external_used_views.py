"""
INV-USED-EXT-01 — the panel's surface for external used devices.

    /api/admin/external-used-cards/                 GET products.view · POST products.manage
    /api/admin/external-used-cards/<pk>/withdraw/   POST products.manage
    /api/admin/supply-incidents/                    GET sales.orders.view (the caller's visible orders)
    /api/admin/supply-incidents/<pk>/reject/        POST a superior (sales.orders.manage + company.manage)

The company is the caller's; another company's row answers 404 like one that
does not exist. The agreed cost travels only with `costs.view`. No answer
carries a whole IMEI or serial: hints only.
"""
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import external_used
from .admin_views import (
    CAP_ORDERS_VIEW, CAP_PRODUCTS_MANAGE, CAP_PRODUCTS_VIEW, _LEGACY_MANAGE_CATALOG_ROLES,
    _LEGACY_VIEW_CATALOG_ROLES, _LEGACY_VIEW_ORDERS_ROLES, _company_context, _paginate,
)
from .cost_services import user_may_view_costs
from .models import ExternalUsedCard, SupplyReceptionIncident

_NOT_FOUND = {'detail': 'No encontrado.'}


def _error(exc: external_used.CardError) -> Response:
    return Response(exc.body(), status=exc.http_status)


def _cards(company):
    return (ExternalUsedCard.objects.filter(company=company)
            .select_related('offer__supplier', 'offer__product', 'reported_grade', 'order_item'))


class AdminExternalUsedCardListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company, error = _company_context(request, CAP_PRODUCTS_VIEW, _LEGACY_VIEW_CATALOG_ROLES)
        if error:
            return error
        qs = _cards(company)
        wanted = (request.query_params.get('status') or '').strip()
        if wanted:
            qs = qs.filter(status=wanted)
        page, meta = _paginate(qs.order_by('-created_at', '-pk'), request)
        with_cost = user_may_view_costs(request.user, company)
        return Response({**meta, 'results': [external_used.card_payload(card, with_cost=with_cost) for card in page]})

    def post(self, request):
        company, error = _company_context(request, CAP_PRODUCTS_MANAGE, _LEGACY_MANAGE_CATALOG_ROLES)
        if error:
            return error
        body = request.data if isinstance(request.data, dict) else {}
        fields = {key: body.get(key) for key in (
            'supplier_id', 'product_id', 'reported_serial', 'reported_imei', 'reported_imei2', 'reported_battery',
            'reported_grade', 'notes', *external_used.OFFER_TERMS)}
        with_cost = user_may_view_costs(request.user, company)
        if not with_cost:
            fields.pop('agreed_cost')          # DC1: without costs.view a cost is neither read nor written
        try:
            card = external_used.create_card(company=company, actor=request.user, may_view_costs=with_cost,
                                             request=request, **fields)
        except external_used.CardError as exc:
            return _error(exc)
        card = _cards(company).get(pk=card.pk)
        return Response(external_used.card_payload(card, with_cost=with_cost), status=status.HTTP_201_CREATED)


class AdminExternalUsedCardWithdrawView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        company, error = _company_context(request, CAP_PRODUCTS_MANAGE, _LEGACY_MANAGE_CATALOG_ROLES)
        if error:
            return error
        card = _cards(company).filter(pk=pk).first()
        if card is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        try:
            card = external_used.withdraw_card(card, actor=request.user, reason=request.data.get('reason'),
                                               request=request)
        except external_used.CardError as exc:
            return _error(exc)
        card = _cards(company).get(pk=card.pk)
        return Response(external_used.card_payload(card, with_cost=user_may_view_costs(request.user, company)))


def _incidents(request, company):
    from .tenancy import visible_orders

    return (SupplyReceptionIncident.objects.filter(company=company, order__in=visible_orders(request.user, company))
            .select_related('order_item__product', 'opened_by'))


class AdminSupplyIncidentListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company, error = _company_context(request, CAP_ORDERS_VIEW, _LEGACY_VIEW_ORDERS_ROLES)
        if error:
            return error
        qs = _incidents(request, company)
        wanted = (request.query_params.get('status') or '').strip()
        if wanted:
            qs = qs.filter(status=wanted)
        page, meta = _paginate(qs.order_by('-opened_at', '-pk'), request)
        return Response({**meta, 'results': [external_used.incident_payload(row) for row in page]})


class AdminSupplyIncidentRejectView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        from .order_cancellation import may_cancel_as_superior

        company, error = _company_context(request, CAP_ORDERS_VIEW, _LEGACY_VIEW_ORDERS_ROLES)
        if error:
            return error
        incident = _incidents(request, company).filter(pk=pk).first()
        if incident is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        if not may_cancel_as_superior(request.user, company):
            return Response({'detail': 'Resolver una incidencia de recepción es de un responsable.'},
                            status=status.HTTP_403_FORBIDDEN)
        try:
            incident = external_used.reject_incident(incident, actor=request.user,
                                                     reason=request.data.get('reason'), request=request)
        except external_used.CardError as exc:
            return _error(exc)
        return Response(external_used.incident_payload(_incidents(request, company).get(pk=incident.pk)))
