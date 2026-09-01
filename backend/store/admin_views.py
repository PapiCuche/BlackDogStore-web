import logging
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import HttpResponse
from django.db.models import F, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .inventory_services import (
    InsufficientStockError, InventoryError,
    apply_initial_stock, apply_manual_stock_movement,
)
from .models import (
    AdminAuditLog, Category, Order, OrderItem, Product, StockMovement, UserProfile,
)
from .tenancy import (
    BranchAccessError, CATALOG_SOURCE_LEGACY, NoBranchError,
    active_memberships, has_capability, is_platform_admin,
    resolve_branch_for_user, resolve_catalog_company, visible_companies,
)
from .permissions import (
    CanManageInventory, CanManageOrderFulfillment, CanManageProducts,
    CanViewAdminOrders, CanViewAdminProducts,
    HasCompanyMembership, IsPlatformAdmin, get_user_role,
)
from .serializers import (
    AdminCategoryWriteSerializer, AdminInventoryAdjustSerializer,
    AdminOrderDetailSerializer, AdminOrderFulfillmentSerializer, AdminOrderListSerializer,
    AdminProductSerializer, AdminProductWriteSerializer,
    CategorySerializer,
)
from .throttles import (
    AdminAuditLogsThrottle, AdminCategoriesThrottle, AdminInventoryAdjustThrottle,
    AdminOrderEmailResendThrottle, AdminOrdersThrottle, AdminOrderStatusChangeThrottle,
    AdminProductsThrottle, AdminProductWriteThrottle, AdminRoleChangeThrottle,
    AdminUsersThrottle,
)

User = get_user_model()
logger = logging.getLogger(__name__)

_VALID_ROLES = {r[0] for r in UserProfile.ROLE_CHOICES}
_DEFAULT_PAGE_SIZE = 25
_MAX_PAGE_SIZE = 100


# --- Catalogue tenant scope (Phase 2B) -------------------------------------
#
# Category and Product now belong to a Company, so these endpoints stop being
# global. Authority is the company capability (products.view / products.manage),
# resolved against the company the caller actually acts on — never against a
# company id taken from the request body.
#
# A caller with no company context at all gets 403, EXCEPT through the narrow
# legacy bridge in tenancy.legacy_catalog_company(), which resolves to the pilot
# tenant and to nothing else.

CAP_PRODUCTS_VIEW = 'products.view'
CAP_PRODUCTS_MANAGE = 'products.manage'
from . import order_fulfillment_services as fulfillment

CAP_ORDERS_VIEW = 'sales.orders.view'
CAP_ORDERS_MANAGE = 'sales.orders.manage'
CAP_SALES_NOTES = 'sales.notes.manage'
# Phase 2D: adjusting stock from the product screen is an inventory action,
# not a catalogue one. It takes the inventory capability, not products.manage.
CAP_INVENTORY_ADJUST = 'inventory.adjust'

# Legacy role sets, mirroring the permission classes these views used before
# Phase 2B. They now apply ONLY on the legacy-bridge path — a pre-SaaS operator
# working on the pilot tenant — never to a caller who has real company context.
_LEGACY_VIEW_CATALOG_ROLES = frozenset([
    UserProfile.ROLE_INVENTORY, UserProfile.ROLE_SALES,
    UserProfile.ROLE_ADMIN, UserProfile.ROLE_SUPERADMIN,
])
_LEGACY_MANAGE_CATALOG_ROLES = frozenset([
    UserProfile.ROLE_ADMIN, UserProfile.ROLE_SUPERADMIN,
])
# Orders (Phase 2C). Same shape as the catalogue sets: they apply only on the
# legacy-bridge path, for a pre-SaaS operator working on the pilot tenant.
_LEGACY_VIEW_ORDERS_ROLES = frozenset([
    UserProfile.ROLE_INVENTORY, UserProfile.ROLE_SALES,
    UserProfile.ROLE_ADMIN, UserProfile.ROLE_SUPERADMIN,
])
_LEGACY_MANAGE_ORDERS_ROLES = frozenset([
    UserProfile.ROLE_INVENTORY, UserProfile.ROLE_SALES,
    UserProfile.ROLE_ADMIN, UserProfile.ROLE_SUPERADMIN,
])
# Inventory adjustment through the product screen. Mirrors the Phase 3.2
# `CanManageInventory` class exactly, so no historical operator changes hands.
_LEGACY_ADJUST_INVENTORY_ROLES = frozenset([
    UserProfile.ROLE_INVENTORY, UserProfile.ROLE_ADMIN, UserProfile.ROLE_SUPERADMIN,
])
_LEGACY_SALES_NOTES_ROLES = frozenset([
    UserProfile.ROLE_SALES, UserProfile.ROLE_ADMIN, UserProfile.ROLE_SUPERADMIN,
])
# Resending a customer email was admin-only before Phase 2C (IsAdminRole) and
# stays admin-only: it puts a message in a customer's inbox, which is a narrower
# authority than moving an order through fulfillment.
_LEGACY_RESEND_EMAIL_ROLES = frozenset([
    UserProfile.ROLE_ADMIN, UserProfile.ROLE_SUPERADMIN,
])

_NO_CATALOG_CONTEXT = 'No tienes acceso a los datos de ninguna empresa.'
_NO_CATALOG_PERMISSION = 'No tienes permisos sobre estos datos en esta empresa.'


def _requested_company_id(request):
    """
    The company id named by the request, if any. UNTRUSTED — it is only ever
    passed to tenancy, which validates it against the caller's own access.

    Read from the QUERY STRING only, never from the body. Context selection and
    object payload are different things: a stray `company` key inside a product
    payload must not change which tenant the request acts on. It is also ignored
    by the serializer, so it cannot be mass-assigned either.
    """
    raw = request.query_params.get('company')
    if raw in (None, ''):
        return None, False
    try:
        return int(raw), True
    except (TypeError, ValueError):
        return None, None  # invalid


def _company_context(request, capability, legacy_roles):
    """
    Resolve the company this catalogue request acts on and authorise it, or
    return an error Response.

    Authority now lives HERE rather than in a DRF permission class, because the
    two are inseparable: which company you act on determines what you may do in
    it. A class that only saw `request.user` could not express that, and gating
    with the old legacy-role classes would have refused a SaaS company admin
    whose UserProfile.role is still `customer` — the normal state of a user
    created through the membership API.

    Two authority paths, matching resolve_catalog_company():

      tenant  — the caller has company context; the company CAPABILITY decides.
      legacy  — a pre-SaaS operator on the pilot tenant; their LEGACY ROLE
                decides, exactly as it did before this phase.
    """
    requested_id, ok = _requested_company_id(request)
    if ok is None:
        return None, Response(
            {'detail': 'Parámetro "company" inválido.'}, status=status.HTTP_400_BAD_REQUEST
        )

    company, source = resolve_catalog_company(request.user, requested_id)
    if company is None:
        return None, Response(
            {'detail': _NO_CATALOG_CONTEXT}, status=status.HTTP_403_FORBIDDEN
        )

    if source == CATALOG_SOURCE_LEGACY:
        if get_user_role(request.user) not in legacy_roles:
            return None, Response(
                {'detail': _NO_CATALOG_PERMISSION}, status=status.HTTP_403_FORBIDDEN
            )
        return company, None

    if not has_capability(request.user, company, capability):
        return None, Response(
            {'detail': _NO_CATALOG_PERMISSION}, status=status.HTTP_403_FORBIDDEN
        )
    return company, None


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
    """
    GET /api/admin/users/ — the people this caller may see, with their roles.

    P0-C — THE LEAK THIS CLOSES
    ---------------------------
    This was `IsAdminRole` over an unfiltered `User.objects`. `IsAdminRole`
    reads `UserProfile.role`, which is a GLOBAL column with no company in it, so
    anybody carrying the legacy `admin` role — an administrator of ONE tenant —
    listed every user of the platform, with their email addresses. A tenant
    administrator is not a platform administrator, and the legacy role cannot
    tell the difference because it predates there being more than one company.

    WHAT REPLACES IT
    ----------------
    Authority is a capability (`memberships.view`) held inside a company, and
    the queryset is built DOWNWARDS from the companies the caller belongs to:
    people who share an active membership with them, plus themselves. A user of
    another tenant is not filtered out of the answer — they are never in it.

    Platform administrators still see everyone, because operating the platform
    is what that authority is for.

    WHY NOT SIMPLY LOCK IT TO PLATFORM ADMINS
    -----------------------------------------
    Because a company administrator has a legitimate reason to see their own
    staff, `/api/admin/memberships/` already serves exactly that, and this
    screen is what the panel links to today. Scoping the queryset closes the
    leak without removing a function somebody uses; moving the screen onto
    memberships entirely is a UI change, not a security fix, and belongs in its
    own phase.
    """

    permission_classes = [permissions.IsAuthenticated, HasCompanyMembership]
    throttle_classes = [AdminUsersThrottle]

    def get(self, request):
        users = User.objects.select_related('profile').order_by('id')

        if not is_platform_admin(request.user):
            companies = list(
                active_memberships(request.user).values_list('company_id', flat=True)
            )
            if not companies:
                return Response({'count': 0, 'page': 1, 'page_size': 0, 'results': []})
            if not any(
                has_capability(request.user, company_id, 'memberships.view')
                for company_id in companies
            ):
                return Response(
                    {'detail': 'No tienes permisos para esta operación.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
            users = users.filter(
                Q(memberships__company_id__in=companies, memberships__is_active=True)
                | Q(pk=request.user.pk)
            ).distinct()

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
    """
    PATCH /api/admin/users/{pk}/role/ — change the GLOBAL legacy role.

    P0-C — PLATFORM AUTHORITY, NOT A TENANT ONE
    -------------------------------------------
    `UserProfile.role` has no company in it: it applies everywhere at once. So
    changing it is a platform operation however it is reached, and it now
    requires a platform administrator.

    It used to require `IsSuperAdminRole`, which is satisfied by
    `UserProfile.role == 'superadmin'` — a value THIS ENDPOINT can write. A
    legacy superadmin who was not a Django superuser could therefore grant that
    same role to anyone, including themselves, across every tenant on the
    platform: a privilege escalation whose ladder was the endpoint itself.

    Company-scoped authority is granted through `Membership` and
    `/api/admin/memberships/`, which is where a company administrator manages
    their own staff.
    """

    permission_classes = [permissions.IsAuthenticated, IsPlatformAdmin]
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
    """
    GET /api/admin/audit-logs/ — administrative actions this caller may read.

    P0-C — WHOSE LOG IS IT
    ----------------------
    This was `IsAdminRole` over an unfiltered `AdminAuditLog.objects`, so one
    tenant's administrator read every tenant's audit trail: who did what, to
    which record, from which address, with the metadata attached. An audit log
    is a record of other people's conduct, which makes reading one across a
    company boundary a worse leak than reading their catalogue.

    Rows carry `company` since SaaS Phase 1. Entries with NO company are either
    older than multi-tenancy or platform-level, and they stay visible ONLY to
    platform administrators — a null is not permission to read.

    This phase changes WHO SEES WHICH TENANT and nothing else. What the rows
    contain — the PII duplicated into `metadata`, the retention — is P1-H.
    """

    permission_classes = [permissions.IsAuthenticated, HasCompanyMembership]
    throttle_classes = [AdminAuditLogsThrottle]

    def get(self, request):
        logs = AdminAuditLog.objects.select_related('actor').order_by('-created_at')

        if not is_platform_admin(request.user):
            companies = list(visible_companies(request.user).values_list('pk', flat=True))
            if not companies or not any(
                has_capability(request.user, company_id, 'memberships.view')
                for company_id in companies
            ):
                return Response(
                    {'detail': 'No tienes permisos para esta operación.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
            logs = logs.filter(company_id__in=companies)

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

    # Authority is decided by _catalog_company(), which needs the resolved
    # company to answer at all. See its docstring.
    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        if self.request.method == 'POST':
            return [AdminProductWriteThrottle()]
        return [AdminProductsThrottle()]

    def get(self, request):
        company, error = _company_context(request, CAP_PRODUCTS_VIEW, _LEGACY_VIEW_CATALOG_ROLES)
        if error:
            return error

        # Born scoped: every filter below narrows within the tenant.
        products = (
            Product.objects
            .filter(company=company)
            .select_related('category')
            .order_by('-created_at')
        )

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
        company, error = _company_context(request, CAP_PRODUCTS_MANAGE, _LEGACY_MANAGE_CATALOG_ROLES)
        if error:
            return error

        ser = AdminProductWriteSerializer(
            data=request.data, context={'company': company},
        )
        ser.is_valid(raise_exception=True)

        # PHASE 2D — OPENING STOCK IS A MOVEMENT, NOT A COLUMN.
        #
        # `inventory` in the payload used to be written straight onto the row,
        # which meant units appeared in a company with nothing in the Kardex
        # explaining where they came from. Now it opens the balance of the
        # branch this operator works in, with an `initial_stock` line.
        #
        # A company with no branch cannot hold stock, and this refuses rather
        # than inventing a location: units have to be SOMEWHERE.
        opening_stock = ser.validated_data.pop('inventory', 0) or 0
        branch = None
        if opening_stock > 0:
            try:
                branch = resolve_branch_for_user(request.user, company, None)
            except (NoBranchError, BranchAccessError):
                return Response(
                    {
                        'inventory': [
                            'No hay una sucursal donde registrar el stock inicial. '
                            'Cree una sucursal y asígnela antes de dar de alta '
                            'productos con stock.',
                        ],
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            with transaction.atomic():
                # company comes from the resolved context, never from the payload.
                product = ser.save(company=company, inventory=0)
                if branch is not None:
                    apply_initial_stock(
                        branch=branch, product=product, quantity=opening_stock,
                        actor=request.user, request=request,
                    )
        except InventoryError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        product.refresh_from_db()
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
                'initial_stock_branch_id': branch.pk if branch else None,
                'category_id': product.category_id,
                'is_active': product.is_active,
            },
            request=request,
            company=company,
        )
        return Response(AdminProductSerializer(product).data, status=status.HTTP_201_CREATED)


class AdminProductDetailView(APIView):
    """
    GET   /api/admin/products/{pk}/ — detail. CanViewAdminProducts.
    PATCH /api/admin/products/{pk}/ — edit. CanManageProducts.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        if self.request.method == 'PATCH':
            return [AdminProductWriteThrottle()]
        return [AdminProductsThrottle()]

    def get(self, request, pk):
        company, error = _company_context(request, CAP_PRODUCTS_VIEW, _LEGACY_VIEW_CATALOG_ROLES)
        if error:
            return error
        # A product of another tenant answers exactly like one that does not exist.
        product = get_object_or_404(
            Product.objects.select_related('category').filter(company=company), pk=pk,
        )
        return Response(AdminProductSerializer(product).data)

    def patch(self, request, pk):
        company, error = _company_context(request, CAP_PRODUCTS_MANAGE, _LEGACY_MANAGE_CATALOG_ROLES)
        if error:
            return error
        product = get_object_or_404(
            Product.objects.select_related('category').filter(company=company), pk=pk,
        )

        # PHASE 2D — STOCK IS NOT AN EDITABLE PRODUCT FIELD.
        #
        # `Product.inventory` is now a derived aggregate over BranchStock. Typing
        # a new number into a product form would change stock with no branch, no
        # Kardex line, no actor and no reason — the exact hole the whole
        # inventory module exists to close. Refused loudly rather than ignored
        # silently, so a client that still sends it finds out.
        if 'inventory' in request.data:
            return Response(
                {
                    'inventory': [
                        'El stock no se edita desde el producto. Registre un '
                        'movimiento de inventario en la sucursal correspondiente.',
                    ],
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        old = _product_snapshot(product)

        ser = AdminProductWriteSerializer(
            product, data=request.data, partial=True, context={'company': company},
        )
        ser.is_valid(raise_exception=True)
        # company is never in the payload, so a product cannot be moved between
        # tenants by editing an id.
        product = ser.save(company=company)
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
    Body: {delta: int (nonzero), reason: str, branch?: int}

    THE SHORTCUT THIS ENDPOINT USED TO BE.
    Until Phase 2D it wrote `Product.inventory` directly: no Kardex line, no
    branch, no lock on anything but the product row, and no tenant scope — any
    staff user could adjust any company's product by id. It is now a thin
    wrapper over the same service layer as every other movement, and it is
    tenant- and branch-scoped like everything else.

    A `delta` becomes a manual entry or a manual exit. `correction_*` is
    deliberately not used: a correction means "the shelf disagreed with the
    system", which is what a physical count records, not what a free-form
    adjustment asserts.
    """
    # Authority is decided by _company_context() + branch access below, which
    # need the resolved company to answer at all.
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminInventoryAdjustThrottle]

    def post(self, request, pk):
        company, error = _company_context(
            request, CAP_INVENTORY_ADJUST, _LEGACY_ADJUST_INVENTORY_ROLES,
        )
        if error:
            return error

        ser = AdminInventoryAdjustSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        delta = ser.validated_data['delta']
        reason = ser.validated_data['reason']

        # A product of another tenant answers exactly like one that does not exist.
        product = Product.objects.filter(company=company, pk=pk).first()
        if product is None:
            return Response(
                {'detail': 'Producto no encontrado.'}, status=status.HTTP_404_NOT_FOUND,
            )

        try:
            branch = resolve_branch_for_user(
                request.user, company, request.data.get('branch'), allow_all=False,
            )
        except NoBranchError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except BranchAccessError:
            return Response(
                {'detail': 'Sucursal no encontrada o sin acceso.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        old_inventory = product.inventory
        movement_type = (
            StockMovement.MANUAL_ENTRY if delta > 0 else StockMovement.MANUAL_EXIT
        )

        try:
            movement = apply_manual_stock_movement(
                branch=branch,
                product_id=product.pk,
                movement_type=movement_type,
                quantity=abs(delta),
                reason=reason,
                actor=request.user,
                request=request,
            )
        except InsufficientStockError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except InventoryError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        product.refresh_from_db()
        AdminAuditLog.log(
            actor=request.user,
            action='product_inventory_adjusted',
            target_type='product',
            target_id=product.pk,
            metadata={
                'product_name': product.name,
                'product_id': product.pk,
                'branch_id': branch.pk,
                'branch_name': branch.name,
                'old_inventory': old_inventory,
                'delta': delta,
                'new_inventory': product.inventory,
                'stock_movement_id': movement.pk,
                'reason': reason,
            },
            request=request,
            company=company,
        )
        return Response(AdminProductSerializer(product).data)


class AdminCategoryListView(APIView):
    """
    GET  /api/admin/categories/ — list all. CanViewAdminProducts.
    POST /api/admin/categories/ — create. CanManageProducts.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        return [AdminCategoriesThrottle()]

    def get(self, request):
        company, error = _company_context(request, CAP_PRODUCTS_VIEW, _LEGACY_VIEW_CATALOG_ROLES)
        if error:
            return error
        categories = Category.objects.filter(company=company).order_by('name')
        return Response(CategorySerializer(categories, many=True).data)

    def post(self, request):
        company, error = _company_context(request, CAP_PRODUCTS_MANAGE, _LEGACY_MANAGE_CATALOG_ROLES)
        if error:
            return error
        ser = AdminCategoryWriteSerializer(
            data=request.data, context={'company': company},
        )
        ser.is_valid(raise_exception=True)
        category = ser.save(company=company)
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
    # Authority is decided by _company_context(), which needs the resolved
    # company to answer at all — same reasoning as the catalogue views.
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminOrdersThrottle]

    def get(self, request):
        from datetime import datetime

        company, error = _company_context(request, CAP_ORDERS_VIEW, _LEGACY_VIEW_ORDERS_ROLES)
        if error:
            return error

        # Born scoped: every filter below narrows within the tenant.
        orders = (
            Order.objects
            .filter(company=company)
            .select_related('user')
            .prefetch_related('items')
            .order_by('-created_at')
        )

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
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminOrdersThrottle]

    def get(self, request, pk):
        company, error = _company_context(request, CAP_ORDERS_VIEW, _LEGACY_VIEW_ORDERS_ROLES)
        if error:
            return error
        # An order of another tenant answers exactly like one that does not exist.
        order = get_object_or_404(
            Order.objects.filter(company=company)
            .select_related('user').prefetch_related('items__product'),
            pk=pk,
        )
        return Response(AdminOrderDetailSerializer(order).data)


class AdminOrderResendEmailView(APIView):
    """
    POST /api/admin/orders/{pk}/resend-confirmation-email/
    Manually resends the customer confirmation email (with PDF).
    Roles: admin, superadmin only (NOT sales/inventory/technician).
    """
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminOrderEmailResendThrottle]

    def post(self, request, pk):
        from .email_services import resend_order_confirmation_email

        company, error = _company_context(request, CAP_ORDERS_MANAGE, _LEGACY_RESEND_EMAIL_ROLES)
        if error:
            return error

        try:
            order = Order.objects.filter(company=company).prefetch_related(
                "items__product").get(pk=pk)
        except Order.DoesNotExist:
            return Response({"detail": "Orden no encontrada."}, status=status.HTTP_404_NOT_FOUND)

        if not order.paid or order.status != Order.Status.PAID:
            return Response(
                {"detail": "Solo se puede reenviar el email para órdenes pagadas."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = resend_order_confirmation_email(order)
        except Exception:
            logger.exception("Failed to resend confirmation email for order %s", pk)
            smtp_note = f"resend_smtp_fail: {timezone.now().strftime('%Y-%m-%d %H:%M')}"
            existing = Order.objects.filter(pk=pk).values_list("email_send_error", flat=True).first() or ""
            new_error = (f"{existing}; {smtp_note}" if existing else smtp_note)[:500]
            Order.objects.filter(pk=pk).update(email_send_error=new_error)
            return Response(
                {"detail": "Error al enviar el email. Verifique la configuración SMTP e inténtelo nuevamente."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        AdminAuditLog.log(
            actor=request.user,
            action="order_confirmation_email_resent",
            target_type="order",
            target_id=order.pk,
            company=company,
            metadata={
                "order_id": order.pk,
                "customer_email": order.customer_email,
                "resent_to": order.customer_email,
                "had_pdf_attachment": result["had_pdf"],
            },
            request=request,
        )

        return Response({
            "detail": "Email de confirmación reenviado correctamente.",
            "had_pdf_attachment": result["had_pdf"],
            "resent_to": order.customer_email,
        })


class AdminOrderReceiptPdfView(APIView):
    """
    GET /api/admin/orders/{pk}/receipt-pdf/
    Returns the PDF receipt for a paid order as application/pdf.
    Roles: inventory, sales, admin, superadmin (CanViewAdminOrders).
    Technician and customer are blocked.
    """
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminOrdersThrottle]

    def get(self, request, pk):
        company, error = _company_context(request, CAP_ORDERS_VIEW, _LEGACY_VIEW_ORDERS_ROLES)
        if error:
            return error

        try:
            order = Order.objects.filter(company=company).prefetch_related(
                "items__product").get(pk=pk)
        except Order.DoesNotExist:
            return Response({"detail": "Orden no encontrada."}, status=status.HTTP_404_NOT_FOUND)

        if not order.paid or order.status != Order.Status.PAID:
            return Response(
                {"detail": "El PDF solo está disponible para órdenes pagadas."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            from .pdf_services import generate_order_receipt_pdf, get_order_receipt_filename
            pdf_bytes = generate_order_receipt_pdf(order)
            filename = get_order_receipt_filename(order)
        except Exception:
            logger.exception("PDF generation failed for order %s (admin download)", pk)
            return Response(
                {"detail": "Error al generar el PDF. Inténtelo nuevamente."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        AdminAuditLog.log(
            actor=request.user,
            action="order_receipt_pdf_downloaded",
            target_type="order",
            target_id=order.pk,
            company=company,
            metadata={"order_id": order.pk, "customer_email": order.customer_email},
            request=request,
        )

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class AdminOrderFulfillmentView(APIView):
    """
    PATCH /api/admin/orders/{pk}/fulfillment-status/
    Changes fulfillment_status only. Creates audit log.
    inventory role: limited to preparing/ready_for_pickup/shipped/delivered.
    sales/admin/superadmin: any value.
    """
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminOrderStatusChangeThrottle]

    def patch(self, request, pk):
        company, error = _company_context(request, CAP_ORDERS_MANAGE, _LEGACY_MANAGE_ORDERS_ROLES)
        if error:
            return error

        ser = AdminOrderFulfillmentSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        new_fs = ser.validated_data['fulfillment_status']
        note = ser.validated_data.get('note', '').strip()

        # Scoped: an admin of company A can never move company B's order.
        order = get_object_or_404(Order.objects.filter(company=company), pk=pk)

        # M6 — the rule and the audit entry moved to a shared service, so the
        # native surface cannot drift from this one. Behaviour is unchanged.
        try:
            fulfillment.change_fulfillment_status(
                order=order, new_status=new_fs, actor=request.user,
                company=company, note=note, request=request,
            )
        except fulfillment.FulfillmentNotAllowed as exc:
            return Response({'detail': exc.detail}, status=status.HTTP_403_FORBIDDEN)

        order = Order.objects.select_related('user').prefetch_related('items__product').get(pk=order.pk)
        return Response(AdminOrderDetailSerializer(order).data)
