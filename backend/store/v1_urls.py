"""
URLs for the versioned public API.

Mounted at `/api/v1/` by the project URLconf. `store/urls.py` — the legacy
`/api/` surface — is not imported here and is not modified: v1 is additive, and
a change to one of these files can never move the other.
"""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .v1_views import V1StorefrontCategoryViewSet, V1StorefrontProductViewSet

storefront_router = DefaultRouter()
storefront_router.register(
    r'products', V1StorefrontProductViewSet, basename='v1-storefront-product',
)
storefront_router.register(
    r'categories', V1StorefrontCategoryViewSet, basename='v1-storefront-category',
)

# The tenant is a path segment, so it is present in every route below by
# construction. `[-a-z0-9_]+` matches the shape Company.slug is stored in;
# anything else never reaches a view.
urlpatterns = [
    path(
        'storefront/<slug:company_slug>/',
        include((storefront_router.urls, 'v1-storefront')),
    ),
]
