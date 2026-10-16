import logging
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.http import HttpResponse
from django.db.models import Count, F, Max, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .inventory_services import (
    InsufficientStockError, InventoryError,
    apply_initial_stock, apply_manual_stock_movement,
    order_ids_with_stock_shortfall, order_stock_shortfall,
    reprocess_order_stock_exit,
)
from .models import (
    AdminAuditLog, Category, Order, OrderItem, Product, StockMovement, UserProfile,
)
from .tenancy import (
    BranchAccessError, CATALOG_SOURCE_LEGACY, NoBranchError,
    active_memberships, has_capability, is_platform_admin,
    resolve_branch_for_user, resolve_catalog_company, uses_legacy_bridge, visible_companies,
    visible_orders,
)
from .permissions import (
    CanManageInventory, CanManageOrderFulfillment, CanManageProducts,
    CanViewAdminOrders, CanViewAdminProducts,
    HasCompanyMembership, IsPlatformAdmin, get_user_role,
)
from .serializers import (
    AdminCategoryUpdateSerializer, AdminCategoryWriteSerializer, AdminInventoryAdjustSerializer,
    AdminOrderDetailSerializer, AdminOrderFulfillmentSerializer, AdminOrderListSerializer,
    AdminProductDetailSerializer, AdminProductSerializer, AdminProductWriteSerializer,
    AdminCategorySerializer,
    CategorySerializer,
    OrderRiskReviewSerializer,
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
from . import product_publication
from . import storefront_media

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

# CLIENT-SEC-01. Deciding the review of an order is a judgement about a SALE.
# On the legacy bridge that is the counter's and the administrators', as with
# cancelling: warehouse staff move goods.
_LEGACY_REVIEW_ORDERS_ROLES = frozenset([
    UserProfile.ROLE_SALES, UserProfile.ROLE_ADMIN, UserProfile.ROLE_SUPERADMIN,
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
            # ISO-01: the capability must hold IN the company whose people you
            # read, not in ANY company you happen to belong to. The old check
            # was `any(...)` over all your companies, then a filter across all of
            # them — so an admin of A who was also a plain member of B read B's
            # user list (emails, roles) with A's capability. Keep only the
            # companies where you actually hold memberships.view and read across
            # exactly those.
            readable = [
                company_id for company_id in companies
                if has_capability(request.user, company_id, 'memberships.view')
            ]
            if not readable:
                return Response(
                    {'detail': 'No tienes permisos para esta operación.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
            users = users.filter(
                Q(memberships__company_id__in=readable, memberships__is_active=True)
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
            # ISO-02: same flaw as the user list. `any(...)` over every company
            # followed by a filter across all of them let an admin of A read B's
            # audit trail — IPs and the PII copied into metadata — with A's
            # capability. Read only the companies where you hold memberships.view.
            readable = [
                company_id
                for company_id in visible_companies(request.user).values_list('pk', flat=True)
                if has_capability(request.user, company_id, 'memberships.view')
            ]
            if not readable:
                return Response(
                    {'detail': 'No tienes permisos para esta operación.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
            logs = logs.filter(company_id__in=readable)

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

_AUDITABLE_PRODUCT_FIELDS = ('name', 'slug', 'description', 'price', 'inventory', 'image_url', 'category_id', 'is_active',
                             'is_published_online')


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


#: The filters of the product list. The bulk visibility action takes these same
#: keys for «all that match», and refuses any other.
PRODUCT_FILTER_KEYS = ('search', 'category', 'is_active', 'is_published_online', 'stock', 'is_serialized')

_TRUE_FALSE = ('true', 'false')
_STOCK_FILTERS = ('in_stock', 'out_of_stock', 'low_stock')


class InvalidProductFilter(ValueError):
    """A filter value that means nothing. Raised only when `strict`."""


def _filter_text(params, key, strict):
    """One filter as text. A JSON `true` is «true»; a list or an object is not a filter."""
    raw = params.get(key)
    if raw is None:
        return ''
    if isinstance(raw, bool):
        return 'true' if raw else 'false'
    if isinstance(raw, int):
        return str(raw)
    if not isinstance(raw, str):
        if strict:
            raise InvalidProductFilter(key)
        return ''
    return raw.strip()


def _filter_choice(params, key, allowed, strict):
    value = _filter_text(params, key, strict).lower()
    if value in allowed:
        return value
    if value and strict:
        raise InvalidProductFilter(key)
    return ''


def filter_admin_products(queryset, params, *, strict=False):
    """
    Narrow `queryset` (already of ONE company) by the product list's filters.

    THE ONE DEFINITION. The list reads `params` from its query string; the bulk
    visibility action reads the same keys from the filter the screen sent, so
    «all 312 results» is decided by the code that counted 312.

    A value that means nothing is ignored by the list, as it always was — a
    hand-typed address shows the whole catalogue. `strict` refuses it instead
    (`InvalidProductFilter`): for a WRITE, «I did not understand, so everything»
    is the wrong reading.
    """
    search = _filter_text(params, 'search', strict)
    if '\x00' in search:
        # No name and no slug can hold a NUL, and the database refuses the
        # literal outright (it answered 500). For a write it is a filter that
        # means nothing; for the list, a search that matches nothing.
        if strict:
            raise InvalidProductFilter('search')
        return queryset.none()
    if search:
        queryset = queryset.filter(Q(name__icontains=search) | Q(slug__icontains=search)).distinct()

    category_id = _filter_text(params, 'category', strict)
    if category_id:
        try:
            queryset = queryset.filter(category_id=int(category_id))
        except (ValueError, TypeError):
            if strict:
                raise InvalidProductFilter('category') from None

    is_active = _filter_choice(params, 'is_active', _TRUE_FALSE, strict)
    if is_active:
        queryset = queryset.filter(is_active=is_active == 'true')

    # PRODUCT-DESTINATION-01: «en la web» o «sólo stock interno».
    published = _filter_choice(params, 'is_published_online', _TRUE_FALSE, strict)
    if published:
        queryset = queryset.filter(is_published_online=published == 'true')

    # How it is counted: device by device, or by quantity.
    serialized = _filter_choice(params, 'is_serialized', _TRUE_FALSE, strict)
    if serialized:
        queryset = queryset.filter(is_serialized=serialized == 'true')

    stock = _filter_choice(params, 'stock', _STOCK_FILTERS, strict)
    if stock == 'in_stock':
        queryset = queryset.filter(inventory__gt=0)
    elif stock == 'out_of_stock':
        queryset = queryset.filter(inventory=0)
    elif stock == 'low_stock':
        queryset = queryset.filter(inventory__gt=0, inventory__lte=_LOW_STOCK_THRESHOLD)

    return queryset


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
        products = filter_admin_products(
            Product.objects.filter(company=company).select_related('category').order_by('-created_at'),
            request.query_params,
        )
        # What each row lacks to be published, without a query per row.
        products = product_publication.with_gallery_flag(products)

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
            # F2 · F-CAP-01. products.manage governs the catalogue; opening a
            # balance moves stock, so it needs the same authority as the direct
            # adjustment — decided the same way, legacy bridge included, and
            # BEFORE anything is written.
            _company, stock_error = _company_context(
                request, CAP_INVENTORY_ADJUST, _LEGACY_ADJUST_INVENTORY_ROLES,
            )
            if stock_error:
                return stock_error
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
                'is_published_online': product.is_published_online,
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
        return Response(AdminProductDetailSerializer(product).data)

    @transaction.atomic
    def patch(self, request, pk):
        company, error = _company_context(request, CAP_PRODUCTS_MANAGE, _LEGACY_MANAGE_CATALOG_ROLES)
        if error:
            return error
        # LOCKED, and that is not optional. The serializer writes the WHOLE row,
        # so a save that started before a gallery change finished would put the
        # old `image_url` back: the gallery would have a primary picture and the
        # catalogue none. The gallery locks this same row for every change, so
        # one waits for the other and each sees what the other left.
        product = get_object_or_404(
            Product.objects.select_for_update(of=('self',))
            .select_related('category').filter(company=company), pk=pk,
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
                # AUDIT-07: without it the row exists but the tenant's own
                # audit log, which reads by company, never shows it.
                company=company,
            )

        return Response(AdminProductDetailSerializer(product).data)


class AdminProductBulkVisibilityView(APIView):
    """
    POST /api/admin/products/bulk-visibility/ — publish or hide many products.

    Body, one of:
      {"action": "publish"|"hide", "ids": [1, 2, 3]}
      {"action": "publish"|"hide", "filter": {...}, "expected_count": N}

    The second form is «all that match»: the filter is the product list's own
    (`filter_admin_products`), evaluated again HERE, and `expected_count` is
    the total the operator was shown. If the two differ nothing is done and the
    answer is 409 `selection_changed` with the current count — confirming «312
    products» must not change 340.

    Answers `{action, changed, unchanged, refused, total}`. A product that is
    not ready to be published (`product_publication`) is left as it was and
    comes back in `refused` with its problem codes; the ones that are ready ARE
    published. That partial result is explicit, not a failure.

    The company is the caller's (`_company_context`); nothing in the body names
    one. An id of another company refuses the whole batch exactly like an id
    that does not exist.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminProductWriteThrottle]
    http_method_names = ['post', 'options']

    def post(self, request):
        company, error = _company_context(request, CAP_PRODUCTS_MANAGE, _LEGACY_MANAGE_CATALOG_ROLES)
        if error:
            return error

        data = request.data if isinstance(request.data, dict) else {}
        action = data.get('action')
        if action not in ('publish', 'hide'):
            return _bulk_error('Indica la acción: «publish» o «hide».')
        has_ids, has_filter = 'ids' in data, 'filter' in data
        if has_ids == has_filter:
            return _bulk_error('Indica los productos («ids») o el filtro («filter»), no ambos.')

        ids = filters = expected = select = None
        if has_ids:
            ids = data['ids']
            if (
                not isinstance(ids, list) or not ids
                or any(isinstance(i, bool) or not isinstance(i, int) or i <= 0 for i in ids)
            ):
                return _bulk_error('«ids» debe ser una lista de identificadores de producto.')
        else:
            filters, expected = data['filter'], data.get('expected_count')
            if not isinstance(filters, dict) or set(filters) - set(PRODUCT_FILTER_KEYS):
                return _bulk_error('El filtro no es válido.')
            if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
                return _bulk_error('Indica cuántos productos se seleccionaron («expected_count»).')

            def select(queryset):
                return filter_admin_products(queryset, filters, strict=True)

        try:
            with transaction.atomic():
                result = product_publication.set_visibility(
                    company=company, publish=action == 'publish',
                    ids=ids, select=select, expected_count=expected,
                )
                changed_ids = result.pop('changed_ids')
                if changed_ids:
                    # ONE line for the batch, in the same transaction as the
                    # change. Counts and ids: no name, no price, nobody's data.
                    AdminAuditLog.log(
                        actor=request.user,
                        action='products_visibility_changed',
                        target_type='product',
                        metadata={
                            'action': action,
                            'changed': result['changed'],
                            'unchanged': result['unchanged'],
                            'refused': len(result['refused']),
                            'total': result['total'],
                            'changed_ids': changed_ids[:product_publication.AUDIT_IDS_LIMIT],
                            'changed_ids_truncated': len(changed_ids) > product_publication.AUDIT_IDS_LIMIT,
                            'filter': filters,
                        },
                        request=request,
                        company=company,
                    )
        except InvalidProductFilter:
            return _bulk_error('El filtro no es válido.')
        except product_publication.UnknownProducts:
            return Response(
                {'detail': 'Alguno de los productos no existe.'}, status=status.HTTP_404_NOT_FOUND,
            )
        except product_publication.TooManyProducts as exc:
            return Response(
                {
                    'code': 'too_many', 'count': exc.count, 'limit': exc.limit,
                    'detail': (
                        f'Son {exc.count} productos y se cambian hasta {exc.limit} a la vez. '
                        'Acota la selección con los filtros.'
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except product_publication.SelectionChanged as exc:
            return Response(
                {
                    'code': 'selection_changed', 'current_count': exc.current_count,
                    'detail': (
                        f'La selección cambió: ahora coinciden {exc.current_count} productos. '
                        'No se cambió nada. Revisa la lista y vuelve a confirmar.'
                    ),
                },
                status=status.HTTP_409_CONFLICT,
            )

        return Response({'action': action, **result})


def _bulk_error(detail):
    return Response({'detail': detail}, status=status.HTTP_400_BAD_REQUEST)


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
        categories = (
            Category.objects.filter(company=company)
            .annotate(product_count=Count('product'))
            .order_by('home_order', 'name')
        )
        return Response(AdminCategorySerializer(categories, many=True).data)

    def post(self, request):
        company, error = _company_context(request, CAP_PRODUCTS_MANAGE, _LEGACY_MANAGE_CATALOG_ROLES)
        if error:
            return error
        ser = AdminCategoryWriteSerializer(
            data=request.data, context={'company': company},
        )
        ser.is_valid(raise_exception=True)
        # A new category goes LAST: it never jumps ahead of an order somebody
        # already arranged.
        last = Category.objects.filter(company=company).aggregate(top=Max('home_order'))['top']
        category = ser.save(company=company, home_order=min(999, (last or 0) + 1))
        AdminAuditLog.log(
            actor=request.user,
            action='category_created',
            target_type='category',
            target_id=category.pk,
            metadata={'name': category.name, 'slug': category.slug},
            request=request,
            company=company,
        )
        return Response(CategorySerializer(category).data, status=status.HTTP_201_CREATED)


class AdminCategoryDetailView(APIView):
    """
    PATCH /api/admin/categories/<id>/ — name and image. `products.manage`.

    A category of another company answers 404, exactly like one that does not
    exist.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        return [AdminCategoriesThrottle()]

    def patch(self, request, pk):
        company, error = _company_context(request, CAP_PRODUCTS_MANAGE, _LEGACY_MANAGE_CATALOG_ROLES)
        if error:
            return error
        category = Category.objects.filter(company=company, pk=pk).first()
        if category is None:
            return Response({'detail': 'Categoría no encontrada.'}, status=status.HTTP_404_NOT_FOUND)

        before = {
            'name': category.name, 'image_url': category.image_url,
            'is_active': category.is_active, 'show_on_home': category.show_on_home,
            'home_order': category.home_order,
        }
        ser = AdminCategoryUpdateSerializer(category, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        # Una sola transacción: lo que se coloca se comprueba, se guarda, y lo
        # que el hueco suelta se retira si nadie más lo muestra. Si algo falla,
        # no se borra nada.
        with transaction.atomic():
            new_image = ser.validated_data.get('image_url', before['image_url'])
            if new_image != before['image_url']:
                try:
                    storefront_media.claim(new_image, company=company, field='image_url')
                except DjangoValidationError as exc:
                    return Response(exc.message_dict, status=status.HTTP_400_BAD_REQUEST)
            category = ser.save()
            changed = {
                field: {'old': before[field], 'new': getattr(category, field)}
                for field in before if before[field] != getattr(category, field)
            }
            if changed:
                AdminAuditLog.log(
                    actor=request.user,
                    action='category_updated',
                    target_type='category',
                    target_id=category.pk,
                    metadata={'slug': category.slug, 'changed_fields': changed},
                    request=request,
                    company=company,
                )
            if 'image_url' in changed:
                storefront_media.release(
                    [before['image_url']], company=company,
                    actor=request.user, request=request,
                )
        category.product_count = category.product_set.count()
        return Response(AdminCategorySerializer(category).data)


# ---------------------------------------------------------------------------
# Phase 3.3 — Admin order views
# ---------------------------------------------------------------------------

# H4.1.2 — every view below starts from tenancy.visible_orders(): tenant AND
# branch, applied before any lookup, count or page. An order outside it answers
# 404 exactly like one that does not exist. The role-keyed copy of the inventory
# fulfilment rule that used to sit here was dead code; the one rule lives in
# order_fulfillment_services.allowed_fulfillment_statuses().

_ORDER_NOT_FOUND = 'Orden no encontrada.'


def _holds(user, company, capability, legacy_roles) -> bool:
    """
    Whether `user` holds `capability` in `company` — by the company's grant, or
    by their legacy role on the one path where that is still the authority.
    The same two roads as `_company_context`, for a company already resolved.
    """
    if uses_legacy_bridge(user, company):
        return get_user_role(user) in legacy_roles
    return has_capability(user, company, capability)


def _risk_block(request, order, company):
    """
    The assessment of an order as the panel shows it, or None.

    For `sales.orders.view`: this payload is also the answer of endpoints that
    ask for another capability, and what was noticed about a sale is not theirs
    to read by that road.
    """
    from . import order_risk

    if not _holds(request.user, company, CAP_ORDERS_VIEW, _LEGACY_VIEW_ORDERS_ROLES):
        return None
    return order_risk.describe(
        order_risk.for_order(order),
        can_review=_holds(request.user, company, CAP_ORDERS_MANAGE, _LEGACY_REVIEW_ORDERS_ROLES),
    )


def _delivery_block(request, order, company):
    # Imported here: `delivery_views` builds on this module.
    from .delivery_views import panel_delivery_block

    return panel_delivery_block(request, order, company)


def _order_detail_payload(request, order, company):
    """
    The order as the web panel shows it, plus the fulfilment states THIS caller
    may set — computed by the server, exactly as the native surface already
    does, so the panel no longer keeps its own role-keyed copy of the rule.
    """
    from .supply_services import order_supply_status

    shortfall = order_stock_shortfall(order)
    supply_status = order_supply_status(order)
    data = AdminOrderDetailSerializer(order).data
    # SERIAL-PICK-01: which lines owe a device, the devices somebody already
    # identified, and how many are still to be.
    from . import unit_identification

    view = unit_identification.order_view(order)
    pending, records = view['pending'], view['records']
    requires_imei = dict(order.items.values_list('product_id', 'product__requires_imei'))
    shown = set()
    for item in data['items']:
        product_id = item['product_id']
        item['is_serialized'] = product_id in pending or product_id in records
        item['requires_imei'] = bool(requires_imei.get(product_id))
        # Two lines of one product share its devices: shown once, on the first.
        item['units'] = [] if product_id in shown else records.get(product_id, [])
        item['units_pending'] = 0 if product_id in shown else pending.get(product_id, 0)
        shown.add(product_id)
    transitions = list(fulfillment.allowed_fulfillment_statuses(request.user, company))
    # ORDER-CANCEL-01: a cancelled order does not move any more.
    from . import order_cancellation as cancelling

    if cancelling.is_cancelled(order):
        transitions = []
    cancel_blockers = cancelling.blockers(order)
    may_cancel = cancelling.may_cancel(request.user, company)
    from . import order_refunds

    refunds_block = None
    if cancelling.is_cancelled(order):
        # COMMERCE-ADMIN-UX-01: what was collected, what is owed back and what
        # went back, and what THIS caller may do about it.
        refunds_block = {
            **order_refunds.payload(order),
            'can_record': may_cancel,
            'can_confirm': order_refunds.may_confirm(request.user, company),
        }
    return {
        **data,
        # ORDER-CANCEL-01: whether THIS caller may cancel it now, why not, and
        # what the cancellation did once it happened. Server-decided.
        'can_cancel': not cancel_blockers and may_cancel,
        # COMMERCE-ADMIN-UX-01: which cancellation it is open to — 'unpaid',
        # 'paid' or 'pos' (a till sale: the cashier confirms the goods are back).
        'cancel_mode': cancelling.mode_of(order) if not cancel_blockers else None,
        'refunds': refunds_block,
        # Why not is said to whoever could cancel it; to a reader it is noise.
        'cancel_blockers': cancel_blockers if may_cancel else [],
        'cancellation': cancelling.payload(order),
        # What the dialog warns about before anyone confirms: the boleta or
        # factura a cancellation would leave pending its credit note.
        'cancel_fiscal_document': (
            cancelling.fiscal_label(cancelling.live_fiscal_document(order)) if not cancel_blockers else ''),
        # CLIENT-SEC-01: what was noticed about this order and what a person
        # decided, for whoever may see orders; null when it was not assessed.
        'risk': _risk_block(request, order, company),
        # DELIVERY-01: the order's delivery record (carrier, agency, freight,
        # waybill), or its empty shape; null for whoever may not see orders.
        'delivery': _delivery_block(request, order, company),
        'units_pending': sum(pending.values()),
        # Whether THIS caller may say which device it is: whoever may change how
        # the order is dispatched, and only until it has left the shop.
        'units_can_change': bool(transitions) and not view['left'],
        'available_fulfillment_transitions': transitions,
        # INV-04: the oversell/shortfall, derived — see order_stock_shortfall.
        'stock_shortfall': shortfall,
        # Whether THIS caller may reprocess the pending exit: a shortfall to
        # resolve plus the inventory-move authority. Server-decided, like the
        # fulfillment transitions above — the panel never reads a role.
        'can_reprocess_stock_exit': bool(shortfall) and has_capability(
            request.user, company, CAP_INVENTORY_ADJUST),
        # DC3: the order's supply, derived from its on-order lines ('' when
        # none), and whether THIS caller may confirm, receive or fail them.
        'supply_status': supply_status,
        # A cancelled order's lines were released with it: nothing to supply.
        'can_manage_supply': bool(supply_status) and not cancelling.is_cancelled(order)
        and has_capability(request.user, company, CAP_ORDERS_MANAGE),
        'can_receive_supply': bool(supply_status) and not cancelling.is_cancelled(order)
        and has_capability(request.user, company, CAP_ORDERS_MANAGE)
        and has_capability(request.user, company, CAP_INVENTORY_ADJUST),
    }


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

        # Born scoped: tenant and branch first; every filter below narrows within.
        orders = (
            visible_orders(request.user, company)
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

        # INV-04: a shortfall is only meaningful once the sale's exit was
        # attempted (at payment), so it is computed over PAID orders. `?shortfall
        # =true` narrows to orders still short of stock; every row carries the
        # flag either way so the panel can mark them without a second request.
        shortfall_only = request.query_params.get('shortfall', '').strip().lower() == 'true'
        if shortfall_only:
            paid_ids = orders.filter(status=Order.Status.PAID).values_list('pk', flat=True)
            orders = orders.filter(pk__in=order_ids_with_stock_shortfall(paid_ids))

        # CLIENT-SEC-01: `?review=pending` narrows to the orders waiting for a
        # person; every row carries its level and review state either way.
        from . import order_risk
        from .supply_services import order_supply_status

        orders = order_risk.filter_by_review(orders, request.query_params.get('review'))

        # DC3: `?supply=to_confirm|pending|failed|received` narrows to orders
        # with an on-order line in that state; every row says its supply.
        supply_filter = request.query_params.get('supply', '').strip()
        if supply_filter in dict(OrderItem.SUPPLY_STATUS_CHOICES) and supply_filter:
            orders = orders.filter(
                items__supply_origin=OrderItem.SUPPLY_SUPPLIER, items__supply_status=supply_filter,
            ).distinct()

        # COMMERCE-ADMIN-UX-01: `?refund=pending|confirmed` — cancelled orders
        # with a refund in that state (the cancellations history's follow-up).
        refund_filter = request.query_params.get('refund', '').strip()
        if refund_filter in ('pending', 'confirmed'):
            orders = orders.filter(refunds__status=refund_filter).distinct()

        page_qs, meta = _paginate(orders, request)
        rows = AdminOrderListSerializer(page_qs, many=True).data
        for row, order in zip(rows, page_qs):
            row['supply_status'] = order_supply_status(order)
        page_short_ids = order_ids_with_stock_shortfall(
            [o.pk for o in page_qs if o.status == Order.Status.PAID]
        )
        for row in rows:
            row['has_stock_shortfall'] = row['id'] in page_short_ids
        order_risk.annotate_rows(company, rows)
        return Response({**meta, 'results': rows})


class AdminOrderDetailView(APIView):
    """
    GET /api/admin/orders/{pk}/ — order detail.
    Roles: inventory, sales, admin, superadmin.
    No gateway identifier, no payment_error.
    """
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminOrdersThrottle]

    def get(self, request, pk):
        company, error = _company_context(request, CAP_ORDERS_VIEW, _LEGACY_VIEW_ORDERS_ROLES)
        if error:
            return error
        # An order of another tenant — or of a branch this caller does not
        # operate — answers exactly like one that does not exist.
        order = (
            visible_orders(request.user, company)
            .select_related('user').prefetch_related('items__product')
            .filter(pk=pk).first()
        )
        if order is None:
            return Response({'detail': _ORDER_NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        return Response(_order_detail_payload(request, order, company))


class AdminOrderReprocessStockExitView(APIView):
    """
    POST /api/admin/orders/{pk}/reprocess-stock-exit/ — INV-04 resolution.

    Re-runs only the still-missing SALE_EXITs of a PAID order, once the branch
    has been replenished, so the shortfall left at payment time can be cleared.

    TWO authorities, because this endpoint both READS and WRITES:

      - it answers with the order DETAIL payload (customer data included), so it
        requires `sales.orders.view` exactly like AdminOrderDetailView — an
        `inventory.adjust`-only caller who is denied GET on this order must not
        harvest the same payload through the POST;
      - it WRITES stock exits, so it also requires the `inventory.adjust`
        capability — the same authority the `can_reprocess_stock_exit` flag is
        computed from. `sales.orders.view` without it can see the shortfall but
        not resolve it.

    It touches no payment field and never pulls stock from another branch — see
    reprocess_order_stock_exit. Answers with the order detail payload (its
    `stock_shortfall` recomputed) plus the movements this call created.
    """
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminOrdersThrottle]

    def post(self, request, pk):
        # Entry authority = the same one that guards the detail payload it
        # returns. This is what closes the PII side-channel: no orders.view, no
        # payload, whatever inventory authority the caller holds.
        company, error = _company_context(
            request, CAP_ORDERS_VIEW, _LEGACY_VIEW_ORDERS_ROLES)
        if error:
            return error

        # Write authority: registering the stock exit needs inventory.adjust,
        # decided exactly as can_reprocess_stock_exit is (capability, not role).
        if not has_capability(request.user, company, CAP_INVENTORY_ADJUST):
            return Response(
                {'detail': 'No tienes permiso para mover inventario en esta empresa.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Out of scope is 404, like every other order view.
        order = (
            visible_orders(request.user, company)
            .prefetch_related('items__product').filter(pk=pk).first()
        )
        if order is None:
            return Response({'detail': _ORDER_NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)

        # A pending/failed/refunded order has no pending SALE_EXIT to resolve;
        # that is a state conflict, not a bad request. The service re-checks this
        # on the LOCKED row, so a status flip racing the lock also lands here.
        if order.status != Order.Status.PAID:
            return Response(
                {'detail': 'Solo un pedido pagado puede reprocesar su salida de stock.'},
                status=status.HTTP_409_CONFLICT,
            )

        try:
            result = reprocess_order_stock_exit(order, actor=request.user, request=request)
        except InventoryError as exc:
            # The order stopped being PAID between this check and the lock.
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        order.refresh_from_db()
        payload = _order_detail_payload(request, order, company)
        payload['created_movements'] = len(result['created_movements'])
        return Response(payload)


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

        # Out of scope is 404 BEFORE anything is sent: an e-mail is not a lookup
        # that can be undone once it reaches a customer's inbox.
        order = (
            visible_orders(request.user, company)
            .prefetch_related("items__product").filter(pk=pk).first()
        )
        if order is None:
            return Response({"detail": _ORDER_NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)

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

        order = (
            visible_orders(request.user, company)
            .prefetch_related("items__product").filter(pk=pk).first()
        )
        if order is None:
            return Response({"detail": _ORDER_NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)

        # ORDER-CANCEL-01: a cancelled order keeps its receipt, marked void.
        if not order.paid or order.status not in (Order.Status.PAID, Order.Status.CANCELLED):
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
    SaaS: `sales.orders.manage` in the company decides, whatever the global role.
    Legacy bridge: inventory limited to preparing/ready_for_pickup/shipped/delivered;
    sales/admin/superadmin any value. See order_fulfillment_services.
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

        # Scoped: an admin of company A can never move company B's order, and a
        # member granted branch A can never move branch B's.
        order = visible_orders(request.user, company).filter(pk=pk).first()
        if order is None:
            return Response({'detail': _ORDER_NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)

        # M6 — the rule and the audit entry moved to a shared service, so the
        # native surface cannot drift from this one.
        try:
            fulfillment.change_fulfillment_status(
                order=order, new_status=new_fs, actor=request.user,
                company=company, note=note, request=request,
            )
        except fulfillment.FulfillmentNotAllowed as exc:
            return Response({'detail': exc.detail}, status=status.HTTP_403_FORBIDDEN)
        except fulfillment.FulfillmentBlocked as exc:
            # SERIAL-PICK-01: allowed, and the order is not ready — a device
            # does not leave without somebody saying which one it is. Or, where
            # the company asked for it (CLIENT-SEC-01), its review is pending.
            return Response(
                {'detail': exc.detail, 'code': exc.code, 'pending': sum(exc.pending.values())},
                status=status.HTTP_409_CONFLICT,
            )

        order = Order.objects.select_related('user').prefetch_related('items__product').get(pk=order.pk)
        return Response(_order_detail_payload(request, order, company))


class AdminOrderCancelView(APIView):
    """
    POST /api/admin/orders/{pk}/cancel/ — ORDER-CANCEL-01, body `{reason}`.

    Cancels a PAID web order whose goods have not left the shop: the stock
    comes back once, the internal note is voided and kept, a live boleta or
    factura is kept and flagged as pending its credit note. It does NOT refund:
    `paid` stays true and no payment is touched. See `order_cancellation`.

    `sales.orders.manage` decides (the legacy bridge: sales, admin and
    superadmin). Out of scope is 404, like every order view. A rule that says
    no is 400 (the request) or 409 (the order), with a `code`.
    """
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminOrderStatusChangeThrottle]

    def post(self, request, pk):
        from . import order_cancellation as cancelling

        company, error = _company_context(request, CAP_ORDERS_MANAGE, cancelling.LEGACY_CANCEL_ROLES)
        if error:
            return error

        order = visible_orders(request.user, company).filter(pk=pk).first()
        if order is None:
            return Response({'detail': _ORDER_NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)

        from . import order_refunds

        body = request.data if isinstance(request.data, dict) else {}
        try:
            cancelling.cancel_order(
                order, request.user, body.get('reason'),
                # Only a literal true: the cashier's word, not something truthy.
                goods_returned=body.get('goods_returned') is True,
                refund_method=body.get('refund_method') or None,
                # Review P2: only a literal false annuls a till sale without stock.
                restore_stock=body.get('restore_stock') is not False,
                request=request,
            )
        except (cancelling.OrderCancelError, order_refunds.RefundError) as exc:
            return Response(exc.body(), status=exc.http_status)

        order = Order.objects.select_related('user').prefetch_related('items__product').get(pk=order.pk)
        return Response(_order_detail_payload(request, order, company))


class AdminOrderRefundListView(APIView):
    """
    POST /api/admin/orders/{pk}/refunds/ — COMMERCE-ADMIN-UX-01, body
    `{amount?, method, note?}`: record a PENDING refund of a cancelled order
    (no amount = everything still refundable). A record, not a payment: no
    gateway is called and no payment row changes. `sales.orders.manage`,
    scoped like every order view (404 out of scope). 201 with the order.
    """
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminOrderStatusChangeThrottle]

    def post(self, request, pk):
        from . import order_cancellation as cancelling
        from . import order_refunds

        company, error = _company_context(request, CAP_ORDERS_MANAGE, cancelling.LEGACY_CANCEL_ROLES)
        if error:
            return error
        order = visible_orders(request.user, company).filter(pk=pk).first()
        if order is None:
            return Response({'detail': _ORDER_NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        body = request.data if isinstance(request.data, dict) else {}
        try:
            order_refunds.record_pending(
                order, request.user, amount=body.get('amount'), method=body.get('method') or '',
                note=body.get('note') or '', request=request)
        except order_refunds.RefundError as exc:
            return Response(exc.body(), status=exc.http_status)
        order = Order.objects.select_related('user').prefetch_related('items__product').get(pk=order.pk)
        return Response(_order_detail_payload(request, order, company), status=status.HTTP_201_CREATED)


class AdminOrderRefundConfirmView(APIView):
    """
    POST /api/admin/orders/{pk}/refunds/{refund_pk}/confirm/ — body
    `{reference, note?}`: the money went back. `sales.orders.manage` AND
    `company.manage` (legacy bridge: admin, superadmin); a reference is
    required. Once: a confirmed refund answers 409.
    """
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminOrderStatusChangeThrottle]

    def post(self, request, pk, refund_pk):
        from . import order_cancellation as cancelling
        from . import order_refunds
        from .models import OrderRefund

        company, error = _company_context(request, CAP_ORDERS_MANAGE, cancelling.LEGACY_CANCEL_ROLES)
        if error:
            return error
        order = visible_orders(request.user, company).filter(pk=pk).first()
        refund = OrderRefund.objects.filter(pk=refund_pk, order_id=order.pk).first() if order else None
        if refund is None:
            return Response({'detail': _ORDER_NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        if not order_refunds.may_confirm(request.user, company):
            return Response(
                {'detail': 'Confirmar un reembolso corresponde al administrador de la empresa.'},
                status=status.HTTP_403_FORBIDDEN)
        body = request.data if isinstance(request.data, dict) else {}
        try:
            order_refunds.confirm(refund, request.user, reference=body.get('reference') or '',
                                  note=body.get('note') or '', request=request)
        except order_refunds.RefundError as exc:
            return Response(exc.body(), status=exc.http_status)
        order = Order.objects.select_related('user').prefetch_related('items__product').get(pk=order.pk)
        return Response(_order_detail_payload(request, order, company))


class AdminOrderRiskReviewView(APIView):
    """
    POST /api/admin/orders/{pk}/risk-review/ — {decision: approve|reject, note?}

    CLIENT-SEC-01. A person decides the review an order was waiting for:
    `sales.orders.manage`, on an order this person can see. Once: a review that
    is not waiting answers 409. The decision is ADVICE — it changes nothing of
    the order (see `order_risk.review`).

    Answers with the assessment alone, not with the order: whoever may manage
    orders without seeing them learns the outcome of what they did and no more.
    """
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminOrderStatusChangeThrottle]

    def post(self, request, pk):
        from . import order_risk

        company, error = _company_context(request, CAP_ORDERS_MANAGE, _LEGACY_REVIEW_ORDERS_ROLES)
        if error:
            return error
        ser = OrderRiskReviewSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        order = visible_orders(request.user, company).filter(pk=pk).first()
        if order is None:
            return Response({'detail': _ORDER_NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        try:
            row = order_risk.review(
                order=order, company=company, actor=request.user,
                decision=ser.validated_data['decision'], note=ser.validated_data.get('note', ''),
                request=request,
            )
        except order_risk.ReviewError as exc:
            return Response(exc.body(), status=exc.http_status)
        return Response({'risk': order_risk.describe(row, can_review=True)})


class _OrderUnitsMixin:
    """
    SERIAL-PICK-01 — the devices of a web order. Whoever may change how an
    order is dispatched may say which device it is handing over; the order is
    looked up among the ones this person can see, like every other order view.
    """
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminOrderStatusChangeThrottle]

    def resolve(self, request, pk):
        company, error = _company_context(request, CAP_ORDERS_MANAGE, _LEGACY_MANAGE_ORDERS_ROLES)
        if error:
            return None, None, error
        order = visible_orders(request.user, company).filter(pk=pk).first()
        if order is None:
            return None, None, Response({'detail': _ORDER_NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        return company, order, None

    def payload(self, request, order, company, *, code=status.HTTP_200_OK):
        order = Order.objects.select_related('user').prefetch_related('items__product').get(pk=order.pk)
        return Response(_order_detail_payload(request, order, company), status=code)


class AdminOrderUnitLookupView(_OrderUnitsMixin, APIView):
    """GET ?code=&method= — the device a code names, if this order is waiting for it. Writes nothing."""

    def get(self, request, pk):
        from . import unit_identification

        company, order, error = self.resolve(request, pk)
        if error:
            return error
        method = request.query_params.get('method') or unit_identification.SCANNER
        try:
            unit = unit_identification.candidate_for_order(
                order=order, code=request.query_params.get('code', ''), method=method,
            )
        except unit_identification.UnitIdentificationError as exc:
            return Response(exc.body(), status=exc.http_status)
        return Response({'unit': unit_identification.describe(unit), 'method': method})


class AdminOrderUnitsView(_OrderUnitsMixin, APIView):
    """POST {code, method} — this is the device being handed over."""

    def post(self, request, pk):
        from . import unit_identification

        company, order, error = self.resolve(request, pk)
        if error:
            return error
        data = request.data if hasattr(request.data, 'get') else {}
        try:
            unit_identification.identify_for_order(
                order=order, code=data.get('code', ''), method=data.get('method'),
                actor=request.user, request=request,
            )
        except unit_identification.UnitIdentificationError as exc:
            return Response(exc.body(), status=exc.http_status)
        return self.payload(request, order, company, code=status.HTTP_201_CREATED)


class AdminOrderUnitDetailView(_OrderUnitsMixin, APIView):
    """DELETE — take back a device identified by mistake, while it has not left the shop."""

    def delete(self, request, pk, unit_pk):
        from . import unit_identification

        company, order, error = self.resolve(request, pk)
        if error:
            return error
        try:
            unit_identification.forget(order=order, order_unit_id=unit_pk, actor=request.user, request=request)
        except unit_identification.UnitIdentificationError as exc:
            return Response(exc.body(), status=exc.http_status)
        return self.payload(request, order, company)
