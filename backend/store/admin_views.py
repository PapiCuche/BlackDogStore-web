from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F, Q
from django.shortcuts import get_object_or_404
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AdminAuditLog, Category, Order, OrderItem, Product, UserProfile
from .permissions import (
    CanManageInventory, CanManageOrderFulfillment, CanManageProducts,
    CanViewAdminOrders, CanViewAdminProducts,
    IsAdminRole, IsSuperAdminRole, get_user_role,
)
from .serializers import (
    AdminCategoryWriteSerializer, AdminInventoryAdjustSerializer,
    AdminOrderDetailSerializer, AdminOrderFulfillmentSerializer, AdminOrderListSerializer,
    AdminProductSerializer, AdminProductWriteSerializer,
    CategorySerializer,
)
from .throttles import (
    AdminAuditLogsThrottle, AdminCategoriesThrottle, AdminInventoryAdjustThrottle,
    AdminOrdersThrottle, AdminOrderStatusChangeThrottle,
    AdminProductsThrottle, AdminProductWriteThrottle, AdminRoleChangeThrottle,
    AdminUsersThrottle,
)

User = get_user_model()

_VALID_ROLES = {r[0] for r in UserProfile.ROLE_CHOICES}
_DEFAULT_PAGE_SIZE = 25
_MAX_PAGE_SIZE = 100


def _paginate(queryset, request):
    """Simple page-based pagination. Returns (sliced_qs, meta_dict)."""
    try:
        page = max(1, int(request.query_params.get('page', 1)))
    except (ValueError, TypeError):
        page = 1
    try:
        page_size = min(_MAX_PAGE_SIZE, max(1, int(request.query_params.get('page_size', _DEFAULT_PAGE_SIZE))))
    except (ValueError, TypeError):
        page_size = _DEFAULT_PAGE_SIZE

    total = queryset.count()
    offset = (page - 1) * page_size
    return (
        queryset[offset: offset + page_size],
        {'count': total, 'page': page, 'page_size': page_size},
    )


class AdminUserListView(APIView):
    """GET /api/admin/users/ — paginated user list with search and role filter. Admin+ only."""
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]
    throttle_classes = [AdminUsersThrottle]

    def get(self, request):
        users = User.objects.select_related('profile').order_by('id')

        search = request.query_params.get('search', '').strip()
        if search:
            users = users.filter(
                Q(username__icontains=search) | Q(email__icontains=search)
            )

        role_filter = request.query_params.get('role', '').strip()
        if role_filter:
            if role_filter == UserProfile.ROLE_SUPERADMIN:
                users = users.filter(
                    Q(is_superuser=True) | Q(profile__role=UserProfile.ROLE_SUPERADMIN)
                )
            elif role_filter in _VALID_ROLES:
                users = users.filter(profile__role=role_filter, is_superuser=False)

        page_qs, meta = _paginate(users, request)
        results = [
            {
                'id': u.id,
                'username': u.username,
                'email': u.email,
                'is_active': u.is_active,
                'role': get_user_role(u),
                'date_joined': u.date_joined,
            }
            for u in page_qs
        ]
        return Response({**meta, 'results': results})


class AdminUserRoleView(APIView):
    """PATCH /api/admin/users/{pk}/role/ — change role. Superadmin only."""
    permission_classes = [permissions.IsAuthenticated, IsSuperAdminRole]
    throttle_classes = [AdminRoleChangeThrottle]

    def patch(self, request, pk):
        if request.user.pk == pk:
            return Response(
                {'detail': 'No puedes cambiar tu propio rol.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        new_role = (request.data.get('role') or '').strip()
        if new_role not in _VALID_ROLES:
            return Response(
                {'detail': f'Rol inválido. Opciones: {", ".join(sorted(_VALID_ROLES))}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            target = User.objects.get(pk=pk)
        except User.DoesNotExist:
            return Response({'detail': 'Usuario no encontrado.'}, status=status.HTTP_404_NOT_FOUND)

        profile, _ = UserProfile.objects.get_or_create(
            user=target,
            defaults={'role': UserProfile.ROLE_CUSTOMER},
        )
        old_role = profile.role
        profile.role = new_role
        profile.save(update_fields=['role', 'updated_at'])

        AdminAuditLog.log(
            actor=request.user,
            action='role_change',
            target_type='user',
            target_id=target.pk,
            metadata={'old_role': old_role, 'new_role': new_role, 'target_username': target.username},
            request=request,
        )

        return Response({
            'id': target.id,
            'username': target.username,
            'role': new_role,
            'detail': f'Rol actualizado de {old_role} a {new_role}.',
        })


class AdminAuditLogListView(APIView):
    """GET /api/admin/audit-logs/ — paginated audit log with optional filters. Admin+ only."""
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]
    throttle_classes = [AdminAuditLogsThrottle]

    def get(self, request):
        logs = AdminAuditLog.objects.select_related('actor').order_by('-created_at')

        action_filter = request.query_params.get('action', '').strip()
        if action_filter:
            logs = logs.filter(action__icontains=action_filter)

        actor_filter = request.query_params.get('actor', '').strip()
        if actor_filter:
            logs = logs.filter(actor__username__icontains=actor_filter)

        target_type_filter = request.query_params.get('target_type', '').strip()
        if target_type_filter:
            logs = logs.filter(target_type=target_type_filter)

        page_qs, meta = _paginate(logs, request)
        results = [
            {
                'id': log.id,
                'actor': log.actor.username if log.actor else None,
                'action': log.action,
                'target_type': log.target_type,
                'target_id': log.target_id,
                'metadata': log.metadata,
                'ip_address': log.ip_address,
                'created_at': log.created_at,
            }
            for log in page_qs
        ]
        return Response({**meta, 'results': results})


# ---------------------------------------------------------------------------
# Phase 3.2 — Admin product & category views
# ---------------------------------------------------------------------------

_LOW_STOCK_THRESHOLD = 5

_AUDITABLE_PRODUCT_FIELDS = ('name', 'slug', 'description', 'price', 'inventory', 'image_url', 'category_id', 'is_active')


def _product_snapshot(product):
    def _fmt(field, value):
        if value is None:
            return None
        if isinstance(value, Decimal):
            return str(value)
        if field in ('description', 'image_url') and isinstance(value, str) and len(value) > 200:
            return value[:200] + '…'
        return value
    return {f: _fmt(f, getattr(product, f)) for f in _AUDITABLE_PRODUCT_FIELDS}


class AdminProductListView(APIView):
    """
    GET  /api/admin/products/ — list with search, filters, pagination. CanViewAdminProducts.
    POST /api/admin/products/ — create product. CanManageProducts.
    """

    def get_permissions(self):
        if self.request.method == 'POST':
            return [permissions.IsAuthenticated(), CanManageProducts()]
        return [permissions.IsAuthenticated(), CanViewAdminProducts()]

    def get_throttles(self):
        if self.request.method == 'POST':
            return [AdminProductWriteThrottle()]
        return [AdminProductsThrottle()]

    def get(self, request):
        products = Product.objects.select_related('category').order_by('-created_at')

        search = request.query_params.get('search', '').strip()
        if search:
            products = products.filter(Q(name__icontains=search) | Q(slug__icontains=search)).distinct()

        category_id = request.query_params.get('category', '').strip()
        if category_id:
            try:
                products = products.filter(category_id=int(category_id))
            except (ValueError, TypeError):
                pass

        is_active_param = request.query_params.get('is_active', '').strip().lower()
        if is_active_param == 'true':
            products = products.filter(is_active=True)
        elif is_active_param == 'false':
            products = products.filter(is_active=False)

        stock_param = request.query_params.get('stock', '').strip().lower()
        if stock_param == 'in_stock':
            products = products.filter(inventory__gt=0)
        elif stock_param == 'out_of_stock':
            products = products.filter(inventory=0)
        elif stock_param == 'low_stock':
            products = products.filter(inventory__gt=0, inventory__lte=_LOW_STOCK_THRESHOLD)

        page_qs, meta = _paginate(products, request)
        return Response({**meta, 'results': AdminProductSerializer(page_qs, many=True).data})

    def post(self, request):
        ser = AdminProductWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        product = ser.save()
        AdminAuditLog.log(
            actor=request.user,
            action='product_created',
            target_type='product',
            target_id=product.pk,
            metadata={
                'product_name': product.name,
                'slug': product.slug,
                'price': str(product.price),
                'inventory': product.inventory,
                'category_id': product.category_id,
                'is_active': product.is_active,
            },
            request=request,
        )
        return Response(AdminProductSerializer(product).data, status=status.HTTP_201_CREATED)


class AdminProductDetailView(APIView):
    """
    GET   /api/admin/products/{pk}/ — detail. CanViewAdminProducts.
    PATCH /api/admin/products/{pk}/ — edit. CanManageProducts.
    """

    def get_permissions(self):
        if self.request.method == 'PATCH':
            return [permissions.IsAuthenticated(), CanManageProducts()]
        return [permissions.IsAuthenticated(), CanViewAdminProducts()]

    def get_throttles(self):
        if self.request.method == 'PATCH':
            return [AdminProductWriteThrottle()]
        return [AdminProductsThrottle()]

    def get(self, request, pk):
        product = get_object_or_404(Product.objects.select_related('category'), pk=pk)
        return Response(AdminProductSerializer(product).data)

    def patch(self, request, pk):
        product = get_object_or_404(Product.objects.select_related('category'), pk=pk)
        old = _product_snapshot(product)

        ser = AdminProductWriteSerializer(product, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        product = ser.save()
        new = _product_snapshot(product)

        changed = {
            f: {'old': old[f], 'new': new[f]}
            for f in _AUDITABLE_PRODUCT_FIELDS
            if old[f] != new[f]
        }

        if changed:
            was_active, is_now_active = old.get('is_active'), new.get('is_active')
            if was_active is True and is_now_active is False:
                action = 'product_deactivated'
            elif was_active is False and is_now_active is True:
                action = 'product_reactivated'
            else:
                action = 'product_updated'

            AdminAuditLog.log(
                actor=request.user,
                action=action,
                target_type='product',
                target_id=product.pk,
                metadata={'product_name': product.name, 'product_id': product.pk, 'changed_fields': changed},
                request=request,
            )

        return Response(AdminProductSerializer(product).data)


class AdminProductInventoryAdjustView(APIView):
    """
    POST /api/admin/products/{pk}/inventory-adjust/
    Body: {delta: int (nonzero), reason: str}
    Allowed roles: inventory, admin, superadmin.
    """
    permission_classes = [permissions.IsAuthenticated, CanManageInventory]
    throttle_classes = [AdminInventoryAdjustThrottle]

    def post(self, request, pk):
        try:
            return self._do_post(request, pk)
        except Product.DoesNotExist:
            return Response({'detail': 'Producto no encontrado.'}, status=status.HTTP_404_NOT_FOUND)

    def _do_post(self, request, pk):
        ser = AdminInventoryAdjustSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        delta = ser.validated_data['delta']
        reason = ser.validated_data['reason']

        with transaction.atomic():
            try:
                product = Product.objects.select_for_update().get(pk=pk)
            except Product.DoesNotExist:
                raise

            new_inventory = product.inventory + delta
            if new_inventory < 0:
                return Response(
                    {'detail': f'Stock insuficiente. Stock actual: {product.inventory}, delta: {delta}.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            old_inventory = product.inventory
            Product.objects.filter(pk=pk).update(inventory=F('inventory') + delta)
            product.refresh_from_db()

        AdminAuditLog.log(
            actor=request.user,
            action='product_inventory_adjusted',
            target_type='product',
            target_id=product.pk,
            metadata={
                'product_name': product.name,
                'product_id': product.pk,
                'old_inventory': old_inventory,
                'delta': delta,
                'new_inventory': product.inventory,
                'reason': reason,
            },
            request=request,
        )
        return Response(AdminProductSerializer(product).data)


class AdminCategoryListView(APIView):
    """
    GET  /api/admin/categories/ — list all. CanViewAdminProducts.
    POST /api/admin/categories/ — create. CanManageProducts.
    """

    def get_permissions(self):
        if self.request.method == 'POST':
            return [permissions.IsAuthenticated(), CanManageProducts()]
        return [permissions.IsAuthenticated(), CanViewAdminProducts()]

    def get_throttles(self):
        return [AdminCategoriesThrottle()]

    def get(self, request):
        categories = Category.objects.order_by('name')
        return Response(CategorySerializer(categories, many=True).data)

    def post(self, request):
        ser = AdminCategoryWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        category = ser.save()
        AdminAuditLog.log(
            actor=request.user,
            action='category_created',
            target_type='category',
            target_id=category.pk,
            metadata={'name': category.name, 'slug': category.slug},
            request=request,
        )
        return Response(CategorySerializer(category).data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Phase 3.3 — Admin order views
# ---------------------------------------------------------------------------

# inventory role can only set these operational statuses
_INVENTORY_ALLOWED_FULFILLMENT = frozenset([
    Order.FulfillmentStatus.PREPARING,
    Order.FulfillmentStatus.READY_FOR_PICKUP,
    Order.FulfillmentStatus.SHIPPED,
    Order.FulfillmentStatus.DELIVERED,
])


class AdminOrderListView(APIView):
    """
    GET /api/admin/orders/ — paginated order list with filters.
    Roles: inventory, sales, admin, superadmin.
    """
    permission_classes = [permissions.IsAuthenticated, CanViewAdminOrders]
    throttle_classes = [AdminOrdersThrottle]

    def get(self, request):
        from datetime import datetime
        orders = Order.objects.select_related('user').prefetch_related('items').order_by('-created_at')

        search = request.query_params.get('search', '').strip()
        if search:
            id_match = int(search) if search.isdigit() else -1
            orders = orders.filter(
                Q(customer_name__icontains=search) |
                Q(customer_email__icontains=search) |
                Q(id=id_match)
            ).distinct()

        status_filter = request.query_params.get('status', '').strip()
        if status_filter:
            orders = orders.filter(status=status_filter)

        fulfillment_filter = request.query_params.get('fulfillment_status', '').strip()
        if fulfillment_filter:
            orders = orders.filter(fulfillment_status=fulfillment_filter)

        paid_param = request.query_params.get('paid', '').strip().lower()
        if paid_param == 'true':
            orders = orders.filter(paid=True)
        elif paid_param == 'false':
            orders = orders.filter(paid=False)

        date_from = request.query_params.get('date_from', '').strip()
        if date_from:
            try:
                orders = orders.filter(created_at__date__gte=datetime.strptime(date_from, '%Y-%m-%d').date())
            except ValueError:
                pass

        date_to = request.query_params.get('date_to', '').strip()
        if date_to:
            try:
                orders = orders.filter(created_at__date__lte=datetime.strptime(date_to, '%Y-%m-%d').date())
            except ValueError:
                pass

        page_qs, meta = _paginate(orders, request)
        return Response({**meta, 'results': AdminOrderListSerializer(page_qs, many=True).data})


class AdminOrderDetailView(APIView):
    """
    GET /api/admin/orders/{pk}/ — order detail.
    Roles: inventory, sales, admin, superadmin.
    No stripe_session_id, no payment_error.
    """
    permission_classes = [permissions.IsAuthenticated, CanViewAdminOrders]
    throttle_classes = [AdminOrdersThrottle]

    def get(self, request, pk):
        order = get_object_or_404(
            Order.objects.select_related('user').prefetch_related('items__product'),
            pk=pk,
        )
        return Response(AdminOrderDetailSerializer(order).data)


class AdminOrderFulfillmentView(APIView):
    """
    PATCH /api/admin/orders/{pk}/fulfillment-status/
    Changes fulfillment_status only. Creates audit log.
    inventory role: limited to preparing/ready_for_pickup/shipped/delivered.
    sales/admin/superadmin: any value.
    """
    permission_classes = [permissions.IsAuthenticated, CanManageOrderFulfillment]
    throttle_classes = [AdminOrderStatusChangeThrottle]

    def patch(self, request, pk):
        ser = AdminOrderFulfillmentSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        new_fs = ser.validated_data['fulfillment_status']
        note = ser.validated_data.get('note', '').strip()

        order = get_object_or_404(Order, pk=pk)

        role = get_user_role(request.user)
        if role == UserProfile.ROLE_INVENTORY and new_fs not in _INVENTORY_ALLOWED_FULFILLMENT:
            allowed = ', '.join(sorted(_INVENTORY_ALLOWED_FULFILLMENT))
            return Response(
                {'detail': f'El rol de inventario no puede establecer el estado "{new_fs}". '
                           f'Estados permitidos: {allowed}.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        old_fs = order.fulfillment_status
        with transaction.atomic():
            order.fulfillment_status = new_fs
            order.save(update_fields=['fulfillment_status'])
            AdminAuditLog.log(
                actor=request.user,
                action='order_fulfillment_status_changed',
                target_type='order',
                target_id=order.pk,
                metadata={
                    'order_id': order.pk,
                    'customer_email': order.customer_email,
                    'old_fulfillment_status': old_fs,
                    'new_fulfillment_status': new_fs,
                    'note': note[:200] if note else '',
                },
                request=request,
            )

        order = Order.objects.select_related('user').prefetch_related('items__product').get(pk=order.pk)
        return Response(AdminOrderDetailSerializer(order).data)
