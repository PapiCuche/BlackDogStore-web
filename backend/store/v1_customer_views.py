"""
The CUSTOMER surface — `/api/v1/customer/<company_slug>/…`.

THREE AUDIENCES, THREE SURFACES (DEC-API-001)

  `/api/v1/storefront/<slug>/`  PUBLIC    anonymous, a shop window
  `/api/v1/customer/<slug>/`    CUSTOMER  a client reading their OWN records
  `/api/v1/internal/<slug>/`    INTERNAL  staff reading the COMPANY's records
                                          under a capability — NOT YET BUILT

They are deliberately separate URL spaces rather than one endpoint that widens
its queryset for staff. An endpoint whose result set depends on who is asking is
one refactor away from returning the wrong set, and the failure is silent: a
customer screen quietly showing every client's purchases.

WHAT MAKES THIS SAFE

Not the path. The path names a company, and anyone can type any company. The
security boundary is `tenancy.customer_owned_orders()`: a queryset that starts
from the two ownership FKs and cannot be widened by any parameter this view
accepts. A member of staff who signs in here sees their own purchases and
nothing else — being an employee is not a customer relation.
"""
from rest_framework import permissions, status, viewsets
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from .throttles import AdminOrdersThrottle, AdminOrderStatusChangeThrottle

from .tenancy import (
    customer_owned_orders,
    has_customer_relation,
    resolve_public_storefront_company,
)
from .v1_authentication import V1BearerAuthentication
from .v1_customer_serializers import V1CustomerOrderSerializer


class V1CustomerSurfaceMixin:
    """
    Shared gate for every customer-audience view.

    Bearer only. The web cookie is not accepted here: this surface exists for
    native clients, and a browser reaching it would be reaching past the
    frontend that was built for it.
    """

    authentication_classes = [V1BearerAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get_customer_company(self):
        """
        The company named by the path, IF this user is a client of it.

        Unknown company, inactive company and "you are not a client here" all
        raise the SAME 404. Distinguishing them would let any authenticated
        account map which companies exist on the platform and which of them it
        has no relation with — a slow but complete tenant enumeration, from a
        single valid login.

        Staff who are not also clients land here too, and that is the point.
        Company-wide order access is `sales.orders.view` on the internal
        surface, not a side effect of being signed in.
        """
        company = resolve_public_storefront_company(self.kwargs.get('company_slug'))
        if company is None or not has_customer_relation(self.request.user, company):
            raise NotFound('No encontrado.')
        return company


class V1CustomerOrderViewSet(V1CustomerSurfaceMixin, viewsets.ReadOnlyModelViewSet):
    """
    A customer's own orders in one company.

    Read-only on purpose. Cancelling, refunding and re-confirming are decisions
    the business makes, and a mobile client asserting them would be asserting an
    outcome the server has to own. See DEC-MOBILE-003 on server authority.
    """

    serializer_class = V1CustomerOrderSerializer

    def get_queryset(self):
        # BORN SCOPED, from ownership. There is no filter applied afterwards
        # that could be forgotten, and no query parameter that widens it.
        return (
            customer_owned_orders(self.request.user, self.get_customer_company())
            .select_related('company')
            .prefetch_related('items__product')
            .order_by('-created_at')
        )


class V1CustomerRepairViewSet(V1CustomerSurfaceMixin, viewsets.ReadOnlyModelViewSet):
    """
    A customer's own repair orders in one company. BR-005A, M8.

    THE SIBLING OF `V1CustomerOrderViewSet`, and deliberately not an extension
    of it: a purchase and a repair are different things to the person waiting,
    and the only reason they appear together here is that both are "mine".

    OWNERSHIP IS A FOREIGN KEY. `service_services.customer_owned_repair_orders`
    matches `Customer.user`, and nothing else. Not the email on the record, not
    the document number, not a name — a household shares an address and often a
    phone, and matching on any of them would hand somebody else's device to
    whoever typed it at the counter.

    A repair belonging to another client, or to another tenant, is 404. Not 403:
    "this order exists but is not yours" is an existence oracle, and an order
    number is short enough to guess.
    """

    serializer_class = None  # resolved per action; see get_serializer_class
    pagination_class = None
    http_method_names = ['get', 'head', 'options']

    def get_serializer_class(self):
        from .v1_service_serializers import (
            V1CustomerRepairDetailSerializer,
            V1CustomerRepairListSerializer,
        )

        return (
            V1CustomerRepairDetailSerializer if self.action == 'retrieve'
            else V1CustomerRepairListSerializer
        )

    def get_serializer_context(self):
        from . import service_services
        from .models import RepairStatusCode

        context = super().get_serializer_context()
        company = getattr(self, '_company', None)
        if company is not None:
            settings_by_code = service_services.status_settings(company)
            context['status_labels'] = {
                code: service_services.status_label(company, code, settings_by_code)
                for code, _label in RepairStatusCode.choices
            }
        return context

    def get_queryset(self):
        from . import service_services

        company = self.get_customer_company()
        # Stashed so the serializer context can resolve this tenant's own words
        # for each lifecycle code without a second lookup.
        self._company = company
        return (
            service_services.customer_owned_repair_orders(self.request.user, company)
            .select_related('device')
            .order_by('-received_at', '-pk')
        )


class V1CustomerRepairQuoteView(V1CustomerSurfaceMixin, APIView):
    """
    GET — the quote on MY repair. BR-005B, M9.

    OWNERSHIP FIRST, ALWAYS. The repair order is looked up through
    `customer_owned_repair_orders`, which matches `Customer.user` and nothing
    else. A repair that is not this person's is not found — and neither is its
    quote, because the quote is only ever reached through the order.

    Being staff of the company grants nothing here. Company-wide access to
    quotes is `service.orders.view` on the internal surface, and a platform
    master does not become somebody's customer by being one.

    A DRAFT IS NEVER RETURNED. It is the shop thinking out loud. An EXPIRED or
    already-decided quote IS returned, because hiding it would make somebody
    believe it never existed — the app renders it as settled or lapsed.
    """

    throttle_classes = [AdminOrdersThrottle]

    def _order(self, company, pk):
        from . import service_services

        order = (
            service_services.customer_owned_repair_orders(self.request.user, company)
            .filter(pk=pk).first()
        )
        if order is None:
            raise NotFound('No encontrado.')
        return order

    def _context(self, company):
        from . import service_services
        from .models import RepairStatusCode

        settings_by_code = service_services.status_settings(company)
        return {
            'request': self.request,
            'status_labels': {
                code: service_services.status_label(company, code, settings_by_code)
                for code, _label in RepairStatusCode.choices
            },
        }

    def get(self, request, company_slug=None, pk=None):
        from . import service_services
        from .v1_service_serializers import V1CustomerQuoteSerializer

        company = self.get_customer_company()
        order = self._order(company, pk)

        quote = service_services.customer_visible_quote(order)
        if quote is None:
            # Not an error: most of a repair's life has no quote on it.
            return Response({'quote': None})

        return Response({
            'quote': V1CustomerQuoteSerializer(quote, context=self._context(company)).data,
        })


class V1CustomerRepairQuoteDecisionView(V1CustomerRepairQuoteView):
    """
    POST — approve or reject the quote on MY repair.

    POST ONLY, DECLARED. It subclasses the quote view for its lookups and would
    otherwise inherit that view's `get(self, request, company_slug, pk)` — while
    this URL supplies a third kwarg, `quote_id`. A GET therefore raised
    TypeError inside the handler, which DRF does not convert, so the endpoint
    answered 500 instead of 405. Naming the verbs fixes it at the router.

    THE ONLY WAY AN ORDER REACHES `approved` OR `rejected`. Staff cannot assert
    a customer's decision through the internal transition endpoint; that is the
    invariant M9 exists to establish.

    IDEMPOTENT FOR THE SAME ANSWER. A double tap on a slow connection is one
    decision, so a repeat returns 200 with the record that already exists. The
    OPPOSITE answer arriving later is a 409: somebody — possibly the same person
    on another device — already decided, and silently overwriting it would let
    whichever request happened to be second win.

    The quote is re-checked under a lock inside the service: expired, cancelled
    or already-decided quotes are refused there, whatever the app drew.
    """

    http_method_names = ['post']

    throttle_classes = [AdminOrderStatusChangeThrottle]

    def post(self, request, company_slug=None, pk=None, quote_id=None):
        from . import service_services
        from .models import Customer, RepairQuote
        from .v1_service_serializers import (
            V1CustomerQuoteDecisionSerializer, V1CustomerQuoteSerializer,
        )

        company = self.get_customer_company()
        order = self._order(company, pk)

        # Reached only through the order, so a quote id from somebody else's
        # repair is not found rather than found-then-refused.
        quote = order.quotes.filter(pk=quote_id).first()
        if quote is None:
            raise NotFound('No encontrado.')

        serializer = V1CustomerQuoteDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        customer = Customer.objects.filter(
            company=company, user=request.user, pk=order.customer_id,
        ).first()
        if customer is None:
            # The ownership helper already matched on this, so reaching here
            # means the relation changed mid-request. Same answer as always.
            raise NotFound('No encontrado.')

        try:
            service_services.record_quote_decision(
                quote=quote,
                customer=customer,
                user=request.user,
                decision=serializer.validated_data['decision'],
                reason=serializer.validated_data.get('reason', ''),
                request=request,
            )
        except service_services.QuoteDecisionConflict as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        except service_services.ServiceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        fresh = RepairQuote.objects.prefetch_related('items').select_related(
            'decision',
        ).get(pk=quote.pk)
        return Response({
            'quote': V1CustomerQuoteSerializer(fresh, context=self._context(company)).data,
        })
