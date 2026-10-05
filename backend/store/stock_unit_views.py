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
"""
from __future__ import annotations

from django.db.models import Q
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import stock_unit_services as units
from .inventory_services import InventoryError
from .inventory_views import (
    CAP_INVENTORY_ADJUST, CAP_INVENTORY_VIEW, _LEGACY_INVENTORY_ADJUST_ROLES,
    _LEGACY_INVENTORY_VIEW_ROLES, _branch_context, _company_context, _paginate,
    _scoped_product,
)
from .models import Product, StockUnit
from .tenancy import (
    BranchAccessError, NoBranchError, resolve_branch_for_user, visible_branches,
)
from .throttles import AdminInventoryReportsThrottle, AdminStockMovementsThrottle

NOT_FOUND = 'Equipo no encontrado.'


def unit_payload(unit) -> dict:
    return {
        'id': unit.pk,
        'product_id': unit.product_id,
        'product_name': unit.product.name,
        'branch_id': unit.branch_id,
        'branch_name': unit.branch.name,
        'serial_number': unit.serial_number,
        'imei': unit.imei,
        'imei2': unit.imei2,
        'condition': unit.condition,
        'condition_label': unit.get_condition_display(),
        'status': unit.status,
        'status_label': unit.get_status_display(),
        'cost': None if unit.cost is None else str(unit.cost),
        'price_override': None if unit.price_override is None else str(unit.price_override),
        'catalogue_price': str(unit.product.price),
        'received_at': unit.received_at,
        'sold_at': unit.sold_at,
        'order_id': unit.order_id,
        'notes': unit.notes,
    }


def _refusal(exc) -> Response:
    body = {'detail': str(exc)}
    line = getattr(exc, 'line', None)
    if line is not None:
        body['line'] = line
    return Response(body, status=status.HTTP_400_BAD_REQUEST)


class StockUnitListView(APIView):
    """
    GET  /api/admin/inventory/units/?branch=&product=&status=&condition=&search=
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

        rows = rows.select_related('product', 'branch').order_by('-received_at', '-pk')
        page, meta = _paginate(rows, request)
        return Response({
            'results': [unit_payload(unit) for unit in page],
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
        try:
            created = units.receive_units(
                branch=branch, product=product, units=rows, actor=request.user,
                reason=data.get('reason') or '', request=request,
            )
        except InventoryError as exc:
            return _refusal(exc)

        fresh = StockUnit.objects.filter(pk__in=[u.pk for u in created]).select_related(
            'product', 'branch').order_by('pk')
        return Response(
            {'results': [unit_payload(unit) for unit in fresh]}, status=status.HTTP_201_CREATED,
        )


class SerializedProductListView(APIView):
    """GET — the products that are counted by unit, for the reception form."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminInventoryReportsThrottle]

    def get(self, request):
        company, error = _company_context(request, CAP_INVENTORY_VIEW, _LEGACY_INVENTORY_VIEW_ROLES)
        if error:
            return error
        rows = Product.objects.filter(
            company=company, is_serialized=True, is_active=True,
        ).order_by('name')
        return Response({'results': [
            {'id': p.pk, 'name': p.name, 'requires_imei': p.requires_imei, 'price': str(p.price)}
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

    def answer(self, unit) -> Response:
        fresh = StockUnit.objects.select_related('product', 'branch').get(pk=unit.pk)
        return Response(unit_payload(fresh))


class StockUnitDetailView(_UnitMixin, APIView):
    """PATCH — condition, cost, price, notes. Never the identifiers or the status."""

    def patch(self, request, pk):
        unit, error = self.resolve(request, pk)
        if error:
            return error
        data = request.data if isinstance(request.data, dict) else {}
        fields = {k: data[k] for k in ('condition', 'cost', 'price_override', 'notes') if k in data}
        try:
            units.update_unit(unit, actor=request.user, request=request, **fields)
        except InventoryError as exc:
            return _refusal(exc)
        return self.answer(unit)


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
        return self.answer(unit)
