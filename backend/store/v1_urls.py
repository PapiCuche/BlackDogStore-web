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
from .v1_customer_views import V1CustomerOrderViewSet
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
    path('auth/login/', V1LoginView.as_view(), name='v1-auth-login'),
    path('auth/refresh/', V1RefreshView.as_view(), name='v1-auth-refresh'),
    path('auth/logout/', V1LogoutView.as_view(), name='v1-auth-logout'),
    path('auth/me/', V1MeView.as_view(), name='v1-auth-me'),
]
