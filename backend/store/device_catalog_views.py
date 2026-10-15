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

from django.db.models import Count, Min
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import device_catalog
from .admin_views import (
    CAP_PRODUCTS_MANAGE, CAP_PRODUCTS_VIEW, _LEGACY_MANAGE_CATALOG_ROLES, _LEGACY_VIEW_CATALOG_ROLES,
    _company_context,
)
from .models import (
    CosmeticGrade, Product, ProductModel, StockUnit, StorefrontPageSettings, VariantAttribute, VariantOption,
)
from .tenancy import company_fulfillment_branch, company_storefront_products, resolve_storefront_company

_NOT_FOUND = {'detail': 'No encontrado.'}


def _error(exc: device_catalog.CatalogError) -> Response:
    code = status.HTTP_404_NOT_FOUND if exc.code == 'not_found' else status.HTTP_400_BAD_REQUEST
    return Response(exc.body(), status=code)


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------

def _public_models(company):
    if company is None:
        return ProductModel.objects.none()
    return ProductModel.objects.filter(company=company, is_published_online=True)


class PublicModelListView(APIView):
    """GET /api/catalog/models/?category=<slug>&line=new|used"""

    permission_classes = [permissions.AllowAny]

    def get(self, request):
        company = resolve_storefront_company(request)
        models = _public_models(company).select_related('category')
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
        used_prices = {}
        if branch is not None:
            used_prices = dict(
                device_catalog.offered_units(sold.filter(condition_line=Product.CONDITION_USED), branch)
                .values('product__product_model').annotate(p=Min('sale_price'))
                .values_list('product__product_model', 'p')
            )

        line = request.query_params.get('line')
        results = []
        for model in models:
            has_new, has_used = bool(new_by_model.get(model.pk)), bool(used_by_model.get(model.pk))
            if not (has_new or has_used):
                continue
            if line == Product.CONDITION_NEW and not has_new:
                continue
            if line == Product.CONDITION_USED and not has_used:
                continue
            prices = [p for p in (new_prices.get(model.pk) if line != 'used' else None,
                                  used_prices.get(model.pk) if line != 'new' else None) if p is not None]
            results.append({
                'slug': model.slug, 'name': model.name, 'image_url': model.image_url,
                'category': {'slug': model.category.slug, 'name': model.category.name} if model.category_id else None,
                'from_price': str(min(prices)) if prices else None,
                'has_new': has_new, 'has_used': has_used,
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
    return {'id': option.pk, 'label': option.label, 'swatch_hex': option.swatch_hex, 'sort_order': option.sort_order}


def _attribute_payload(attribute):
    return {
        'id': attribute.pk, 'name': attribute.name, 'kind': attribute.kind, 'sort_order': attribute.sort_order,
        'options': [_option_payload(option) for option in attribute.options.all()],
    }


def _model_payload(model, *, variants=False):
    data = {
        'id': model.pk, 'name': model.name, 'slug': model.slug,
        'category_id': model.category_id, 'category_name': model.category.name if model.category_id else '',
        'description': model.description, 'image_url': model.image_url,
        'is_published_online': model.is_published_online, 'sort_order': model.sort_order,
        'variant_count': model.variants.count(),
        # The storefront's branch, like the availability the shop shows.
        'used_unit_count': StockUnit.objects.filter(
            product__product_model=model, product__condition_line=Product.CONDITION_USED,
            status=StockUnit.Status.AVAILABLE, branch=company_fulfillment_branch(model.company),
        ).count(),
    }
    if variants:
        rows = []
        for product in model.variants.order_by('condition_line', 'pk'):
            rows.append({
                'product_id': product.pk, 'name': product.name, 'slug': product.slug,
                'condition_line': product.condition_line, 'price': str(product.price),
                'is_serialized': product.is_serialized, 'stock': product.inventory,
                'options': [
                    {'attribute_id': link.attribute_id, 'attribute_name': link.attribute.name,
                     'option_id': link.option_id, 'label': link.option.label, 'swatch_hex': link.option.swatch_hex}
                    for link in product.variant_options.select_related('attribute', 'option')
                    .order_by('attribute__sort_order', 'attribute__name')
                ],
            })
        data['variants'] = rows
    return data


class AdminModelListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company, error = _view(request)
        if error:
            return error
        models = ProductModel.objects.filter(company=company).select_related('category')
        return Response({'results': [_model_payload(model) for model in models]})

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
        allowed = ('name', 'category_id', 'slug', 'description', 'image_url', 'is_published_online', 'sort_order')
        fields = {key: request.data[key] for key in allowed if key in request.data}
        try:
            model = device_catalog.update_model(model, actor=request.user, request=request, **fields)
        except device_catalog.CatalogError as exc:
            return _error(exc)
        return Response(_model_payload(model, variants=True))


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
                sort_order=request.data.get('sort_order') or 0, actor=request.user, request=request,
            )
        except device_catalog.CatalogError as exc:
            return _error(exc)
        return Response(_attribute_payload(attribute), status=status.HTTP_201_CREATED)


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
                sort_order=request.data.get('sort_order'), actor=request.user, request=request,
            )
        except device_catalog.CatalogError as exc:
            return _error(exc)
        return Response(_option_payload(option))


class AdminProductVariantView(APIView):
    """PUT /api/admin/products/<pk>/variant/ {model_id|null, condition_line, option_ids}"""

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
        try:
            product = device_catalog.set_variant(
                product, model_id=request.data.get('model_id'),
                condition_line=request.data.get('condition_line') or Product.CONDITION_NEW,
                option_ids=option_ids, actor=request.user, request=request,
            )
        except device_catalog.CatalogError as exc:
            return _error(exc)
        return Response({
            'product_id': product.pk, 'model_id': product.product_model_id,
            'condition_line': product.condition_line,
            'option_ids': [o.pk for o in device_catalog.variant_options_of(product)],
        })


def _grade_payload(grade):
    return {'id': grade.pk, 'code': grade.code, 'label': grade.label, 'description': grade.description,
            'sort_order': grade.sort_order, 'is_active': grade.is_active}


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

