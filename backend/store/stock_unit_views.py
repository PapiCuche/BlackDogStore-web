"""
SERIALIZED-STOCK — `Inventario › Equipos`, the HTTP surface.

Same authority as the rest of the inventory: `inventory.view` to read,
`inventory.adjust` to change, and always inside the branches the caller
reaches. A unit of another company or of a branch the caller cannot see answers
404 — exactly like one that does not exist.

The serial number and the IMEI are shown WHOLE here. This is the screen of the
people who put the device on the shelf and take it off; they identify it by
reading those numbers. Everywhere a customer or a message can see them, they
are masked.

DC1 — THE COST IS NOT PART OF THAT. It travels only to a caller holding
`costs.view` (`inventory_views.may_view_costs`); for anybody else the key is
absent, not null, because a null reads as «no cost recorded». Changing the cost
of a unit that exists needs it too: correcting a valuation blind is not moving
stock. Registering a unit WITH a cost needs it as well (COST-WRITE-AUTHORITY,
owner's decision C): without it a stated cost is refused (400
`costs_forbidden`), never stored; a unit registered without a cost waits for
somebody who holds it.
"""
from __future__ import annotations

from django.db.models import Count, Q, Sum
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import availability_views as availability
from . import cost_services, label_batches
from . import stock_unit_services as units
from . import storefront_media, unit_media
from .product_media import ProductMediaError
from .inventory_services import InventoryError
from .admin_views import CAP_PRODUCTS_MANAGE, _LEGACY_MANAGE_CATALOG_ROLES
from .admin_views import _company_context as _catalog_context
from .inventory_views import (
    CAP_INVENTORY_ADJUST, CAP_INVENTORY_VIEW, _LEGACY_INVENTORY_ADJUST_ROLES,
    _LEGACY_INVENTORY_VIEW_ROLES, _branch_context, _company_context, _paginate,
    _scoped_product, may_view_costs,
)
from .models import BranchStock, Product, StockUnit, UnitPhoto
from .tenancy import (
    BranchAccessError, NoBranchError, company_fulfillment_branch, resolve_branch_for_user, visible_branches,
)
from .throttles import AdminInventoryReportsThrottle, AdminStockMovementsThrottle

NOT_FOUND = 'Equipo no encontrado.'


def unit_payload(unit, *, include_cost: bool = False) -> dict:
    payload = {
        'id': unit.pk,
        'product_id': unit.product_id,
        'product_name': unit.product.name,
        'branch_id': unit.branch_id,
        'branch_name': unit.branch.name,
        'serial_number': unit.serial_number,
        'imei': unit.imei,
        'imei2': unit.imei2,
        # LABEL-02 — the device's internal code (staff only; never public).
        'label_code': unit.label_code,
        'condition': unit.condition,
        'condition_label': unit.get_condition_display(),
        'status': unit.status,
        'status_label': unit.get_status_display(),
        'cost': None if unit.cost is None else str(unit.cost),
        'received_at': unit.received_at,
        'sold_at': unit.sold_at,
        'order_id': unit.order_id,
        'notes': unit.notes,
        # DC2 — what the shop tells buyers about this device.
        'public_id': str(unit.public_id),
        'condition_line': unit.product.condition_line,
        'battery_health': unit.battery_health,
        'cosmetic_grade': unit.cosmetic_grade_id,
        'cosmetic_grade_label': unit.cosmetic_grade.label if unit.cosmetic_grade_id else '',
        'public_notes': unit.public_notes,
        'sale_price': None if unit.sale_price is None else str(unit.sale_price),
        'held_online': bool(unit.hold_key),
        'hold_until': unit.hold_until if unit.hold_key else None,
        'photos': [unit_media.payload(photo) for photo in unit.photos.all()],
    }
    if not include_cost:
        del payload['cost']
    return payload


def web_status(unit, *, dispatch) -> dict:
    """
    INV-RECEIVE-PRICE-03 — whether the online shop offers THIS device right now, and every reason it does not.

    The same conditions as `device_catalog.offered_units` plus the two the model page adds (the variant and its
    model published): available, on the dispatch branch, and — a used device — with a price greater than zero.
    Codes, never amounts: the screen words them. Read only; nothing here publishes anything.
    """
    product = unit.product
    model = product.product_model if product.product_model_id else None
    reasons = []
    if unit.status != StockUnit.Status.AVAILABLE:
        reasons.append('not_available')
    if product.condition_line == Product.CONDITION_USED and not units.has_usable_price(unit.sale_price):
        reasons.append('no_price')
    if dispatch is None:
        reasons.append('no_dispatch_branch')
    elif unit.branch_id != dispatch.pk:
        reasons.append('not_in_dispatch_branch')
    if not product.is_sold_online:
        reasons.append('product_hidden')
    if model is not None and not model.is_published_online:
        reasons.append('model_hidden')
    return {'offered': not reasons, 'reasons': reasons}


def _states_a_price(row) -> bool:
    """Whether one row of «Registrar equipos» carries a sale price (blank, spaces or null are none)."""
    if not isinstance(row, dict) or 'sale_price' not in row:
        return False
    value = row['sale_price']
    return not (value is None or (isinstance(value, str) and not value.strip()))


def _refusal(exc) -> Response:
    body = {'detail': str(exc)}
    for name in ('line', 'field', 'code'):
        value = getattr(exc, name, None)
        if value is not None:
            body[name] = value
    # LABEL-02: a conflict with another record of the company is a 409.
    return Response(body, status=getattr(exc, 'status', None) or status.HTTP_400_BAD_REQUEST)


class StockUnitListView(APIView):
    """
    GET  /api/admin/inventory/units/?branch=&product=&status=&condition=&line=&model=&option=&search=
    POST /api/admin/inventory/units/  — receive a batch of units.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        scope = AdminStockMovementsThrottle if self.request.method == 'POST' else AdminInventoryReportsThrottle
        return [scope()]

    def get(self, request):
        company, branch, branches, error = _branch_context(
            request, CAP_INVENTORY_VIEW, _LEGACY_INVENTORY_VIEW_ROLES, allow_all=True,
        )
        if error:
            return error

        rows = StockUnit.objects.filter(company=company, branch__in=branches)
        params = request.query_params

        for name, allowed in (('status', StockUnit.Status.values), ('condition', StockUnit.Condition.values)):
            value = (params.get(name) or '').strip()
            if value:
                if value not in allowed:
                    return Response(
                        {'detail': f'Parámetro "{name}" inválido.'}, status=status.HTTP_400_BAD_REQUEST,
                    )
                rows = rows.filter(**{name: value})

        line = (params.get('line') or '').strip()
        if line:
            # DC2 — «Seminuevos»: the devices of used variants.
            if line not in ('new', 'used'):
                return Response({'detail': 'Parámetro "line" inválido.'}, status=status.HTTP_400_BAD_REQUEST)
            rows = rows.filter(product__condition_line=line)

        # COMMERCE-ADMIN-UX-01 — by model and by option (capacity, colour…),
        # with the same rules as «Consultar disponibilidad».
        try:
            model_id, option_ids = availability.parse_variant_filters(params)
        except availability.BadParameter as exc:
            return Response({'detail': f'Parámetro "{exc.args[0]}" inválido.'}, status=status.HTTP_400_BAD_REQUEST)
        if model_id or option_ids:
            rows = rows.filter(product__in=availability.filter_variants(
                Product.objects.filter(company=company), model_id=model_id, option_ids=option_ids,
            ).values('pk'))

        priced = (params.get('priced') or '').strip()
        if priced:
            # INV-RECEIVE-PRICE-03 — «Sin precio»: the used devices nobody has priced yet (a zero is no price).
            if priced not in ('yes', 'no'):
                return Response({'detail': 'Parámetro "priced" inválido.'}, status=status.HTTP_400_BAD_REQUEST)
            usable = Q(sale_price__gt=0)
            rows = rows.filter(usable) if priced == 'yes' else rows.exclude(usable)

        product_id = params.get('product')
        if product_id:
            try:
                rows = rows.filter(product_id=int(product_id))
            except (TypeError, ValueError):
                return Response(
                    {'detail': 'Parámetro "product" inválido.'}, status=status.HTTP_400_BAD_REQUEST,
                )

        search = ''.join((params.get('search') or '').split())[:40]
        if search:
            # By what is printed on the box: the serial, or the last digits of
            # either IMEI. Never by customer — a unit has none.
            rows = rows.filter(
                Q(serial_number__icontains=search) | Q(imei__endswith=search)
                | Q(imei2__endswith=search) | Q(imei=search) | Q(imei2=search)
            )

        rows = (
            rows.select_related('product', 'product__product_model', 'branch', 'cosmetic_grade')
            .prefetch_related('photos')
            .order_by('-received_at', '-pk')
        )
        page, meta = _paginate(rows, request)
        include_cost = may_view_costs(request)
        dispatch = company_fulfillment_branch(company)
        return Response({
            'results': [
                {**unit_payload(unit, include_cost=include_cost), 'web': web_status(unit, dispatch=dispatch)}
                for unit in page
            ],
            'statuses': [{'value': v, 'label': label} for v, label in StockUnit.Status.choices],
            'conditions': [{'value': v, 'label': label} for v, label in StockUnit.Condition.choices],
            **meta,
        })

    def post(self, request):
        company, error = _company_context(
            request, CAP_INVENTORY_ADJUST, _LEGACY_INVENTORY_ADJUST_ROLES,
        )
        if error:
            return error
        data = request.data if isinstance(request.data, dict) else {}

        try:
            branch = resolve_branch_for_user(
                request.user, company, data.get('branch'), allow_all=False,
            )
        except NoBranchError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except BranchAccessError:
            return Response(
                {'detail': 'Sucursal no encontrada o sin acceso.'}, status=status.HTTP_404_NOT_FOUND,
            )

        product = _scoped_product(company, data.get('product_id'))
        if product is None:
            return Response({'detail': 'Producto no encontrado.'}, status=status.HTTP_404_NOT_FOUND)

        rows = data.get('units')
        if not isinstance(rows, list):
            return Response(
                {'detail': 'Indica al menos un equipo.'}, status=status.HTTP_400_BAD_REQUEST,
            )
        # INV-RECEIVE-PRICE-03 (review B, P1) — a device's price is the catalogue's decision on EVERY door: the
        # ficha (PATCH), the receipt (`prices_forbidden`) and here. One priced row without `products.manage`
        # refuses the whole batch, before anything is written; a blank or absent price is no price.
        if any(_states_a_price(row) for row in rows) and not may_manage_products(request):
            return Response(
                {'detail': 'No tienes permiso para fijar precios de venta: registra los equipos sin precio.',
                 'field': 'sale_price'},
                status=status.HTTP_403_FORBIDDEN,
            )
        # COST-WRITE-AUTHORITY (owner's decision C) — a cost is written only by `costs.view`, on this door as
        # on the receipt's: one costed row without it refuses the whole batch, before anything is written.
        # A blank or absent cost is no cost: registering devices without seeing costs stays possible.
        if any(isinstance(row, dict) and cost_services.states_a_cost(row.get('cost')) for row in rows) \
                and not may_view_costs(request):
            return Response(
                {'detail': cost_services.UNIT_COSTS_REQUIRED, 'code': cost_services.COSTS_FORBIDDEN,
                 'field': 'cost'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            created = units.receive_units(
                branch=branch, product=product, units=rows, actor=request.user,
                reason=data.get('reason') or '', request=request,
            )
        except InventoryError as exc:
            return _refusal(exc)

        # LABEL-A — the labels of what just entered; the screen says «Etiquetas listas para imprimir».
        batch = label_batches.create_for_movement(created.movement, units=created, actor=request.user)

        fresh = StockUnit.objects.filter(pk__in=[u.pk for u in created]).select_related(
            'product', 'branch').order_by('pk')
        include_cost = may_view_costs(request)
        return Response(
            {'results': [unit_payload(unit, include_cost=include_cost) for unit in fresh],
             **label_batches.answer(batch)},
            status=status.HTTP_201_CREATED,
        )


class SerializedProductListView(APIView):
    """
    GET — the products that are counted by unit, for the filter and the form.

    `?scope=all` lists EVERY active product of the company with how it is
    counted, how much stock it holds and whether that can still be changed.
    The reception form needs it: choosing a product that is not tracked by
    serial has to produce an explanation (and, with stock at zero, the way to
    turn tracking on), not a form whose fields quietly do nothing.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminInventoryReportsThrottle]

    def get(self, request):
        company, error = _company_context(request, CAP_INVENTORY_VIEW, _LEGACY_INVENTORY_VIEW_ROLES)
        if error:
            return error
        rows = Product.objects.filter(company=company, is_active=True).order_by('name')
        if (request.query_params.get('scope') or '') != 'all':
            return Response({'results': [
                {'id': p.pk, 'name': p.name, 'requires_imei': p.requires_imei, 'price': str(p.price)}
                for p in rows.filter(is_serialized=True)
            ]})

        # Two aggregates, two queries: summing shelves and counting devices in one
        # statement multiplies one by the other through the join.
        #
        # WHAT IS SHOWN is the stock of the branches the caller operates in, like
        # every other inventory figure. WHAT DECIDES whether tracking can change
        # is the whole company's shelf, because that is the rule of
        # `set_serialized`; it is told as a yes or no, never as a number.
        totals = BranchStock.objects.filter(product__company=company)
        shown = dict(
            totals.filter(branch__in=visible_branches(request.user, company))
            .values_list('product_id').annotate(total=Sum('quantity'))
        )
        held = dict(totals.values_list('product_id').annotate(total=Sum('quantity')))
        live = dict(
            StockUnit.objects.filter(
                company=company,
                status__in=(StockUnit.Status.AVAILABLE, StockUnit.Status.RESERVED),
            ).values_list('product_id').annotate(total=Count('id'))
        )
        return Response({'results': [
            {
                'id': p.pk, 'name': p.name, 'price': str(p.price),
                'is_serialized': p.is_serialized, 'requires_imei': p.requires_imei,
                'stock': shown.get(p.pk) or 0,
                # The rule of `set_serialized`, told in advance: only with an empty shelf.
                'can_change_tracking': (held.get(p.pk) or 0) <= 0 and not live.get(p.pk),
            }
            for p in rows
        ]})


class ProductSerializationView(APIView):
    """
    POST /api/admin/inventory/units/products/<id>/serialization/

    Decide whether a product is counted by unit. Refused while it has stock:
    see `stock_unit_services.set_serialized`.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminStockMovementsThrottle]

    def post(self, request, pk):
        company, error = _company_context(
            request, CAP_INVENTORY_ADJUST, _LEGACY_INVENTORY_ADJUST_ROLES,
        )
        if error:
            return error
        product = _scoped_product(company, pk)
        if product is None:
            return Response({'detail': 'Producto no encontrado.'}, status=status.HTTP_404_NOT_FOUND)
        serialized = request.data.get('is_serialized')
        if not isinstance(serialized, bool):
            return Response(
                {'detail': 'Indica si el producto se controla por número de serie.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            updated = units.set_serialized(
                product, serialized=serialized,
                requires_imei=bool(request.data.get('requires_imei')),
                actor=request.user, request=request,
            )
        except InventoryError as exc:
            return _refusal(exc)
        return Response({
            'id': updated.pk, 'name': updated.name,
            'is_serialized': updated.is_serialized, 'requires_imei': updated.requires_imei,
        })


class _UnitMixin:
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminStockMovementsThrottle]

    def resolve(self, request, pk):
        """The unit, if it is this company's and in a branch the caller reaches."""
        company, error = _company_context(
            request, CAP_INVENTORY_ADJUST, _LEGACY_INVENTORY_ADJUST_ROLES,
        )
        if error:
            return None, error
        unit = StockUnit.objects.filter(
            company=company, pk=pk, branch__in=visible_branches(request.user, company),
        ).select_related('product', 'branch').first()
        if unit is None:
            return None, Response({'detail': NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        return unit, None

    def answer(self, request, unit) -> Response:
        fresh = StockUnit.objects.select_related('product', 'branch', 'cosmetic_grade').get(pk=unit.pk)
        return Response(unit_payload(fresh, include_cost=may_view_costs(request)))


def may_manage_products(request) -> bool:
    """Whether this caller may manage the catalogue (prices included) in the company it acts on."""
    _company, error = _catalog_context(request, CAP_PRODUCTS_MANAGE, _LEGACY_MANAGE_CATALOG_ROLES)
    return error is None


class StockUnitDetailView(_UnitMixin, APIView):
    """PATCH — condition, cost, notes. Never the identifiers or the status."""

    def patch(self, request, pk):
        unit, error = self.resolve(request, pk)
        if error:
            return error
        data = request.data if isinstance(request.data, dict) else {}
        fields = {k: data[k] for k in (
            'condition', 'cost', 'notes', 'battery_health', 'cosmetic_grade', 'public_notes', 'sale_price',
        ) if k in data}
        if 'cost' in fields and not may_view_costs(request):
            return Response(
                {'detail': 'No tienes permiso para ver ni corregir costos.', 'field': 'cost'},
                status=status.HTTP_403_FORBIDDEN,
            )
        if 'sale_price' in fields and not may_manage_products(request):
            # DC2 review — what a device sells for is the catalogue's decision,
            # not the stockroom's: the same authority as a product's price.
            return Response(
                {'detail': 'No tienes permiso para cambiar precios de venta.', 'field': 'sale_price'},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            units.update_unit(
                unit, actor=request.user, request=request,
                # A1 — a correction of cost from the panel says why.
                cost_reason=data.get('cost_reason'), require_cost_reason='cost' in fields,
                **fields,
            )
        except InventoryError as exc:
            return _refusal(exc)
        return self.answer(request, unit)


class StockUnitActionView(_UnitMixin, APIView):
    """POST …/<id>/write-off/ · reserve/ · release/ · return/"""

    action = ''

    def post(self, request, pk):
        unit, error = self.resolve(request, pk)
        if error:
            return error
        reason = (request.data.get('reason') if isinstance(request.data, dict) else '') or ''
        try:
            if self.action == 'write-off':
                units.write_off(unit, reason=reason, actor=request.user, request=request)
            elif self.action == 'reserve':
                units.reserve(unit, reason=reason, actor=request.user, request=request)
            elif self.action == 'release':
                units.release(unit, actor=request.user, request=request)
            else:
                units.return_unit(unit, reason=reason, actor=request.user, request=request)
        except InventoryError as exc:
            return _refusal(exc)
        return self.answer(request, unit)


class StockUnitPhotoListView(_UnitMixin, APIView):
    """POST …/units/<id>/photos/ — multipart `file` (+ `alt_text`). DC2."""

    def post(self, request, pk):
        unit, error = self.resolve(request, pk)
        if error:
            return error
        uploaded = request.FILES.get('file')
        if uploaded is None:
            return Response({'detail': 'Adjunta una imagen.', 'field': 'file'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            unit_media.add_photo(unit=unit, actor=request.user, uploaded=uploaded,
                                 alt_text=request.data.get('alt_text') or '', request=request)
        except (unit_media.UnitMediaError, storefront_media.StorefrontImageError, ProductMediaError) as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return self.answer(request, unit)


class StockUnitPhotoDetailView(_UnitMixin, APIView):
    """DELETE …/units/<id>/photos/<photo_id>/"""

    def delete(self, request, pk, photo_id):
        unit, error = self.resolve(request, pk)
        if error:
            return error
        photo = UnitPhoto.objects.filter(unit=unit, company_id=unit.company_id, pk=photo_id).first()
        if photo is None:
            return Response({'detail': NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
        unit_media.remove_photo(photo=photo, actor=request.user, request=request)
        return self.answer(request, unit)
