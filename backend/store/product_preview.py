"""
ECOM-READY-D1 — «Vista previa para el cliente».

    GET /api/admin/products/<id>/storefront-preview/        products.view

What the public shop would answer for ONE product, for the staff who are
preparing it — including when it is hidden, which the public shop will not
answer at all. READ ONLY.

THE CARD IS NOT REBUILT HERE. `product` is the public `ProductSerializer`, the
one `/api/products/` uses, fed with the same stock number (the dispatch
branch's, `available_stock`). A field added to the public card tomorrow shows
up here by itself, and a field that is not on it cannot be shown here.

Around the card, only what staff need to understand it:
  * `visibility`   — visible / hidden / inactive, in the words of the screen;
  * `availability` — immediate delivery, on order or none (published is NOT
                     available: owner, 2026-10-20);
  * `publication_problems` — what is still missing to publish in bulk;
  * `offered_units` — for a used device, the units of the dispatch branch with
                     the reason one is not offered. Label code only: no IMEI,
                     no serial, no hint of either, no cost.

Company: the caller's, from `_company_context`; the view answers 404 for a
product of another company exactly as for one that does not exist.
"""
from __future__ import annotations

from . import device_catalog, product_publication
from .models import BranchStock, Product, StockUnit
from .serializers import ProductSerializer
from .tenancy import company_fulfillment_branch

#: Units listed in one preview. A long shelf helps nobody on a side panel.
UNITS_LIMIT = 100

VISIBILITY_LABELS = {
    'visible': 'Visible',
    'hidden': 'Oculto: el cliente no lo ve',
    'inactive': 'Inactivo: el cliente no lo ve',
}

UNIT_REASONS = {
    'no_sale_price': 'Falta el precio de este equipo',
}


def _is_used_device(product) -> bool:
    return bool(product.condition_line == Product.CONDITION_USED and product.product_model_id)


def visibility(product) -> dict:
    if not product.is_active:
        state = 'inactive'
    elif not product.is_published_online:
        state = 'hidden'
    else:
        state = 'visible'
    model = product.product_model if product.product_model_id else None
    return {
        'state': state,
        'visible': state == 'visible',
        # The destination, apart from being active: an inactive product can still be marked for the web.
        'published': product.is_published_online,
        'label': VISIBILITY_LABELS[state],
        # A variant of a hidden model keeps its own card but loses its model page.
        'model_hidden': bool(model is not None and not model.is_published_online),
        # A used device has no card of its own: it is offered on its model's page.
        'placement': 'model_page' if _is_used_device(product) else 'card',
    }


def _units(product, branch):
    """(rows, offered_count) of the AVAILABLE devices of `product` on `branch`."""
    if branch is None:
        return [], 0
    offered_ids = set(device_catalog.offered_units([product], branch).values_list('pk', flat=True))
    shelf = (
        StockUnit.objects.filter(
            company_id=branch.company_id, branch=branch, product=product, status=StockUnit.Status.AVAILABLE,
        )
        .select_related('cosmetic_grade').prefetch_related('photos')
        .order_by('sale_price', 'received_at', 'pk')
    )
    rows = []
    for unit in shelf[:UNITS_LIMIT]:
        offered = unit.pk in offered_ids
        grade = unit.cosmetic_grade
        rows.append({
            'label_code': unit.label_code,
            'offered': offered,
            'reason': '' if offered else 'no_sale_price',
            'reason_label': '' if offered else UNIT_REASONS['no_sale_price'],
            'price': str(unit.sale_price) if unit.sale_price is not None else None,
            'condition': unit.condition,
            'battery_health': unit.battery_health,
            'cosmetic_grade': grade.label if grade else None,
            'photo_count': len(unit.photos.all()),
        })
    return rows, len(offered_ids)


def availability(product, branch, *, stock: int, offered_count: int, on_order) -> dict:
    if branch is None:
        return {
            'state': 'none', 'reason': 'no_dispatch_branch',
            'label': 'Sin existencias: no se ofrece para entrega inmediata',
            'note': 'La empresa no tiene una sucursal de despacho definida: la tienda no puede vender este producto.',
        }
    if _is_used_device(product):
        if offered_count:
            noun = 'equipo' if offered_count == 1 else 'equipos'
            return {'state': 'own_stock', 'label': f'{offered_count} {noun} a la venta, entrega inmediata',
                    'offered_count': offered_count}
        return {'state': 'none', 'offered_count': 0,
                'label': 'Sin equipos a la venta: no se ofrece para entrega inmediata'}
    if stock > 0:
        return {'state': 'own_stock', 'label': 'Con existencias: entrega inmediata'}
    if on_order:
        days = on_order['lead_time_days']
        return {'state': 'supplier', 'label': f'Bajo pedido: entrega en {days} {"día" if days == 1 else "días"}'}
    return {'state': 'none', 'label': 'Sin existencias: no se ofrece para entrega inmediata'}


def build(product) -> dict:
    """The preview of `product` (already of the caller's company)."""
    branch = company_fulfillment_branch(product.company)
    stock = 0
    if branch is not None:
        row = BranchStock.objects.filter(branch=branch, product=product).first()
        stock = row.quantity if row else 0
    # Same annotation the public queryset gives (`tenancy.company_storefront_products`).
    product.available_stock = stock

    card = ProductSerializer(product).data
    used = _is_used_device(product)
    units, offered_count = _units(product, branch) if used else ([], 0)
    return {
        'product': card,
        'visibility': visibility(product),
        'availability': availability(
            product, branch, stock=stock, offered_count=offered_count, on_order=card['on_order'],
        ),
        'publication_problems': product_publication.publication_problems(product),
        'offered_units': units,
    }
