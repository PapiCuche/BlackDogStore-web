"""
LABEL-03 — «Inventario › Etiquetas y códigos de barras»: preview and PDF.

    GET /api/admin/inventory/labels/preview/?branch=&products=<id>:<copies>,…&units=<id>,…
    GET /api/admin/inventory/labels/pdf/?…the same…

READ-ONLY. Both read the catalogue and the units and answer; neither writes a
row. Downloading the PDF is NOT printing: nothing is marked printed, nothing is
queued, no batch is recorded. Those are LABEL-03b (`LabelBatch`) and LABEL-04.

AUTHORITY. Company and branch are resolved here, never taken from the client:
an id the client sends selects inside what the caller reaches; anything else
answers 404, like something that does not exist.
  * Until LABEL-02 adds `inventory.labels.print`, the surface opens with
    `inventory.view` (the same as the stock it labels).
  * An IMEI or a serial on paper needs the company's `imei1` profile AND
    `inventory.labels.sensitive` (D6). Neither exists yet, so `_device_options`
    answers «internal, not sensitive» and no identifier leaves this surface.
    A device label waits, in review, for its LABEL-02 internal code.

THE COPIES. A product sold by quantity gets `copies` stickers of ONE label;
a device gets exactly one per `StockUnit`.
"""
from __future__ import annotations

from django.http import HttpResponse
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import fiscal_logo, labels
from .capabilities import ALL_CAPABILITY_CODES
from .labels.data import PROFILE_INTERNAL
from .inventory_views import CAP_INVENTORY_VIEW, _LEGACY_INVENTORY_VIEW_ROLES, _branch_context
from .models import BranchStock, CompanySettings, Product, ProductBarcode, ProductVariantOption, StockUnit
from .tax_services import currency_symbol
from .tenancy import has_capability
from .throttles import AdminInventoryReportsThrottle

#: LABEL-02 adds these. Until then the first is `inventory.view` and the
#: second is held by nobody (see the module docstring).
CAP_LABELS_SENSITIVE = 'inventory.labels.sensitive'

MAX_PRODUCTS = 100
MAX_COPIES = 500
MAX_UNITS = 200
MAX_PAGES = 1000

PRODUCT_NOT_FOUND = 'Producto no encontrado.'
UNIT_NOT_FOUND = 'Equipo no encontrado.'
LABELLABLE_UNIT_STATUSES = (StockUnit.Status.AVAILABLE, StockUnit.Status.RESERVED)


class _BadRequest(ValueError):
    pass


def _items(raw: str) -> list[str]:
    return [part.strip() for part in (raw or '').split(',') if part.strip()]


def _parse(params) -> tuple[list[tuple[int, int]], list[int]]:
    products, seen = [], set()
    for part in _items(params.get('products')):
        pk, _, copies = part.partition(':')
        try:
            pk, copies = int(pk), int(copies or 1)
        except ValueError:
            raise _BadRequest('Parámetro "products" inválido: id:copias separados por comas.')
        if not (1 <= copies <= MAX_COPIES):
            raise _BadRequest(f'Las copias van de 1 a {MAX_COPIES}.')
        if pk in seen:
            raise _BadRequest('Un producto repetido: suma sus copias en una sola entrada.')
        seen.add(pk)
        products.append((pk, copies))
    unit_ids = []
    for part in _items(params.get('units')):
        try:
            unit_ids.append(int(part))
        except ValueError:
            raise _BadRequest('Parámetro "units" inválido.')
    if len(set(unit_ids)) != len(unit_ids):
        raise _BadRequest('Un equipo repetido: cada equipo lleva una sola etiqueta.')
    if not products and not unit_ids:
        raise _BadRequest('Elige al menos un producto o un equipo.')
    if len(products) > MAX_PRODUCTS or len(unit_ids) > MAX_UNITS:
        raise _BadRequest(f'Como máximo {MAX_PRODUCTS} productos y {MAX_UNITS} equipos por pedido.')
    if sum(c for _, c in products) + len(unit_ids) > MAX_PAGES:
        raise _BadRequest(f'Como máximo {MAX_PAGES} etiquetas por pedido.')
    return products, unit_ids


def _device_options(request, company) -> labels.DeviceOptions:
    """
    The two keys of an IMEI on paper. The profile is the company's
    (`CompanyLabelSettings.device_symbol`, LABEL-02, `internal` by default) —
    until that model exists every company is `internal`.
    """
    sensitive = (CAP_LABELS_SENSITIVE in ALL_CAPABILITY_CODES
                 and has_capability(request.user, company, CAP_LABELS_SENSITIVE))
    return labels.DeviceOptions(profile=PROFILE_INTERNAL, sensitive_allowed=sensitive)


def _attributes(product_ids) -> dict[int, list[tuple[str, str]]]:
    found: dict[int, list[tuple[str, str]]] = {}
    rows = (ProductVariantOption.objects.filter(product_id__in=product_ids)
            .select_related('attribute', 'option')
            .order_by('attribute__sort_order', 'attribute__name', 'pk'))
    for row in rows:
        found.setdefault(row.product_id, []).append((row.attribute.name, row.option.label))
    return found


def _resolve(request):
    """((plans, options), None), or (None, the error response)."""
    company, branch, _branches, error = _branch_context(
        request, CAP_INVENTORY_VIEW, _LEGACY_INVENTORY_VIEW_ROLES, allow_all=False)
    if error:
        return None, error
    try:
        wanted, unit_ids = _parse(request.query_params)
    except _BadRequest as exc:
        return None, Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    product_ids = [pk for pk, _ in wanted]
    products = {p.pk: p for p in Product.objects.filter(company=company, pk__in=product_ids)}
    if len(products) != len(product_ids):
        return None, Response({'detail': PRODUCT_NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)
    unit_rows = {u.pk: u for u in StockUnit.objects.filter(
        company=company, branch=branch, pk__in=unit_ids,
    ).select_related('product', 'product__product_model', 'cosmetic_grade')}
    if len(unit_rows) != len(unit_ids):
        return None, Response({'detail': UNIT_NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)

    currency = currency_symbol(
        CompanySettings.objects.filter(company=company).values_list('currency', flat=True).first() or 'PEN')
    codes: dict[int, list[labels.LabelCode]] = {}
    for code in ProductBarcode.objects.filter(
            company=company, product_id__in=product_ids, is_active=True).order_by('-is_primary', 'code'):
        codes.setdefault(code.product_id, []).append(
            labels.LabelCode(code.code, code.symbology, code.is_primary))
    on_shelf = dict(BranchStock.objects.filter(
        branch=branch, product_id__in=product_ids).values_list('product_id', 'quantity'))
    attributes = _attributes(set(product_ids) | {u.product_id for u in unit_rows.values()})

    accessories, extra = [], {}
    for pk, copies in wanted:
        product = products[pk]
        accessories.append(labels.AccessoryData(
            product_id=pk, name=product.name, price=product.price, currency=currency,
            codes=tuple(codes.get(pk, ())),
            variant=' · '.join(value for _n, value in attributes.get(pk, ())), copies=copies))
        reasons, warnings = [], []
        if product.is_serialized:
            reasons.append('Es un producto con número de serie: se etiqueta equipo por equipo.')
        if not product.is_active:
            reasons.append('El producto está desactivado.')
        quantity = on_shelf.get(pk, 0)
        if quantity <= 0:
            reasons.append('Sin existencias en esta sucursal: no hay nada que etiquetar.')
        elif copies > quantity:
            warnings.append(f'Pides {copies} etiquetas y la sucursal tiene {quantity} unidades.')
        extra[('accessory', pk)] = (reasons, warnings)

    devices = []
    for pk in unit_ids:
        unit = unit_rows[pk]
        product = unit.product
        used = product.condition_line == Product.CONDITION_USED
        model = product.product_model
        devices.append(labels.DeviceData(
            unit_id=pk, product_id=product.pk,
            model=model.name if model is not None else product.name,
            # A seminuevo is sold at ITS price; without one it goes to review (P7).
            price=unit.sale_price if used else product.price,
            currency=currency, condition_line=product.condition_line,
            condition_label=product.get_condition_line_display(),
            attributes=tuple(attributes.get(product.pk, ())) if model is not None else (),
            battery_health=unit.battery_health, imei=unit.imei, imei2=unit.imei2,
            serial=unit.serial_number,
            # LABEL-02 adds `StockUnit.label_code`; until then there is none.
            label_code=getattr(unit, 'label_code', None),
            grade=unit.cosmetic_grade.label if unit.cosmetic_grade_id else ''))
        if unit.status not in LABELLABLE_UNIT_STATUSES:
            extra[('device', pk)] = (['El equipo ya no está en el stock de esta sucursal.'], [])

    options = _device_options(request, company)
    logo = fiscal_logo.current_png(company) if (accessories or devices) else None
    plans = labels.plan_labels(
        accessories=accessories, devices=devices, options=options, logo_png=logo,
        fallback=labels.DEVICE_TECHNICAL_60X40 if options.sensitive_allowed else None)
    for plan in plans:
        reasons, warnings = extra.get((plan.kind, plan.ref), ([], []))
        plan.reasons += reasons
        plan.warnings += warnings
    return (plans, options), None


def _plan_payload(plan) -> dict:
    symbol = plan.symbol
    first = plan.stickers[0] if plan.stickers and not plan.reasons else None
    return {
        'kind': plan.kind,
        'ref': plan.ref,
        'title': plan.title,
        'status': plan.status,
        'reasons': plan.reasons,
        'warnings': plan.warnings,
        'copies': plan.copies,
        'stickers': len(plan.stickers) if plan.ready else 0,
        'pages': plan.pages,
        'layout': {
            'key': plan.layout.key, 'name': plan.layout.name,
            'width_mm': plan.layout.width_mm, 'height_mm': plan.layout.height_mm,
        },
        'code': None if symbol is None or plan.reasons else {
            'value': symbol.value, 'symbology': symbol.symbology, 'kind': symbol.kind,
            'width_mm': round(symbol.width_mm, 2), 'module_mm': round(symbol.module_mm, 3),
            'module_dots': symbol.module_dots,
        },
        'lines': first.text_lines() if first else [],
        'has_logo': bool(first and first.logo is not None),
        'sensitive': plan.sensitive,
    }


def _payload(plans, options) -> dict:
    ready = [p for p in plans if p.ready]
    return {
        'labels': [_plan_payload(p) for p in plans],
        'summary': {'ready': len(ready), 'review': len(plans) - len(ready),
                    'pages': sum(p.pages for p in ready)},
        'permissions': {'sensitive': options.sensitive_allowed, 'device_profile': options.profile},
    }


class _LabelView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminInventoryReportsThrottle]


class LabelPreviewView(_LabelView):
    def get(self, request):
        resolved, error = _resolve(request)
        if error:
            return error
        plans, options = resolved
        response = Response(_payload(plans, options))
        response['Cache-Control'] = 'no-store'
        return response


class LabelPdfView(_LabelView):
    def get(self, request):
        resolved, error = _resolve(request)
        if error:
            return error
        plans, options = resolved
        try:
            pdf = labels.render_pdf(plans)
        except labels.NothingToRender as exc:
            body = _payload(plans, options)
            body['detail'] = str(exc)
            return Response(body, status=status.HTTP_409_CONFLICT)
        ready = [p for p in plans if p.ready]
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="etiquetas.pdf"'
        response['Cache-Control'] = 'no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        response['X-Labels-Pages'] = str(sum(p.pages for p in ready))
        response['X-Labels-Review'] = str(len(plans) - len(ready))
        return response
