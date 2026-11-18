"""
ORDERITEM-SNAPSHOT-01 — what a sale line WAS, frozen when the line is written.

A sales note is redrawn on every download, e-mail and print job. Its money is
the sale's (C2.1) and its identity the order's (`company_snapshot`), but its
lines used to read the LIVE product: rename «iPhone 16 128 GB» tomorrow and a
note issued last month reprints with the new name. Every surface that creates
an `OrderItem` (the web checkout and the till, shelf and on-order lines alike)
passes through `fields_for_lines` and writes three columns once:

  * `description_snapshot` — the product's name, as the paper prints it;
  * `variant_snapshot` — the variant in the catalogue's own words, by role:
    `model`, `capacity`, `color`, `sim`, `region`, `other` (a list of
    `{name, value}`), `condition` / `condition_label` (new or used line) and,
    for a line that IS one device, the device's cosmetic `grade`. A product
    that is no variant (an accessory) freezes `{}`;
  * `list_price_snapshot` — the catalogue price at the moment of sale: the
    device's own price for a used device sold as itself, the product's price
    otherwise; NULL when the catalogue had none (zero or absent).

Lines written before this (or by a test that builds rows by hand) keep
`description_snapshot=''` and NULLs: `line_description` falls back to the live
product for them. Nothing here rewrites a line once it exists.
"""
from __future__ import annotations

from decimal import Decimal


def _list_price(product, unit) -> Decimal | None:
    if unit is not None:
        from .unit_hold_services import price_for

        price = price_for(unit, product)
    else:
        price = product.price
    if price is None:
        return None
    price = Decimal(str(price))
    return price if price.is_finite() and price > 0 else None


def fields_for_lines(lines) -> list[dict]:
    """
    The snapshot columns of each `(product, unit_or_None)` in `lines`, in order.

    A handful of queries for the whole sale (models, options, grades), never one per line.
    """
    from .model_palette import attribute_sort_key
    from .models import CosmeticGrade, Product, ProductModel, ProductVariantOption, VariantAttribute

    lines = list(lines)
    products = {product.pk: product for product, _unit in lines}
    model_ids = {p.product_model_id for p in products.values() if p.product_model_id}
    models = {m.pk: m for m in ProductModel.objects.filter(pk__in=model_ids)} if model_ids else {}
    options: dict[int, list] = {}
    if products:
        for link in (ProductVariantOption.objects
                     .filter(product_id__in=list(products))
                     .select_related('option', 'attribute')):
            # Inside the product's company: a foreign row is never part of a line.
            if link.company_id == products[link.product_id].company_id:
                options.setdefault(link.product_id, []).append(link)
    grade_ids = {unit.cosmetic_grade_id for _p, unit in lines if unit is not None and unit.cosmetic_grade_id}
    grades = {g.pk: g for g in CosmeticGrade.objects.filter(pk__in=grade_ids)} if grade_ids else {}
    condition_labels = dict(Product.CONDITION_LINE_CHOICES)

    out = []
    for product, unit in lines:
        variant: dict = {}
        model = models.get(product.product_model_id)
        if model is not None and model.company_id == product.company_id:
            variant['model'] = model.name
            variant['condition'] = product.condition_line
            variant['condition_label'] = condition_labels.get(product.condition_line, '')
        for link in sorted(options.get(product.pk, []), key=lambda row: attribute_sort_key(row.attribute)):
            role = link.attribute.role
            if role == VariantAttribute.ROLE_OTHER:
                variant.setdefault('other', []).append({'name': link.attribute.name, 'value': link.option.label})
            else:
                variant[role] = link.option.label
        grade = grades.get(unit.cosmetic_grade_id) if unit is not None and unit.cosmetic_grade_id else None
        if grade is not None and grade.company_id == product.company_id:
            variant['grade'] = grade.label
        out.append({
            'description_snapshot': product.name or '',
            'variant_snapshot': variant,
            'list_price_snapshot': _list_price(product, unit),
        })
    return out


def fields_for(product, unit=None) -> dict:
    """`fields_for_lines` for one line."""
    return fields_for_lines([(product, unit)])[0]


def line_description(item) -> str:
    """What a document prints as the line's description: the frozen one, or the live name of an old line."""
    if item.description_snapshot:
        return item.description_snapshot
    return item.product.name if item.product_id else '—'
