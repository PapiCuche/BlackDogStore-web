"""
URL configuration for backend project.

The public/admin product surface lives in Next.js. Django Admin is a development
operator tool only: production must not expose a second administrative login if
someone accidentally makes the backend origin reachable.
"""
from django.conf import settings
from django.urls import path, include


def build_urlpatterns(*, debug: bool):
    """
    Build the URL surface for the selected environment.

    The API is always present. Django Admin is intentionally registered only in
    DEBUG environments. This is defense in depth: the production edge already
    routes /admin to Next.js, but a proxy/firewall mistake must not reveal a
    second administrative surface on the Django origin.
    """
    patterns = [
        path('api/', include('store.urls')),
        # Versioned surface for native clients. ADDITIVE: `api/` above is
        # untouched, and nothing under `api/v1/` is reachable from it.
        path('api/v1/', include('store.v1_urls')),
    ]

    if debug:
        from django.contrib import admin
        patterns.insert(0, path('admin/', admin.site.urls))

    return patterns


urlpatterns = build_urlpatterns(debug=settings.DEBUG)
