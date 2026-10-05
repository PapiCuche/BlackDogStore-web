"""
TRACKING — el seguimiento de una reparación, por enlace y desde la cuenta.

    EL ENLACE ES LA CREDENCIAL, Y SÓLO DE UNA ORDEN.

Las rutas públicas no tienen sesión: quien presenta un enlace vivo ve esa orden
tal como la ve su cliente, y nada más. Todo lo que no resuelve —enlace
inventado, alterado, revocado, de una empresa desactivada— responde el mismo
404 con el mismo cuerpo, para que no se pueda distinguir "no existe" de "ya no
vale".

Las rutas de la cuenta sí tienen sesión (la cookie de la web) y devuelven las
órdenes del cliente que esa cuenta ES, con el enlace de cada una. El detalle se
lee siempre por el enlace: una sola vista del cliente, no dos que divergen.
"""

from __future__ import annotations

from rest_framework import permissions, status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from . import evidence_services, service_services
from . import tracking_services as tracking
from .evidence_views import _no_store, _serve
from .models import RepairQuote, RepairQuoteDecision
from .tenancy import resolve_public_storefront_company, resolve_storefront_company
from .throttles import (
    AccountRepairsThrottle, TrackingDecisionThrottle, TrackingReadThrottle,
)
from .v1_service_serializers import (
    V1CustomerQuoteDecisionSerializer, V1CustomerQuoteSerializer,
)

NOT_FOUND = 'No encontrado.'


class _TrackingLinkMixin:
    """Sin sesión y sin cookies: lo único que autoriza es el enlace de la ruta."""

    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    def order(self, token):
        link = tracking.resolve(token)
        if link is None:
            raise NotFound(NOT_FOUND)
        self.link = link
        return link.repair_order


class TrackingView(_TrackingLinkMixin, APIView):
    """GET — la orden, su avance, su cotización, sus pagos y sus fotos compartidas."""

    throttle_classes = [TrackingReadThrottle]

    def get(self, request, token=None):
        order = self.order(token)
        tracking.record_view(self.link)
        return _no_store(Response(tracking.public_view(order)))


class TrackingEvidenceContentView(_TrackingLinkMixin, APIView):
    """GET — los bytes de una foto compartida de ESTA orden."""

    throttle_classes = [TrackingReadThrottle]

    def get(self, request, token=None, evidence_id=None):
        order = self.order(token)
        evidence = evidence_services.customer_evidence_for_order(order).filter(
            pk=evidence_id,
        ).first()
        if evidence is None:
            raise NotFound(NOT_FOUND)
        return _serve(evidence)


class TrackingQuoteDecisionView(_TrackingLinkMixin, APIView):
    """
    POST — aprobar o rechazar la cotización desde el enlace.

    Queda registrado como lo que fue: respondió quien tenía el enlace, sin
    cuenta. El canal lo pone esta puerta (`tracking_link`), no el cuerpo.
    """

    http_method_names = ['post']
    throttle_classes = [TrackingDecisionThrottle]

    def post(self, request, token=None, quote_id=None):
        order = self.order(token)
        visible = service_services.customer_visible_quote(order)
        if visible is None or visible.pk != quote_id:
            raise NotFound(NOT_FOUND)

        serializer = V1CustomerQuoteDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            service_services.record_quote_decision(
                quote=visible, customer=order.customer, user=None,
                decision=serializer.validated_data['decision'],
                reason=serializer.validated_data.get('reason', ''),
                request=request,
                channel=RepairQuoteDecision.CHANNEL_TRACKING_LINK,
            )
        except service_services.QuoteDecisionConflict as exc:
            return _no_store(Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT))
        except service_services.ServiceError as exc:
            return _no_store(Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST))

        fresh = RepairQuote.objects.prefetch_related('items').select_related(
            'decision',
        ).get(pk=visible.pk)
        return _no_store(Response({'quote': V1CustomerQuoteSerializer(fresh).data}))


class AccountRepairsView(APIView):
    """
    GET — mis reparaciones en esta tienda.

    La empresa es la de la tienda que se está mirando (por host, o el
    `company_slug` que la nombra). Nombrarla no concede nada: las filas salen de
    `Customer.user`, y quien no es cliente de esa empresa recibe una lista vacía.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AccountRepairsThrottle]

    def get(self, request):
        slug = request.query_params.get('company_slug')
        company = (
            resolve_public_storefront_company(slug) if slug
            else resolve_storefront_company(request)
        )
        rows = tracking.account_rows(request.user, company) if company is not None else []
        return _no_store(Response({'count': len(rows), 'results': rows}))


class AccountRepairClaimView(APIView):
    """POST — sumar a mi cuenta la orden cuyo enlace tengo."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [TrackingDecisionThrottle]

    def post(self, request):
        try:
            customer = tracking.claim(
                request.data.get('token'), user=request.user, request=request,
            )
        except tracking.TrackingError as exc:
            if exc.conflict:
                return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
            raise NotFound(NOT_FOUND)
        return _no_store(Response({'linked': True, 'company_slug': customer.company.slug}))
