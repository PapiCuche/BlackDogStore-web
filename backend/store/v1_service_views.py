"""
INTERNAL technical service — `/api/v1/internal/<company_slug>/service/…`.

The third module of the internal surface, after sales orders (M6) and inventory
(M7A), and it inherits their shape unchanged:

  no active membership       → 404, indistinguishable from an unknown company
  membership, no capability  → 403, re-resolved on every request
  a branch outside the grant → 404, never 403

WHAT THIS MODULE DOES NOT DECIDE
--------------------------------
The lifecycle. Every legal move, the history it writes, the lock it takes and
the number it allocates belong to `service_services`. These views establish WHO
is asking and WHICH company, resolve the ids inside that tenant, and then call
it. A view that knew a transition rule would be a second copy of the machine.

WHY THE BRANCH GATE IS HERE AND NOT ONLY IN THE SERVICE
-------------------------------------------------------
Because it is an ACCESS question, not a data-integrity one. The service refuses
a branch belonging to another company — that would corrupt the row. Refusing a
branch that belongs to this company but not to this member is about what a
person may reach, and it answers 404 for the same reason M7A does: a 403 would
confirm the branch exists, and an employee could sweep ids to map their
company's shops.
"""
from django.db.models import Prefetch, Q
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from . import service_services as service
from .models import (
    Branch,
    Customer,
    Device,
    Product,
    RepairOrder,
    RepairStatusCode,
    RepairStatusSetting,
    TechnicianAssignment,
)
from .tenancy import visible_branches
from .throttles import AdminOrdersThrottle, AdminOrderStatusChangeThrottle
from .v1_internal_views import V1InternalSurfaceMixin
from .v1_service_serializers import (
    V1RepairStatusSettingSerializer,
    V1ServiceDiagnosticSerializer,
    V1ServiceDiagnosticWriteSerializer,
    V1ServiceQuoteItemWriteSerializer,
    V1ServiceQuoteSerializer,
    V1ServiceQuoteWriteSerializer,
    V1ServiceAssignmentSerializer,
    V1ServiceAssignmentWriteSerializer,
    V1ServiceCustomerSerializer,
    V1ServiceDeviceCreateSerializer,
    V1ServiceDeviceSerializer,
    V1ServiceOrderCreateSerializer,
    V1ServiceOrderDetailSerializer,
    V1ServiceOrderListSerializer,
    V1ServiceTransitionSerializer,
    _user_display,
)

CAP_DEVICES_VIEW = 'service.devices.view'
CAP_DEVICES_MANAGE = 'service.devices.manage'
CAP_ORDERS_VIEW = 'service.orders.view'
CAP_ORDERS_CREATE = 'service.orders.create'
CAP_ORDERS_MANAGE = 'service.orders.manage'
CAP_CUSTOMERS_VIEW = 'service.customers.view'

_DEFAULT_PAGE_SIZE = 25
_MAX_PAGE_SIZE = 100


def _paginate(params) -> tuple[int, int]:
    try:
        page = max(1, int(params.get('page', 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        size = min(_MAX_PAGE_SIZE, max(1, int(params.get('page_size', _DEFAULT_PAGE_SIZE))))
    except (TypeError, ValueError):
        size = _DEFAULT_PAGE_SIZE
    return page, size


class V1ServiceSurfaceMixin(V1InternalSurfaceMixin):
    """The internal gates, plus the two lookups every service view needs."""

    def status_labels(self, company) -> dict:
        """This company's word for each lifecycle code, resolved once per request."""
        settings_by_code = service.status_settings(company)
        return {
            code: service.status_label(company, code, settings_by_code)
            for code, _label in RepairStatusCode.choices
        }

    def serializer_context(self, company) -> dict:
        return {'request': self.request, 'status_labels': self.status_labels(company)}

    def resolve_branch(self, company, raw_branch_id, *, required=False):
        """
        A `branch_id` from the client is a SELECTOR, validated against the
        member's own set. Absent → every branch they may see.
        """
        allowed = visible_branches(self.request.user, company)
        if raw_branch_id in (None, ''):
            if required:
                raise NotFound('No encontrado.')
            return None, allowed
        try:
            wanted = int(raw_branch_id)
        except (TypeError, ValueError):
            raise NotFound('No encontrado.')
        branch = allowed.filter(pk=wanted).first()
        if branch is None:
            raise NotFound('No encontrado.')
        return branch, Branch.objects.filter(pk=branch.pk)

    def get_customer(self, company, raw_id) -> Customer:
        """Resolved WITHIN the tenant, so a foreign id is not found, not refused."""
        try:
            wanted = int(raw_id)
        except (TypeError, ValueError):
            raise NotFound('No encontrado.')
        customer = Customer.objects.filter(company=company, pk=wanted).first()
        if customer is None:
            raise NotFound('No encontrado.')
        return customer

    def get_device(self, company, raw_id) -> Device:
        try:
            wanted = int(raw_id)
        except (TypeError, ValueError):
            raise NotFound('No encontrado.')
        device = Device.objects.filter(company=company, pk=wanted).select_related(
            'customer',
        ).first()
        if device is None:
            raise NotFound('No encontrado.')
        return device

    def get_order(self, company, pk) -> RepairOrder:
        """
        One order of this company, within the member's branches.

        The branch scope is applied to the LOOKUP rather than checked after it,
        so an order in a shop this person cannot reach is simply not found.
        """
        allowed = visible_branches(self.request.user, company)
        order = (
            RepairOrder.objects
            .filter(company=company, pk=pk, branch__in=allowed)
            .select_related('customer', 'device', 'branch', 'received_by')
            .prefetch_related(
                'status_history__actor',
                Prefetch(
                    'assignments',
                    queryset=TechnicianAssignment.objects.select_related('technician'),
                ),
            )
            .first()
        )
        if order is None:
            raise NotFound('No encontrado.')
        return order


class V1ServiceContextView(V1ServiceSurfaceMixin, APIView):
    """
    GET — what the service module looks like for this company, right now.

    The lifecycle as THIS tenant presents it, and the branches this member may
    receive devices into. Both come from the server so the app draws a picker
    and a set of state labels it did not invent — and so a company that renamed
    "Recibido" sees its own word everywhere without the app shipping a
    translation table.
    """

    def get(self, request, company_slug=None):
        company = self.get_internal_company()
        self.require_capability(company, CAP_ORDERS_VIEW)

        settings_rows = RepairStatusSetting.objects.filter(company=company).order_by(
            'sort_order', 'code',
        )
        return Response({
            'statuses': V1RepairStatusSettingSerializer(settings_rows, many=True).data,
            'available_branches': [
                {'id': b.pk, 'name': b.name}
                for b in visible_branches(request.user, company)
            ],
        })


class V1ServiceCustomerSearchView(V1ServiceSurfaceMixin, APIView):
    """
    GET — find a customer, for intake only.

    THE NARROWEST THING THAT WORKS. The CRM lives on the web admin surface and
    is not reachable from a Bearer token; a receptionist opening an order still
    has to find the person standing in front of them. So this returns a name, a
    document and a phone — enough to tell two people apart — and nothing else.

    It is NOT a customer list: without a search term it returns the most recent
    few rather than the whole file, because "download every client of this
    company" is not a request intake ever needs to make.
    """

    throttle_classes = [AdminOrdersThrottle]

    def get(self, request, company_slug=None):
        company = self.get_internal_company()
        self.require_capability(company, CAP_CUSTOMERS_VIEW)

        search = request.query_params.get('search', '').strip()
        rows = Customer.objects.filter(company=company, is_active=True)
        if search:
            rows = rows.filter(
                Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
                | Q(business_name__icontains=search)
                | Q(document_number__icontains=search)
                | Q(phone__icontains=search)
            )

        page, size = _paginate(request.query_params)
        total = rows.count()
        return Response({
            'count': total,
            'page': page,
            'page_size': size,
            'results': V1ServiceCustomerSerializer(
                rows.order_by('-created_at', '-pk')[(page - 1) * size: page * size],
                many=True,
            ).data,
        })


class V1ServiceDeviceListView(V1ServiceSurfaceMixin, APIView):
    """GET — the company's registered devices. POST — register one."""

    throttle_classes = [AdminOrdersThrottle]

    def get(self, request, company_slug=None):
        company = self.get_internal_company()
        self.require_capability(company, CAP_DEVICES_VIEW)

        rows = Device.objects.filter(company=company).select_related('customer')

        raw_customer = request.query_params.get('customer_id')
        if raw_customer:
            rows = rows.filter(customer=self.get_customer(company, raw_customer))

        search = request.query_params.get('search', '').strip()
        if search:
            rows = rows.filter(
                Q(brand__icontains=search)
                | Q(model__icontains=search)
                | Q(serial_number__icontains=search)
                | Q(imei__icontains=search)
            )

        page, size = _paginate(request.query_params)
        total = rows.count()
        return Response({
            'count': total,
            'page': page,
            'page_size': size,
            'results': V1ServiceDeviceSerializer(
                rows.order_by('-created_at', '-pk')[(page - 1) * size: page * size],
                many=True,
            ).data,
        })

    def post(self, request, company_slug=None):
        company = self.get_internal_company()
        self.require_capability(company, CAP_DEVICES_MANAGE)

        serializer = V1ServiceDeviceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)

        customer = self.get_customer(company, data.pop('customer_id'))

        try:
            device = service.create_device(
                company=company, customer=customer, actor=request.user,
                request=request, **data,
            )
        except service.ServiceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        payload = V1ServiceDeviceSerializer(device).data
        # A WARNING, not a refusal. Serial numbers are transcribed by hand from
        # a sticker; a duplicate is usually a returning device and sometimes a
        # typo, and the person at the counter is better placed than a constraint
        # to tell which.
        duplicates = service.find_possible_duplicate_devices(
            company, serial_number=device.serial_number, imei=device.imei,
            exclude_pk=device.pk,
        )
        payload['possible_duplicates'] = V1ServiceDeviceSerializer(
            duplicates, many=True,
        ).data
        return Response(payload, status=status.HTTP_201_CREATED)


class V1ServiceDeviceDetailView(V1ServiceSurfaceMixin, APIView):
    """GET — one device."""

    def get(self, request, company_slug=None, pk=None):
        company = self.get_internal_company()
        self.require_capability(company, CAP_DEVICES_VIEW)
        return Response(V1ServiceDeviceSerializer(self.get_device(company, pk)).data)


class V1ServiceOrderListView(V1ServiceSurfaceMixin, APIView):
    """GET — the company's service orders. POST — receive a device."""

    throttle_classes = [AdminOrdersThrottle]

    def get(self, request, company_slug=None):
        company = self.get_internal_company()
        self.require_capability(company, CAP_ORDERS_VIEW)

        _, branches = self.resolve_branch(company, request.query_params.get('branch_id'))

        rows = (
            RepairOrder.objects
            .filter(company=company, branch__in=branches)
            .select_related('customer', 'device', 'branch')
            .prefetch_related(Prefetch(
                'assignments',
                queryset=TechnicianAssignment.objects.filter(
                    unassigned_at__isnull=True,
                ).select_related('technician'),
            ))
        )

        wanted_status = request.query_params.get('status', '').strip()
        if wanted_status:
            rows = rows.filter(status=wanted_status)

        search = request.query_params.get('search', '').strip()
        if search:
            rows = rows.filter(
                Q(number__icontains=search)
                | Q(customer__first_name__icontains=search)
                | Q(customer__last_name__icontains=search)
                | Q(customer__business_name__icontains=search)
                | Q(device__brand__icontains=search)
                | Q(device__model__icontains=search)
                | Q(device__serial_number__icontains=search)
            )

        raw_technician = request.query_params.get('technician_id')
        if raw_technician:
            try:
                rows = rows.filter(
                    assignments__technician_id=int(raw_technician),
                    assignments__unassigned_at__isnull=True,
                )
            except (TypeError, ValueError):
                raise NotFound('No encontrado.')

        page, size = _paginate(request.query_params)
        total = rows.count()
        return Response({
            'count': total,
            'page': page,
            'page_size': size,
            'results': V1ServiceOrderListSerializer(
                rows.order_by('-received_at', '-pk')[(page - 1) * size: page * size],
                many=True, context=self.serializer_context(company),
            ).data,
        })

    def post(self, request, company_slug=None):
        company = self.get_internal_company()
        self.require_capability(company, CAP_ORDERS_CREATE)

        serializer = V1ServiceOrderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # The branch is REQUIRED and validated against this member's set: an
        # order has to be somewhere, and "any of my shops" is not a place a
        # device can be left.
        branch, _ = self.resolve_branch(company, data['branch_id'], required=True)
        customer = self.get_customer(company, data['customer_id'])
        device = self.get_device(company, data['device_id'])

        try:
            order = service.create_repair_order(
                company=company,
                branch=branch,
                customer=customer,
                device=device,
                reported_issue=data['reported_issue'],
                physical_condition=data.get('physical_condition', ''),
                received_accessories=data.get('received_accessories', ''),
                internal_notes=data.get('internal_notes', ''),
                actor=request.user,
                request=request,
            )
        except service.ServiceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            V1ServiceOrderDetailSerializer(
                self.get_order(company, order.pk),
                context=self.serializer_context(company),
            ).data,
            status=status.HTTP_201_CREATED,
        )


class V1ServiceOrderDetailView(V1ServiceSurfaceMixin, APIView):
    """GET — one order, with its full internal timeline."""

    def get(self, request, company_slug=None, pk=None):
        company = self.get_internal_company()
        self.require_capability(company, CAP_ORDERS_VIEW)
        return Response(V1ServiceOrderDetailSerializer(
            self.get_order(company, pk), context=self.serializer_context(company),
        ).data)


class V1ServiceOrderHistoryView(V1ServiceSurfaceMixin, APIView):
    """
    GET — the order's timeline on its own.

    The detail already embeds it. This exists so a screen can refresh a growing
    timeline without re-fetching the whole order, and it is read-only for a
    reason no endpoint could change: the history is append-only in the model.
    """

    def get(self, request, company_slug=None, pk=None):
        from .v1_service_serializers import V1ServiceHistorySerializer

        company = self.get_internal_company()
        self.require_capability(company, CAP_ORDERS_VIEW)
        order = self.get_order(company, pk)

        events = order.status_history.select_related('actor').order_by('created_at', 'pk')
        return Response({
            'count': events.count(),
            'results': V1ServiceHistorySerializer(
                events, many=True, context=self.serializer_context(company),
            ).data,
        })


class V1ServiceOrderTransitionView(V1ServiceSurfaceMixin, APIView):
    """
    POST — move an order to another state.

    The target is checked against the machine in `service_services`, inside a
    transaction, with the row locked. Whatever the app drew, this decides.
    """

    throttle_classes = [AdminOrderStatusChangeThrottle]

    def post(self, request, company_slug=None, pk=None):
        company = self.get_internal_company()
        self.require_capability(company, CAP_ORDERS_MANAGE)
        order = self.get_order(company, pk)

        serializer = V1ServiceTransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            service.transition_repair_order(
                repair_order=order,
                to_status=serializer.validated_data['status'],
                actor=request.user,
                comment=serializer.validated_data.get('comment', ''),
                request=request,
            )
        except service.ServiceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(V1ServiceOrderDetailSerializer(
            self.get_order(company, pk), context=self.serializer_context(company),
        ).data)


class V1ServiceOrderAssignmentView(V1ServiceSurfaceMixin, APIView):
    """
    GET — who may be assigned. POST — assign, or release with a null id.

    THE CANDIDATE LIST IS THE SERVER'S. An app cannot be asked to work out who
    is staff of a company; it would have to read a user list it has no business
    holding. `eligible_technicians` is the only definition, and this endpoint
    returns exactly it — with a display name and nothing else, because a
    technician's email and phone are personnel data.
    """

    throttle_classes = [AdminOrderStatusChangeThrottle]

    def get(self, request, company_slug=None, pk=None):
        company = self.get_internal_company()
        self.require_capability(company, CAP_ORDERS_MANAGE)
        order = self.get_order(company, pk)

        current = order.assignments.filter(unassigned_at__isnull=True).select_related(
            'technician',
        ).first()
        return Response({
            'current': V1ServiceAssignmentSerializer(current).data if current else None,
            'candidates': [
                {'id': u.pk, 'name': _user_display(u)}
                for u in service.eligible_technicians(company)
            ],
        })

    def post(self, request, company_slug=None, pk=None):
        company = self.get_internal_company()
        self.require_capability(company, CAP_ORDERS_MANAGE)
        order = self.get_order(company, pk)

        serializer = V1ServiceAssignmentWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        technician_id = serializer.validated_data.get('technician_id')

        if technician_id is None:
            service.unassign_technician(
                repair_order=order, actor=request.user, request=request,
            )
        else:
            # Resolved from the company's OWN eligible set, so a user id from
            # another tenant is not found rather than found-then-refused.
            technician = service.eligible_technicians(company).filter(
                pk=technician_id,
            ).first()
            if technician is None:
                raise NotFound('No encontrado.')
            try:
                service.assign_technician(
                    repair_order=order, technician=technician,
                    actor=request.user, request=request,
                )
            except service.ServiceError as exc:
                return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(V1ServiceOrderDetailSerializer(
            self.get_order(company, pk), context=self.serializer_context(company),
        ).data)


# ---------------------------------------------------------------------------
# BR-005B — diagnosis and quotes, internal surface
# ---------------------------------------------------------------------------

CAP_DIAGNOSTIC_MANAGE = 'service.diagnostic.manage'


class V1ServiceQuotingMixin(V1ServiceSurfaceMixin):
    """
    Lookups for the diagnosis/quote surface.

    READING uses `service.orders.view`, WRITING uses `service.diagnostic.manage`.
    A colleague who may open an order may read the quote on it — requiring a
    second capability to see what the order already shows would be authority
    theatre. Composing one is a different act and needs its own permission.

    Every lookup is SCOPED FROM THE START, through `get_order`, which already
    applies the branch gate. A quote id belonging to another order, another
    branch or another tenant is simply not found — never found-then-refused.
    """

    def get_diagnostic(self, company, order, pk):
        diagnostic = order.diagnostics.filter(pk=pk).first()
        if diagnostic is None:
            raise NotFound('No encontrado.')
        return diagnostic

    def get_quote(self, company, order, pk):
        quote = (
            order.quotes.filter(pk=pk)
            .select_related('diagnostic')
            .prefetch_related('items')
            .first()
        )
        if quote is None:
            raise NotFound('No encontrado.')
        return quote


class V1ServiceDiagnosticListView(V1ServiceQuotingMixin, APIView):
    """GET — every revision, newest first. POST — open a new draft."""

    throttle_classes = [AdminOrdersThrottle]

    def get(self, request, company_slug=None, pk=None):
        company = self.get_internal_company()
        self.require_capability(company, CAP_ORDERS_VIEW)
        order = self.get_order(company, pk)

        rows = order.diagnostics.select_related('diagnosed_by').order_by('-revision', '-pk')
        return Response({
            'count': rows.count(),
            'results': V1ServiceDiagnosticSerializer(rows, many=True).data,
        })

    def post(self, request, company_slug=None, pk=None):
        company = self.get_internal_company()
        self.require_capability(company, CAP_DIAGNOSTIC_MANAGE)
        order = self.get_order(company, pk)

        serializer = V1ServiceDiagnosticWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            diagnostic = service.create_diagnostic(
                repair_order=order,
                actor=request.user,
                request=request,
                **serializer.validated_data,
            )
        except service.ServiceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            V1ServiceDiagnosticSerializer(diagnostic).data,
            status=status.HTTP_201_CREATED,
        )


class V1ServiceDiagnosticDetailView(V1ServiceQuotingMixin, APIView):
    """GET — one revision. PATCH — edit it, while it is still a draft."""

    throttle_classes = [AdminOrdersThrottle]

    def get(self, request, company_slug=None, pk=None, diagnostic_id=None):
        company = self.get_internal_company()
        self.require_capability(company, CAP_ORDERS_VIEW)
        order = self.get_order(company, pk)
        return Response(V1ServiceDiagnosticSerializer(
            self.get_diagnostic(company, order, diagnostic_id),
        ).data)

    def patch(self, request, company_slug=None, pk=None, diagnostic_id=None):
        company = self.get_internal_company()
        self.require_capability(company, CAP_DIAGNOSTIC_MANAGE)
        order = self.get_order(company, pk)
        diagnostic = self.get_diagnostic(company, order, diagnostic_id)

        serializer = V1ServiceDiagnosticWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        try:
            updated = service.update_diagnostic(
                diagnostic=diagnostic, actor=request.user, request=request,
                **serializer.validated_data,
            )
        except service.ServiceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(V1ServiceDiagnosticSerializer(updated).data)


class V1ServiceQuoteListView(V1ServiceQuotingMixin, APIView):
    """GET — every revision. POST — open a new draft."""

    throttle_classes = [AdminOrdersThrottle]

    def get(self, request, company_slug=None, pk=None):
        company = self.get_internal_company()
        self.require_capability(company, CAP_ORDERS_VIEW)
        order = self.get_order(company, pk)

        rows = (
            order.quotes.select_related('created_by', 'decision')
            .prefetch_related('items').order_by('-revision', '-pk')
        )
        return Response({
            'count': rows.count(),
            'results': V1ServiceQuoteSerializer(rows, many=True).data,
        })

    def post(self, request, company_slug=None, pk=None):
        company = self.get_internal_company()
        self.require_capability(company, CAP_DIAGNOSTIC_MANAGE)
        order = self.get_order(company, pk)

        serializer = V1ServiceQuoteWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)

        diagnostic = None
        raw_diagnostic = data.pop('diagnostic_id', None)
        if raw_diagnostic is not None:
            diagnostic = self.get_diagnostic(company, order, raw_diagnostic)

        try:
            quote = service.create_quote(
                repair_order=order, diagnostic=diagnostic,
                actor=request.user, request=request, **data,
            )
        except service.ServiceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            V1ServiceQuoteSerializer(quote).data, status=status.HTTP_201_CREATED,
        )


class V1ServiceQuoteDetailView(V1ServiceQuotingMixin, APIView):
    """GET — one revision. PATCH — edit its header while it is a draft."""

    throttle_classes = [AdminOrdersThrottle]

    def get(self, request, company_slug=None, pk=None, quote_id=None):
        company = self.get_internal_company()
        self.require_capability(company, CAP_ORDERS_VIEW)
        order = self.get_order(company, pk)
        return Response(V1ServiceQuoteSerializer(
            self.get_quote(company, order, quote_id),
        ).data)

    def patch(self, request, company_slug=None, pk=None, quote_id=None):
        company = self.get_internal_company()
        self.require_capability(company, CAP_DIAGNOSTIC_MANAGE)
        order = self.get_order(company, pk)
        quote = self.get_quote(company, order, quote_id)

        serializer = V1ServiceQuoteWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)

        if 'diagnostic_id' in data:
            raw = data.pop('diagnostic_id')
            data['diagnostic'] = (
                None if raw is None else self.get_diagnostic(company, order, raw)
            )

        try:
            updated = service.update_quote(
                quote=quote, actor=request.user, request=request, **data,
            )
        except service.ServiceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(V1ServiceQuoteSerializer(updated).data)


class V1ServiceQuoteItemView(V1ServiceQuotingMixin, APIView):
    """POST — add a line to a draft quote."""

    throttle_classes = [AdminOrdersThrottle]

    def post(self, request, company_slug=None, pk=None, quote_id=None):
        company = self.get_internal_company()
        self.require_capability(company, CAP_DIAGNOSTIC_MANAGE)
        order = self.get_order(company, pk)
        quote = self.get_quote(company, order, quote_id)

        serializer = V1ServiceQuoteItemWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)

        product = None
        raw_product = data.pop('product_id', None)
        if raw_product is not None:
            # Resolved WITHIN the tenant, so a product id from another company
            # is not found rather than found-then-refused.
            product = Product.objects.filter(company=company, pk=raw_product).first()
            if product is None:
                raise NotFound('No encontrado.')

        try:
            service.add_quote_item(quote=quote, product=product, **data)
        except service.ServiceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            V1ServiceQuoteSerializer(self.get_quote(company, order, quote_id)).data,
            status=status.HTTP_201_CREATED,
        )


class V1ServiceQuoteItemDetailView(V1ServiceQuotingMixin, APIView):
    """
    DELETE — remove a line from a draft quote.

    Its own class rather than a second method on the collection view: sharing
    one class between `items/` and `items/<id>/` would leave POST reachable on
    the item URL, where it would silently create a new line instead of touching
    the one the URL names.
    """

    throttle_classes = [AdminOrdersThrottle]

    def delete(self, request, company_slug=None, pk=None, quote_id=None, item_id=None):
        company = self.get_internal_company()
        self.require_capability(company, CAP_DIAGNOSTIC_MANAGE)
        order = self.get_order(company, pk)
        quote = self.get_quote(company, order, quote_id)

        item = quote.items.filter(pk=item_id).first()
        if item is None:
            raise NotFound('No encontrado.')

        try:
            service.remove_quote_item(item=item)
        except service.ServiceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            V1ServiceQuoteSerializer(self.get_quote(company, order, quote_id)).data,
        )


class V1ServiceQuotePublishView(V1ServiceQuotingMixin, APIView):
    """
    POST — send the quote to the customer.

    THE ONLY WAY AN ORDER REACHES `waiting_approval`. The generic transition
    endpoint refuses that state precisely so this one can guarantee what it
    means: a frozen quote with lines, built on a finalized diagnosis, waiting.
    """

    throttle_classes = [AdminOrderStatusChangeThrottle]

    def post(self, request, company_slug=None, pk=None, quote_id=None):
        company = self.get_internal_company()
        self.require_capability(company, CAP_DIAGNOSTIC_MANAGE)
        order = self.get_order(company, pk)
        quote = self.get_quote(company, order, quote_id)

        try:
            service.publish_quote(quote=quote, actor=request.user, request=request)
        except service.ServiceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            V1ServiceQuoteSerializer(self.get_quote(company, order, quote_id)).data,
        )


class V1ServiceQuoteCancelView(V1ServiceQuotingMixin, APIView):
    """POST — withdraw a quote the customer has not answered."""

    throttle_classes = [AdminOrderStatusChangeThrottle]

    def post(self, request, company_slug=None, pk=None, quote_id=None):
        company = self.get_internal_company()
        self.require_capability(company, CAP_DIAGNOSTIC_MANAGE)
        order = self.get_order(company, pk)
        quote = self.get_quote(company, order, quote_id)

        try:
            service.cancel_quote(quote=quote, actor=request.user, request=request)
        except service.ServiceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            V1ServiceQuoteSerializer(self.get_quote(company, order, quote_id)).data,
        )
