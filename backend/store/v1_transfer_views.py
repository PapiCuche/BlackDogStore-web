"""
Inter-branch stock transfers, for native clients. IP1B.

THIS FILE CREATES NO BUSINESS LOGIC. `inventory_services` has owned the whole
document since M7A: `create_stock_transfer` opens a draft, `set_transfer_item`
puts a line on it, `dispatch_transfer` takes the units off the origin shelf,
`receive_transfer` puts them on the destination shelf, `cancel_transfer` closes
it without moving anything. Four states, and stock moves at exactly two of the
transitions. `inventory_views` calls those functions; this adds a second caller.

WHAT THE STATE MACHINE IS, AND WHERE IT LIVES
---------------------------------------------
    draft ──dispatch──▶ in_transit ──receive──▶ received
      │                      │
      └──────cancel──────────┘

It lives in `inventory_services`, not here and CERTAINLY not in a client. This
module exposes one endpoint per transition and lets the domain refuse an illegal
one; a native app is told the current status and which actions the server will
accept, and draws buttons from that.

Units are in flight between dispatch and receive, and that is the point of the
document: a shop that has sent something is short of it before the other shop is
long of it, and pretending the move is instantaneous would make one of the two
counts wrong for as long as the van is on the road.

TWO DIFFERENT BRANCH QUESTIONS, AND THEY ARE NOT THE SAME
----------------------------------------------------------
SEEING a transfer needs access to EITHER end: a manager who runs the destination
must see what is coming even if the origin is a shop they never enter.

ACTING on one needs BOTH ends. Dispatching moves units off a shelf and receiving
puts them on another, and somebody who reaches only one of the two is not in a
position to say the whole thing happened.

Both rules are the Web surface's, reused rather than restated — `visible_branches`
for the first and `assert_branch_access` for the second.
"""

from __future__ import annotations

from django.db.models import Q
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from .inventory_services import (
    InsufficientStockError,
    InventoryError,
    TransferError,
    cancel_transfer,
    create_stock_transfer,
    dispatch_transfer,
    receive_transfer,
    set_transfer_item,
)
from .models import AdminAuditLog, Product, StockTransfer
from .serializers import StockTransferCreateSerializer, StockTransferSerializer
from .tenancy import (
    BranchAccessError,
    assert_branch_access,
    visible_branches,
)
from .throttles import AdminInventoryReportsThrottle, AdminStockMovementsThrottle
from .v1_internal_views import V1InternalSurfaceMixin

CAP_INVENTORY_VIEW = 'inventory.view'
CAP_INVENTORY_ADJUST = 'inventory.adjust'

_PAGE_SIZE = 25
_MAX_PAGE_SIZE = 100


class V1TransferSurfaceMixin(V1InternalSurfaceMixin):
    """The gates, in the house order, plus the two branch rules."""

    def get_company_for_read(self):
        company = self.get_internal_company()
        self.require_capability(company, CAP_INVENTORY_VIEW)
        return company

    def get_company_for_write(self):
        company = self.get_internal_company()
        self.require_capability(company, CAP_INVENTORY_ADJUST)
        return company

    def scoped_transfer(self, company, pk):
        """
        A transfer that touches at least one branch this caller reaches.

        404 rather than 403 for anything else — including a transfer between two
        shops of this very company that the caller has no access to. A 403 would
        confirm the document exists.
        """
        branch_ids = list(
            visible_branches(self.request.user, company).values_list('pk', flat=True)
        )
        transfer = (
            StockTransfer.objects
            .filter(company=company)
            .filter(
                Q(source_branch_id__in=branch_ids)
                | Q(destination_branch_id__in=branch_ids)
            )
            .select_related('source_branch', 'destination_branch', 'created_by')
            .prefetch_related('items__product')
            .filter(pk=pk)
            .first()
        )
        if transfer is None:
            raise NotFound('No encontrado.')
        return transfer

    def resolve_product(self, company, data):
        """
        The article, named EITHER way this surface already names one.

        `product_slug` is how the rest of the v1 internal inventory identifies
        an article: `/inventory/stock/` returns a slug and no id, and
        `/inventory/adjustments/` takes a slug, so a native client that has read
        a shelf has a slug in its hand and nothing else. Requiring a numeric pk
        here would have made this endpoint unreachable from the very list it is
        meant to be used with — and reachable only by a client that had gone to
        `/api/admin/`, which native clients must never do.

        `product` (the pk) keeps working because the Web console speaks it and a
        contract that is already merged does not get taken away. Both are
        scoped to the company, so an article of another tenant is not found
        here, exactly as it is not found anywhere else on this surface.
        """
        raw_slug = data.get('product_slug')
        raw_pk = data.get('product')
        if raw_slug in (None, '') and raw_pk in (None, ''):
            raise NotFound('Producto no encontrado en esta empresa.')

        products = Product.objects.filter(company=company)
        if raw_slug not in (None, ''):
            product = products.filter(slug=raw_slug).first()
        else:
            # A pk that is not a number is not found rather than a 500. Django
            # raises ValueError on `filter(pk='abc')`, and an unparseable id is
            # a client mistake, not a server fault.
            try:
                product = products.filter(pk=int(raw_pk)).first()
            except (TypeError, ValueError):
                product = None
        if product is None:
            raise NotFound('Producto no encontrado en esta empresa.')
        return product

    def require_both_ends(self, transfer):
        """
        403 unless the caller reaches BOTH shops.

        Seeing is not acting. Somebody who can watch a transfer arrive is not
        thereby able to say it left.
        """
        for branch in (transfer.source_branch, transfer.destination_branch):
            try:
                assert_branch_access(self.request.user, branch)
            except BranchAccessError:
                raise PermissionDenied(
                    'Necesitas acceso a la sucursal de origen y a la de destino '
                    'para operar esta transferencia.'
                ) from None


class V1TransferListView(V1TransferSurfaceMixin, APIView):
    """
    GET — transfers touching my branches. POST — open a DRAFT.

    Reading takes `inventory.view`; opening one takes `inventory.adjust`,
    because a draft is the first step of moving stock even though it moves none
    yet.
    """

    def get_throttles(self):
        if self.request.method == 'POST':
            return [AdminStockMovementsThrottle()]
        return [AdminInventoryReportsThrottle()]

    def get(self, request, company_slug=None):
        company = self.get_company_for_read()
        branch_ids = list(
            visible_branches(request.user, company).values_list('pk', flat=True)
        )
        qs = (
            StockTransfer.objects
            .filter(company=company)
            .filter(
                Q(source_branch_id__in=branch_ids)
                | Q(destination_branch_id__in=branch_ids)
            )
            .select_related('source_branch', 'destination_branch', 'created_by')
            .prefetch_related('items__product')
            .order_by('-created_at', '-pk')
        )

        raw_status = (request.query_params.get('status') or '').strip()
        if raw_status:
            if raw_status not in dict(StockTransfer.STATUS_CHOICES):
                return Response(
                    {'detail': 'Estado de transferencia inválido.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            qs = qs.filter(status=raw_status)

        raw_branch = (request.query_params.get('branch') or '').strip()
        if raw_branch:
            try:
                branch_id = int(raw_branch)
            except ValueError:
                return Response(
                    {'detail': 'Sucursal inválida.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            # Filtering by a shop the caller cannot reach yields nothing rather
            # than everything: the AND with the scope above already holds.
            qs = qs.filter(
                Q(source_branch_id=branch_id) | Q(destination_branch_id=branch_id)
            )

        total = qs.count()
        try:
            page_size = min(int(request.query_params.get('page_size', _PAGE_SIZE)), _MAX_PAGE_SIZE)
            page = max(int(request.query_params.get('page', 1)), 1)
        except ValueError:
            page_size, page = _PAGE_SIZE, 1
        start = (page - 1) * page_size
        rows = qs[start:start + page_size]

        return Response({
            'count': total,
            'page': page,
            'page_size': page_size,
            'results': StockTransferSerializer(rows, many=True).data,
        })

    def post(self, request, company_slug=None):
        company = self.get_company_for_write()

        serializer = StockTransferCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # BOTH branch ids are untrusted input, and a shop the caller cannot
        # reach answers as not-found whether it belongs to another tenant or
        # simply to a shop they do not work in.
        visible = visible_branches(request.user, company)
        source = visible.filter(pk=data['source_branch']).first()
        destination = visible.filter(pk=data['destination_branch']).first()
        if source is None or destination is None:
            raise NotFound('Sucursal no encontrada o sin acceso.')

        try:
            transfer = create_stock_transfer(
                company=company,
                source_branch=source,
                destination_branch=destination,
                actor=request.user,
                reason=data.get('reason', ''),
                reference=data.get('reference', ''),
            )
        except InventoryError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        AdminAuditLog.log(
            actor=request.user,
            action='stock_transfer_created',
            target_type='stock_transfer',
            target_id=transfer.pk,
            metadata={
                'transfer_id': transfer.pk,
                'source_branch_id': source.pk,
                'destination_branch_id': destination.pk,
            },
            request=request,
            company=company,
        )
        return Response(
            StockTransferSerializer(transfer).data, status=status.HTTP_201_CREATED,
        )


class V1TransferDetailView(V1TransferSurfaceMixin, APIView):
    """GET — one transfer, with its lines and whatever state it is in."""

    throttle_classes = [AdminInventoryReportsThrottle]

    def get(self, request, company_slug=None, pk=None):
        company = self.get_company_for_read()
        transfer = self.scoped_transfer(company, pk)
        return Response(StockTransferSerializer(transfer).data)


class V1TransferItemsView(V1TransferSurfaceMixin, APIView):
    """
    PUT — set the quantity of ONE product on a DRAFT.

    The article may be named by `product_slug` — how the rest of this surface
    names one — or by `product`, the pk the Web console speaks.

    A quantity of zero removes the line, which is how a line is deleted: there
    is no separate DELETE, because "how many of this go" and "this does not go"
    are the same question asked twice.

    The domain refuses a transfer that has already left. Editing what was sent
    after it was sent would make the paperwork disagree with the van.
    """

    throttle_classes = [AdminStockMovementsThrottle]

    def put(self, request, company_slug=None, pk=None):
        company = self.get_company_for_write()
        transfer = self.scoped_transfer(company, pk)
        self.require_both_ends(transfer)

        raw_quantity = request.data.get('quantity')
        try:
            quantity = int(raw_quantity)
        except (TypeError, ValueError):
            return Response(
                {'detail': 'Cantidad inválida.'}, status=status.HTTP_400_BAD_REQUEST,
            )
        if quantity < 0:
            return Response(
                {'detail': 'La cantidad no puede ser negativa.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        product = self.resolve_product(company, request.data)

        try:
            set_transfer_item(transfer, product=product, quantity=quantity)
        except (TransferError, InventoryError) as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        transfer.refresh_from_db()
        return Response(StockTransferSerializer(transfer).data)


class _V1TransferActionView(V1TransferSurfaceMixin, APIView):
    """
    Shared plumbing for dispatch / receive / cancel.

    Each is its own endpoint rather than a `status` field somebody PATCHes,
    because they are not the same act: one takes units off a shelf, one puts
    them on another, one closes a document that never moved anything. A single
    "set the status" endpoint would let a client assert `received` for stock
    that never left.
    """

    throttle_classes = [AdminStockMovementsThrottle]
    action = None
    audit_action = ''

    def post(self, request, company_slug=None, pk=None):
        company = self.get_company_for_write()
        transfer = self.scoped_transfer(company, pk)
        self.require_both_ends(transfer)

        try:
            self.action(transfer, actor=request.user, request=request)
        except InsufficientStockError as exc:
            # The whole transition rolled back. Nothing moved.
            return Response(
                {'detail': str(exc), 'code': 'insufficient_stock'},
                status=status.HTTP_409_CONFLICT,
            )
        except (TransferError, InventoryError) as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        transfer.refresh_from_db()
        return Response(StockTransferSerializer(transfer).data)


class V1TransferDispatchView(_V1TransferActionView):
    """POST — the van leaves. Units come OFF the origin shelf, now."""

    action = staticmethod(dispatch_transfer)


class V1TransferReceiveView(_V1TransferActionView):
    """POST — the van arrives. Units go ON the destination shelf, now."""

    action = staticmethod(receive_transfer)


class V1TransferCancelView(_V1TransferActionView):
    """
    POST — close a transfer that will not happen.

    The domain decides which states may be cancelled. Cancelling something in
    transit would leave units belonging to neither shop, so if the domain
    refuses it, this refuses it.
    """

    action = staticmethod(cancel_transfer)
