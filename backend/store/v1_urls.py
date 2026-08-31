"""
URLs for the versioned public API.

Mounted at `/api/v1/` by the project URLconf. `store/urls.py` — the legacy
`/api/` surface — is not imported here and is not modified: v1 is additive, and
a change to one of these files can never move the other.
"""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .v1_auth_views import V1LoginView, V1LogoutView, V1MeView, V1RefreshView
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
    # Native session core (BR-001A). Separate from `/api/auth/`, which belongs
    # to the web frontend and is not modified by any of this.
    path('auth/login/', V1LoginView.as_view(), name='v1-auth-login'),
    path('auth/refresh/', V1RefreshView.as_view(), name='v1-auth-refresh'),
    path('auth/logout/', V1LogoutView.as_view(), name='v1-auth-logout'),
    path('auth/me/', V1MeView.as_view(), name='v1-auth-me'),
]
