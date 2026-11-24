"""
DEVICE-COMMERCE-01 · DC2 — the HTTP surface of models, options and variants.

PUBLIC  /api/catalog/models/               models of the storefront's company
        /api/catalog/models/<slug>/        one model: options, new variants, used devices
ADMIN   /api/admin/catalog/…               products.view to read, products.manage to write

The tenant of a public request is the STOREFRONT's (Host), never the body; an
admin request's is the caller's, as every catalogue screen. A model, an option
or a product of another company answers 404, like one that does not exist.
"""
from __future__ import annotations

from django.db import transaction
from django.db.models import Count, Exists, Min, OuterRef, Prefetch, Q
from rest_framework import permissions, serializers as drf_serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import accessory_compat, catalog_image_sources, catalog_readiness, device_catalog, model_palette
from .admin_views import (
    CAP_PRODUCTS_MANAGE, CAP_PRODUCTS_VIEW, _LEGACY_MANAGE_CATALOG_ROLES, _LEGACY_VIEW_CATALOG_ROLES,
    AdminProductWriteThrottle, _company_context, write_product_fields,
)
from .models import (
    AdminAuditLog, CatalogAlias, CosmeticGrade, Product, ProductModel, ProductVariantOption, StockUnit,
    StorefrontPageSettings, VariantAttribute, VariantOption,
)
from .tenancy import company_fulfillment_branch, company_storefront_products, resolve_storefront_company
from .throttles import AdminProductsThrottle

_NOT_FOUND = {'detail': 'No encontrado.'}


_ERROR_STATUS = {
    'not_found': status.HTTP_404_NOT_FOUND,
    'stale': status.HTTP_409_CONFLICT,
    'no_palette': status.HTTP_409_CONFLICT,
    'already_enabled': status.HTTP_409_CONFLICT,
    'history_needs_superior': status.HTTP_403_FORBIDDEN,
    'variant_reserved': status.HTTP_409_CONFLICT,
    'line_change_with_units': status.HTTP_409_CONFLICT,
    'variant_in_use': status.HTTP_409_CONFLICT,
    'variant_archived': status.HTTP_409_CONFLICT,
    # CATALOG-ADMIN-UX-B: the pair to exclude is already a variant of the model (the body names them).
    'exclusion_held_by_variants': status.HTTP_409_CONFLICT,
}


def _error(exc: device_catalog.CatalogError) -> Response:
    return Response(exc.body(), status=_ERROR_STATUS.get(exc.code, status.HTTP_400_BAD_REQUEST))


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------

def _public_models(company):
    if company is None:
        return ProductModel.objects.none()
    # VARIANT-SAFE-01: a model the company deactivated leaves the public shop.
    return ProductModel.objects.filter(company=company, is_published_online=True, is_active=True)


class PublicModelListView(APIView):
    """GET /api/catalog/models/?category=<slug>&line=new|used"""

    permission_classes = [permissions.AllowAny]

    def get(self, request):
        company = resolve_storefront_company(request)
        # PR E: featured models first, then the company's order. Nobody featured = the order it always was.
        models = _public_models(company).select_related('category').order_by('-is_featured', 'sort_order', 'name', 'pk')
        category = request.query_params.get('category')
        if category:
            models = models.filter(category__slug=category)
        sold = company_storefront_products(company).filter(product_model__in=models)
        branch = company_fulfillment_branch(company)

        new_by_model = dict(
            sold.filter(condition_line=Product.CONDITION_NEW).values('product_model')
            .annotate(n=Count('pk')).values_list('product_model', 'n')
        )
        used_by_model = {}
        if branch is not None:
            used_by_model = dict(
                device_catalog.offered_units(sold.filter(condition_line=Product.CONDITION_USED), branch)
                .values('product__product_model').annotate(n=Count('pk'))
                .values_list('product__product_model', 'n')
            )
        new_prices = dict(
            sold.filter(condition_line=Product.CONDITION_NEW).values('product_model')
            .annotate(p=Min('price')).values_list('product_model', 'p')
        )
        # «Desde» names what can be bought now; the last configured price only when nothing can (P2-F2 review).
        new_prices.update(device_catalog.new_for_sale_prices(
            sold.filter(condition_line=Product.CONDITION_NEW), branch,
        ))
        used_prices = {}
        if branch is not None:
            used_prices = dict(
                device_catalog.offered_units(sold.filter(condition_line=Product.CONDITION_USED), branch)
                .values('product__product_model').annotate(p=Min('sale_price'))
                .values_list('product__product_model', 'p')
            )

        # SUPPLIER-DEVICE-FLOW-02 (owner, 2026-10-20) — PUBLISHED IS NOT
        # AVAILABLE, and SOLD OUT IS NOT HIDDEN. A published model stays on the
        # list while it has a published variant; `sold_out` says that nothing
        # of it can be bought right now (no device on the shelf, no valid offer).
        used_line_models = set(
            sold.filter(condition_line=Product.CONDITION_USED).values_list('product_model', flat=True)
        )
        new_available = device_catalog.models_with_new_for_sale(
            sold.filter(condition_line=Product.CONDITION_NEW), branch,
        )

        line = request.query_params.get('line')
        results = []
        for model in models:
            has_new, has_units = bool(new_by_model.get(model.pk)), bool(used_by_model.get(model.pk))
            # A model sold only as used devices, with none on the shelf, is
            # listed under its line as sold out — never with a device to offer.
            has_used = has_units or (model.pk in used_line_models and not has_new)
            if not (has_new or has_used):
                continue
            if line == Product.CONDITION_NEW and not has_new:
                continue
            if line == Product.CONDITION_USED and not has_used:
                continue
            prices = [p for p in (new_prices.get(model.pk) if line != 'used' else None,
                                  used_prices.get(model.pk) if line != 'new' else None) if p is not None]
            for_sale = {
                Product.CONDITION_NEW: model.pk in new_available,
                Product.CONDITION_USED: has_units,
            }
            sold_out = not (for_sale[line] if line in for_sale else any(for_sale.values()))
            results.append({
                'slug': model.slug, 'name': model.name, 'image_url': model.image_url,
                'category': {'slug': model.category.slug, 'name': model.category.name} if model.category_id else None,
                # Sold out, it is the last configured price: shown, not sold.
                'from_price': f'{min(prices):.2f}' if prices else None,
                'has_new': has_new, 'has_used': has_used,
                'sold_out': sold_out,
                # PR E: only on a featured card, so a company that never features one gets the cards it always got.
                **({'is_featured': True} if model.is_featured else {}),
            })
        labels = device_catalog.line_labels(company)
        return Response({
            'lines': {
                'new': {'label': labels['new'], 'count': sum(1 for r in results if r['has_new'])},
                'used': {'label': labels['used'], 'count': sum(1 for r in results if r['has_used'])},
            },
            'results': results,
        })


class PublicModelDetailView(APIView):
    """GET /api/catalog/models/<slug>/"""

    permission_classes = [permissions.AllowAny]

    def get(self, request, slug):
        from . import unit_hold_services

        company = resolve_storefront_company(request)
        model = _public_models(company).select_related('category', 'company').filter(slug=slug).first()
        if model is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        # Cleanup on read: what a cart stopped holding is offered again.
        unit_hold_services.release_expired(company=company)
        return Response(device_catalog.public_model(model, company_fulfillment_branch(company)))


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

def _view(request):
    return _company_context(request, CAP_PRODUCTS_VIEW, _LEGACY_VIEW_CATALOG_ROLES)


def _manage(request):
    return _company_context(request, CAP_PRODUCTS_MANAGE, _LEGACY_MANAGE_CATALOG_ROLES)


def _option_payload(option):
    # FASE 1.2 · Y: whether the swatch is an approximation pending commercial validation (admin only).
    return {'id': option.pk, 'label': option.label, 'swatch_hex': option.swatch_hex, 'sort_order': option.sort_order,
            'swatch_is_approximate': option.swatch_is_approximate}


def _attribute_payload(attribute):
    return {
        'id': attribute.pk, 'name': attribute.name, 'kind': attribute.kind, 'role': attribute.role,
        'sort_order': attribute.sort_order,
        'options': [_option_payload(option) for option in attribute.options.all()],
    }


def _model_payload(model, *, variants=False, has_palette=None, counts=None):
    # A1: the palette, read once for the model and for each of its variants' status. A list passes
    # `has_palette` it computed for every model at once, and reads nothing more here.
    # CATALOG-ADMIN-UX-B: a list passes `counts` too ({'variant_count', 'used_unit_count'}, read for every model at
    # once); the detail reads its variants and their histories in a fixed number of queries, however many there are.
    palette = model_palette.palette_of(model) if (variants or has_palette is None) else {}
    products = None
    if variants:
        # CATALOG-VARIANT-IMAGES: the count of pictures in the same query as the rows, not one per variant.
        products = list(
            model.variants.annotate(image_count=Count('images')).order_by('condition_line', 'pk')
            .prefetch_related(Prefetch(
                'variant_options',
                queryset=ProductVariantOption.objects.select_related('attribute', 'option')
                .order_by('attribute__sort_order', 'attribute__name'),
            ))
        )
    if counts is None:
        counts = {
            'variant_count': len(products) if products is not None else model.variants.count(),
            # The storefront's branch, like the availability the shop shows.
            'used_unit_count': StockUnit.objects.filter(
                product__product_model=model, product__condition_line=Product.CONDITION_USED,
                status=StockUnit.Status.AVAILABLE, branch=company_fulfillment_branch(model.company),
            ).count(),
        }
    data = {
        'id': model.pk, 'name': model.name, 'slug': model.slug,
        'category_id': model.category_id, 'category_name': model.category.name if model.category_id else '',
        'description': model.description, 'image_url': model.image_url,
        'is_published_online': model.is_published_online, 'sort_order': model.sort_order,
        # PR E: shown first in the public list.
        'is_featured': model.is_featured,
        # VARIANT-SAFE-01: a deactivated model is out of the public shop.
        'is_active': model.is_active,
        'variant_count': counts['variant_count'],
        'used_unit_count': counts['used_unit_count'],
        # A1: whether the model says which options it admits (the «Opciones de este modelo» screen).
        'has_palette': bool(palette) if has_palette is None else bool(has_palette),
    }
    if variants:
        rows = []
        # CATALOG-ADMIN-UX-B: every variant's history in five aggregate queries, not seven per variant.
        histories = device_catalog.variant_histories([product.pk for product in products])
        for product in products:
            history = histories[product.pk]
            links = list(product.variant_options.all())
            rows.append({
                'product_id': product.pk, 'name': product.name, 'slug': product.slug,
                'condition_line': product.condition_line, 'price': str(product.price),
                'is_serialized': product.is_serialized, 'stock': product.inventory,
                'is_published_online': product.is_published_online,
                # VARIANT-SAFE-01: deactivated and archived are two states the row shows.
                'is_active': product.is_active,
                'archived': product.is_archived,
                'archived_at': product.archived_at.isoformat() if product.archived_at else None,
                'archive_reason': product.archive_reason,
                # CATALOG-VARIANT-IMAGES: what the variant shows (the primary's mirror) and how many it has.
                'image_url': product.image_url, 'image_count': product.image_count,
                # CATALOG-VARIANTS-UX: what it carries, and so how it may change.
                'history': history, **device_catalog.edit_rules(history),
                'options': [
                    {'attribute_id': link.attribute_id, 'attribute_name': link.attribute.name,
                     'option_id': link.option_id, 'label': link.option.label, 'swatch_hex': link.option.swatch_hex}
                    for link in links
                ],
                # A1: how it stands against the palette (null without one). Reported, never corrected.
                'palette_status': model_palette.variant_status(
                    model, [link.option_id for link in links], palette=palette),
            })
        data['variants'] = rows
    return data


#: CATALOG-ADMIN-UX-B — the most rows one page of the models list carries.
MODEL_LIST_MAX_PAGE_SIZE = 100
_TRUE, _FALSE = ('1', 'true', 'yes', 'si', 'sí'), ('0', 'false', 'no')


def _bad_param(field, text):
    return Response({'detail': text, 'code': 'invalid', 'field': field}, status=status.HTTP_400_BAD_REQUEST)


def _positive(value):
    text = str(value).strip()
    return int(text) if model_palette.is_ascii_id(text) and int(text) > 0 else None


class AdminModelListView(APIView):
    """
    GET  /api/admin/catalog/models/   the company's models
    POST /api/admin/catalog/models/   create one (`products.manage`)

    CATALOG-ADMIN-UX-B — the GET searches and filters ON THE SERVER, in a fixed number of queries however many
    models and variants the company has:

        search=<text>         the name (case and accents folded), the address (slug) or a supplier alias of the model
        category=<id>|none    one of the company's categories, or the models without one
        active=true|false     VARIANT-SAFE-01's «activo»
        incomplete=1          with live variants incomplete or outside the palette
        missing=1             with valid configurations still without a variant
        no_palette=1          without a palette (they admit any option of the company)
        page, page_size       a bounded page (at most 100); WITHOUT `page_size` every row comes, as always

    Answers `results`, `count` (after every filter), `facets` (how many of the searched models each state flag
    would keep) and, with `page_size`, `page` / `page_size`. Every row says its palette state too.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company, error = _view(request)
        if error:
            return error
        params = request.query_params
        models = ProductModel.objects.filter(company=company)
        search = ' '.join(str(params.get('search') or '').split())[:100]
        if search:
            from .configuration_resolver import phrase_key

            folded, key = device_catalog.normalize_model_name(search), phrase_key(search)
            aliases = CatalogAlias.objects.filter(company=company, scope=CatalogAlias.SCOPE_MODEL, model=OuterRef('pk'))
            match = Q(name__icontains=search) | Q(slug__icontains=search)
            if folded:
                match |= Q(normalized_name__contains=folded)
            alias_match = Q(alias__icontains=search) | (Q(alias_key__contains=key) if key else Q())
            models = models.filter(match | Exists(aliases.filter(alias_match)))
        category = str(params.get('category') or '').strip()
        if category == 'none':
            models = models.filter(category__isnull=True)
        elif category:
            if _positive(category) is None:
                return _bad_param('category', 'La categoría es un número o «none».')
            models = models.filter(category_id=_positive(category))
        active = str(params.get('active') or '').strip().lower()
        if active in _TRUE:
            models = models.filter(is_active=True)
        elif active in _FALSE:
            models = models.filter(is_active=False)
        elif active:
            return _bad_param('active', '«active» es true o false.')
        page_size = page = None
        if params.get('page_size') not in (None, ''):
            page_size = _positive(params.get('page_size'))
            if page_size is None:
                return _bad_param('page_size', 'El tamaño de página es un número positivo.')
            page_size = min(page_size, MODEL_LIST_MAX_PAGE_SIZE)
            page = _positive(params.get('page') or 1)
            if page is None:
                return _bad_param('page', 'La página es un número positivo.')

        states = model_palette.catalogue_states(company, models)
        empty = {'has_palette': False, 'valid_without_variant': None, 'incomplete_variant_count': 0,
                 'outside_variant_count': 0}
        rows = list(models.select_related('category'))
        flags = {
            'incomplete': lambda s: s['incomplete_variant_count'] + s['outside_variant_count'] > 0,
            'missing': lambda s: bool(s['valid_without_variant']),
            'no_palette': lambda s: not s['has_palette'],
        }
        facets = {name: sum(1 for m in rows if test(states.get(m.pk, empty))) for name, test in flags.items()}
        for name, test in flags.items():
            if str(params.get(name) or '').strip().lower() in _TRUE:
                rows = [m for m in rows if test(states.get(m.pk, empty))]
        count = len(rows)
        if page_size is not None:
            rows = rows[(page - 1) * page_size:page * page_size]

        variant_counts = dict(
            Product.objects.filter(product_model__in=[m.pk for m in rows]).order_by().values('product_model')
            .annotate(n=Count('pk')).values_list('product_model', 'n')
        ) if rows else {}
        branch = company_fulfillment_branch(company)
        used_counts = dict(
            StockUnit.objects.filter(
                product__product_model__in=[m.pk for m in rows], product__condition_line=Product.CONDITION_USED,
                status=StockUnit.Status.AVAILABLE, branch=branch,
            ).order_by().values('product__product_model').annotate(n=Count('pk'))
            .values_list('product__product_model', 'n')
        ) if rows and branch is not None else {}
        results = []
        for model in rows:
            state = states.get(model.pk, empty)
            results.append({
                **_model_payload(model, has_palette=state['has_palette'], counts={
                    'variant_count': variant_counts.get(model.pk, 0), 'used_unit_count': used_counts.get(model.pk, 0)}),
                'valid_without_variant': state['valid_without_variant'],
                'incomplete_variant_count': state['incomplete_variant_count'],
                'outside_variant_count': state['outside_variant_count'],
            })
        body = {'results': results, 'count': count, 'facets': facets}
        if page_size is not None:
            body.update(page=page, page_size=page_size)
        return Response(body)

    def post(self, request):
        company, error = _manage(request)
        if error:
            return error
        data = request.data
        try:
            model = device_catalog.create_model(
                company=company, name=data.get('name'), category_id=data.get('category_id'),
                slug=data.get('slug') or '', description=data.get('description') or '',
                image_url=data.get('image_url') or '',
                is_published_online=data.get('is_published_online', True) not in (False, 'false', '0', 0),
                sort_order=data.get('sort_order') or 0, actor=request.user, request=request,
            )
        except device_catalog.CatalogError as exc:
            return _error(exc)
        return Response(_model_payload(model, variants=True), status=status.HTTP_201_CREATED)


class AdminModelDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        company, error = _view(request)
        if error:
            return error
        model = ProductModel.objects.filter(company=company, pk=pk).first()
        if model is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        return Response(_model_payload(model, variants=True))

    def patch(self, request, pk):
        company, error = _manage(request)
        if error:
            return error
        model = ProductModel.objects.filter(company=company, pk=pk).first()
        if model is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        allowed = ('name', 'category_id', 'slug', 'description', 'image_url', 'is_published_online', 'sort_order',
                   'is_featured', 'is_active')
        fields = {key: request.data[key] for key in allowed if key in request.data}
        try:
            model = device_catalog.update_model(model, actor=request.user, request=request, **fields)
        except device_catalog.CatalogError as exc:
            return _error(exc)
        return Response(_model_payload(model, variants=True))


class AdminModelBulkVisibilityView(APIView):
    """
    POST /api/admin/catalog/models/bulk-visibility/ — {"action": "publish"|"hide", "ids": [..]}

    COMMERCE-ADMIN-UX-01 — «Publicación e-commerce». `products.manage`, like
    the model form. Answers `{action, changed, unchanged, total}`.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        from .throttles import AdminProductWriteThrottle

        return [AdminProductWriteThrottle()]

    def post(self, request):
        company, error = _manage(request)
        if error:
            return error
        data = request.data if isinstance(request.data, dict) else {}
        action = data.get('action')
        ids = data.get('ids')
        if action not in ('publish', 'hide'):
            return Response({'detail': 'Indica la acción: «publish» o «hide».'}, status=status.HTTP_400_BAD_REQUEST)
        if (not isinstance(ids, list) or not ids
                or any(isinstance(i, bool) or not isinstance(i, int) or i <= 0 for i in ids)):
            return Response({'detail': '«ids» debe ser una lista de identificadores de modelo.'},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            result = device_catalog.set_models_visibility(
                company=company, ids=ids, publish=action == 'publish', actor=request.user, request=request,
            )
        except device_catalog.CatalogError as exc:
            return _error(exc)
        return Response({'action': action, **result})


class AdminAttributeListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company, error = _view(request)
        if error:
            return error
        attributes = VariantAttribute.objects.filter(company=company).prefetch_related('options')
        return Response({'results': [_attribute_payload(a) for a in attributes]})

    def post(self, request):
        company, error = _manage(request)
        if error:
            return error
        try:
            attribute = device_catalog.create_attribute(
                company=company, name=request.data.get('name'),
                kind=request.data.get('kind') or VariantAttribute.KIND_TEXT,
                role=request.data.get('role') or VariantAttribute.ROLE_OTHER,
                sort_order=request.data.get('sort_order') or 0, actor=request.user, request=request,
            )
        except device_catalog.CatalogError as exc:
            return _error(exc)
        return Response(_attribute_payload(attribute), status=status.HTTP_201_CREATED)


class AdminAttributeDetailView(APIView):
    """PATCH /api/admin/catalog/attributes/<pk>/ {name?, role?, sort_order?} — A1: the role of an attribute."""

    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, pk):
        company, error = _manage(request)
        if error:
            return error
        attribute = VariantAttribute.objects.filter(company=company, pk=pk).prefetch_related('options').first()
        if attribute is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        if not isinstance(request.data, dict):
            return Response({'detail': 'Envía un objeto con name, role o sort_order.', 'code': 'invalid'},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            attribute = device_catalog.update_attribute(
                attribute, name=request.data.get('name'), role=request.data.get('role'),
                sort_order=request.data.get('sort_order'), actor=request.user, request=request,
            )
        except device_catalog.CatalogError as exc:
            return _error(exc)
        return Response(_attribute_payload(attribute))


class AdminModelPaletteView(APIView):
    """
    GET   /api/admin/catalog/models/<pk>/palette/    the palette, every combination and its state
    PUT   /api/admin/catalog/models/<pk>/palette/    {"option_ids": [...], "expected_option_ids": [...]?} — the
                                                     palette as a SET; `expected_option_ids` (the palette last
                                                     read) makes a save over someone else's a 409
    PATCH /api/admin/catalog/models/<pk>/palette/    {"option_id", "swatch_hex"?, "swatch_is_approximate"?} — this
                                                     model's own swatch for one colour of its palette ('' = the
                                                     company's); FASE 1.2 · Y (`model_palette.set_model_swatch`)

    CATALOG-COMMERCIAL-01 · A1 («Opciones de este modelo»). `products.view` to
    read, `products.manage` to write. A model of another company answers 404;
    an option of another company in the body refuses the whole call (404).
    Nothing here reaches the public catalogue.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        company, error = _view(request)
        if error:
            return error
        model = ProductModel.objects.filter(company=company, pk=pk).first()
        if model is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        return Response(model_palette.payload(model))

    def put(self, request, pk):
        company, error = _manage(request)
        if error:
            return error
        model = ProductModel.objects.filter(company=company, pk=pk).first()
        if model is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        data = request.data if isinstance(request.data, dict) else {}
        # B1-SIMREG: `add_option_ids` ADDS to the palette (never removes); `option_ids` replaces it as a set. One or the other.
        if 'add_option_ids' in data:
            if 'option_ids' in data or not isinstance(data.get('add_option_ids'), list):
                return Response({'detail': 'add_option_ids debe ser una lista, sin option_ids.', 'code': 'invalid',
                                 'field': 'add_option_ids'}, status=status.HTTP_400_BAD_REQUEST)
            try:
                changed = model_palette.add_to_palette(model, option_ids=data['add_option_ids'], actor=request.user,
                                                       request=request)
            except device_catalog.CatalogError as exc:
                return _error(exc)
            return Response({**model_palette.payload(model), 'changed': changed})
        option_ids = data.get('option_ids')
        if not isinstance(option_ids, list):
            return Response({'detail': 'option_ids debe ser una lista.', 'code': 'invalid', 'field': 'option_ids'},
                            status=status.HTTP_400_BAD_REQUEST)
        # PALETTE-SUGGEST-01 review: `expected_option_ids` (the palette last read) makes a save over someone else's
        # a 409. Present, it must be a list; absent, the PUT replaces as before.
        expected = data.get('expected_option_ids') if 'expected_option_ids' in data else None
        if 'expected_option_ids' in data and not isinstance(expected, list):
            return Response({'detail': 'expected_option_ids debe ser una lista.', 'code': 'invalid',
                             'field': 'expected_option_ids'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            changed = model_palette.set_palette(model, option_ids=option_ids, actor=request.user, request=request,
                                                expected_option_ids=expected)
        except device_catalog.CatalogError as exc:
            return _error(exc)
        return Response({**model_palette.payload(model), 'changed': changed})

    def patch(self, request, pk):
        company, error = _manage(request)
        if error:
            return error
        model = ProductModel.objects.filter(company=company, pk=pk).first()
        if model is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        data = request.data if isinstance(request.data, dict) else {}
        try:
            body = model_palette.set_model_swatch(
                model, option_id=data.get('option_id'), swatch_hex=data.get('swatch_hex'),
                swatch_is_approximate=data.get('swatch_is_approximate'), actor=request.user, request=request,
            )
        except device_catalog.CatalogError as exc:
            return _error(exc)
        return Response(body)


class AdminModelExclusionsView(APIView):
    """
    POST   /api/admin/catalog/models/<pk>/exclusions/   {"pairs": [[a, b], ...]} — these configurations do NOT exist
    DELETE /api/admin/catalog/models/<pk>/exclusions/   {"pairs": [[a, b], ...]} — they exist again

    CATALOG-ADMIN-UX-B — the panel's «Combinaciones que no existen» (a SIM type a region does not sell, say), on
    `ModelOptionExclusion` (CATALOG-REFERENCE-01 · PR B): `products.manage`, audited with the company, under the
    model's lock; a model of another company answers 404. ADDING: each pair is two options of the model's palette of
    two different attributes; an option of another company refuses the whole call (404 `not_found`), and a pair that
    an existing variant IS answers 409 `exclusion_held_by_variants` naming those variants — nothing is written.
    REMOVING deletes only this model's own rows: a pair it does not have (an unknown id, another company's option)
    is simply not there and is ignored (`changed.removed` counts what went); it never invalidates a variant. Both
    answer the palette payload (as `PUT …/palette/`) plus `changed`.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        return [AdminProductWriteThrottle()]

    def _change(self, request, pk, write):
        company, error = _manage(request)
        if error:
            return error
        model = ProductModel.objects.filter(company=company, pk=pk).first()
        if model is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        data = request.data if isinstance(request.data, dict) else {}
        pairs = data.get('pairs')
        if not isinstance(pairs, list) or not pairs:
            return _bad_param('pairs', 'Indica los pares: una lista de dos opciones cada uno.')
        try:
            changed = write(model, pairs=pairs, actor=request.user, request=request)
        except device_catalog.CatalogError as exc:
            return _error(exc)
        return Response({**model_palette.payload(model), 'changed': changed})

    def post(self, request, pk):
        return self._change(request, pk, lambda model, **kw: model_palette.add_exclusions(model, refuse_held=True, **kw))

    def delete(self, request, pk):
        return self._change(request, pk, model_palette.remove_exclusions)


class AdminModelAccessoriesView(APIView):
    """
    GET /api/admin/catalog/models/<pk>/accessories/   the accessories that fit the model, in order
    PUT /api/admin/catalog/models/<pk>/accessories/   {"accessory_ids": [...], "expected_ids": [...]?} — the
                                                      whole ORDERED list; `expected_ids` (the list last read)
                                                      makes a save over someone else's a 409

    P2 · F3 («Accesorios compatibles»). `products.view` to read, `products.manage` to write. A model of
    another company answers 404; a product of another company, unknown, a device or a repeated id in the
    body refuses the whole call (400). Sale prices only: never a cost.
    """

    permission_classes = [permissions.IsAuthenticated]

    @staticmethod
    def _payload(model, **extra):
        return {'model_id': model.pk, 'accessories': accessory_compat.for_model_admin(model), **extra}

    def get(self, request, pk):
        company, error = _view(request)
        if error:
            return error
        model = ProductModel.objects.filter(company=company, pk=pk).first()
        if model is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        return Response(self._payload(model))

    def put(self, request, pk):
        company, error = _manage(request)
        if error:
            return error
        model = ProductModel.objects.filter(company=company, pk=pk).first()
        if model is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        data = request.data if isinstance(request.data, dict) else {}
        if not isinstance(data.get('accessory_ids'), list):
            return Response({'detail': 'accessory_ids debe ser una lista.', 'code': 'invalid',
                             'field': 'accessory_ids'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            result = accessory_compat.set_for_model(model, accessory_ids=data['accessory_ids'], actor=request.user,
                                                    request=request, expected_ids=data.get('expected_ids'))
        except device_catalog.CatalogError as exc:
            # Somebody saved since the caller read the list: 409, nothing written. Every other refusal of the
            # body is a 400, `invalid_accessory` included: an id of another company reads as one that does not exist.
            code = (status.HTTP_409_CONFLICT if exc.code == accessory_compat.ACCESSORIES_CHANGED
                    else status.HTTP_400_BAD_REQUEST)
            return Response(exc.body(), status=code)
        return Response(self._payload(model, changed=result['changed']))


class AdminAccessoryCandidatesView(APIView):
    """
    GET /api/admin/catalog/accessories/?search=<text>   the company's accessories (products without a model)

    P2 · F3 — what «Accesorios compatibles» may add. `products.view`. At most 20, by name. Never a cost.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company, error = _view(request)
        if error:
            return error
        return Response({'results': accessory_compat.candidates(company, search=request.query_params.get('search', ''))})


class AdminCatalogImageSourcesView(APIView):
    """
    GET /api/admin/catalog/image-sources/?page=&page_size=   the models and products whose images the shop did not
                                                            upload, with a summary by host

    P2 · F4 («Imágenes por reemplazar»). Read-only, `products.view`, the caller's company only (never a company
    the caller is not a member of). The host of each image, never its address; never a price or a cost. At most
    200 per page. `licensing` (CATALOG-REFERENCE-01 · PR C): finishes without pictures and the shop's uploads whose
    source is unknown or whose licence nobody checked (`catalog_image_sources.licensing`).
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company, error = _view(request)
        if error:
            return error
        params = request.query_params
        return Response({
            **catalog_image_sources.report(company, page=params.get('page'), page_size=params.get('page_size')),
            # CATALOG-REFERENCE-01 · PR C: finishes without pictures and uploads without a checked licence.
            'licensing': catalog_image_sources.licensing(company),
        })


class AdminCatalogImageHealthView(APIView):
    """
    GET /api/admin/catalog/image-health/   IMG-HEALTH (IMG-GATE-01): broken managed files, external hotlinks by host,
                                           pictures shown without a verified licence, a file on two colours of one
                                           model and new variants on sale with «Imagen pendiente»

    Read-only, `products.view`, the caller's company only. It asks the storage whether each file is still there, so
    the panel calls it on demand («Revisar ahora»), not on every visit. At most `image_health.MAX_ROWS` rows (the
    counts are the whole catalogue's); every row: `manage.py report_image_health`. It never deletes anything.
    """

    permission_classes = [permissions.IsAuthenticated]
    # Review of #408: it asks the storage for each file; the admin catalogue's read rate is its limit.
    throttle_classes = [AdminProductsThrottle]

    def get(self, request):
        from . import image_health

        company, error = _view(request)
        if error:
            return error
        data = image_health.report(company)
        return Response({'summary': data['summary'], 'rows': data['rows'][:image_health.MAX_ROWS],
                         'count': len(data['rows']), 'truncated': len(data['rows']) > image_health.MAX_ROWS})


class AdminAttributeOptionsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        company, error = _manage(request)
        if error:
            return error
        try:
            option = device_catalog.create_option(
                company=company, attribute_id=pk, label=request.data.get('label'),
                swatch_hex=request.data.get('swatch_hex') or '', sort_order=request.data.get('sort_order') or 0,
                actor=request.user, request=request,
            )
        except device_catalog.CatalogError as exc:
            return _error(exc)
        return Response(_option_payload(option), status=status.HTTP_201_CREATED)


class AdminOptionDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, pk):
        company, error = _manage(request)
        if error:
            return error
        option = VariantOption.objects.filter(company=company, pk=pk).select_related('attribute').first()
        if option is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        try:
            option = device_catalog.update_option(
                option, label=request.data.get('label'), swatch_hex=request.data.get('swatch_hex'),
                sort_order=request.data.get('sort_order'),
                swatch_is_approximate=request.data.get('swatch_is_approximate'), actor=request.user, request=request,
            )
        except device_catalog.CatalogError as exc:
            return _error(exc)
        return Response(_option_payload(option))


class AdminProductVariantView(APIView):
    """
    PUT /api/admin/products/<pk>/variant/ {model_id|null, condition_line, option_ids, reason?, price?,
    is_published_online?}

    CATALOG-EDITOR-FIX-01: price and publication, when sent, are saved in the SAME transaction as the
    variant, through the product's own write (`write_product_fields`): all of it or nothing.
    """

    permission_classes = [permissions.IsAuthenticated]

    def put(self, request, pk):
        company, error = _manage(request)
        if error:
            return error
        product = Product.objects.filter(company=company, pk=pk).first()
        if product is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        option_ids = request.data.get('option_ids') or []
        if not isinstance(option_ids, list):
            return Response({'detail': 'option_ids debe ser una lista.', 'code': 'invalid'},
                            status=status.HTTP_400_BAD_REQUEST)
        fields = {key: request.data[key] for key in ('price', 'is_published_online') if key in request.data}
        expected, refusal = _expected_identity(request.data)
        if refusal:
            return refusal
        try:
            with transaction.atomic():
                if expected:
                    # CATALOG-ADMIN-UX-B review: the identity the caller READ, compared under the product's lock (the
                    # same row `set_variant` locks first): somebody changed it meanwhile → 409 `stale`, nothing written,
                    # never their change silently put back.
                    locked = Product.objects.select_for_update(of=('self',)).get(pk=product.pk)
                    if any(current != expected[key] for key, current in (
                            ('model_id', locked.product_model_id), ('condition_line', locked.condition_line),
                            ('variant_key', locked.variant_key)) if key in expected):
                        raise device_catalog.CatalogError(
                            f'Otra persona acaba de cambiar «{locked.name}» (modelo, línea u opciones). No se guardó '
                            'nada: vuelve a cargar el modelo.', field='product', code='stale')
                product = device_catalog.set_variant(
                    product, model_id=request.data.get('model_id'),
                    condition_line=request.data.get('condition_line') or Product.CONDITION_NEW,
                    option_ids=option_ids, actor=request.user, request=request,
                    reason=request.data.get('reason') or '',
                )
                if fields:
                    product = Product.objects.select_for_update(of=('self',)).get(pk=product.pk)
                    product, changed = write_product_fields(company, product, fields)
                    if changed:
                        AdminAuditLog.log(
                            actor=request.user, action='product_updated', target_type='product',
                            target_id=product.pk, request=request, company=company,
                            metadata={'product_name': product.name, 'product_id': product.pk,
                                      'changed_fields': changed, 'via': 'variant_editor'},
                        )
        except device_catalog.CatalogError as exc:
            return _error(exc)
        except drf_serializers.ValidationError as exc:
            # The whole save was rolled back: the variant did not change either.
            return _validation_error(exc)
        return Response({
            'product_id': product.pk, 'model_id': product.product_model_id,
            'condition_line': product.condition_line,
            'option_ids': [o.pk for o in device_catalog.variant_options_of(product)],
            'price': str(product.price), 'is_published_online': product.is_published_online,
        })


def _expected_identity(data):
    """
    CATALOG-ADMIN-UX-B review — the optional `expected_model_id` (id or null), `expected_condition_line` and
    `expected_option_ids` of `PUT …/variant/`: what the caller read. Returns ({'model_id', 'condition_line',
    'variant_key'} of the ones sent, None) or (None, a 400). Absent, the PUT behaves as before.
    """
    data = data if isinstance(data, dict) else {}
    expected = {}
    if 'expected_model_id' in data:
        value = data['expected_model_id']
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value <= 0):
            return None, _bad_param('expected_model_id', 'expected_model_id es un id o null.')
        expected['model_id'] = value
    if 'expected_condition_line' in data:
        if data['expected_condition_line'] not in dict(Product.CONDITION_LINE_CHOICES):
            return None, _bad_param('expected_condition_line', 'Línea desconocida: nuevo o seminuevo.')
        expected['condition_line'] = data['expected_condition_line']
    if 'expected_option_ids' in data:
        try:
            ids = model_palette.clean_option_ids(data['expected_option_ids'], field='expected_option_ids')
        except device_catalog.CatalogError as exc:
            return None, _error(exc)
        if not isinstance(data['expected_option_ids'], list):
            return None, _bad_param('expected_option_ids', 'expected_option_ids debe ser una lista.')
        expected['variant_key'] = device_catalog.variant_key(ids) if ids else ''
    return expected, None


def _validation_error(exc) -> Response:
    """A product-write refusal as one field, one message and its code (A1b-2: `draft_pending`; else `invalid`)."""
    field, messages = next(iter(exc.detail.items())) if isinstance(exc.detail, dict) else ('', exc.detail)
    message = messages[0] if isinstance(messages, list) and messages else str(messages)
    code = getattr(message, 'code', None)
    return Response({'detail': str(message), 'code': code if code in ('draft_pending',) else 'invalid', 'field': field},
                    status=status.HTTP_400_BAD_REQUEST)


def _draft_payload(product):
    """What the cockpit needs of a draft it just created or enabled (admin only; never public)."""
    from .serializers import AdminProductSerializer

    data = AdminProductSerializer(product).data
    data.update({
        'model_id': product.product_model_id,
        'option_ids': [o.pk for o in device_catalog.variant_options_of(product)],
    })
    return data


class AdminModelVariantCreateView(APIView):
    """
    POST /api/admin/catalog/models/<pk>/variants/ {condition_line, option_ids, name?}

    CATALOG-COMMERCIAL-01 · A1b-2 («Crear variante» from the palette matrix). `products.manage` only:
    the row is born without stock and without counting by unit, so no `inventory.adjust` is involved
    (owner's P1). It is a PENDING DRAFT (hidden, 0.00, `draft_origin='palette'`) until
    `POST /api/admin/products/<pk>/enable-sale/`. A model of another company answers 404; an option of
    another company refuses the whole call (404). `is_published_online` is not accepted here (P2).
    """

    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        return [AdminProductWriteThrottle()]

    def post(self, request, pk):
        company, error = _manage(request)
        if error:
            return error
        model = ProductModel.objects.filter(company=company, pk=pk).first()
        if model is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        option_ids = request.data.get('option_ids')
        if not isinstance(option_ids, list):
            return Response({'detail': 'option_ids debe ser una lista.', 'code': 'invalid', 'field': 'option_ids'},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            product = device_catalog.create_variant_product(
                company, model, condition_line=request.data.get('condition_line') or Product.CONDITION_NEW,
                option_ids=option_ids, name=request.data.get('name'), actor=request.user, request=request,
            )
        except device_catalog.CatalogError as exc:
            return _error(exc)
        return Response(_draft_payload(product), status=status.HTTP_201_CREATED)


class AdminProductEnableSaleView(APIView):
    """
    POST /api/admin/products/<pk>/enable-sale/ {publish?: bool}

    A1b-2 — the ONE commercial transition of a variant created from the palette
    (`device_catalog.enable_variant_for_sale`). `products.manage`. 400 `not_ready` with `problems`
    (the publication rule's codes and the palette's), 400 `not_a_draft` for a historical row,
    409 `already_enabled`, 404 for a product of another company. `publish: true` publishes THIS
    product in the same transaction, or nothing.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        return [AdminProductWriteThrottle()]

    def post(self, request, pk):
        company, error = _manage(request)
        if error:
            return error
        product = Product.objects.filter(company=company, pk=pk).first()
        if product is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        publish = request.data.get('publish') is True
        try:
            product = device_catalog.enable_variant_for_sale(product, publish=publish, actor=request.user, request=request)
        except device_catalog.CatalogError as exc:
            return _error(exc)
        except drf_serializers.ValidationError as exc:
            # `publish=True` and the product write refused: the transition was rolled back with it.
            return _validation_error(exc)
        return Response(_draft_payload(product))


def _grade_name_clash(company, label, *, exclude=None) -> str:
    """
    INV-GRADES-01 review — why `label` cannot name a grade of `company`, or ''. `clean_grade` reads a typed grade by
    id, then code, then name: a name that is another grade's name (accents and case folded), another grade's code, or
    only digits would make a reception or an upload pick ANOTHER grade.
    """
    import unicodedata

    from django.utils.text import slugify

    def fold(text):
        return ' '.join(''.join(c for c in unicodedata.normalize('NFKD', str(text)) if not unicodedata.combining(c))
                        .casefold().split())

    if label.strip().isdigit():
        return 'El nombre de un estado no puede ser sólo un número.'
    others = CosmeticGrade.objects.filter(company=company)
    if exclude is not None:
        others = others.exclude(pk=exclude)
    for other in others.only('code', 'label'):
        if fold(other.label) == fold(label):
            return 'Ya existe un estado con ese nombre.'
        if other.code == slugify(label) or fold(other.code) == fold(label):
            return f'«{label}» es el código de otro estado: elige otro nombre.'
    return ''


def _grade_payload(grade):
    # INV-GRADES-01: `in_use` tells the panel whether the NAME may still change (never once a device has the grade).
    return {'id': grade.pk, 'code': grade.code, 'label': grade.label, 'description': grade.description,
            'sort_order': grade.sort_order, 'is_active': grade.is_active, 'in_use': grade.units.exists()}


class AdminGradeListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company, error = _view(request)
        if error:
            return error
        return Response({'results': [_grade_payload(g) for g in CosmeticGrade.objects.filter(company=company)]})

    def post(self, request):
        from django.db import IntegrityError, transaction
        from django.utils.text import slugify

        company, error = _manage(request)
        if error:
            return error
        label = ' '.join(str(request.data.get('label') or '').split())[:60]
        if not label:
            return Response({'detail': 'Indica el nombre del estado.', 'field': 'label'},
                            status=status.HTTP_400_BAD_REQUEST)
        clash = _grade_name_clash(company, label)
        if clash:
            return Response({'detail': clash, 'field': 'label', 'code': 'duplicate'}, status=status.HTTP_400_BAD_REQUEST)
        code = slugify(request.data.get('code') or label)[:40] or 'estado'
        try:
            with transaction.atomic():
                grade = CosmeticGrade.objects.create(
                    company=company, code=code, label=label,
                    description=' '.join(str(request.data.get('description') or '').split())[:300],
                    sort_order=int(request.data.get('sort_order') or 0),
                )
        except (IntegrityError, ValueError):
            return Response({'detail': 'Ya existe un estado con ese código.', 'field': 'code'},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response(_grade_payload(grade), status=status.HTTP_201_CREATED)


class AdminGradeDetailView(APIView):
    """
    INV-GRADES-01 — `PATCH /api/admin/catalog/grades/<pk>/` (`products.manage`): the company edits its scale.

    Description, order and «active» always; the NAME only while no device uses the grade (a device's grade is a
    foreign key: renaming a used grade would rewrite how received and sold devices read). The code never changes.
    An inactive grade is not offered to a new reception (`stock_unit_services.clean_grade` reads active ones) and
    stays on the devices that already have it. Audited with what changed.
    """
    permission_classes = [permissions.IsAuthenticated]
    FIELDS = {'label', 'description', 'sort_order', 'is_active'}

    def patch(self, request, pk):
        from django.db import transaction

        company, error = _manage(request)
        if error:
            return error
        data = request.data if isinstance(request.data, dict) else None
        if not data or set(data) - self.FIELDS:
            return Response({'detail': 'Sólo se pueden cambiar el nombre, la descripción, el orden y si está activo.',
                             'code': 'invalid'}, status=status.HTTP_400_BAD_REQUEST)
        changes = {}
        if 'label' in data:
            label = ' '.join(str(data.get('label') or '').split())[:60]
            if not label:
                return Response({'detail': 'Indica el nombre del estado.', 'field': 'label', 'code': 'invalid'},
                                status=status.HTTP_400_BAD_REQUEST)
            changes['label'] = label
        if 'description' in data:
            changes['description'] = ' '.join(str(data.get('description') or '').split())[:300]
        if 'sort_order' in data:
            order = data.get('sort_order')
            if isinstance(order, bool) or not isinstance(order, int) or not 0 <= order <= 9999:
                return Response({'detail': 'El orden es un número entre 0 y 9999.', 'field': 'sort_order',
                                 'code': 'invalid'}, status=status.HTTP_400_BAD_REQUEST)
            changes['sort_order'] = order
        if 'is_active' in data:
            if not isinstance(data.get('is_active'), bool):
                return Response({'detail': 'Indica sí o no.', 'field': 'is_active', 'code': 'invalid'},
                                status=status.HTTP_400_BAD_REQUEST)
            changes['is_active'] = data['is_active']
        with transaction.atomic():
            grade = CosmeticGrade.objects.select_for_update().filter(company=company, pk=pk).first()
            if grade is None:
                return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
            if 'label' in changes and changes['label'] != grade.label:
                if grade.units.exists():
                    return Response({'detail': 'Este estado ya lo tienen equipos: su nombre no cambia para no reescribir '
                                               'su historial. Cambia la descripción o desactívalo y crea otro.',
                                     'field': 'label', 'code': 'grade_in_use'}, status=status.HTTP_409_CONFLICT)
                clash = _grade_name_clash(company, changes['label'], exclude=grade.pk)
                if clash:
                    return Response({'detail': clash, 'field': 'label', 'code': 'duplicate'},
                                    status=status.HTTP_400_BAD_REQUEST)
            before = {field: getattr(grade, field) for field in changes}
            for field, value in changes.items():
                setattr(grade, field, value)
            grade.save(update_fields=list(changes))
            changed = {field: [before[field], value] for field, value in changes.items() if before[field] != value}
            if changed:
                AdminAuditLog.log(actor=request.user, action='cosmetic_grade_updated', target_type='cosmetic_grade',
                                  target_id=grade.pk, request=request, company=company,
                                  metadata={'code': grade.code, 'changed': changed})
        return Response(_grade_payload(grade))


class AdminGradeSeedView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        company, error = _manage(request)
        if error:
            return error
        created = device_catalog.seed_cosmetic_grades(company)
        return Response({
            'created': created,
            'results': [_grade_payload(g) for g in CosmeticGrade.objects.filter(company=company)],
        })


class AdminCatalogSettingsView(APIView):
    """GET/PATCH /api/admin/catalog/settings/ — the two line labels (blank = platform words)."""

    permission_classes = [permissions.IsAuthenticated]

    def _payload(self, row):
        return {'new_line_label': row.new_line_label, 'used_line_label': row.used_line_label,
                'defaults': {'new': device_catalog.DEFAULT_NEW_LABEL, 'used': device_catalog.DEFAULT_USED_LABEL}}

    def get(self, request):
        company, error = _view(request)
        if error:
            return error
        row, _ = StorefrontPageSettings.objects.get_or_create(company=company)
        return Response(self._payload(row))

    def patch(self, request):
        company, error = _manage(request)
        if error:
            return error
        row, _ = StorefrontPageSettings.objects.get_or_create(company=company)
        for field in ('new_line_label', 'used_line_label'):
            if field in request.data:
                setattr(row, field, ' '.join(str(request.data.get(field) or '').split())[:40])
        row.save()
        return Response(self._payload(row))


class AdminPaletteSuggestionsView(APIView):
    """
    GET /api/admin/catalog/palette-suggestions/ — PALETTE-SUGGEST-01, «Paletas por armar».

    The models of the caller's company (server context) without a palette, each with the palette its variants
    suggest. `products.view`, like reading a palette. READ-ONLY: every other method is 405; saving is the
    model's own `PUT …/palette/` (`products.manage`, audited).
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company, error = _view(request)
        if error:
            return error
        return Response(model_palette.suggestions(company))


class AdminCatalogReadinessView(APIView):
    """
    GET /api/admin/catalog/readiness/ — CATALOG-READINESS-01, «Preparación del catálogo».

    `products.view`; the company is the caller's (server context). READ-ONLY: every other method is 405.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company, error = _view(request)
        if error:
            return error
        return Response(catalog_readiness.readiness(company))
