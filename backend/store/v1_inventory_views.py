"""
INTERNAL inventory — `/api/v1/internal/<company_slug>/inventory/…`.

The second module of the internal surface, and the point of M7A: proving the
shape M6 established carries a second module without being bent.

TWO BOUNDARIES, NOT ONE.

Sales orders are tenant-scoped. Inventory is tenant-scoped AND **branch**-scoped:
a member with access to one shop must not read or move another shop's stock,
even inside their own company. So every queryset here starts from
`visible_branches(user, company)` and a `branch_id` from the client is a
SELECTOR validated against that set — never an instruction.

WHAT THIS DOES NOT DO.

It does not write stock. `inventory_services` owns every mutation, with its own
locking, its own `StockMovement` rows and its own audit entries. This module
establishes WHO may act and on WHICH branch, and then calls it — a division the
service layer states in its own docstring.

Transfers and inventory counts are NOT exposed. Both are multi-step workflows in
the domain (create → set items → dispatch → receive), and wrapping them in a
single POST would be inventing a semantics the business does not have. They stay
on the web admin until a phase designs them properly.
"""
from django.db.models import Q
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from . import inventory_services as inventory
from .models import Branch, Product, StockMovement
from .tenancy import visible_branches
from .throttles import (
    AdminInventoryAdjustThrottle,
    AdminInventoryReportsThrottle,
    AdminStockMovementsThrottle,
)
from .v1_internal_views import V1InternalSurfaceMixin
from .v1_inventory_serializers import (
    V1BranchSerializer,
    V1StockAdjustmentSerializer,
    V1StockMovementSerializer,
    V1StockRowSerializer,
)

CAP_INVENTORY_VIEW = 'inventory.view'
CAP_INVENTORY_ADJUST = 'inventory.adjust'

_DEFAULT_PAGE_SIZE = 25
_MAX_PAGE_SIZE = 100


class V1InventorySurfaceMixin(V1InternalSurfaceMixin):
    """
    Adds the BRANCH boundary on top of the internal one.

    Inherits both of M6's gates — 404 for "you do not belong here", 403 for
    "you belong but may not" — and adds the third question inventory needs:
    WHICH shops.
    """

    def visible_branches_for(self, company):
        """
        The branches this member may operate, from the server's own resolution.

        `visible_branches` reads `Membership.access_mode` and
        `MembershipBranchAccess`: ALL means every active branch including ones
        opened tomorrow, SELECTED means exactly the granted ones and no others.
        A member with SELECTED and zero grants reaches nothing, which is a valid
        state and denies rather than allows.
        """
        return visible_branches(self.request.user, company)

    def resolve_branch(self, company, raw_branch_id):
        """
        A `branch_id` from the client is a SELECTOR, validated here.

        Absent → every branch the member may see. Present → that branch IF it is
        in their set; otherwise 404, indistinguishable from a branch that does
        not exist. A 403 would confirm the branch is real and belongs to some
        company, which is the shape of a cross-tenant probe.
        """
        allowed = self.visible_branches_for(company)
        if raw_branch_id in (None, ''):
            return None, allowed

        try:
            wanted = int(raw_branch_id)
        except (TypeError, ValueError):
            raise NotFound('No encontrado.')

        branch = allowed.filter(pk=wanted).first()
        if branch is None:
            raise NotFound('No encontrado.')
        return branch, Branch.objects.filter(pk=branch.pk)


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


class V1InventorySummaryView(V1InventorySurfaceMixin, APIView):
    """
    GET — headline counters for the branches this member may see.

    Requires `inventory.view`, not `inventory.reports`. These are the figures a
    stock screen needs to open at all — how many products, how many units, how
    many are out — and gating them behind the reporting capability would leave
    someone who may see stock unable to see a summary OF that stock.
    `inventory.reports` remains for the analytical surfaces the web admin has.

    `inventory_value` is stock × SALE price, and the payload says so in
    `inventory_value_basis`. There is no cost model in the system, so a figure
    called "capital invested" would be a number with a false name on it.
    """

    throttle_classes = [AdminInventoryReportsThrottle]

    def get(self, request, company_slug=None):
        company = self.get_internal_company()
        self.require_capability(company, CAP_INVENTORY_VIEW)

        branch, branches = self.resolve_branch(company, request.query_params.get('branch_id'))
        summary = inventory.get_inventory_summary(company=company, branches=branches)

        return Response({
            **summary,
            'branch': V1BranchSerializer(branch).data if branch is not None else None,
            # So the app can offer a picker ONLY when there is a choice to make.
            'available_branches': V1BranchSerializer(
                self.visible_branches_for(company), many=True,
            ).data,
        })


class V1InventoryStockView(V1InventorySurfaceMixin, APIView):
    """GET — current stock, one row per product and branch. `inventory.view`."""

    throttle_classes = [AdminInventoryReportsThrottle]

    def get(self, request, company_slug=None):
        company = self.get_internal_company()
        self.require_capability(company, CAP_INVENTORY_VIEW)

        _, branches = self.resolve_branch(company, request.query_params.get('branch_id'))

        # BORN SCOPED, from the branches the member may reach. Nothing below
        # widens it; a member who reaches no branch gets an empty queryset.
        rows = inventory.branch_stock_queryset(branches)

        search = request.query_params.get('search', '').strip()
        if search:
            rows = rows.filter(
                Q(product__name__icontains=search) | Q(product__slug__icontains=search)
            )

        if request.query_params.get('low_stock') == 'true':
            rows = rows.filter(
                Q(quantity__gt=0)
                & inventory.low_stock_filter(inventory.DEFAULT_LOW_STOCK_THRESHOLD)
            )
        if request.query_params.get('out_of_stock') == 'true':
            rows = rows.filter(quantity__lte=0)

        rows = rows.order_by('product__name', 'branch__name')

        page, size = _paginate(request.query_params)
        total = rows.count()

        return Response({
            'count': total,
            'page': page,
            'page_size': size,
            'results': V1StockRowSerializer(
                rows[(page - 1) * size: page * size],
                many=True,
                context={'low_stock_threshold': inventory.DEFAULT_LOW_STOCK_THRESHOLD},
            ).data,
        })


class V1InventoryMovementsView(V1InventorySurfaceMixin, APIView):
    """
    GET — the Kardex. `inventory.view`.

    Paginated without exception: this table only grows, and an unpaginated
    history is a request that gets slower every day it is used.
    """

    throttle_classes = [AdminStockMovementsThrottle]

    def get(self, request, company_slug=None):
        company = self.get_internal_company()
        self.require_capability(company, CAP_INVENTORY_VIEW)

        _, branches = self.resolve_branch(company, request.query_params.get('branch_id'))
        branch_ids = list(branches.values_list('pk', flat=True))

        # Scoped by company AND branch. The company filter is not redundant: a
        # branch id list is derived per request, and asserting the tenant too
        # means a bug in that derivation cannot become a cross-tenant read.
        movements = (
            StockMovement.objects
            .filter(company=company, branch_id__in=branch_ids)
            .select_related('product', 'branch', 'actor')
            .order_by('-created_at', '-id')
        )

        product_slug = request.query_params.get('product_slug', '').strip()
        if product_slug:
            movements = movements.filter(product__slug=product_slug)

        movement_type = request.query_params.get('movement_type', '').strip()
        if movement_type:
            movements = movements.filter(movement_type=movement_type)

        page, size = _paginate(request.query_params)
        total = movements.count()

        return Response({
            'count': total,
            'page': page,
            'page_size': size,
            'results': V1StockMovementSerializer(
                movements[(page - 1) * size: page * size], many=True,
            ).data,
        })


class V1InventoryAdjustmentView(V1InventorySurfaceMixin, APIView):
    """
    POST — record a manual entry or exit. `inventory.adjust`.

    The client sends an INTENT: a product, a branch, a movement type, a positive
    quantity and a reason. It does not send a resulting stock figure, and the
    contract has no field for one — a final quantity from a client is a claim
    about a number two people may be changing at the same moment.

    Everything after authorisation belongs to `inventory_services`: the lock, the
    `StockMovement` row, the `BranchStock` update and the audit entry. This view
    does not touch stock and must never learn how.
    """

    throttle_classes = [AdminInventoryAdjustThrottle]

    def post(self, request, company_slug=None):
        company = self.get_internal_company()
        self.require_capability(company, CAP_INVENTORY_ADJUST)

        serializer = V1StockAdjustmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # The branch is REQUIRED here and validated against the member's set: a
        # movement has to land somewhere specific, and "all my branches" is not
        # a place stock can move to.
        branch, _ = self.resolve_branch(company, data['branch_id'])
        if branch is None:
            raise NotFound('No encontrado.')

        # Resolved within the company, so a slug from another tenant is not
        # found rather than found-then-refused.
        product = Product.objects.filter(company=company, slug=data['product_slug']).first()
        if product is None:
            raise NotFound('No encontrado.')

        try:
            movement = inventory.apply_manual_stock_movement(
                branch=branch,
                product_id=product.pk,
                movement_type=data['movement_type'],
                quantity=data['quantity'],
                reason=data['reason'],
                actor=request.user,
                request=request,
            )
        except inventory.InsufficientStockError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except inventory.InvalidMovementError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            V1StockMovementSerializer(movement).data, status=status.HTTP_201_CREATED,
        )
