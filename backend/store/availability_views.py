"""
COMMERCE-ADMIN-UX-01 — «Ventas › Consultar disponibilidad». READ ONLY.

    GET /api/admin/availability/
        ?q=         words; each must match the name, a barcode, the model or an option
        &line=      new | used
        &branch=    one branch the caller reaches (default: all of them)
        &category=  &model=  &option= (repeatable: capacity, colour… — the company's attributes)
        &product=   one product
        &facets=1   adds the filter lists (categories, models, attributes)
        &page= &page_size=
    GET /api/admin/availability/?product=<id>&units=1[&unit_status=live|all|<status>]
        the devices of one product, one by one.

WHO. Anybody who may sell at the counter: `sales.pos.use`. It is the least
authority every seller already holds (the «Ventas» preset and the legacy sales
role both carry it), and the till already shows what this shows per branch —
the shelf, the price and whether it sells «bajo pedido». `inventory.view` is
NOT asked: the Ventas preset does not carry it, and it would open the stockroom
(full IMEIs, the Kardex) to make a sales question answerable. Like the till,
there is no legacy-role bridge: a Membership or nothing.

WHERE. The caller's company and the branches they reach, nothing else; a
branch outside them answers 404 like one that does not exist.

THE OWNER'S RULES
  * Own stock is `BranchStock.quantity` (what decides a sale) and, for devices,
    the states of `StockUnit`. A supplier's quantity is NEVER added to it: the
    valid offers travel in their own list, and the screen labels them «no es
    stock propio».
  * NO COST, EVER — not even for `costs.view`. Costs live on «Costos y precios»;
    a screen opened at the counter in front of a customer does not carry them.
  * No full IMEI or serial (the last four), no hold key, no hold origin, no
    internal note. The supplier's NAME only for whoever may see suppliers
    (`products.view`, as DC3's own screens ask).

COST OF A PAGE: a fixed number of queries whatever its size (asserted).
"""
from __future__ import annotations

from django.db.models import Count, Exists, Max, Min, OuterRef, Prefetch, Q
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import supply_services
from .inventory_views import _paginate
from .models import (
    BranchStock, Category, Product, ProductBarcode, ProductModel, ProductVariantOption, StockUnit,
    SupplierOffer, VariantAttribute, VariantOption,
)
from .pos_views import _context as _pos_context
from .tenancy import BranchAccessError, NoBranchError, has_capability, resolve_branch_for_user, visible_branches
from .throttles import AdminPosThrottle

CAP_POS = 'sales.pos.use'
#: DC3: who may see suppliers (their names).
CAP_SUPPLIERS = 'products.view'

#: How many offers per product: the best ones. A long tail helps nobody at a counter.
OFFERS_PER_PRODUCT = 3
#: Devices of one product per answer.
UNITS_LIMIT = 100
MAX_TOKENS = 6
MAX_TOKEN_LENGTH = 40

LIVE = (StockUnit.Status.AVAILABLE, StockUnit.Status.RESERVED)


class BadParameter(ValueError):
    pass


def _pk(params, name):
    raw = (params.get(name) or '').strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        raise BadParameter(name) from None
    if value <= 0:
        raise BadParameter(name)
    return value


def _pks(params, name):
    values = []
    for raw in params.getlist(name):
        for piece in str(raw).split(','):
            piece = piece.strip()
            if not piece:
                continue
            try:
                values.append(int(piece))
            except ValueError:
                raise BadParameter(name) from None
    return values[:10]


def _line(params):
    line = (params.get('line') or '').strip()
    if line and line not in (Product.CONDITION_NEW, Product.CONDITION_USED):
        raise BadParameter('line')
    return line


def search_products(rows, q: str):
    """
    Every word must match somewhere: the name, a barcode (from its start), the
    model's name or one of the variant's options («256», «azul»).
    """
    tokens = [t[:MAX_TOKEN_LENGTH] for t in (q or '').split()][:MAX_TOKENS]
    for token in tokens:
        rows = rows.filter(
            Q(name__icontains=token)
            | Q(product_model__name__icontains=token)
            | Exists(ProductBarcode.objects.filter(product=OuterRef('pk'), is_active=True,
                                                   code__istartswith=token))
            | Exists(ProductVariantOption.objects.filter(product=OuterRef('pk'),
                                                         option__label__icontains=token))
        )
    return rows


def filter_variants(rows, *, model_id=None, option_ids=()):
    """A model, and options that ALL must hold (128 GB and black). Shared with «Equipos»."""
    if model_id:
        rows = rows.filter(product_model_id=model_id)
    for option_id in option_ids:
        rows = rows.filter(Exists(ProductVariantOption.objects.filter(product=OuterRef('pk'), option_id=option_id)))
    return rows


def parse_variant_filters(params):
    """(`model`, `option` ids) of a request. Raises BadParameter."""
    return _pk(params, 'model'), _pks(params, 'option')


def _mask_tail(value) -> str:
    text = str(value or '')
    return text[-4:] if len(text) > 4 else ''


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------

def _empty_counts():
    return {'available': 0, 'reserved_web': 0, 'reserved_staff': 0, 'sold': 0, 'written_off': 0}


def _product_rows(company, products, branches, *, with_suppliers: bool):
    """The rows of a page. Four queries whatever its size: shelves, devices, offers (+ prefetches)."""
    ids = [p.pk for p in products]
    branch_ids = [b.pk for b in branches]

    shelves = {}
    for product_id, branch_id, quantity in BranchStock.objects.filter(
            product_id__in=ids, branch_id__in=branch_ids).values_list('product_id', 'branch_id', 'quantity'):
        shelves[(product_id, branch_id)] = max(int(quantity or 0), 0)

    devices, prices = {}, {}
    reserved = Q(status=StockUnit.Status.RESERVED)
    unit_rows = (
        StockUnit.objects.filter(company=company, product_id__in=ids, branch_id__in=branch_ids)
        .values('product_id', 'branch_id')
        .annotate(
            reserved_web=Count('pk', filter=reserved & ~Q(hold_key='')),
            reserved_staff=Count('pk', filter=reserved & Q(hold_key='')),
            sold=Count('pk', filter=Q(status=StockUnit.Status.SOLD)),
            written_off=Count('pk', filter=Q(status=StockUnit.Status.WRITTEN_OFF)),
            min_price=Min('sale_price', filter=Q(status=StockUnit.Status.AVAILABLE)),
            max_price=Max('sale_price', filter=Q(status=StockUnit.Status.AVAILABLE)),
        )
        .order_by()
    )
    for row in unit_rows:
        devices[(row['product_id'], row['branch_id'])] = row
        if row['min_price'] is not None:
            low, high = prices.get(row['product_id'], (row['min_price'], row['max_price']))
            prices[row['product_id']] = (min(low, row['min_price']), max(high, row['max_price']))

    offers = {}
    offer_rows = (
        SupplierOffer.objects.filter(supply_services.valid_offer_q(), company=company, product_id__in=ids)
        .select_related('supplier')
        .order_by('product_id', *supply_services.BEST_OFFER_ORDER)
    )
    for offer in offer_rows:
        bucket = offers.setdefault(offer.product_id, [])
        if len(bucket) >= OFFERS_PER_PRODUCT:
            continue
        item = {
            'commercial_price': str(offer.commercial_price),
            'remaining': offer.remaining,
            'valid_until': offer.valid_until.isoformat(),
            'lead_time_days': offer.lead_time_days,
        }
        if with_suppliers:
            item['supplier_name'] = offer.supplier.name
        bucket.append(item)

    results = []
    for product in products:
        by_branch, total = [], _empty_counts()
        for branch in branches:
            counts = _empty_counts()
            counts['available'] = shelves.get((product.pk, branch.pk), 0)
            row = devices.get((product.pk, branch.pk))
            if row:
                for key in ('reserved_web', 'reserved_staff', 'sold', 'written_off'):
                    counts[key] = row[key]
            for key, value in counts.items():
                total[key] += value
            by_branch.append({'branch_id': branch.pk, 'branch_name': branch.name, **counts})

        barcodes = list(product.barcodes.all())
        price_range = prices.get(product.pk)
        results.append({
            'id': product.pk,
            'name': product.name,
            'barcode': barcodes[0].code if barcodes else '',
            'model': ({'id': product.product_model_id, 'name': product.product_model.name}
                      if product.product_model_id else None),
            'category': {'id': product.category_id, 'name': product.category.name} if product.category_id else None,
            'line': product.condition_line,
            'line_label': product.get_condition_line_display(),
            'options': [
                {'attribute': link.attribute.name, 'kind': link.attribute.kind, 'label': link.option.label,
                 'swatch_hex': link.option.swatch_hex}
                for link in product.variant_options.all()
            ],
            'is_serialized': product.is_serialized,
            'price': str(product.price),
            'unit_price_range': (
                {'min': str(price_range[0]), 'max': str(price_range[1])} if price_range else None
            ),
            'sale_mode': product.sale_mode,
            'sells_on_order': supply_services.sells_on_order(product),
            'is_published_online': product.is_published_online,
            'stock': {'by_branch': by_branch, 'total': total},
            'supplier_offers': offers.get(product.pk, []),
        })
    return results


def _unit_payload(unit) -> dict:
    web_hold = unit.status == StockUnit.Status.RESERVED and bool(unit.hold_key)
    hold_kind = None
    if unit.status == StockUnit.Status.RESERVED:
        hold_kind = 'web' if web_hold else 'staff'
    return {
        'id': unit.pk,
        'branch_id': unit.branch_id,
        'branch_name': unit.branch.name,
        'status': unit.status,
        'status_label': unit.get_status_display(),
        'hold_kind': hold_kind,
        'hold_until': unit.hold_until if web_hold else None,
        'imei_last4': _mask_tail(unit.imei),
        'serial_last4': _mask_tail(unit.serial_number),
        'condition': unit.condition,
        'condition_label': unit.get_condition_display(),
        'battery_health': unit.battery_health,
        'cosmetic_grade_label': unit.cosmetic_grade.label if unit.cosmetic_grade_id else '',
        'public_notes': unit.public_notes,
        'sale_price': None if unit.sale_price is None else str(unit.sale_price),
        'received_at': unit.received_at,
        'sold_at': unit.sold_at,
    }


def _facets(company):
    return {
        'categories': [{'id': c.pk, 'name': c.name}
                       for c in Category.objects.filter(company=company).order_by('name', 'pk')],
        'models': [{'id': m.pk, 'name': m.name}
                   for m in ProductModel.objects.filter(company=company).order_by('sort_order', 'name', 'pk')],
        'attributes': [
            {'id': a.pk, 'name': a.name, 'kind': a.kind,
             'options': [{'id': o.pk, 'label': o.label, 'swatch_hex': o.swatch_hex} for o in a.options.all()]}
            for a in VariantAttribute.objects.filter(company=company).prefetch_related(
                Prefetch('options', queryset=VariantOption.objects.order_by('sort_order', 'label', 'pk')))
        ],
    }


def _bad(name) -> Response:
    return Response({'detail': f'Parámetro "{name}" inválido.'}, status=status.HTTP_400_BAD_REQUEST)


class AdminAvailabilityView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AdminPosThrottle]

    def get(self, request):
        company, error = _pos_context(request, CAP_POS)
        if error:
            return error
        params = request.query_params
        try:
            # No branch named = every branch the caller reaches: the question is «where is it».
            branch = resolve_branch_for_user(request.user, company, params.get('branch') or 'all', allow_all=True)
        except NoBranchError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except BranchAccessError:
            return Response({'detail': 'Sucursal no encontrada o sin acceso.'}, status=status.HTTP_404_NOT_FOUND)
        visible = visible_branches(request.user, company)
        branches = list((visible.filter(pk=branch.pk) if branch is not None else visible).order_by('name', 'pk'))

        try:
            product_id = _pk(params, 'product')
            category_id = _pk(params, 'category')
            model_id, option_ids = parse_variant_filters(params)
            line = _line(params)
        except BadParameter as exc:
            return _bad(exc.args[0])

        with_suppliers = has_capability(request.user, company, CAP_SUPPLIERS)
        products = (
            Product.objects.filter(company=company, is_active=True)
            .select_related('product_model', 'category')
            .prefetch_related(
                Prefetch('barcodes', queryset=ProductBarcode.objects.filter(is_active=True).order_by('-is_primary', 'pk')),
                Prefetch('variant_options', queryset=ProductVariantOption.objects.select_related('attribute', 'option')
                         .order_by('attribute__sort_order', 'attribute__name', 'pk')),
            )
        )

        if params.get('units') in ('1', 'true'):
            return self._units(request, company, products, product_id, branches, with_suppliers)

        if product_id:
            products = products.filter(pk=product_id)
        if category_id:
            products = products.filter(category_id=category_id)
        if line:
            products = products.filter(condition_line=line)
        products = filter_variants(products, model_id=model_id, option_ids=option_ids)
        products = search_products(products, params.get('q') or '')

        page, meta = _paginate(products.order_by('name', 'pk'), request)
        body = {
            **meta,
            'scope': {
                'branch': None if branch is None else {'id': branch.pk, 'name': branch.name},
                'branches': [{'id': b.pk, 'name': b.name} for b in branches],
                'is_aggregate': branch is None,
            },
            'can_see_suppliers': with_suppliers,
            'results': _product_rows(company, list(page), branches, with_suppliers=with_suppliers),
        }
        if params.get('facets') in ('1', 'true'):
            body['facets'] = _facets(company)
        return Response(body)

    def _units(self, request, company, products, product_id, branches, with_suppliers):
        if not product_id:
            return _bad('product')
        product = products.filter(pk=product_id).first()
        if product is None:
            return Response({'detail': 'Producto no encontrado.'}, status=status.HTTP_404_NOT_FOUND)

        wanted = (request.query_params.get('unit_status') or 'live').strip()
        rows = StockUnit.objects.filter(company=company, product=product, branch__in=branches)
        if wanted == 'live':
            rows = rows.filter(status__in=LIVE)
        elif wanted in StockUnit.Status.values:
            rows = rows.filter(status=wanted)
        elif wanted != 'all':
            return _bad('unit_status')
        rows = rows.select_related('branch', 'cosmetic_grade').order_by('status', 'branch__name', 'received_at', 'pk')
        total = rows.count()
        return Response({
            'product': _product_rows(company, [product], branches, with_suppliers=with_suppliers)[0],
            'count': total,
            'truncated': total > UNITS_LIMIT,
            'results': [_unit_payload(unit) for unit in rows[:UNITS_LIMIT]],
        })
