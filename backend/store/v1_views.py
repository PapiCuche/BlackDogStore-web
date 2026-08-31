"""
The versioned PUBLIC catalogue — `/api/v1/storefront/<company_slug>/…`.

WHY THIS SURFACE EXISTS

`/api/` identifies its tenant by Host, which is right for the web storefront:
DNS and the reverse proxy set the Host, and page JavaScript cannot. A mobile app
has no equivalent — it talks to one shared API host — so without an explicit
selector it would either see an empty catalogue or, far worse, whichever tenant
the fallback happened to pick.

So the tenant is named IN THE PATH. Not a header, not a query parameter, not a
body field: the path, where it is impossible to add by accident and impossible
to miss when reading a log.

WHAT THE SLUG IS AND IS NOT

It selects a public shop window. It authorizes nothing. Every view here is
read-only and anonymous, and none of them can reach an order, a customer, a
branch or a membership. The private surface (BR-001) will derive its company
from the authenticated user's membership and will not consult this path segment
at all. Two different questions, two different mechanisms, deliberately never
sharing a code path.

ADDITIVE BY CONSTRUCTION

Nothing here imports from or modifies the legacy views, and the legacy URLs are
untouched. `/api/` behaves today exactly as it did before this module existed.
"""
from rest_framework import permissions, viewsets
from rest_framework.exceptions import NotFound

from .tenancy import (
    company_storefront_categories,
    company_storefront_products,
    resolve_public_storefront_company,
)
from .v1_serializers import V1CategorySerializer, V1ProductSerializer

# Sort keys a client may ask for, mapped to the ORM expression that serves them.
# An allowlist because `order_by` takes a field path: without one, `?ordering=`
# becomes a way to sort by — and thereby infer — any column on the model.
V1_PRODUCT_ORDERING = {
    'price': 'price',
    '-price': '-price',
    'name': 'name',
    '-name': '-name',
    'newest': '-created_at',
}


class V1PublicStorefrontMixin:
    """
    Shared tenant resolution for every public v1 catalogue view.

    Public means public: authentication is switched OFF rather than left to the
    project default. `CookieJWTAuthentication` would otherwise run on these
    requests, and a surface that reads a session cookie is a surface whose
    behaviour depends on who is logged in — exactly what a cacheable, anonymous
    shop window must not do.
    """

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def get_storefront_company(self):
        """
        The tenant named by the path, or 404.

        Unknown, inactive, malformed and blank all produce the SAME 404. A 403
        for "inactive" and a 404 for "unknown" would answer, to anyone willing
        to iterate, the question "which companies exist on this platform?" — so
        they are made indistinguishable.
        """
        company = resolve_public_storefront_company(self.kwargs.get('company_slug'))
        if company is None:
            raise NotFound('Storefront not found.')
        return company


class V1StorefrontCategoryViewSet(V1PublicStorefrontMixin, viewsets.ReadOnlyModelViewSet):
    """Categories of one storefront."""

    serializer_class = V1CategorySerializer
    lookup_field = 'slug'

    def get_queryset(self):
        return company_storefront_categories(self.get_storefront_company()).order_by('name')


class V1StorefrontProductViewSet(V1PublicStorefrontMixin, viewsets.ReadOnlyModelViewSet):
    """
    Products of one storefront, addressed by slug.

    `lookup_field = 'slug'` because the app routes on slug and deep links carry
    slugs. It also means a numeric id from another tenant is not even a valid
    address here.
    """

    serializer_class = V1ProductSerializer
    lookup_field = 'slug'

    def get_queryset(self):
        # BORN SCOPED. The company constrains the queryset before any filter
        # runs, so a slug or category belonging to another tenant matches
        # nothing — rather than matching and then being filtered out, which is
        # the same result until the day one filter is forgotten.
        queryset = (
            company_storefront_products(self.get_storefront_company())
            .select_related('category')
            .prefetch_related('reviews')
        )

        params = self.request.query_params
        category = params.get('category')
        search = params.get('search')
        in_stock = params.get('in_stock')
        ordering = params.get('ordering')

        if category:
            queryset = queryset.filter(category__slug=category)
        if search:
            queryset = queryset.filter(name__icontains=search)
        if in_stock == 'true':
            # The annotation, not the stored column: "in stock" has to mean the
            # branch that ships can ship it, or the filter promises deliveries
            # checkout will refuse.
            queryset = queryset.filter(available_stock__gt=0)
        if ordering in V1_PRODUCT_ORDERING:
            queryset = queryset.order_by(V1_PRODUCT_ORDERING[ordering])

        return queryset
