"""
The INTERNAL surface — `/api/v1/internal/<company_slug>/…`.

FOUR AUDIENCES, FOUR SURFACES (DEC-API-001, extended in M6):

  `/api/v1/storefront/<slug>/`  PUBLIC    anonymous
  `/api/v1/customer/<slug>/`    CUSTOMER  a client's OWN records
  `/api/v1/internal/<slug>/`    INTERNAL  the COMPANY's records, this file
  `/api/admin/`                 WEB ADMIN the browser panel, untouched

There is no endpoint anywhere that widens its result set for staff. A customer
asking for orders gets theirs; an employee asking for orders gets the company's;
they are different URLs, different permissions and different serializers. An
endpoint whose answer depends on who is asking is one refactor from returning
the wrong set, and the failure is silent.

TWO GATES, IN ORDER, AND THEY ANSWER DIFFERENTLY

  1. DO YOU BELONG HERE?  No active membership → 404, indistinguishable from an
     unknown company. Anything else lets a valid login map the platform's
     tenants one slug at a time.

  2. MAY YOU DO THIS?     Membership confirmed but capability missing → 403.
     Once the server has admitted the company exists and this person works
     there, hiding the reason is no longer protecting anything and only makes
     the app harder to explain.
"""
from datetime import datetime

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import permissions, status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from . import order_fulfillment_services as fulfillment
from .models import Order
from .tenancy import (
    Membership,
    has_capability,
    is_platform_admin,
    resolve_capabilities,
    resolve_public_storefront_company,
)
from .throttles import AdminOrdersThrottle, AdminOrderStatusChangeThrottle
from .v1_authentication import V1BearerAuthentication
from .v1_internal_serializers import (
    V1InternalFulfillmentSerializer,
    V1InternalOrderDetailSerializer,
    V1InternalOrderListSerializer,
)

CAP_ORDERS_VIEW = 'sales.orders.view'
CAP_ORDERS_MANAGE = 'sales.orders.manage'

_DEFAULT_PAGE_SIZE = 20
_MAX_PAGE_SIZE = 100


def _is_internal_member(user, company) -> bool:
    """
    Whether `user` may enter `company`'s internal area at all.

    An ACTIVE membership of an ACTIVE company. A customer relation is not
    enough and never will be: buying from a business is not working for it.

    A platform administrator also passes, but only for the company NAMED IN THE
    PATH. They are never handed a tenant implicitly, and this grants them no
    capability of its own — `resolve_capabilities` decides that separately.
    """
    if user is None or not user.is_authenticated:
        return False
    if is_platform_admin(user):
        return True
    return Membership.objects.filter(
        user=user, company=company, is_active=True, company__is_active=True,
    ).exists()


class V1InternalSurfaceMixin:
    """Shared gate for every internal-audience view."""

    authentication_classes = [V1BearerAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get_internal_company(self):
        """Gate 1 — belonging. Unknown, inactive and 'not staff here' all 404."""
        company = resolve_public_storefront_company(self.kwargs.get('company_slug'))
        if company is None or not _is_internal_member(self.request.user, company):
            raise NotFound('No encontrado.')
        return company

    def require_capability(self, company, capability: str):
        """
        Gate 2 — permission. 403, and re-resolved on EVERY request.

        Never trusted from the client. A capability list travelled to the app so
        it could decide which tab to draw; what a request may actually do is
        decided here, now, from the database — which is also why revoking a
        permission takes effect on the next call rather than at the next login.
        """
        if not has_capability(self.request.user, company, capability):
            raise PermissionDenied('No tienes permiso para esta acción.')


class V1InternalContextView(V1InternalSurfaceMixin, APIView):
    """
    GET — who this person is INSIDE this company, right now.

    Called when the internal area is opened, not read from the session. The
    access context minted at login is a snapshot: roles change while a session
    stays alive, and someone whose permission was revoked an hour ago must not
    keep seeing a module because their token is still valid.

    Deliberately minimal. No customers, no orders, no staff list, no
    configuration — this endpoint answers "what may I see?", and answering more
    would make it a data source nobody audited.
    """

    def get(self, request, company_slug=None):
        company = self.get_internal_company()
        return Response({
            'company': {'slug': company.slug, 'name': company.name},
            'member': Membership.objects.filter(
                user=request.user, company=company, is_active=True,
            ).exists(),
            'capabilities': sorted(resolve_capabilities(request.user, company)),
            'platform': {'is_master': is_platform_admin(request.user)},
        })


class V1InternalOrderListView(V1InternalSurfaceMixin, APIView):
    """
    GET — the COMPANY's orders. Requires `sales.orders.view`.

    Not the caller's own orders: that is the customer surface, and an employee
    who also shops here sees their purchases there and the company's here.
    """

    throttle_classes = [AdminOrdersThrottle]

    def get(self, request, company_slug=None):
        company = self.get_internal_company()
        self.require_capability(company, CAP_ORDERS_VIEW)

        # BORN SCOPED. Every filter below narrows within the tenant; none can
        # widen it, because the company constraint is applied before any of them.
        orders = (
            Order.objects
            .filter(company=company)
            .prefetch_related('items')
            .order_by('-created_at')
        )

        params = request.query_params

        search = params.get('search', '').strip()
        if search:
            # An id search is exact and numeric; `-1` can never match a pk, so a
            # non-numeric search simply contributes nothing to the OR.
            id_match = int(search) if search.isdigit() else -1
            orders = orders.filter(
                Q(customer_name__icontains=search)
                | Q(customer_email__icontains=search)
                | Q(id=id_match)
            ).distinct()

        payment_status = params.get('status', '').strip()
        if payment_status:
            orders = orders.filter(status=payment_status)

        fulfillment_status = params.get('fulfillment_status', '').strip()
        if fulfillment_status:
            orders = orders.filter(fulfillment_status=fulfillment_status)

        for param, lookup in (('date_from', 'gte'), ('date_to', 'lte')):
            raw = params.get(param, '').strip()
            if not raw:
                continue
            try:
                parsed = datetime.strptime(raw, '%Y-%m-%d').date()
            except ValueError:
                # An unparseable date is ignored rather than fatal, matching the
                # web admin. A typo should not empty the screen.
                continue
            orders = orders.filter(**{f'created_at__date__{lookup}': parsed})

        page, page_size = self._pagination(params)
        total = orders.count()
        rows = orders[(page - 1) * page_size: page * page_size]

        return Response({
            'count': total,
            'page': page,
            'page_size': page_size,
            'results': V1InternalOrderListSerializer(rows, many=True).data,
        })

    @staticmethod
    def _pagination(params) -> tuple[int, int]:
        try:
            page = max(1, int(params.get('page', 1)))
        except (TypeError, ValueError):
            page = 1
        try:
            size = min(_MAX_PAGE_SIZE, max(1, int(params.get('page_size', _DEFAULT_PAGE_SIZE))))
        except (TypeError, ValueError):
            size = _DEFAULT_PAGE_SIZE
        return page, size


class V1InternalOrderDetailView(V1InternalSurfaceMixin, APIView):
    """GET — one of the company's orders. Requires `sales.orders.view`."""

    throttle_classes = [AdminOrdersThrottle]

    def get(self, request, company_slug=None, pk=None):
        company = self.get_internal_company()
        self.require_capability(company, CAP_ORDERS_VIEW)

        # Scoped in the queryset, so another tenant's id is simply not found —
        # rather than found and then refused, which leaks that it exists.
        order = get_object_or_404(
            Order.objects.filter(company=company).prefetch_related('items__product'), pk=pk,
        )
        return Response(self._payload(request, order))

    def _payload(self, request, order):
        return {
            **V1InternalOrderDetailSerializer(order).data,
            # The allowed next states, FROM THE SERVER.
            #
            # Sent so the app does not carry a second copy of the rule. A client
            # that computes its own transitions drifts the first time the rule
            # changes, and the drift shows up as a button that fails — which
            # reads as a broken app rather than as a policy.
            #
            # Presentation input, not permission: the PATCH re-checks.
            'available_fulfillment_transitions':
                list(fulfillment.allowed_fulfillment_statuses(request.user)),
        }


class V1InternalOrderFulfillmentView(V1InternalOrderDetailView):
    """
    PATCH — move an order's fulfilment state. Requires `sales.orders.manage`.

    `manage` is checked, NOT `view`, and the catalogue declares them as separate
    capabilities. Nothing here assumes one implies the other: a company that
    grants manage without view has said something unusual but not incoherent,
    and inventing an implication would quietly widen a role someone configured
    deliberately.

    Payment state is untouched. Whether money arrived is the gateway's answer, and it
    reaches the order through the webhook — never through a staff member saying
    so.
    """

    throttle_classes = [AdminOrderStatusChangeThrottle]

    def get(self, request, company_slug=None, pk=None):
        # Only PATCH belongs on this route; the detail view serves GET.
        return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

    def patch(self, request, company_slug=None, pk=None):
        company = self.get_internal_company()
        self.require_capability(company, CAP_ORDERS_MANAGE)

        serializer = V1InternalFulfillmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        order = get_object_or_404(Order.objects.filter(company=company), pk=pk)

        try:
            fulfillment.change_fulfillment_status(
                order=order,
                new_status=serializer.validated_data['fulfillment_status'],
                actor=request.user,
                company=company,
                note=serializer.validated_data.get('note', ''),
                request=request,
            )
        except fulfillment.FulfillmentNotAllowed as exc:
            return Response({'detail': exc.detail}, status=status.HTTP_403_FORBIDDEN)

        order = Order.objects.prefetch_related('items__product').get(pk=order.pk)
        return Response(self._payload(request, order))
