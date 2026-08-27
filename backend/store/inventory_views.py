"""
Admin inventory + internal sales note endpoints — Phase 6.0.

Everything here is admin-panel only (session cookie auth + CSRF, no Bearer).
Stock is never mutated in this module directly: all writes go through
store.inventory_services so stock and the Kardex stay in one transaction.

Roles (see store.permissions):
  - inventory / admin / superadmin → inventory dashboards, Kardex, movements
  - sales / inventory / admin / superadmin → sales reports
  - sales / admin / superadmin → internal sales notes
  - customer / technician / anonymous → no access
"""

import logging
from datetime import datetime

from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .inventory_services import (
    DEFAULT_LOW_STOCK_THRESHOLD,
    InsufficientStockError,
    InventoryError,
    apply_manual_stock_movement,
    get_best_selling_products,
    get_high_stock_products,
    get_inventory_summary,
    get_low_stock_products,
    get_products_without_movement,
    get_stock_card,
)
from .models import AdminAuditLog, Order, Product, SalesNote, StockMovement, UserProfile
from .tenancy import has_capability, resolve_catalog_company
from .permissions import (
    CanManageSalesNotes,
    CanManageStockMovements,
    CanViewInventoryReports,
    CanViewSalesReports,
)
from .sales_note_services import (
    SalesNoteError,
    generate_sales_note_pdf,
    get_or_create_sales_note,
    get_sales_note_filename,
)
from .serializers import (
    InventoryProductSerializer,
    SalesNoteSerializer,
    StockMovementCreateSerializer,
    StockMovementSerializer,
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


# ---------------------------------------------------------------------------
# Inventory dashboards and Kardex
# ---------------------------------------------------------------------------

class AdminInventorySummaryView(APIView):
    """GET /api/admin/inventory/summary/ — headline counters for the dashboard."""

    permission_classes = [permissions.IsAuthenticated, CanViewInventoryReports]
    throttle_classes = [AdminInventoryReportsThrottle]

    def get(self, request):
        threshold = _int_param(
            request, 'threshold', DEFAULT_LOW_STOCK_THRESHOLD, minimum=0, maximum=10_000
        )
        return Response(get_inventory_summary(low_stock_threshold=threshold))


class AdminStockMovementListView(APIView):
    """
    GET  /api/admin/inventory/movements/ — paginated Kardex across all products.
    POST /api/admin/inventory/movements/ — register a MANUAL entry or exit.
    """

    def get_permissions(self):
        if self.request.method == 'POST':
            return [permissions.IsAuthenticated(), CanManageStockMovements()]
        return [permissions.IsAuthenticated(), CanViewInventoryReports()]

    def get_throttles(self):
        if self.request.method == 'POST':
            return [AdminStockMovementsThrottle()]
        return [AdminInventoryReportsThrottle()]

    def get(self, request):
        qs = StockMovement.objects.select_related('product', 'actor', 'order')

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
            'results': StockMovementSerializer(page_qs, many=True).data,
            **meta,
        })

    def post(self, request):
        ser = StockMovementCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        try:
            movement = apply_manual_stock_movement(
                product_id=data['product_id'],
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
    """GET /api/admin/inventory/low-stock/?threshold=5"""

    permission_classes = [permissions.IsAuthenticated, CanViewInventoryReports]
    throttle_classes = [AdminInventoryReportsThrottle]

    def get(self, request):
        threshold = _int_param(
            request, 'threshold', DEFAULT_LOW_STOCK_THRESHOLD, minimum=0, maximum=10_000
        )
        limit = _int_param(request, 'limit', 50, minimum=1)
        products = get_low_stock_products(threshold=threshold, limit=limit)
        return Response({
            'threshold': threshold,
            'count': len(products),
            'results': InventoryProductSerializer(products, many=True).data,
        })


class AdminHighStockView(APIView):
    """GET /api/admin/inventory/high-stock/ — most units on hand first."""

    permission_classes = [permissions.IsAuthenticated, CanViewInventoryReports]
    throttle_classes = [AdminInventoryReportsThrottle]

    def get(self, request):
        limit = _int_param(request, 'limit', 20, minimum=1)
        products = get_high_stock_products(limit=limit)
        return Response({
            'count': len(products),
            'results': InventoryProductSerializer(products, many=True).data,
        })


class AdminBestSellingView(APIView):
    """GET /api/admin/inventory/best-selling/?date_from&date_to&limit"""

    permission_classes = [permissions.IsAuthenticated, CanViewSalesReports]
    throttle_classes = [AdminInventoryReportsThrottle]

    def get(self, request):
        limit = _int_param(request, 'limit', 10, minimum=1, maximum=100)
        rows = get_best_selling_products(
            date_from=_date_param(request, 'date_from'),
            date_to=_date_param(request, 'date_to'),
            limit=limit,
        )
        return Response({'count': len(rows), 'results': rows})


class AdminStaleStockView(APIView):
    """GET /api/admin/inventory/no-movement/?days=60 — products with no Kardex activity."""

    permission_classes = [permissions.IsAuthenticated, CanViewInventoryReports]
    throttle_classes = [AdminInventoryReportsThrottle]

    def get(self, request):
        days = _int_param(request, 'days', 60, minimum=1, maximum=3650)
        limit = _int_param(request, 'limit', 50, minimum=1)
        products = get_products_without_movement(days=days, limit=limit)
        return Response({
            'days': days,
            'count': len(products),
            'results': InventoryProductSerializer(products, many=True).data,
        })


class AdminProductStockCardView(APIView):
    """GET /api/admin/products/{pk}/stock-card/ — Kardex for one product."""

    permission_classes = [permissions.IsAuthenticated, CanViewInventoryReports]
    throttle_classes = [AdminInventoryReportsThrottle]

    def get(self, request, pk):
        # Phase 2B: Product now has an owner, so the Kardex of another tenant's
        # product must not be readable even though StockMovement itself is not
        # tenantised yet. A foreign product answers like a missing one.
        company, _source = resolve_catalog_company(request.user)
        products = Product.objects.all() if company is None else Product.objects.filter(
            company=company)
        try:
            product = products.get(pk=pk)
        except Product.DoesNotExist:
            return Response(
                {'detail': 'Producto no encontrado.'}, status=status.HTTP_404_NOT_FOUND
            )

        limit = _int_param(request, 'limit', 200, minimum=1)
        movements = get_stock_card(product, limit=limit)

        return Response({
            'product': InventoryProductSerializer(product).data,
            'current_stock': product.inventory,
            'movements': StockMovementSerializer(movements, many=True).data,
        })


# ---------------------------------------------------------------------------
# Internal sales notes — NOT SUNAT electronic receipts
# ---------------------------------------------------------------------------

_SALES_NOTE_NOTICE = (
    'Documento interno de venta. No válido como comprobante electrónico SUNAT.'
)

CAP_SALES_NOTES = 'sales.notes.manage'
_LEGACY_SALES_NOTES_ROLES = frozenset([
    UserProfile.ROLE_SALES, UserProfile.ROLE_ADMIN, UserProfile.ROLE_SUPERADMIN,
])


def _sales_note_order(request, pk):
    """
    Resolve the order a sales-note request acts on, scoped to the caller's tenant.

    Authority mirrors the catalogue and order views: the company capability when
    the caller has company context, the legacy role when they reach the pilot
    through the bridge. Returns (order, error_response).
    """
    from .permissions import get_user_role
    from .tenancy import CATALOG_SOURCE_LEGACY

    company, source = resolve_catalog_company(request.user)
    if company is None:
        return None, Response(
            {'detail': 'No tienes acceso a los datos de ninguna empresa.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    if source == CATALOG_SOURCE_LEGACY:
        if get_user_role(request.user) not in _LEGACY_SALES_NOTES_ROLES:
            return None, Response(
                {'detail': 'No tienes permisos sobre las notas de venta.'},
                status=status.HTTP_403_FORBIDDEN,
            )
    elif not has_capability(request.user, company, CAP_SALES_NOTES):
        return None, Response(
            {'detail': 'No tienes permisos sobre las notas de venta.'},
            status=status.HTTP_403_FORBIDDEN,
        )

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
                },
                request=request,
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
        )

        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = (
            f'attachment; filename="{get_sales_note_filename(note)}"'
        )
        response['Cache-Control'] = 'no-store'
        return response
