"""
URLs for the versioned public API.

Mounted at `/api/v1/` by the project URLconf. `store/urls.py` — the legacy
`/api/` surface — is not imported here and is not modified: v1 is additive, and
a change to one of these files can never move the other.
"""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .v1_auth_views import V1LoginView, V1LogoutView, V1MeView, V1RefreshView
from .v1_checkout_views import V1CustomerCheckoutView
from .v1_customer_views import (
    V1CustomerOrderViewSet,
    V1CustomerRepairQuoteDecisionView,
    V1CustomerRepairQuoteView,
    V1CustomerRepairViewSet,
)
from .v1_service_views import (
    V1ServiceContextView,
    V1ServiceDiagnosticDetailView,
    V1ServiceQuoteItemDetailView,
    V1ServiceDiagnosticListView,
    V1ServiceExecutionCompleteView,
    V1ServiceExecutionPauseView,
    V1ServiceExecutionResumeView,
    V1ServiceExecutionStartView,
    V1ServiceExecutionView,
    V1ServicePartCandidateView,
    V1ServicePartUsageReverseView,
    V1ServicePartUsageView,
    V1ServiceDeliveryView,
    V1ServiceQualityFailView,
    V1ServiceQualityHistoryView,
    V1ServiceQualityItemView,
    V1ServiceQualityPassView,
    V1ServiceQualityView,
    V1ServiceQuoteCancelView,
    V1ServiceQuoteDetailView,
    V1ServiceQuoteItemView,
    V1ServiceQuoteListView,
    V1ServiceQuotePublishView,
    V1ServiceCustomerSearchView,
    V1ServiceDeviceDetailView,
    V1ServiceDeviceListView,
    V1ServiceOrderAssignmentView,
    V1ServiceOrderDetailView,
    V1ServiceOrderHistoryView,
    V1ServiceOrderListView,
    V1ServiceOrderTransitionView,
)
from .v1_inventory_views import (
    V1InventoryAdjustmentView, V1InventoryMovementsView,
    V1InventoryStockView, V1InventorySummaryView,
)
from .v1_internal_views import (
    V1InternalContextView, V1InternalOrderDetailView,
    V1InternalOrderFulfillmentView, V1InternalOrderListView,
)
from .v1_views import (
    V1StorefrontCategoryViewSet, V1StorefrontConfigView, V1StorefrontProductViewSet,
)

storefront_router = DefaultRouter()
storefront_router.register(
    r'products', V1StorefrontProductViewSet, basename='v1-storefront-product',
)
storefront_router.register(
    r'categories', V1StorefrontCategoryViewSet, basename='v1-storefront-category',
)

# CUSTOMER audience. A separate router under a separate prefix, because a client
# reading their own orders and staff reading the company's are different
# questions with different answers (DEC-API-001).
customer_router = DefaultRouter()
customer_router.register(r'orders', V1CustomerOrderViewSet, basename='v1-customer-order')
# BR-005A — a client's own repairs. `repairs`, not `service-orders`: it is the
# word the customer uses for the thing they are waiting for, and the mobile app
# has called it that since M0. The INTERNAL surface says `service/orders/`
# because that is the word the shop uses. Two audiences, two vocabularies.
customer_router.register(r'repairs', V1CustomerRepairViewSet, basename='v1-customer-repair')

# The tenant is a path segment, so it is present in every route below by
# construction. `[-a-z0-9_]+` matches the shape Company.slug is stored in;
# anything else never reaches a view.
urlpatterns = [
    path(
        'storefront/<slug:company_slug>/config/',
        V1StorefrontConfigView.as_view(), name='v1-storefront-config',
    ),
    path(
        'storefront/<slug:company_slug>/',
        include((storefront_router.urls, 'v1-storefront')),
    ),
    # Native session core (BR-001A). Separate from `/api/auth/`, which belongs
    # to the web frontend and is not modified by any of this.
    path(
        'customer/<slug:company_slug>/checkout/',
        V1CustomerCheckoutView.as_view(), name='v1-customer-checkout',
    ),
    # CUSTOMER — the quote on my own repair, and my answer to it. Literal
    # sub-paths on the `customer/` prefix, registered BEFORE the router
    # include() on that same prefix, following the file's existing ordering.
    path(
        'customer/<slug:company_slug>/repairs/<int:pk>/quote/',
        V1CustomerRepairQuoteView.as_view(), name='v1-customer-repair-quote',
    ),
    path(
        'customer/<slug:company_slug>/repairs/<int:pk>/quotes/<int:quote_id>/decision/',
        V1CustomerRepairQuoteDecisionView.as_view(),
        name='v1-customer-repair-quote-decision',
    ),
    path(
        'customer/<slug:company_slug>/',
        include((customer_router.urls, 'v1-customer')),
    ),
    # INTERNAL audience. Staff reading the COMPANY's records under a
    # capability — a different question from a client reading their own, so a
    # different URL space (DEC-API-001).
    path(
        'internal/<slug:company_slug>/context/',
        V1InternalContextView.as_view(), name='v1-internal-context',
    ),
    path(
        'internal/<slug:company_slug>/orders/',
        V1InternalOrderListView.as_view(), name='v1-internal-orders',
    ),
    path(
        'internal/<slug:company_slug>/orders/<int:pk>/',
        V1InternalOrderDetailView.as_view(), name='v1-internal-order-detail',
    ),
    path(
        'internal/<slug:company_slug>/orders/<int:pk>/fulfillment/',
        V1InternalOrderFulfillmentView.as_view(), name='v1-internal-order-fulfillment',
    ),
    # INTERNAL inventory. Tenant-scoped AND branch-scoped.
    path(
        'internal/<slug:company_slug>/inventory/summary/',
        V1InventorySummaryView.as_view(), name='v1-internal-inventory-summary',
    ),
    path(
        'internal/<slug:company_slug>/inventory/stock/',
        V1InventoryStockView.as_view(), name='v1-internal-inventory-stock',
    ),
    path(
        'internal/<slug:company_slug>/inventory/movements/',
        V1InventoryMovementsView.as_view(), name='v1-internal-inventory-movements',
    ),
    path(
        'internal/<slug:company_slug>/inventory/adjustments/',
        V1InventoryAdjustmentView.as_view(), name='v1-internal-inventory-adjustments',
    ),
    # INTERNAL technical service (BR-005A / M8). Tenant-scoped AND
    # branch-scoped: an order lives in the shop that received the device.
    path(
        'internal/<slug:company_slug>/service/context/',
        V1ServiceContextView.as_view(), name='v1-internal-service-context',
    ),
    # Intake needs to find the person at the counter. The CRM itself stays on
    # the web admin surface; this is the narrowest slice that makes an order
    # possible, under `service.customers.view`.
    path(
        'internal/<slug:company_slug>/service/customers/',
        V1ServiceCustomerSearchView.as_view(), name='v1-internal-service-customers',
    ),
    path(
        'internal/<slug:company_slug>/service/devices/',
        V1ServiceDeviceListView.as_view(), name='v1-internal-service-devices',
    ),
    path(
        'internal/<slug:company_slug>/service/devices/<int:pk>/',
        V1ServiceDeviceDetailView.as_view(), name='v1-internal-service-device-detail',
    ),
    path(
        'internal/<slug:company_slug>/service/orders/',
        V1ServiceOrderListView.as_view(), name='v1-internal-service-orders',
    ),
    path(
        'internal/<slug:company_slug>/service/orders/<int:pk>/',
        V1ServiceOrderDetailView.as_view(), name='v1-internal-service-order-detail',
    ),
    path(
        'internal/<slug:company_slug>/service/orders/<int:pk>/history/',
        V1ServiceOrderHistoryView.as_view(), name='v1-internal-service-order-history',
    ),
    path(
        'internal/<slug:company_slug>/service/orders/<int:pk>/transition/',
        V1ServiceOrderTransitionView.as_view(), name='v1-internal-service-order-transition',
    ),
    path(
        'internal/<slug:company_slug>/service/orders/<int:pk>/assignment/',
        V1ServiceOrderAssignmentView.as_view(), name='v1-internal-service-order-assignment',
    ),
    # BR-005B — diagnosis and quotes hang off an order, because that is what
    # they are about. Reaching one goes through the order's own lookup, so the
    # branch gate applies without a second check.
    path(
        'internal/<slug:company_slug>/service/orders/<int:pk>/diagnostics/',
        V1ServiceDiagnosticListView.as_view(),
        name='v1-internal-service-diagnostics',
    ),
    path(
        'internal/<slug:company_slug>/service/orders/<int:pk>/diagnostics/<int:diagnostic_id>/',
        V1ServiceDiagnosticDetailView.as_view(),
        name='v1-internal-service-diagnostic-detail',
    ),
    path(
        'internal/<slug:company_slug>/service/orders/<int:pk>/quotes/',
        V1ServiceQuoteListView.as_view(), name='v1-internal-service-quotes',
    ),
    path(
        'internal/<slug:company_slug>/service/orders/<int:pk>/quotes/<int:quote_id>/',
        V1ServiceQuoteDetailView.as_view(), name='v1-internal-service-quote-detail',
    ),
    path(
        'internal/<slug:company_slug>/service/orders/<int:pk>/quotes/<int:quote_id>/items/',
        V1ServiceQuoteItemView.as_view(), name='v1-internal-service-quote-items',
    ),
    path(
        'internal/<slug:company_slug>/service/orders/<int:pk>/quotes/<int:quote_id>/items/<int:item_id>/',
        V1ServiceQuoteItemDetailView.as_view(), name='v1-internal-service-quote-item-detail',
    ),
    path(
        'internal/<slug:company_slug>/service/orders/<int:pk>/quotes/<int:quote_id>/publish/',
        V1ServiceQuotePublishView.as_view(), name='v1-internal-service-quote-publish',
    ),
    path(
        'internal/<slug:company_slug>/service/orders/<int:pk>/quotes/<int:quote_id>/cancel/',
        V1ServiceQuoteCancelView.as_view(), name='v1-internal-service-quote-cancel',
    ),
    # --- M10 / BR-005C — the bench ---
    #
    # `parts/candidates/` is declared BEFORE `parts/<int:usage_id>/...` would
    # ever be considered, and it is a literal segment rather than an id, so the
    # two cannot shadow each other. Every path hangs off `orders/<pk>/`, which
    # is what folds the branch gate in: the order lookup is scoped, so nothing
    # underneath it can be reached out of scope.
    path(
        'internal/<slug:company_slug>/service/orders/<int:pk>/execution/',
        V1ServiceExecutionView.as_view(), name='v1-internal-service-execution',
    ),
    path(
        'internal/<slug:company_slug>/service/orders/<int:pk>/execution/start/',
        V1ServiceExecutionStartView.as_view(),
        name='v1-internal-service-execution-start',
    ),
    path(
        'internal/<slug:company_slug>/service/orders/<int:pk>/execution/complete/',
        V1ServiceExecutionCompleteView.as_view(),
        name='v1-internal-service-execution-complete',
    ),
    path(
        'internal/<slug:company_slug>/service/orders/<int:pk>/execution/pause/',
        V1ServiceExecutionPauseView.as_view(),
        name='v1-internal-service-execution-pause',
    ),
    path(
        'internal/<slug:company_slug>/service/orders/<int:pk>/execution/resume/',
        V1ServiceExecutionResumeView.as_view(),
        name='v1-internal-service-execution-resume',
    ),
    path(
        'internal/<slug:company_slug>/service/orders/<int:pk>/parts/candidates/',
        V1ServicePartCandidateView.as_view(),
        name='v1-internal-service-part-candidates',
    ),
    path(
        'internal/<slug:company_slug>/service/orders/<int:pk>/parts/',
        V1ServicePartUsageView.as_view(), name='v1-internal-service-parts',
    ),
    path(
        'internal/<slug:company_slug>/service/orders/<int:pk>/parts/<int:usage_id>/reverse/',
        V1ServicePartUsageReverseView.as_view(),
        name='v1-internal-service-part-reverse',
    ),
    # --- M11 / BR-005D — control de calidad ---
    #
    # `history/` is a literal segment and `items/<id>/` an id, so nothing
    # shadows anything. Every path hangs off `orders/<pk>/`, which is what folds
    # the branch gate in.
    path(
        'internal/<slug:company_slug>/service/orders/<int:pk>/quality/',
        V1ServiceQualityView.as_view(), name='v1-internal-service-quality',
    ),
    path(
        'internal/<slug:company_slug>/service/orders/<int:pk>/quality/history/',
        V1ServiceQualityHistoryView.as_view(),
        name='v1-internal-service-quality-history',
    ),
    path(
        'internal/<slug:company_slug>/service/orders/<int:pk>/quality/items/<int:item_id>/',
        V1ServiceQualityItemView.as_view(), name='v1-internal-service-quality-item',
    ),
    path(
        'internal/<slug:company_slug>/service/orders/<int:pk>/quality/pass/',
        V1ServiceQualityPassView.as_view(), name='v1-internal-service-quality-pass',
    ),
    path(
        'internal/<slug:company_slug>/service/orders/<int:pk>/quality/fail/',
        V1ServiceQualityFailView.as_view(), name='v1-internal-service-quality-fail',
    ),
    # --- M12 / BR-005E — entrega ---
    path(
        'internal/<slug:company_slug>/service/orders/<int:pk>/delivery/',
        V1ServiceDeliveryView.as_view(), name='v1-internal-service-delivery',
    ),
    path('auth/login/', V1LoginView.as_view(), name='v1-auth-login'),
    path('auth/refresh/', V1RefreshView.as_view(), name='v1-auth-refresh'),
    path('auth/logout/', V1LogoutView.as_view(), name='v1-auth-logout'),
    path('auth/me/', V1MeView.as_view(), name='v1-auth-me'),
]
