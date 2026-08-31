"""
Admin inventory + internal sales note endpoints — Phase 6.0, tenantised in 2D.

Everything here is admin-panel only (session cookie auth + CSRF, no Bearer).
Stock is never mutated in this module directly: all writes go through
store.inventory_services so stock and the Kardex stay in one transaction.

AUTHORITY — TWO CHECKS, EVERY TIME
----------------------------------
Phase 2D retired the legacy DRF permission classes as the primary authority for
these endpoints. `CanViewInventoryReports` and friends could only ever answer
"is this person an inventory role SOMEWHERE", which on a multi-tenant install is
not a question worth answering. Every endpoint now resolves:

    1. WHICH COMPANY   from the caller's own memberships (never from the body),
       then the capability — inventory.view / inventory.adjust / inventory.reports.
    2. WHICH BRANCH    from the caller's branch grants, then the `?branch=` value
       validated against them.

Both must pass. `inventory.adjust` is not permission to adjust every branch, and
reaching a branch is not permission to move its stock.

A branch id the caller cannot reach answers exactly like one that does not
exist, so ids cannot be probed for existence.

LEGACY BRIDGE — narrow and temporary
------------------------------------
A pre-SaaS operator holding only `UserProfile.role` and no Membership still
reaches the PILOT tenant, and only it, with their legacy role as authority.
Without it every operator of the existing installation would lose the inventory
they have always managed the moment this phase deploys. It disappears once every
operator holds a Membership — tracked in docs/saas-multiempresa.md.
"""

import logging
from datetime import datetime

from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .inventory_services import (
    DEFAULT_LOW_STOCK_THRESHOLD,
    InsufficientStockError,
    InventoryCountError,
    InventoryError,
    TransferError,
    apply_manual_stock_movement,
    approve_inventory_count,
    branch_stock_queryset,
    cancel_inventory_count,
    cancel_transfer,
    create_inventory_count,
    create_stock_transfer,
    dispatch_transfer,
    get_best_selling_products,
    get_high_stock_rows,
    get_inventory_summary,
    get_low_stock_by_branch,
    get_low_stock_rows,
    get_movement_flow_trend,
    get_movement_type_distribution,
    get_pending_counts_count,
    get_products_without_movement,
    get_replenishment_rows,
    get_stock_by_branch,
    get_stock_card,
    get_surplus_branches,
    get_transfers_in_transit_count,
    low_stock_filter,
    receive_transfer,
    set_count_item,
    set_transfer_item,
)
from .models import (
    AdminAuditLog,
    BranchStock,
    InventoryCount,
    Order,
    Product,
    SalesNote,
    StockMovement,
    StockTransfer,
    UserProfile,
)
from .tenancy import (
    BranchAccessError,
    CATALOG_SOURCE_LEGACY,
    NoBranchError,
    assert_branch_access,
    has_capability,
    resolve_branch_for_user,
    resolve_catalog_company,
    visible_branches,
)
from .sales_note_services import (
    SalesNoteError,
    generate_sales_note_pdf,
    get_or_create_sales_note,
    get_sales_note_filename,
)
from .serializers import (
    BranchSerializer,
    BranchStockPolicySerializer,
    BranchStockSerializer,
    InventoryCountCreateSerializer,
    InventoryCountItemWriteSerializer,
    InventoryCountSerializer,
    SalesNoteSerializer,
    StockMovementCreateSerializer,
    StockMovementSerializer,
    StockTransferCreateSerializer,
    StockTransferItemWriteSerializer,
    StockTransferSerializer,
)
from .throttles import (
    AdminInventoryReportsThrottle,
    AdminSalesNotesThrottle,
    AdminStockMovementsThrottle,
)

logger = logging.getLogger(__name__)

_DEFAULT_PAGE_SIZE = 25
_MAX_PAGE_SIZE = 100
_MAX_LIMIT = 200

# Capability codes — ACTIVE from Phase 2D. See store/capabilities.py.
CAP_INVENTORY_VIEW = 'inventory.view'
CAP_INVENTORY_ADJUST = 'inventory.adjust'
CAP_INVENTORY_REPORTS = 'inventory.reports'
CAP_SALES_NOTES = 'sales.notes.manage'

# What a pre-SaaS operator (legacy bridge, pilot tenant only) is worth. These
# reproduce the Phase 6.0 DRF permission classes exactly, so no historical
# operator gains or loses anything in this phase.
_LEGACY_INVENTORY_VIEW_ROLES = frozenset([
    UserProfile.ROLE_INVENTORY, UserProfile.ROLE_ADMIN, UserProfile.ROLE_SUPERADMIN,
])
_LEGACY_INVENTORY_ADJUST_ROLES = _LEGACY_INVENTORY_VIEW_ROLES
_LEGACY_SALES_REPORT_ROLES = frozenset([
    UserProfile.ROLE_SALES, UserProfile.ROLE_INVENTORY,
    UserProfile.ROLE_ADMIN, UserProfile.ROLE_SUPERADMIN,
])
_LEGACY_SALES_NOTES_ROLES = frozenset([
    UserProfile.ROLE_SALES, UserProfile.ROLE_ADMIN, UserProfile.ROLE_SUPERADMIN,
])

_NO_COMPANY = 'No tienes acceso a los datos de ninguna empresa.'
_NO_PERMISSION = 'No tienes permisos sobre el inventario.'
_NOT_FOUND = 'No encontrado.'


# ---------------------------------------------------------------------------
# Context resolution
# ---------------------------------------------------------------------------

def _requested_company_id(request):
    """
    The company id named by the request, if any. UNTRUSTED — it is only ever
    passed to tenancy, which validates it against the caller's own access.

    Read from the QUERY STRING only, never from the body, for the same reason as
    the catalogue: context selection and object payload are different things,
    and a stray `company` key inside a movement payload must not change which
    tenant the request acts on.

    Returns `(value, ok)` — `ok is None` means the value was unparseable.
    """
    raw = request.query_params.get('company')
    if raw in (None, ''):
        return None, False
    try:
        return int(raw), True
    except (TypeError, ValueError):
        return None, None


def _company_context(request, capability, legacy_roles):
    """
    Resolve the tenant an inventory request acts on and check authority.

    Returns `(company, error_response)`. Mirrors admin_views._company_context so
    the catalogue and the inventory answer identically to the same caller —
    including `?company=`, which is how a platform master (who belongs to no
    tenant) says which one they are operating.
    """
    from .permissions import get_user_role

    requested_id, ok = _requested_company_id(request)
    if ok is None:
        return None, Response(
            {'detail': 'Parámetro "company" inválido.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    company, source = resolve_catalog_company(request.user, requested_id)
    if company is None:
        return None, Response(
            {'detail': _NO_COMPANY}, status=status.HTTP_403_FORBIDDEN,
        )

    if source == CATALOG_SOURCE_LEGACY:
        if get_user_role(request.user) not in legacy_roles:
            return None, Response(
                {'detail': _NO_PERMISSION}, status=status.HTTP_403_FORBIDDEN,
            )
    elif not has_capability(request.user, company, capability):
        return None, Response(
            {'detail': _NO_PERMISSION}, status=status.HTTP_403_FORBIDDEN,
        )

    return company, None


def _branch_context(request, capability, legacy_roles, *, allow_all=False):
    """
    Resolve company AND branch scope in one step.

    Returns `(company, branch, branches, error_response)` where:
      - `branch` is the single Branch the request acts on, or None when the
        caller asked for the aggregate of everything they can see.
      - `branches` is ALWAYS the set to aggregate over — either the one branch
        or every visible one. Reports take this and nothing else, which is what
        makes "you only ever see your own branches" structural rather than a
        filter somebody has to remember to apply.
    """
    company, error = _company_context(request, capability, legacy_roles)
    if error:
        return None, None, None, error

    requested = request.query_params.get('branch')
    try:
        branch = resolve_branch_for_user(
            request.user, company, requested, allow_all=allow_all,
        )
    except NoBranchError as exc:
        return None, None, None, Response(
            {'detail': str(exc)}, status=status.HTTP_403_FORBIDDEN,
        )
    except BranchAccessError:
        # Same answer as a branch that does not exist.
        return None, None, None, Response(
            {'detail': 'Sucursal no encontrada o sin acceso.'},
            status=status.HTTP_404_NOT_FOUND,
        )

    visible = visible_branches(request.user, company)
    branches = visible.filter(pk=branch.pk) if branch is not None else visible
    return company, branch, branches, None


def _scoped_product(company, product_id):
    """A product of `company`, or None. A foreign id behaves like a missing one."""
    try:
        return Product.objects.filter(company=company).get(pk=product_id)
    except (Product.DoesNotExist, ValueError, TypeError):
        return None


def _paginate(queryset, request):
    """Page-based pagination mirroring admin_views._paginate."""
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


def _int_param(request, name, default, *, minimum=0, maximum=_MAX_LIMIT):
    try:
        value = int(request.query_params.get(name, default))
    except (ValueError, TypeError):
        return default
    return max(minimum, min(maximum, value))


def _date_param(request, name):
    """Accept both YYYY-MM-DD and full ISO-8601. Returns an aware datetime or None."""
    raw = (request.query_params.get(name) or '').strip()
    if not raw:
        return None
    dt = parse_datetime(raw)
    if dt is None:
        d = parse_date(raw)
        if d is None:
            return None
        dt = datetime.combine(d, datetime.min.time())
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _branch_payload(branch):
    return None if branch is None else {'id': branch.pk, 'name': branch.name}


def _scope_payload(branch, branches):
    """
    How the answer was scoped, echoed back so the UI never has to guess.

    `branch` null with `branches` populated means "aggregate of everything you
    can see" — which for a restricted user is NOT the whole company, and the
    payload says so rather than letting a heading lie.
    """
    return {
        'branch': _branch_payload(branch),
        'branches': [{'id': b.pk, 'name': b.name} for b in branches],
        'is_aggregate': branch is None,
    }


# ---------------------------------------------------------------------------
# Branch selector
# ---------------------------------------------------------------------------

class AdminInventoryBranchListView(APIView):
    """
    GET /api/admin/inventory/branches/ — branches this operator may work in.

    Feeds the branch selector. It is UX data, not authority: every other
    endpoint re-resolves the branch against the same grants, so a client that
    invents an id gets a 404 rather than a different shop's stock.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminInventoryReportsThrottle]

    def get(self, request):
        company, error = _company_context(
            request, CAP_INVENTORY_VIEW, _LEGACY_INVENTORY_VIEW_ROLES,
        )
        if error:
            return error

        from .tenancy import default_branch_for_user, get_membership

        branches = visible_branches(request.user, company).order_by('name')
        membership = get_membership(request.user, company)
        default = default_branch_for_user(request.user, company)

        return Response({
            'company': {'id': company.pk, 'name': company.name},
            'results': BranchSerializer(branches, many=True).data,
            'count': branches.count(),
            'default_branch': _branch_payload(default),
            'access_mode': membership.branch_access_mode if membership else None,
            # Only a caller who reaches more than one branch has anything to
            # aggregate; the UI uses this to decide whether to offer the option.
            'allows_aggregate': branches.count() > 1,
        })


# ---------------------------------------------------------------------------
# Inventory dashboards and Kardex
# ---------------------------------------------------------------------------

class AdminInventorySummaryView(APIView):
    """GET /api/admin/inventory/summary/?branch= — headline counters."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminInventoryReportsThrottle]

    def get(self, request):
        company, branch, branches, error = _branch_context(
            request, CAP_INVENTORY_VIEW, _LEGACY_INVENTORY_VIEW_ROLES, allow_all=True,
        )
        if error:
            return error

        threshold = _int_param(
            request, 'threshold', DEFAULT_LOW_STOCK_THRESHOLD, minimum=0, maximum=10_000
        )
        summary = get_inventory_summary(
            company=company, branches=branches, low_stock_threshold=threshold,
        )
        return Response({**summary, 'scope': _scope_payload(branch, branches)})


class AdminInventoryDashboardView(APIView):
    """
    GET /api/admin/inventory/dashboard/?branch= — KPIs and chart series.

    Everything here is computed over the caller's visible branches. There is
    deliberately no profit, margin or cost figure: the system has no purchase
    cost, so any of the three would be a number wearing a name it has not
    earned. See docs/saas-multiempresa.md.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminInventoryReportsThrottle]

    def get(self, request):
        company, branch, branches, error = _branch_context(
            request, CAP_INVENTORY_VIEW, _LEGACY_INVENTORY_VIEW_ROLES, allow_all=True,
        )
        if error:
            return error

        threshold = _int_param(
            request, 'threshold', DEFAULT_LOW_STOCK_THRESHOLD, minimum=0, maximum=10_000
        )
        summary = get_inventory_summary(
            company=company, branches=branches, low_stock_threshold=threshold,
        )
        flow = get_movement_flow_trend(branches, days=7)

        return Response({
            'scope': _scope_payload(branch, branches),
            'summary': summary,
            'transfers_in_transit': get_transfers_in_transit_count(branches),
            'pending_counts': get_pending_counts_count(branches),
            'charts': {
                'stock_by_branch': get_stock_by_branch(branches),
                'low_stock_by_branch': get_low_stock_by_branch(branches, threshold),
                'entries_trend': flow['entries'],
                'exits_trend': flow['exits'],
                'movement_types': get_movement_type_distribution(branches, days=30),
            },
        })


class AdminBranchStockListView(APIView):
    """GET /api/admin/inventory/stock/?branch=&search=&status= — stock per branch."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminInventoryReportsThrottle]

    def get(self, request):
        company, branch, branches, error = _branch_context(
            request, CAP_INVENTORY_VIEW, _LEGACY_INVENTORY_VIEW_ROLES, allow_all=True,
        )
        if error:
            return error

        threshold = _int_param(
            request, 'threshold', DEFAULT_LOW_STOCK_THRESHOLD, minimum=0, maximum=10_000
        )
        qs = branch_stock_queryset(branches, active_products_only=False)

        search = (request.query_params.get('search') or '').strip()
        if search:
            qs = qs.filter(product__name__icontains=search)

        product_id = request.query_params.get('product')
        if product_id:
            try:
                qs = qs.filter(product_id=int(product_id))
            except (ValueError, TypeError):
                return Response(
                    {'detail': 'Parámetro "product" inválido.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        stock_status = (request.query_params.get('status') or '').strip().lower()
        if stock_status == 'out_of_stock':
            qs = qs.filter(quantity__lte=0)
        elif stock_status == 'low_stock':
            qs = qs.filter(quantity__gt=0).filter(low_stock_filter(threshold))
        elif stock_status == 'in_stock':
            qs = qs.filter(quantity__gt=0)

        qs = qs.order_by('product__name', 'branch__name')
        page_qs, meta = _paginate(qs, request)
        return Response({
            'scope': _scope_payload(branch, branches),
            'results': BranchStockSerializer(page_qs, many=True).data,
            **meta,
        })


class AdminBranchStockPolicyView(APIView):
    """
    PATCH /api/admin/inventory/stock/{pk}/policy/ — minimum / target for one row.

    Quantity is NOT editable here and never will be: changing stock is a
    movement, with an actor and a reason, not a form field.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminStockMovementsThrottle]

    def patch(self, request, pk):
        company, error = _company_context(
            request, CAP_INVENTORY_ADJUST, _LEGACY_INVENTORY_ADJUST_ROLES,
        )
        if error:
            return error

        row = (
            BranchStock.objects
            .select_related('branch', 'product')
            .filter(pk=pk, branch__company=company)
            .first()
        )
        if row is None:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)

        # Belonging to the company is not enough: the caller must reach THIS branch.
        try:
            assert_branch_access(request.user, row.branch)
        except BranchAccessError:
            return Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)

        ser = BranchStockPolicySerializer(row, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        old = {'minimum_stock': row.minimum_stock, 'target_stock': row.target_stock}
        for field in ('minimum_stock', 'target_stock'):
            if field in data:
                setattr(row, field, data[field])
        row.save(update_fields=['minimum_stock', 'target_stock', 'updated_at'])

        AdminAuditLog.log(
            actor=request.user,
            action='branch_stock_policy_updated',
            target_type='branch_stock',
            target_id=row.pk,
            metadata={
                'branch_id': row.branch_id,
                'product_id': row.product_id,
                'product_name': row.product.name,
                'old': old,
                'new': {'minimum_stock': row.minimum_stock, 'target_stock': row.target_stock},
            },
            request=request,
            company=company,
        )
        return Response(BranchStockSerializer(row).data)


class AdminStockMovementListView(APIView):
    """
    GET  /api/admin/inventory/movements/ — paginated Kardex, branch-scoped.
    POST /api/admin/inventory/movements/ — register a MANUAL entry or exit.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        if self.request.method == 'POST':
            return [AdminStockMovementsThrottle()]
        return [AdminInventoryReportsThrottle()]

    def get(self, request):
        company, branch, branches, error = _branch_context(
            request, CAP_INVENTORY_VIEW, _LEGACY_INVENTORY_VIEW_ROLES, allow_all=True,
        )
        if error:
            return error

        branch_ids = list(branches.values_list('pk', flat=True))
        # Born scoped: company AND branch first, every other filter afterwards.
        qs = (
            StockMovement.objects
            .filter(company=company, branch_id__in=branch_ids)
            .select_related('product', 'actor', 'order', 'branch')
        )

        product_id = request.query_params.get('product')
        if product_id:
            try:
                qs = qs.filter(product_id=int(product_id))
            except (ValueError, TypeError):
                return Response(
                    {'detail': 'Parámetro "product" inválido.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        movement_type = (request.query_params.get('movement_type') or '').strip()
        if movement_type:
            if movement_type not in dict(StockMovement.MOVEMENT_TYPE_CHOICES):
                return Response(
                    {'detail': 'Tipo de movimiento inválido.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            qs = qs.filter(movement_type=movement_type)

        order_id = request.query_params.get('order')
        if order_id:
            try:
                qs = qs.filter(order_id=int(order_id))
            except (ValueError, TypeError):
                return Response(
                    {'detail': 'Parámetro "order" inválido.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        actor_id = request.query_params.get('actor')
        if actor_id:
            try:
                qs = qs.filter(actor_id=int(actor_id))
            except (ValueError, TypeError):
                return Response(
                    {'detail': 'Parámetro "actor" inválido.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        date_from = _date_param(request, 'date_from')
        if date_from:
            qs = qs.filter(created_at__gte=date_from)
        date_to = _date_param(request, 'date_to')
        if date_to:
            qs = qs.filter(created_at__lte=date_to)

        search = (request.query_params.get('search') or '').strip()
        if search:
            qs = qs.filter(product__name__icontains=search)

        page_qs, meta = _paginate(qs, request)
        return Response({
            'scope': _scope_payload(branch, branches),
            'results': StockMovementSerializer(page_qs, many=True).data,
            **meta,
        })

    def post(self, request):
        company, error = _company_context(
            request, CAP_INVENTORY_ADJUST, _LEGACY_INVENTORY_ADJUST_ROLES,
        )
        if error:
            return error

        ser = StockMovementCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        # The branch in the payload is a SELECTION among the caller's grants,
        # never an instruction. Omitted means their default branch.
        try:
            branch = resolve_branch_for_user(
                request.user, company, data.get('branch'), allow_all=False,
            )
        except NoBranchError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except BranchAccessError:
            return Response(
                {'detail': 'Sucursal no encontrada o sin acceso.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # A product of another tenant answers exactly like one that does not exist.
        product = _scoped_product(company, data['product_id'])
        if product is None:
            return Response(
                {'detail': 'Producto no encontrado.'}, status=status.HTTP_404_NOT_FOUND,
            )

        try:
            movement = apply_manual_stock_movement(
                branch=branch,
                product_id=product.pk,
                movement_type=data['movement_type'],
                quantity=data['quantity'],
                reason=data['reason'],
                actor=request.user,
                request=request,
            )
        except InsufficientStockError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except InventoryError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            StockMovementSerializer(movement).data, status=status.HTTP_201_CREATED
        )


class AdminLowStockView(APIView):
    """GET /api/admin/inventory/low-stock/?branch=&threshold=5"""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminInventoryReportsThrottle]

    def get(self, request):
        company, branch, branches, error = _branch_context(
            request, CAP_INVENTORY_REPORTS, _LEGACY_INVENTORY_VIEW_ROLES, allow_all=True,
        )
        if error:
            return error

        threshold = _int_param(
            request, 'threshold', DEFAULT_LOW_STOCK_THRESHOLD, minimum=0, maximum=10_000
        )
        limit = _int_param(request, 'limit', 50, minimum=1)
        rows = get_low_stock_rows(branches, threshold=threshold, limit=limit)
        return Response({
            'scope': _scope_payload(branch, branches),
            'threshold': threshold,
            'count': len(rows),
            'results': BranchStockSerializer(rows, many=True).data,
        })


class AdminHighStockView(APIView):
    """GET /api/admin/inventory/high-stock/?branch= — most units on hand first."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminInventoryReportsThrottle]

    def get(self, request):
        company, branch, branches, error = _branch_context(
            request, CAP_INVENTORY_REPORTS, _LEGACY_INVENTORY_VIEW_ROLES, allow_all=True,
        )
        if error:
            return error

        limit = _int_param(request, 'limit', 20, minimum=1)
        rows = get_high_stock_rows(branches, limit=limit)
        return Response({
            'scope': _scope_payload(branch, branches),
            'count': len(rows),
            'results': BranchStockSerializer(rows, many=True).data,
        })


class AdminReplenishmentView(APIView):
    """
    GET /api/admin/inventory/replenishment/?branch= — what to restock.

    Read-only, and it stays that way. The endpoint suggests; it never opens a
    purchase and never opens a transfer. `surplus_branches` shows where the
    units may already be inside the company, so an operator can decide to move
    them — the decision, and the transfer, are theirs and are permission-checked
    when they make it.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminInventoryReportsThrottle]

    def get(self, request):
        company, branch, branches, error = _branch_context(
            request, CAP_INVENTORY_REPORTS, _LEGACY_INVENTORY_VIEW_ROLES, allow_all=True,
        )
        if error:
            return error

        limit = _int_param(request, 'limit', 100, minimum=1)
        rows = get_replenishment_rows(branches, limit=limit)

        if request.query_params.get('with_surplus') == 'true':
            all_visible = visible_branches(request.user, company)
            products = {
                p.pk: p for p in Product.objects.filter(
                    pk__in=[r['product_id'] for r in rows],
                )
            }
            for row in rows:
                product = products.get(row['product_id'])
                row['surplus_branches'] = [] if product is None else get_surplus_branches(
                    product, all_visible, exclude_branch=row['branch_id'],
                )

        return Response({
            'scope': _scope_payload(branch, branches),
            'count': len(rows),
            'results': rows,
        })


class AdminBestSellingView(APIView):
    """GET /api/admin/inventory/best-selling/?date_from&date_to&limit&branch"""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminInventoryReportsThrottle]

    def get(self, request):
        company, branch, branches, error = _branch_context(
            request, CAP_INVENTORY_REPORTS, _LEGACY_SALES_REPORT_ROLES, allow_all=True,
        )
        if error:
            return error

        limit = _int_param(request, 'limit', 10, minimum=1, maximum=100)
        rows = get_best_selling_products(
            company=company,
            branches=branches,
            date_from=_date_param(request, 'date_from'),
            date_to=_date_param(request, 'date_to'),
            limit=limit,
        )
        return Response({
            'scope': _scope_payload(branch, branches),
            'count': len(rows),
            'results': rows,
        })


class AdminStaleStockView(APIView):
    """GET /api/admin/inventory/no-movement/?days=60&branch= — dead stock."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminInventoryReportsThrottle]

    def get(self, request):
        company, branch, branches, error = _branch_context(
            request, CAP_INVENTORY_REPORTS, _LEGACY_INVENTORY_VIEW_ROLES, allow_all=True,
        )
        if error:
            return error

        days = _int_param(request, 'days', 60, minimum=1, maximum=3650)
        limit = _int_param(request, 'limit', 50, minimum=1)
        rows = get_products_without_movement(
            company=company, branches=branches, days=days, limit=limit,
        )
        return Response({
            'scope': _scope_payload(branch, branches),
            'days': days,
            'count': len(rows),
            'results': BranchStockSerializer(rows, many=True).data,
        })


class AdminProductStockCardView(APIView):
    """GET /api/admin/products/{pk}/stock-card/?branch= — Kardex for one product."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminInventoryReportsThrottle]

    def get(self, request, pk):
        company, branch, branches, error = _branch_context(
            request, CAP_INVENTORY_VIEW, _LEGACY_INVENTORY_VIEW_ROLES, allow_all=True,
        )
        if error:
            return error

        product = _scoped_product(company, pk)
        if product is None:
            return Response(
                {'detail': 'Producto no encontrado.'}, status=status.HTTP_404_NOT_FOUND
            )

        limit = _int_param(request, 'limit', 200, minimum=1)
        movements = get_stock_card(product, branches=branches, limit=limit)

        stock_rows = branch_stock_queryset(branches, active_products_only=False).filter(
            product=product,
        )
        current = sum(row.quantity for row in stock_rows)

        return Response({
            'scope': _scope_payload(branch, branches),
            'product': {
                'id': product.pk, 'name': product.name, 'slug': product.slug,
                'price': str(product.price), 'is_active': product.is_active,
                'category_name': product.category.name if product.category_id else None,
            },
            # The stock VISIBLE TO THIS CALLER, which for a restricted user is
            # not the company total. Named `current_stock` for continuity with
            # the pre-2D response shape the admin UI already reads.
            'current_stock': current,
            'stock_by_branch': BranchStockSerializer(stock_rows, many=True).data,
            'movements': StockMovementSerializer(movements, many=True).data,
        })


# ---------------------------------------------------------------------------
# Inter-branch transfers
# ---------------------------------------------------------------------------
#
# WHO MAY MOVE STOCK BETWEEN TWO BRANCHES: someone who reaches BOTH.
#
# The alternative — origin-only, with the destination confirming later — is a
# real workflow and probably where this ends up. It is not V1, because it needs
# a notification path, a "pending my receipt" queue and a rule for who chases an
# unreceived transfer. Requiring both ends today is the restriction that cannot
# lose units while that is designed. Tracked in docs/saas-multiempresa.md.

def _transfer_branches(request, company, data):
    """
    Validate the two branch ids of a transfer against the caller's own grants.

    Returns `(source, destination, error_response)`. A branch the caller cannot
    reach answers as not-found, whether it belongs to another tenant or simply
    to a shop they do not work in.
    """
    visible = visible_branches(request.user, company)
    source = visible.filter(pk=data['source_branch']).first()
    destination = visible.filter(pk=data['destination_branch']).first()
    if source is None or destination is None:
        return None, None, Response(
            {'detail': 'Sucursal no encontrada o sin acceso.'},
            status=status.HTTP_404_NOT_FOUND,
        )
    return source, destination, None


def _scoped_transfer(request, company, pk):
    """
    A transfer of `company` that touches at least one branch the caller reaches.

    Returns `(transfer, error_response)`. Visibility follows the ENDS: a manager
    who runs the destination shop must see what is coming to them even if the
    origin is a branch they have no access to. Acting on it is checked
    separately and needs both ends — see the dispatch/receive views.
    """
    branch_ids = list(visible_branches(request.user, company).values_list('pk', flat=True))
    from django.db.models import Q

    transfer = (
        StockTransfer.objects
        .filter(company=company)
        .filter(Q(source_branch_id__in=branch_ids) | Q(destination_branch_id__in=branch_ids))
        .select_related('source_branch', 'destination_branch', 'created_by')
        .prefetch_related('items__product')
        .filter(pk=pk)
        .first()
    )
    if transfer is None:
        return None, Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
    return transfer, None


def _assert_both_ends(request, transfer):
    """Error response unless the caller reaches BOTH ends of the transfer."""
    for branch in (transfer.source_branch, transfer.destination_branch):
        try:
            assert_branch_access(request.user, branch)
        except BranchAccessError:
            return Response(
                {
                    'detail': 'Necesitas acceso a la sucursal de origen y a la de '
                              'destino para operar esta transferencia.',
                },
                status=status.HTTP_403_FORBIDDEN,
            )
    return None


class AdminStockTransferListView(APIView):
    """
    GET  /api/admin/inventory/transfers/?status=&branch= — transfers on my branches.
    POST /api/admin/inventory/transfers/ — open a DRAFT transfer.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        if self.request.method == 'POST':
            return [AdminStockMovementsThrottle()]
        return [AdminInventoryReportsThrottle()]

    def get(self, request):
        company, error = _company_context(
            request, CAP_INVENTORY_VIEW, _LEGACY_INVENTORY_VIEW_ROLES,
        )
        if error:
            return error

        from django.db.models import Q

        branch_ids = list(
            visible_branches(request.user, company).values_list('pk', flat=True)
        )
        qs = (
            StockTransfer.objects
            .filter(company=company)
            .filter(Q(source_branch_id__in=branch_ids) | Q(destination_branch_id__in=branch_ids))
            .select_related('source_branch', 'destination_branch', 'created_by')
            .prefetch_related('items__product')
        )

        transfer_status = (request.query_params.get('status') or '').strip()
        if transfer_status:
            if transfer_status not in dict(StockTransfer.STATUS_CHOICES):
                return Response(
                    {'detail': 'Estado de transferencia inválido.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            qs = qs.filter(status=transfer_status)

        branch_param = (request.query_params.get('branch') or '').strip()
        if branch_param and branch_param.lower() != 'all':
            try:
                branch_id = int(branch_param)
            except (ValueError, TypeError):
                return Response(
                    {'detail': 'Parámetro "branch" inválido.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if branch_id not in branch_ids:
                return Response(
                    {'detail': 'Sucursal no encontrada o sin acceso.'},
                    status=status.HTTP_404_NOT_FOUND,
                )
            qs = qs.filter(Q(source_branch_id=branch_id) | Q(destination_branch_id=branch_id))

        page_qs, meta = _paginate(qs, request)
        return Response({
            'results': StockTransferSerializer(page_qs, many=True).data, **meta,
        })

    def post(self, request):
        company, error = _company_context(
            request, CAP_INVENTORY_ADJUST, _LEGACY_INVENTORY_ADJUST_ROLES,
        )
        if error:
            return error

        ser = StockTransferCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        source, destination, branch_error = _transfer_branches(request, company, data)
        if branch_error:
            return branch_error

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


class AdminStockTransferDetailView(APIView):
    """GET /api/admin/inventory/transfers/{pk}/"""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminInventoryReportsThrottle]

    def get(self, request, pk):
        company, error = _company_context(
            request, CAP_INVENTORY_VIEW, _LEGACY_INVENTORY_VIEW_ROLES,
        )
        if error:
            return error
        transfer, not_found = _scoped_transfer(request, company, pk)
        if not_found:
            return not_found
        return Response(StockTransferSerializer(transfer).data)


class AdminStockTransferItemsView(APIView):
    """
    PUT /api/admin/inventory/transfers/{pk}/items/ — replace the DRAFT lines.

    A whole-list PUT rather than per-line verbs: a transfer is edited as a
    document, and sending the final list makes "remove this line" expressible
    without a second endpoint.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminStockMovementsThrottle]

    def put(self, request, pk):
        company, error = _company_context(
            request, CAP_INVENTORY_ADJUST, _LEGACY_INVENTORY_ADJUST_ROLES,
        )
        if error:
            return error
        transfer, not_found = _scoped_transfer(request, company, pk)
        if not_found:
            return not_found
        denied = _assert_both_ends(request, transfer)
        if denied:
            return denied

        ser = StockTransferItemWriteSerializer(data=request.data, many=True)
        ser.is_valid(raise_exception=True)
        lines = ser.validated_data

        products = {
            p.pk: p for p in Product.objects.filter(
                company=company, pk__in=[line['product'] for line in lines],
            )
        }
        missing = [line['product'] for line in lines if line['product'] not in products]
        if missing:
            return Response(
                {'detail': 'Producto no encontrado.'}, status=status.HTTP_404_NOT_FOUND,
            )

        try:
            with transaction.atomic():
                # The PUT is the whole list: lines not sent are removed, so a
                # partial payload cannot leave a stale line behind.
                keep = {line['product'] for line in lines if line['quantity'] > 0}
                transfer.items.exclude(product_id__in=keep).delete()
                for line in lines:
                    set_transfer_item(
                        transfer,
                        product=products[line['product']],
                        quantity=line['quantity'],
                    )
        except InventoryError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        transfer.refresh_from_db()
        return Response(StockTransferSerializer(transfer).data)


class _TransferActionView(APIView):
    """Shared plumbing for dispatch / receive / cancel."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminStockMovementsThrottle]
    action = None

    def post(self, request, pk):
        company, error = _company_context(
            request, CAP_INVENTORY_ADJUST, _LEGACY_INVENTORY_ADJUST_ROLES,
        )
        if error:
            return error
        transfer, not_found = _scoped_transfer(request, company, pk)
        if not_found:
            return not_found
        denied = _assert_both_ends(request, transfer)
        if denied:
            return denied

        try:
            self.action(transfer, actor=request.user, request=request)
        except InsufficientStockError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except TransferError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        transfer.refresh_from_db()
        return Response(StockTransferSerializer(transfer).data)


class AdminStockTransferDispatchView(_TransferActionView):
    """POST /api/admin/inventory/transfers/{pk}/dispatch/ — idempotent."""
    action = staticmethod(dispatch_transfer)


class AdminStockTransferReceiveView(_TransferActionView):
    """POST /api/admin/inventory/transfers/{pk}/receive/ — idempotent."""
    action = staticmethod(receive_transfer)


class AdminStockTransferCancelView(_TransferActionView):
    """POST /api/admin/inventory/transfers/{pk}/cancel/ — DRAFT only."""
    action = staticmethod(cancel_transfer)


# ---------------------------------------------------------------------------
# Physical counts
# ---------------------------------------------------------------------------

def _scoped_count(request, company, pk):
    """A count of `company` in a branch the caller reaches, or an error response."""
    branch_ids = list(visible_branches(request.user, company).values_list('pk', flat=True))
    count = (
        InventoryCount.objects
        .filter(company=company, branch_id__in=branch_ids)
        .select_related('branch', 'created_by')
        .prefetch_related('items__product')
        .filter(pk=pk)
        .first()
    )
    if count is None:
        return None, Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
    return count, None


class AdminInventoryCountListView(APIView):
    """
    GET  /api/admin/inventory/counts/?status=&branch=
    POST /api/admin/inventory/counts/ — open a count for one branch.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        if self.request.method == 'POST':
            return [AdminStockMovementsThrottle()]
        return [AdminInventoryReportsThrottle()]

    def get(self, request):
        company, branch, branches, error = _branch_context(
            request, CAP_INVENTORY_VIEW, _LEGACY_INVENTORY_VIEW_ROLES, allow_all=True,
        )
        if error:
            return error

        qs = (
            InventoryCount.objects
            .filter(company=company, branch__in=branches)
            .select_related('branch', 'created_by')
            .prefetch_related('items__product')
        )

        count_status = (request.query_params.get('status') or '').strip()
        if count_status:
            if count_status not in dict(InventoryCount.STATUS_CHOICES):
                return Response(
                    {'detail': 'Estado de recuento inválido.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            qs = qs.filter(status=count_status)

        page_qs, meta = _paginate(qs, request)
        return Response({
            'scope': _scope_payload(branch, branches),
            'results': InventoryCountSerializer(page_qs, many=True).data,
            **meta,
        })

    def post(self, request):
        company, error = _company_context(
            request, CAP_INVENTORY_ADJUST, _LEGACY_INVENTORY_ADJUST_ROLES,
        )
        if error:
            return error

        ser = InventoryCountCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        try:
            branch = resolve_branch_for_user(
                request.user, company, data.get('branch'), allow_all=False,
            )
        except NoBranchError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except BranchAccessError:
            return Response(
                {'detail': 'Sucursal no encontrada o sin acceso.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            count = create_inventory_count(
                company=company, branch=branch, actor=request.user,
                reason=data.get('reason', ''),
            )
        except InventoryError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        AdminAuditLog.log(
            actor=request.user,
            action='inventory_count_created',
            target_type='inventory_count',
            target_id=count.pk,
            metadata={'inventory_count_id': count.pk, 'branch_id': branch.pk},
            request=request,
            company=company,
        )
        return Response(
            InventoryCountSerializer(count).data, status=status.HTTP_201_CREATED,
        )


class AdminInventoryCountDetailView(APIView):
    """GET /api/admin/inventory/counts/{pk}/"""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminInventoryReportsThrottle]

    def get(self, request, pk):
        company, error = _company_context(
            request, CAP_INVENTORY_VIEW, _LEGACY_INVENTORY_VIEW_ROLES,
        )
        if error:
            return error
        count, not_found = _scoped_count(request, company, pk)
        if not_found:
            return not_found
        return Response(InventoryCountSerializer(count).data)


class AdminInventoryCountItemsView(APIView):
    """
    PUT /api/admin/inventory/counts/{pk}/items/ — record what was found.

    Additive per product: sending a subset updates those and leaves the rest
    alone. Unlike a transfer, a count is filled in over hours by people walking
    different aisles, and a whole-list PUT would let the last save wipe out
    everyone else's work.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminStockMovementsThrottle]

    def put(self, request, pk):
        company, error = _company_context(
            request, CAP_INVENTORY_ADJUST, _LEGACY_INVENTORY_ADJUST_ROLES,
        )
        if error:
            return error
        count, not_found = _scoped_count(request, company, pk)
        if not_found:
            return not_found

        ser = InventoryCountItemWriteSerializer(data=request.data, many=True)
        ser.is_valid(raise_exception=True)
        lines = ser.validated_data

        products = {
            p.pk: p for p in Product.objects.filter(
                company=company, pk__in=[line['product'] for line in lines],
            )
        }
        missing = [line['product'] for line in lines if line['product'] not in products]
        if missing:
            return Response(
                {'detail': 'Producto no encontrado.'}, status=status.HTTP_404_NOT_FOUND,
            )

        try:
            with transaction.atomic():
                for line in lines:
                    set_count_item(
                        count,
                        product=products[line['product']],
                        physical_quantity=line.get('physical_quantity'),
                        note=line.get('note', ''),
                    )
        except InventoryError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        count.refresh_from_db()
        return Response(InventoryCountSerializer(count).data)


class AdminInventoryCountApproveView(APIView):
    """POST /api/admin/inventory/counts/{pk}/approve/ — apply the differences."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminStockMovementsThrottle]

    def post(self, request, pk):
        company, error = _company_context(
            request, CAP_INVENTORY_ADJUST, _LEGACY_INVENTORY_ADJUST_ROLES,
        )
        if error:
            return error
        count, not_found = _scoped_count(request, company, pk)
        if not_found:
            return not_found

        try:
            movements = approve_inventory_count(
                count, actor=request.user, request=request,
            )
        except InventoryCountError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except InventoryError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        count.refresh_from_db()
        return Response({
            **InventoryCountSerializer(count).data,
            'movements': StockMovementSerializer(movements, many=True).data,
        })


class AdminInventoryCountCancelView(APIView):
    """POST /api/admin/inventory/counts/{pk}/cancel/ — never for approved counts."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminStockMovementsThrottle]

    def post(self, request, pk):
        company, error = _company_context(
            request, CAP_INVENTORY_ADJUST, _LEGACY_INVENTORY_ADJUST_ROLES,
        )
        if error:
            return error
        count, not_found = _scoped_count(request, company, pk)
        if not_found:
            return not_found

        try:
            cancel_inventory_count(count, actor=request.user, request=request)
        except InventoryError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        count.refresh_from_db()
        return Response(InventoryCountSerializer(count).data)


# ---------------------------------------------------------------------------
# Internal sales notes — NOT SUNAT electronic receipts
# ---------------------------------------------------------------------------

_SALES_NOTE_NOTICE = (
    'Documento interno de venta. No válido como comprobante electrónico SUNAT.'
)


def _sales_note_order(request, pk):
    """
    Resolve the order a sales-note request acts on, scoped to the caller's tenant.

    Authority mirrors the catalogue and order views: the company capability when
    the caller has company context, the legacy role when they reach the pilot
    through the bridge. Returns (order, error_response).

    Deliberately NOT branch-scoped. A sales note is a commercial document about
    a sale, not a stock operation; gating it on branch access would hide a
    company's own paperwork from its own sales staff.
    """
    company, error = _company_context(
        request, CAP_SALES_NOTES, _LEGACY_SALES_NOTES_ROLES,
    )
    if error:
        # The inventory message is wrong for this surface.
        detail = error.data.get('detail')
        if detail == _NO_PERMISSION:
            error.data['detail'] = 'No tienes permisos sobre las notas de venta.'
        return None, error

    # An order of another tenant answers exactly like one that does not exist.
    order = (
        Order.objects.filter(company=company)
        .prefetch_related('items__product')
        .filter(pk=pk)
        .first()
    )
    if order is None:
        return None, Response(
            {'detail': 'Orden no encontrada.'}, status=status.HTTP_404_NOT_FOUND
        )
    return order, None


class AdminOrderSalesNoteView(APIView):
    """
    GET  /api/admin/orders/{pk}/sales-note/ — fetch the note (404 if not issued).
    POST /api/admin/orders/{pk}/sales-note/ — issue it (idempotent).

    Issuing a note never touches payment state and never touches inventory.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminSalesNotesThrottle]

    def get(self, request, pk):
        order, error = _sales_note_order(request, pk)
        if error:
            return error

        note = SalesNote.objects.filter(order=order).select_related('order', 'created_by').first()
        if not note:
            return Response(
                {'detail': 'Esta orden todavía no tiene nota de venta interna.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response({**SalesNoteSerializer(note).data, 'notice': _SALES_NOTE_NOTICE})

    def post(self, request, pk):
        order, error = _sales_note_order(request, pk)
        if error:
            return error

        try:
            note, created = get_or_create_sales_note(order, actor=request.user)
        except SalesNoteError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        if created:
            AdminAuditLog.log(
                actor=request.user,
                action='sales_note_created',
                target_type='sales_note',
                target_id=note.pk,
                metadata={
                    'sales_note_id': note.pk,
                    'sales_note_number': note.number,
                    'order_id': order.pk,
                    # Phase 2E: WHICH SERIES handed out the number, and the
                    # ordinal inside it. The display string alone cannot say
                    # that once two companies can both show NV-000001.
                    'sequence_id': note.sequence_id,
                    'sequence_value': note.sequence_value,
                    'branch_id': note.sequence.branch_id if note.sequence_id else None,
                },
                request=request,
                company=order.company,
            )

        return Response(
            {**SalesNoteSerializer(note).data, 'notice': _SALES_NOTE_NOTICE},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class AdminOrderSalesNotePdfView(APIView):
    """GET /api/admin/orders/{pk}/sales-note/pdf/ — download the internal note PDF."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminSalesNotesThrottle]

    def get(self, request, pk):
        order, error = _sales_note_order(request, pk)
        if error:
            return error

        note = SalesNote.objects.filter(order=order).select_related('order').first()
        if not note:
            return Response(
                {'detail': 'Esta orden todavía no tiene nota de venta interna.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            pdf_bytes = generate_sales_note_pdf(note)
        except SalesNoteError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            logger.exception('Sales note PDF generation failed for note %s', note.pk)
            return Response(
                {'detail': 'Error al generar el PDF. Inténtelo nuevamente.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        AdminAuditLog.log(
            actor=request.user,
            action='sales_note_pdf_downloaded',
            target_type='sales_note',
            target_id=note.pk,
            metadata={
                'sales_note_id': note.pk,
                'sales_note_number': note.number,
                'order_id': order.pk,
            },
            request=request,
            company=order.company,
        )

        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = (
            f'attachment; filename="{get_sales_note_filename(note)}"'
        )
        response['Cache-Control'] = 'no-store'
        return response
