"""
Internal CRM endpoints — SaaS Phase 4.

EVERY ONE OF THESE IS INTERNAL CONTROL. There is no public customer endpoint and
there is not going to be one in this phase: a client's phone number, document,
address and the shop's private notes about them are exactly the data an
e-commerce site must never serve to the internet. The future customer portal
will show a person their OWN orders, which is a different feature with a
different shape.

TENANT RESOLUTION
-----------------
The company is resolved from the caller, never read from the body. `?company=`
exists only so a platform master can say WHICH of the companies they already
reach they mean; it can never widen access, because `resolve_company_for_user`
selects from that set rather than trusting the number.

Every queryset starts from `Customer.objects.filter(company=company)`. Loading
globally and hiding afterwards is the pattern that leaks the moment somebody
adds a code path that forgets the hiding step.

BRANCH ACCESS DOES NOT APPLY HERE, ON PURPOSE
---------------------------------------------
Phase 2D gave staff a second axis of authority: which branches they may operate.
It governs stock, and it will govern repair orders. It does NOT govern this
model, because a customer does not belong to a branch — the same person buys in
one shop, leaves a laptop at another and collects it at a third. Scoping master
data by branch would fragment one client into three files and break the history
this module exists to keep. Recorded in docs/saas-multiempresa.md.
"""

from django.db.models import Count, Max, Q, Sum
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .customer_services import (
    DuplicateCustomerError,
    assert_document_available,
    find_possible_duplicates,
)
from .models import AdminAuditLog, Customer, Order
from .serializers import (
    CustomerListSerializer,
    CustomerOrderSerializer,
    CustomerSerializer,
    CustomerWriteSerializer,
)
from .tenancy import (
    CrossTenantError,
    NoTenantError,
    has_capability,
    resolve_company_for_user,
)
from .throttles import AdminCustomerWriteThrottle, AdminCustomersThrottle

CAP_CUSTOMERS_VIEW = 'service.customers.view'
CAP_CUSTOMERS_MANAGE = 'service.customers.manage'

_NOT_FOUND = 'No encontrado.'
_DEFAULT_PAGE_SIZE = 25
_MAX_PAGE_SIZE = 100
_HISTORY_LIMIT = 50


def _context(request, capability):
    """
    Resolve the company for this request and authorise it.

    Returns `(company, error_response)`. A company the caller cannot reach
    answers exactly like one that does not exist.
    """
    raw = request.query_params.get('company')
    requested_id = None
    if raw not in (None, ''):
        try:
            requested_id = int(raw)
        except (TypeError, ValueError):
            return None, Response(
                {'detail': 'Parámetro "company" inválido.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

    try:
        company = resolve_company_for_user(request.user, requested_id)
    except CrossTenantError:
        return None, Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
    except NoTenantError as exc:
        return None, Response({'detail': str(exc)}, status=status.HTTP_403_FORBIDDEN)

    if not has_capability(request.user, company, capability):
        return None, Response(
            {'detail': 'No tienes permisos sobre los clientes de esta empresa.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    return company, None


def _paginate(queryset, request):
    try:
        page = max(1, int(request.query_params.get('page', 1)))
    except (ValueError, TypeError):
        page = 1
    try:
        page_size = min(
            _MAX_PAGE_SIZE,
            max(1, int(request.query_params.get('page_size', _DEFAULT_PAGE_SIZE))),
        )
    except (ValueError, TypeError):
        page_size = _DEFAULT_PAGE_SIZE

    total = queryset.count()
    offset = (page - 1) * page_size
    return (
        queryset[offset: offset + page_size],
        {'count': total, 'page': page, 'page_size': page_size},
    )


def _search(queryset, term):
    """
    Find a client the way somebody at a counter would.

    A document is matched from the START (`istartswith`): people read the first
    digits off the card and stop. Names, email and phone are matched anywhere,
    because a surname is as likely to be typed as a first name.

    Normalised before matching, so `+51 999 111 222` finds a record stored as
    `+51999111222`. Without that, every phone search would silently return
    nothing and look like an empty CRM.
    """
    term = (term or '').strip()
    if not term:
        return queryset

    from .models import normalize_customer_phone, normalize_document_number

    criteria = (
        Q(first_name__icontains=term)
        | Q(last_name__icontains=term)
        | Q(business_name__icontains=term)
        | Q(email__icontains=term)
    )

    document = normalize_document_number(term)
    if document:
        criteria |= Q(document_number__istartswith=document)

    phone = normalize_customer_phone(term)
    if phone and len(phone.lstrip('+')) >= 3:
        criteria |= Q(phone__icontains=phone.lstrip('+'))

    return queryset.filter(criteria)


def _audit(request, company, action, customer, **metadata):
    """
    Record WHAT changed, never the values.

    A customer's document number, phone, email and the notes about them are the
    PII this model exists to protect. Copying them into an audit row would create
    a second, longer-lived store of the same data in a table that is read by more
    people and purged by nobody.
    """
    AdminAuditLog.log(
        actor=request.user,
        action=action,
        target_type='customer',
        target_id=customer.pk,
        metadata={'company_id': company.pk, 'customer_id': customer.pk, **metadata},
        request=request,
        company=company,
    )


class AdminCustomerListView(APIView):
    """
    GET  /api/admin/customers/   — `service.customers.view`
    POST /api/admin/customers/   — `service.customers.manage`
    """

    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        if self.request.method == 'POST':
            return [AdminCustomerWriteThrottle()]
        return [AdminCustomersThrottle()]

    def get(self, request):
        company, error = _context(request, CAP_CUSTOMERS_VIEW)
        if error:
            return error

        queryset = Customer.objects.filter(company=company)

        state = request.query_params.get('state', 'active')
        if state == 'active':
            queryset = queryset.filter(is_active=True)
        elif state == 'archived':
            queryset = queryset.filter(is_active=False)
        # `all` falls through — archived clients stay findable, which is the
        # point of archiving rather than deleting.

        customer_type = request.query_params.get('type')
        if customer_type in (Customer.TYPE_PERSON, Customer.TYPE_BUSINESS):
            queryset = queryset.filter(customer_type=customer_type)

        queryset = _search(queryset, request.query_params.get('search'))

        page, meta = _paginate(queryset, request)
        return Response({
            **meta,
            'can_manage': has_capability(request.user, company, CAP_CUSTOMERS_MANAGE),
            'results': CustomerListSerializer(page, many=True).data,
        })

    def post(self, request):
        company, error = _context(request, CAP_CUSTOMERS_MANAGE)
        if error:
            return error

        serializer = CustomerWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            assert_document_available(
                company, data.get('document_type', ''), data.get('document_number', ''),
            )
        except DuplicateCustomerError as exc:
            # 409, not 400: the request is well-formed and the client is not at
            # fault. Something already exists, and the useful answer says which
            # record, so the UI can offer to open it instead of a dead end.
            return Response(
                {
                    'detail': str(exc),
                    'existing': CustomerListSerializer(exc.existing).data,
                },
                status=status.HTTP_409_CONFLICT,
            )

        customer = Customer(company=company, created_by=request.user, **data)
        try:
            customer.save()
        except Exception as exc:  # model validation
            from django.core.exceptions import ValidationError as DjangoValidationError
            if isinstance(exc, DjangoValidationError):
                return Response(
                    getattr(exc, 'message_dict', {'detail': exc.messages}),
                    status=status.HTTP_400_BAD_REQUEST,
                )
            raise

        _audit(request, company, 'customer_created', customer,
               customer_type=customer.customer_type,
               has_document=bool(customer.document_number))

        # Advisory only. Reported alongside a SUCCESSFUL create, because a shared
        # email is not a reason to refuse a client — see customer_services.
        duplicates = find_possible_duplicates(
            company, email=customer.email, phone=customer.phone,
            exclude_pk=customer.pk,
        )
        return Response(
            {
                **CustomerSerializer(customer).data,
                'possible_duplicates': CustomerListSerializer(duplicates, many=True).data,
            },
            status=status.HTTP_201_CREATED,
        )


class AdminCustomerDetailView(APIView):
    """
    GET   /api/admin/customers/{pk}/ — `service.customers.view`
    PATCH /api/admin/customers/{pk}/ — `service.customers.manage`

    There is no DELETE. A client with history is not deletable — the database
    says so too, through `Order.customer`'s PROTECT — and a DELETE verb that
    silently archives would be a lie in the URL. Archiving is
    `PATCH {"is_active": false}`, which is what it does.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        if self.request.method in ('PATCH', 'PUT'):
            return [AdminCustomerWriteThrottle()]
        return [AdminCustomersThrottle()]

    def _get(self, company, pk):
        return Customer.objects.filter(company=company).filter(pk=pk).first()

    def get(self, request, pk):
        company, error = _context(request, CAP_CUSTOMERS_VIEW)
        if error:
            return error
        customer = self._get(company, pk)
        if customer is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)

        orders = (
            Order.objects
            .filter(company=company, customer=customer)
            .order_by('-created_at')
        )
        # Aggregated in the database, in one round trip. Summing in Python would
        # mean loading every order a long-standing client ever placed in order to
        # display one number.
        totals = orders.aggregate(
            orders_total=Count('pk'),
            paid_count=Count('pk', filter=Q(paid=True)),
            paid_amount=Sum('total', filter=Q(paid=True)),
            last_purchase_at=Max('paid_at', filter=Q(paid=True)),
        )

        return Response({
            **CustomerSerializer(customer).data,
            'can_manage': has_capability(request.user, company, CAP_CUSTOMERS_MANAGE),
            'summary': {
                # "Sales" means PAID. An abandoned checkout is a record of
                # intent, not money, and reporting the two together would
                # overstate every client's value.
                'orders_total': totals['orders_total'] or 0,
                'paid_orders': totals['paid_count'] or 0,
                'paid_amount': str(totals['paid_amount'] or 0),
                'last_purchase_at': totals['last_purchase_at'],
            },
            'orders': CustomerOrderSerializer(orders[:_HISTORY_LIMIT], many=True).data,
            'orders_truncated': (totals['orders_total'] or 0) > _HISTORY_LIMIT,
        })

    def patch(self, request, pk):
        company, error = _context(request, CAP_CUSTOMERS_MANAGE)
        if error:
            return error
        customer = self._get(company, pk)
        if customer is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)

        serializer = CustomerWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # `company`, `user` and `created_by` are not writable fields, so there is
        # nothing to reject here — they are simply not part of the serializer.
        if 'document_number' in data or 'document_type' in data:
            try:
                assert_document_available(
                    company,
                    data.get('document_type', customer.document_type),
                    data.get('document_number', customer.document_number),
                    exclude_pk=customer.pk,
                )
            except DuplicateCustomerError as exc:
                return Response(
                    {
                        'detail': str(exc),
                        'existing': CustomerListSerializer(exc.existing).data,
                    },
                    status=status.HTTP_409_CONFLICT,
                )

        changed = []
        for field, value in data.items():
            if getattr(customer, field) != value:
                setattr(customer, field, value)
                changed.append(field)

        if changed:
            try:
                customer.save()
            except Exception as exc:
                from django.core.exceptions import ValidationError as DjangoValidationError
                if isinstance(exc, DjangoValidationError):
                    return Response(
                        getattr(exc, 'message_dict', {'detail': exc.messages}),
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                raise

            action = 'customer_updated'
            if 'is_active' in changed:
                action = (
                    'customer_archived' if not customer.is_active
                    else 'customer_reactivated'
                )
            # Field NAMES only. What they changed to is the client's data.
            _audit(request, company, action, customer, changed_fields=sorted(changed))

        customer.refresh_from_db()
        return Response(CustomerSerializer(customer).data)
