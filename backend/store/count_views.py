"""
Physical counts — `/api/admin/inventory/counts/…` (Phase 2D, MIGRATION-C).

Moved here from `inventory_views.py` when the count grew its own engine
(docs/adr-migration-count.md). Authority is resolved exactly as for the rest of
the inventory — the same company context, the same legacy bridge — through the
helpers of `inventory_views`.

  GET  counts/                      list (inventory.view)
  POST counts/                      open: {branch, kind, source_job?, reason}
  GET  counts/{id}/                 detail, lines described (inventory.view)
  GET  counts/{id}/products/?search= the count's own search, by name or code
  PUT  counts/{id}/items/           totals [{product, physical_quantity, entry?, entry_note?}]
  POST counts/{id}/scan/            {code|product, mode, expected?, step?}
  POST counts/{id}/recount/         {products}
  POST counts/{id}/exclude/         {products, reason}
  POST counts/{id}/submit/          counting → review
  POST counts/{id}/approve/         inventory.counts.approve
  POST counts/{id}/cancel/

The branch is resolved here from the caller's grants; a count of a branch the
caller cannot reach — or of another company — answers 404. Errors carry a
machine `code` (`expected_mismatch`, `not_found`, `serialized_product`,
`recount_required`, `entry_required`, `approval_blocked`) next to `detail`.
"""

from django.db import transaction
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import count_services
from .inventory_services import (
    CountApprovalBlocked,
    CountEntryRequired,
    InventoryError,
    approve_inventory_count,
    cancel_inventory_count,
    create_inventory_count,
    set_count_item,
)
from .inventory_views import (
    CAP_INVENTORY_ADJUST,
    CAP_INVENTORY_VIEW,
    _LEGACY_INVENTORY_ADJUST_ROLES,
    _LEGACY_INVENTORY_VIEW_ROLES,
    _NOT_FOUND,
    _branch_context,
    _company_context,
    _paginate,
    _scope_payload,
    may_view_costs,
)
from .models import AdminAuditLog, BulkImportJob, InventoryCount, Product
from .serializers import (
    InventoryCountCreateSerializer,
    InventoryCountItemWriteSerializer,
    InventoryCountLinesSerializer,
    InventoryCountScanSerializer,
    InventoryCountSerializer,
    StockMovementSerializer,
)
from .tenancy import BranchAccessError, NoBranchError, resolve_branch_for_user, visible_branches
from .throttles import AdminInventoryReportsThrottle, AdminStockMovementsThrottle

CAP_COUNTS_APPROVE = 'inventory.counts.approve'
#: Who approved before the capability existed (the legacy bridge keeps it).
_LEGACY_COUNTS_APPROVE_ROLES = _LEGACY_INVENTORY_ADJUST_ROLES


def _error(exc, http_status, **extra):
    body = {'detail': str(exc), **extra}
    code = getattr(exc, 'code', None)
    if code:
        body['code'] = code
    return Response(body, status=http_status)


def _scoped_count(request, company, pk):
    """A count of `company` in a branch the caller reaches, or an error response."""
    branch_ids = list(visible_branches(request.user, company).values_list('pk', flat=True))
    count = (
        InventoryCount.objects
        .filter(company=company, branch_id__in=branch_ids)
        .select_related('branch', 'company', 'created_by', 'submitted_by')
        .prefetch_related('items__product', 'items__counted_by')
        .filter(pk=pk)
        .first()
    )
    if count is None:
        return None, Response({'detail': _NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
    return count, None


def _fresh(request, count):
    count, _ = _scoped_count(request, count.company, count.pk)
    return count


def _detail(request, count, **extra):
    data = InventoryCountSerializer(
        _fresh(request, count), context={'detailed': True, 'costs': may_view_costs(request)},
    ).data
    return {**data, **extra}


def _line(request, count, item):
    item.refresh_from_db()
    return count_services.describe_lines(count, [item], costs=may_view_costs(request))[0]


def _is_approver(request, company):
    _company, error = _company_context(request, CAP_COUNTS_APPROVE, _LEGACY_COUNTS_APPROVE_ROLES)
    return error is None


class _CountView(APIView):
    """Resolve company → authority → the count, or answer the error."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminStockMovementsThrottle]
    capability = CAP_INVENTORY_ADJUST
    legacy_roles = _LEGACY_INVENTORY_ADJUST_ROLES

    def resolve(self, request, pk):
        company, error = _company_context(request, self.capability, self.legacy_roles)
        if error:
            return None, None, error
        count, not_found = _scoped_count(request, company, pk)
        if not_found:
            return None, None, not_found
        return company, count, None


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
            .select_related('branch', 'created_by', 'submitted_by')
            .prefetch_related('items__product', 'items__counted_by')
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

        source_job = None
        if data.get('source_job'):
            source_job = BulkImportJob.objects.filter(company=company, pk=data['source_job']).first()
            if source_job is None:
                return Response({'detail': 'Importación no encontrada.'}, status=status.HTTP_404_NOT_FOUND)

        kind = data.get('kind') or InventoryCount.KIND_REGULAR
        try:
            if kind == InventoryCount.KIND_MIGRATION:
                count = count_services.create_migration_count(
                    company=company, branch=branch, actor=request.user,
                    reason=data.get('reason', ''), source_job=source_job,
                )
            else:
                if source_job is not None:
                    return Response(
                        {'detail': 'Sólo un recuento de migración se abre sobre una importación.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                count = create_inventory_count(
                    company=company, branch=branch, actor=request.user,
                    reason=data.get('reason', ''),
                )
        except InventoryError as exc:
            return _error(exc, status.HTTP_400_BAD_REQUEST)

        AdminAuditLog.log(
            actor=request.user,
            action='inventory_count_created',
            target_type='inventory_count',
            target_id=count.pk,
            metadata={
                'inventory_count_id': count.pk, 'branch_id': branch.pk, 'kind': count.kind,
                'source_job_id': count.source_job_id,
            },
            request=request,
            company=company,
        )
        return Response(_detail(request, count), status=status.HTTP_201_CREATED)


class AdminInventoryCountDetailView(_CountView):
    """GET /api/admin/inventory/counts/{pk}/"""

    throttle_classes = [AdminInventoryReportsThrottle]
    capability = CAP_INVENTORY_VIEW
    legacy_roles = _LEGACY_INVENTORY_VIEW_ROLES

    def get(self, request, pk):
        _company, count, error = self.resolve(request, pk)
        if error:
            return error
        return Response(_detail(request, count))


class AdminInventoryCountProductsView(_CountView):
    """GET /api/admin/inventory/counts/{pk}/products/?search= — by name or code."""

    throttle_classes = [AdminInventoryReportsThrottle]
    capability = CAP_INVENTORY_VIEW
    legacy_roles = _LEGACY_INVENTORY_VIEW_ROLES

    def get(self, request, pk):
        _company, count, error = self.resolve(request, pk)
        if error:
            return error
        return Response({'results': count_services.search_products(count, request.query_params.get('search', ''))})


class AdminInventoryCountItemsView(_CountView):
    """
    PUT /api/admin/inventory/counts/{pk}/items/ — record what was found.

    Additive per product: sending a subset updates those and leaves the rest
    alone. Unlike a transfer, a count is filled in over hours by people walking
    different aisles, and a whole-list PUT would let the last save wipe out
    everyone else's work.

    Each value is a TOTAL. A line whose stock moved since it was counted needs
    `entry` (`recount` | `correction`) on a migration count: without it the whole
    request is refused with 409 `entry_required` and the products that need it.
    """

    def put(self, request, pk):
        company, count, error = self.resolve(request, pk)
        if error:
            return error

        ser = InventoryCountItemWriteSerializer(data=request.data, many=True)
        ser.is_valid(raise_exception=True)
        # One lock order for every writer: by product id.
        lines = sorted(ser.validated_data, key=lambda line: line['product'])

        products = {
            p.pk: p for p in Product.objects.filter(
                company=company, pk__in=[line['product'] for line in lines],
            )
        }
        if any(line['product'] not in products for line in lines):
            return Response(
                {'detail': 'Producto no encontrado.', 'code': 'not_found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        needing, message = [], ''
        try:
            with transaction.atomic():
                for line in lines:
                    try:
                        with transaction.atomic():
                            set_count_item(
                                count,
                                product=products[line['product']],
                                physical_quantity=line.get('physical_quantity'),
                                note=line.get('note', ''),
                                entry=line.get('entry'),
                                entry_note=line.get('entry_note', ''),
                                actor=request.user,
                                request=request,
                            )
                    except CountEntryRequired as exc:
                        needing.extend(exc.products)
                        message = str(exc)
                if needing:
                    transaction.set_rollback(True)
        except InventoryError as exc:
            return _error(exc, status.HTTP_400_BAD_REQUEST)
        if needing:
            # Nothing of this request was kept: it is all or nothing.
            if len(needing) > 1:
                message = ('Estos productos tuvieron movimientos de stock desde que se contaron: indica '
                           'para cada uno si es un reconteo o la corrección de un error de tecleo.')
            return Response(
                {'code': CountEntryRequired.code, 'detail': message, 'products': needing},
                status=status.HTTP_409_CONFLICT,
            )

        return Response(_detail(request, count))


class AdminInventoryCountScanView(_CountView):
    """POST /api/admin/inventory/counts/{pk}/scan/ — `lookup` or `increment`."""

    def post(self, request, pk):
        _company, count, error = self.resolve(request, pk)
        if error:
            return error
        ser = InventoryCountScanSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        try:
            result, item = count_services.scan(
                count, mode=data['mode'], code=data.get('code') or None, product=data.get('product'),
                expected=data.get('expected'), step=data.get('step', 1), actor=request.user,
            )
        except count_services.CountSerializedProduct as exc:
            product = {'id': exc.product.pk, 'name': exc.product.name} if exc.product else None
            return _error(exc, status.HTTP_400_BAD_REQUEST, product=product)
        except count_services.CountCodeNotFound as exc:
            return _error(exc, status.HTTP_404_NOT_FOUND)
        except (count_services.CountExpectedMismatch, count_services.CountRecountRequired) as exc:
            return _error(exc, status.HTTP_409_CONFLICT, line=_line(request, count, exc.item))
        except InventoryError as exc:
            return _error(exc, status.HTTP_400_BAD_REQUEST)
        return Response({'result': result, 'line': _line(request, count, item)})


class _LinesActionView(_CountView):
    """Recount / exclude: a counter while counting; in `review`, only an approver."""

    def post(self, request, pk):
        company, count, error = self.resolve(request, pk)
        if error:
            return error
        ser = InventoryCountLinesSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            self.act(request, count, ser.validated_data, approver=_is_approver(request, company))
        except count_services.CountPermissionDenied as exc:
            return _error(exc, status.HTTP_403_FORBIDDEN)
        except count_services.CountCodeNotFound as exc:
            return _error(exc, status.HTTP_404_NOT_FOUND)
        except InventoryError as exc:
            return _error(exc, status.HTTP_400_BAD_REQUEST)
        return Response(_detail(request, count))


class AdminInventoryCountRecountView(_LinesActionView):
    """POST /api/admin/inventory/counts/{pk}/recount/ — back to «not counted»."""

    def act(self, request, count, data, *, approver):
        count_services.recount(count, products=data['products'], actor=request.user,
                               approver=approver, request=request)


class AdminInventoryCountExcludeView(_LinesActionView):
    """POST /api/admin/inventory/counts/{pk}/exclude/ — out of the approval, with a reason."""

    def act(self, request, count, data, *, approver):
        count_services.exclude(count, products=data['products'], reason=data.get('reason', ''),
                               actor=request.user, approver=approver, request=request)


class AdminInventoryCountSubmitView(_CountView):
    """POST /api/admin/inventory/counts/{pk}/submit/ — counting → review. Moves no stock."""

    def post(self, request, pk):
        _company, count, error = self.resolve(request, pk)
        if error:
            return error
        try:
            count_services.submit_for_review(count, actor=request.user, request=request)
        except InventoryError as exc:
            return _error(exc, status.HTTP_400_BAD_REQUEST)
        return Response(_detail(request, count))


class AdminInventoryCountApproveView(_CountView):
    """POST /api/admin/inventory/counts/{pk}/approve/ — apply the differences."""

    capability = CAP_COUNTS_APPROVE
    legacy_roles = _LEGACY_COUNTS_APPROVE_ROLES

    def post(self, request, pk):
        _company, count, error = self.resolve(request, pk)
        if error:
            return error
        try:
            movements = approve_inventory_count(count, actor=request.user, request=request)
        except CountApprovalBlocked as exc:
            return _error(exc, status.HTTP_400_BAD_REQUEST, problems=exc.problems)
        except InventoryError as exc:
            return _error(exc, status.HTTP_400_BAD_REQUEST)
        return Response(_detail(
            request, count, movements=StockMovementSerializer(movements, many=True).data,
        ))


class AdminInventoryCountCancelView(_CountView):
    """POST /api/admin/inventory/counts/{pk}/cancel/ — never for approved counts."""

    def post(self, request, pk):
        _company, count, error = self.resolve(request, pk)
        if error:
            return error
        try:
            cancel_inventory_count(count, actor=request.user, request=request)
        except InventoryError as exc:
            return _error(exc, status.HTTP_400_BAD_REQUEST)
        return Response(_detail(request, count))
