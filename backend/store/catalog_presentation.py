"""
CATALOG-REFERENCE-01 · PR E — «Configuración comercial» of one model: what the existing editors keep, read in one
place so a person sees what the shop will show and what is still missing.

    READ ONLY. NOTHING HERE WRITES, AND NOTHING HERE IS NEW STATE.

Each part comes from the screen (and service) that already owns it; the panel links to it instead of copying its form:

  * visibility        `ProductModel.is_published_online`, `is_featured` (PR E), `sort_order`   — the model's form
  * palette           `ModelOption` per role and `ModelOptionExclusion`                           — `model_palette`
  * variants / price  `Product` of the model, its line, price, publication, sale mode             — variants table
  * finishes          `finish_media.listing` (colours without pictures = «Imagen pendiente»)    — finish manager
  * suppliers         active `SupplierOffer` of its variants, per supplier                        — supplier offers
  * promotions        `Promotion` with an item among its variants                                 — promotions

COSTS: the lowest agreed cost of a supplier is in the answer ONLY for a caller with `costs.view` (the same answer as
every other surface, `inventory_views.may_view_costs`). PROMOTIONS: only with `sales.promotions.view`; without it the
key is `None` (not an empty list: «you may not see them» is not «there are none»).
"""
from __future__ import annotations

from collections import Counter

from . import finish_media, model_palette
from .models import ModelOption, Product, Promotion, SupplierOffer


def commercial_summary(model, *, may_view_costs: bool, may_view_promotions: bool) -> dict:
    variants = list(model.variants.all().only(
        'pk', 'condition_line', 'price', 'is_active', 'is_published_online', 'sale_mode', 'draft_origin',
        'sale_enabled_at'))
    ids = [product.pk for product in variants]
    new = [product for product in variants if product.condition_line == Product.CONDITION_NEW]

    roles = Counter(
        ModelOption.objects.filter(model=model, company_id=model.company_id)
        .values_list('option__attribute__role', flat=True)
    )
    finishes = [row for row in finish_media.listing(model)['finishes'] if not row['not_colour']]

    # One read of the active offers of its variants. The agreed cost is read only for a caller who may see it.
    suppliers: dict[int, dict] = {}
    fields = ('supplier_id', 'supplier__name', 'product_id') + (('agreed_cost',) if may_view_costs else ())
    for row in (SupplierOffer.objects.filter(company_id=model.company_id, product_id__in=ids, is_active=True)
                .order_by('supplier__name', 'supplier_id', 'pk').values(*fields)) if ids else []:
        entry = suppliers.setdefault(row['supplier_id'], {'id': row['supplier_id'], 'name': row['supplier__name'],
                                                          'offers': 0, '_variants': set(), '_costs': []})
        entry['offers'] += 1
        entry['_variants'].add(row['product_id'])
        if may_view_costs and row['agreed_cost'] is not None:
            entry['_costs'].append(row['agreed_cost'])
    supplier_rows = []
    for entry in suppliers.values():
        item = {'id': entry['id'], 'name': entry['name'], 'offers': entry['offers'],
                'variants': len(entry['_variants'])}
        if may_view_costs:
            item['lowest_agreed_cost'] = f'{min(entry["_costs"]):.2f}' if entry['_costs'] else None
        supplier_rows.append(item)

    promotions = None
    if may_view_promotions:
        promotions = [
            {'id': promo.pk, 'name': promo.name, 'is_live': promo.is_live()}
            for promo in Promotion.objects.filter(company_id=model.company_id, items__product_id__in=ids)
            .distinct().order_by('-is_active', 'name', 'pk')
        ] if ids else []

    return {
        'model_id': model.pk,
        'visibility': {'is_published_online': model.is_published_online, 'is_featured': model.is_featured,
                       'sort_order': model.sort_order},
        'content': {'has_description': bool(model.description.strip()), 'has_cover': bool(model.image_url),
                    'reference_key': model.reference_key},
        'palette': {
            'has_palette': bool(roles),
            'options': dict(sorted(roles.items(), key=lambda item: model_palette.ROLE_RANK.get(item[0], 99))),
            'exclusions': model.option_exclusions.count() if roles else 0,
        },
        'variants': {
            'total': len(variants),
            'new': len(new),
            'used': len(variants) - len(new),
            'published': sum(1 for p in variants if p.is_active and p.is_published_online and not p.is_pending_draft),
            'priced': sum(1 for p in new if p.price and p.price > 0),
            'pending_drafts': sum(1 for p in variants if p.is_pending_draft),
            'on_order': sum(1 for p in variants if p.sale_mode == Product.SALE_MODE_STOCK_OR_ORDER),
        },
        'finishes': {'colours': len(finishes), 'pending': sum(1 for row in finishes if row['image_pending'])},
        'suppliers': supplier_rows,
        'promotions': promotions,
    }
