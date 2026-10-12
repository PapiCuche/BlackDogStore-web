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
from rest_framework import permissions, status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from . import order_fulfillment_services as fulfillment
from . import order_risk
from .models import Order
from .tenancy import (
    Membership,
    has_capability,
    is_platform_admin,
    resolve_capabilities,
    resolve_public_storefront_company,
    visible_orders,
)
from .throttles import AdminOrdersThrottle, AdminOrderStatusChangeThrottle
from .v1_internal_authentication import V1InternalAuthentication
from .v1_internal_serializers import (
    V1InternalFulfillmentSerializer,
    V1InternalOrderDetailSerializer,
    V1InternalOrderListSerializer,
    V1InternalRiskReviewSerializer,
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


def _visible_order(request, company, pk, *prefetch):
    """
    Order `pk`, if and only if this caller may see it (H4.1.2, BRANCH-SCOPE-01).

    Filtered BEFORE the lookup. An order of another tenant, of a branch the
    caller does not operate, or with no branch at all for a SELECTED member is
    not in the queryset — so it is not found, rather than found and refused,
    which would confirm that the id exists.
    """
    queryset = visible_orders(request.user, company)
    if prefetch:
        queryset = queryset.prefetch_related(*prefetch)
    order = queryset.filter(pk=pk).first()
    if order is None:
        raise NotFound('No encontrado.')
    return order


class V1InternalSurfaceMixin:
    """
    Shared gate for every internal-audience view.

    TWO CLIENTS, ONE SURFACE (H4.1.1, docs/adr-auth-v1-internal.md). Native apps
    authenticate with `Authorization: Bearer`; the web panel with its HttpOnly
    cookie, which carries CSRF on every unsafe method. `V1InternalAuthentication`
    admits exactly ONE of the two per request and refuses a request that
    presents both. Nothing below this line knows or cares which one it was:
    the gates read `request.user`, never the credential.
    """

    authentication_classes = [V1InternalAuthentication]
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

    def require_any_capability(self, company, *capabilities: str):
        """
        Gate 2 for an act that more than one capability authorises.

        Used where a narrower capability was split out of a wider one and the
        wider one must keep implying it — assigning a technician, recording a
        payment. Same answer as `require_capability` when none is held.
        """
        if not any(
            has_capability(self.request.user, company, code) for code in capabilities
        ):
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

        # BORN SCOPED — by tenant AND by branch (H4.1.2). Every filter below, the
        # count and the page narrow within what this caller may see; none can
        # widen it, because the boundary is applied before any of them.
        orders = (
            visible_orders(request.user, company)
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

        # CLIENT-SEC-01, as on the web panel: `?review=pending` narrows to the
        # orders waiting for a person, and every row says its level and state.
        orders = order_risk.filter_by_review(orders, params.get('review'))

        page, page_size = self._pagination(params)
        total = orders.count()
        rows = V1InternalOrderListSerializer(
            orders[(page - 1) * page_size: page * page_size], many=True,
        ).data
        order_risk.annotate_rows(company, rows)

        return Response({
            'count': total,
            'page': page,
            'page_size': page_size,
            'results': rows,
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

        order = _visible_order(request, company, pk, 'items__product')
        return Response(self._payload(request, order, company))

    def _payload(self, request, order, company):
        from . import unit_identification

        transitions = list(fulfillment.allowed_fulfillment_statuses(request.user, company))
        view = unit_identification.order_view(order)
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
            'available_fulfillment_transitions': transitions,
            # SERIAL-PICK-01, the same facts the web panel shows: the devices
            # somebody identified, how many are missing per product, and
            # whether THIS caller may still say which device it is.
            'units': [record for records in view['records'].values() for record in records],
            'units_pending': sum(view['pending'].values()),
            'units_pending_by_product': {str(product_id): count for product_id, count in view['pending'].items()},
            'units_can_change': bool(transitions) and not view['left'],
            # CLIENT-SEC-01, the same block the web panel shows: what was
            # noticed about this order and what a person decided; null when it
            # was not assessed. For `sales.orders.view`: this payload is also
            # the answer of routes that ask only for `manage`.
            'risk': order_risk.describe(
                order_risk.for_order(order),
                can_review=has_capability(request.user, company, CAP_ORDERS_MANAGE),
            ) if has_capability(request.user, company, CAP_ORDERS_VIEW) else None,
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

        order = _visible_order(request, company, pk)

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
        except fulfillment.FulfillmentBlocked as exc:
            # SERIAL-PICK-01, the same answer as the web panel: the devices of
            # the order have to be identified before it leaves the shop.
            return Response(
                {'detail': exc.detail, 'code': 'units_pending', 'pending': sum(exc.pending.values())},
                status=status.HTTP_409_CONFLICT,
            )

        order = Order.objects.prefetch_related('items__product').get(pk=order.pk)
        return Response(self._payload(request, order, company))


class V1InternalOrderRiskReviewView(V1InternalOrderDetailView):
    """
    POST {decision: approve|reject, note?} — decide the review an order was
    waiting for. CLIENT-SEC-01. Requires `sales.orders.manage`.

    ONE RULE, TWO SURFACES: the service the web panel calls
    (`order_risk.review`), behind the same capability and the same order
    visibility. Once: a review that is not waiting answers 409. The decision is
    advice — the order's payment and dispatch are exactly what they were.

    POST ONLY, DECLARED: it builds on the detail view for its payload and would
    otherwise inherit that view's GET.
    """

    http_method_names = ['post', 'options']
    throttle_classes = [AdminOrderStatusChangeThrottle]

    def post(self, request, company_slug=None, pk=None):
        company = self.get_internal_company()
        self.require_capability(company, CAP_ORDERS_MANAGE)

        serializer = V1InternalRiskReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        order = _visible_order(request, company, pk)
        try:
            row = order_risk.review(
                order=order, company=company, actor=request.user,
                decision=serializer.validated_data['decision'],
                note=serializer.validated_data.get('note', ''),
                request=request,
            )
        except order_risk.ReviewError as exc:
            return Response(exc.body(), status=exc.http_status)
        # The assessment alone, not the order: `manage` does not imply `view`.
        return Response({'risk': order_risk.describe(row, can_review=True)})


class _V1OrderUnitsBase(V1InternalOrderDetailView):
    """
    SERIAL-PICK-01 — the devices of an order, from the app.

    ONE RULE, TWO SURFACES: the same service the web panel calls, behind the
    same capability as the fulfilment change (`sales.orders.manage`) and the
    same order visibility. The app that is refused «delivered» because a device
    is missing must be able to say which device it is.
    """

    throttle_classes = [AdminOrderStatusChangeThrottle]

    def manageable_order(self, request, pk):
        company = self.get_internal_company()
        self.require_capability(company, CAP_ORDERS_MANAGE)
        return company, _visible_order(request, company, pk)

    def after(self, request, order, company, code=status.HTTP_200_OK):
        order = Order.objects.prefetch_related('items__product').get(pk=order.pk)
        return Response(self._payload(request, order, company), status=code)


class V1InternalOrderUnitLookupView(_V1OrderUnitsBase):
    """GET ?code=&method= — the device a code names, if this order is waiting for it. Writes nothing."""

    http_method_names = ['get', 'head', 'options']

    def get(self, request, company_slug=None, pk=None):
        from . import unit_identification

        company, order = self.manageable_order(request, pk)
        method = request.query_params.get('method') or unit_identification.SCANNER
        try:
            unit = unit_identification.candidate_for_order(
                order=order, code=request.query_params.get('code', ''), method=method,
            )
        except unit_identification.UnitIdentificationError as exc:
            return Response(exc.body(), status=exc.http_status)
        return Response({'unit': unit_identification.describe(unit), 'method': method})


class V1InternalOrderUnitsView(_V1OrderUnitsBase):
    """POST {code, method} — this is the device being handed over."""

    http_method_names = ['post', 'options']

    def post(self, request, company_slug=None, pk=None):
        from . import unit_identification

        company, order = self.manageable_order(request, pk)
        data = request.data if hasattr(request.data, 'get') else {}
        try:
            unit_identification.identify_for_order(
                order=order, code=data.get('code', ''), method=data.get('method'),
                actor=request.user, request=request,
            )
        except unit_identification.UnitIdentificationError as exc:
            return Response(exc.body(), status=exc.http_status)
        return self.after(request, order, company, status.HTTP_201_CREATED)


class V1InternalOrderUnitDetailView(_V1OrderUnitsBase):
    """DELETE — take back a device identified by mistake, while it has not left the shop."""

    http_method_names = ['delete', 'options']

    def delete(self, request, company_slug=None, pk=None, unit_pk=None):
        from . import unit_identification

        company, order = self.manageable_order(request, pk)
        try:
            unit_identification.forget(order=order, order_unit_id=unit_pk, actor=request.user, request=request)
        except unit_identification.UnitIdentificationError as exc:
            return Response(exc.body(), status=exc.http_status)
        return self.after(request, order, company)
