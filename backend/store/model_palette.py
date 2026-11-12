"""
CATALOG-COMMERCIAL-01 · A1 — the PALETTE of a model (ADR §5.7, §8, decisions 1 and 2).

    A MODEL SAYS WHICH OPTIONS IT ADMITS. A VARIANT COMBINES ONLY THOSE.

`ModelOption(model, option)` rows are the palette: the capacities, colours, SIM
types and regions (whatever the company calls them, `VariantAttribute.role`)
that this model's variants may combine. The only writer of those rows is
`set_palette`, inside ONE company: an option of another company answers like
one that does not exist.

Rules, and where each is held:

  * every option of a variant is in the palette          `check_variant` (service, before writing)
  * a variant carries ONE option of EVERY palette         `check_variant` — all variants of a model
    attribute (same roles, ADR §5.7)                      with a palette have the same roles
  * a model with no palette accepts any company option    nothing changes for it (as before A1)
  * what already exists is never rewritten               `set_palette` only touches `ModelOption`;
                                                          `variant_status` REPORTS 'fuera_de_paleta'
  * catalogue changes never create stock                  nothing here touches `BranchStock`/units

What the admin screen reads (`payload`) is the cartesian product of the
palette, each combination with its state (ADR §8.3): `variante_existente`,
`combinacion_valida_sin_variante`; and, for the variants that exist,
`fuera_de_paleta` or `incompleta`. Nothing of this reaches the PUBLIC payload
(`device_catalog.public_model`), which A1 leaves byte for byte as it was.
"""
from __future__ import annotations

from itertools import product as cartesian

from django.db import IntegrityError, transaction

from .models import AdminAuditLog, ModelOption, Product, ProductVariantOption, VariantAttribute, VariantOption

#: Statuses of a configuration, as the ADR names them (§8.3, training-lines.json).
STATUS_EXISTING = 'variante_existente'
STATUS_VALID = 'combinacion_valida_sin_variante'
STATUS_OUTSIDE = 'fuera_de_paleta'
STATUS_INCOMPLETE = 'incompleta'

#: Combinations the screen receives at most; the summary still counts them all.
MAX_COMBINATIONS = 1000

ROLE_RANK = {role: n for n, role in enumerate(VariantAttribute.ROLE_ORDER)}


def attribute_sort_key(attribute) -> tuple:
    """Roles in their reading order (capacity, colour, SIM, region, other), then the company's own order."""
    return (ROLE_RANK.get(attribute.role, len(ROLE_RANK)), attribute.sort_order, attribute.name, attribute.pk)


def palette_of(model) -> dict[int, set[int]]:
    """{attribute_id: {option ids}} of `model`'s palette. Empty: the model has no palette."""
    result: dict[int, set[int]] = {}
    for attribute_id, option_id in (ModelOption.objects.filter(model=model, company_id=model.company_id)
                                    .values_list('option__attribute_id', 'option_id')):
        result.setdefault(attribute_id, set()).add(option_id)
    return result


def check_variant(model, options, *, palette=None) -> None:
    """
    Refuse (`CatalogError`) a combination the palette of `model` does not admit.
    `options` are `VariantOption` rows with their attribute loaded. A model
    without a palette admits anything, as before.
    """
    from .device_catalog import CatalogError

    palette = palette_of(model) if palette is None else palette
    if not palette:
        return
    outside = [o for o in options if o.pk not in palette.get(o.attribute_id, ())]
    if outside:
        names = ', '.join(f'«{o.label}» ({o.attribute.name})' for o in outside)
        raise CatalogError(
            f'«{model.name}» no admite {names}: no está en las opciones de este modelo. '
            'Agrégala a la paleta del modelo o elige otra.', field='options', code='option_not_in_palette')
    missing = set(palette) - {o.attribute_id for o in options}
    if missing:
        names = ', '.join(f'«{a.name}»'
                          for a in VariantAttribute.objects.filter(pk__in=missing).order_by('sort_order', 'name'))
        raise CatalogError(
            f'Una variante de «{model.name}» lleva {names}: elige una opción de cada uno.',
            field='options', code='variant_incomplete')


def variant_status(model, option_ids, *, palette=None) -> str | None:
    """
    How a variant that EXISTS stands against the palette: `variante_existente`
    (every option in it and one per palette attribute), `fuera_de_paleta` (an
    option or an attribute the palette does not hold) or `incompleta` (only
    palette options, but an attribute of the palette missing). None when the
    model has no palette.
    """
    palette = palette_of(model) if palette is None else palette
    if not palette:
        return None
    by_option = {}
    for attribute_id, ids in palette.items():
        for option_id in ids:
            by_option[option_id] = attribute_id
    chosen = set(int(pk) for pk in option_ids)
    if any(pk not in by_option for pk in chosen):
        return STATUS_OUTSIDE
    if set(palette) - {by_option[pk] for pk in chosen}:
        return STATUS_INCOMPLETE
    return STATUS_EXISTING


@transaction.atomic
def set_palette(model, *, option_ids, actor=None, request=None) -> dict:
    """
    Make `option_ids` THE palette of `model` (a set: what is missing goes, what
    is new comes, what is equal stays). Only options of the model's company;
    one of another company, or none, refuses the whole call (`not_found`).

    Taking an option out never rewrites a variant that uses it: that variant is
    reported as outside the palette from then on (ADR §5: history is kept).
    """
    from .device_catalog import CatalogError, _audit

    try:
        wanted = sorted({int(pk) for pk in (option_ids or [])})
    except (TypeError, ValueError):
        raise CatalogError('Opciones no válidas.', field='option_ids') from None
    company = model.company
    # The model's row is the lock: two palette saves of one model queue; a variant save reads the
    # palette inside its own lock of the product, so it sees a whole palette, never half of one.
    model = type(model).objects.select_for_update().get(pk=model.pk)
    options = list(VariantOption.objects.filter(company=company, pk__in=wanted).select_related('attribute'))
    if len(options) != len(wanted):
        raise CatalogError('Alguna opción no existe.', field='option_ids', code='not_found')
    current = set(ModelOption.objects.filter(model=model, company=company).values_list('option_id', flat=True))
    to_add = [o for o in options if o.pk not in current]
    to_remove = sorted(current - set(wanted))
    if to_remove:
        ModelOption.objects.filter(model=model, company=company, option_id__in=to_remove).delete()
    try:
        for option in to_add:
            ModelOption.objects.create(company=company, model=model, option=option)
    except IntegrityError:
        raise CatalogError('Otra persona acaba de cambiar la paleta de este modelo. Vuelve a cargarla.',
                           field='option_ids', code='stale') from None
    _audit('product_model_palette_set', company=company, actor=actor, request=request,
           target_type='product_model', target_id=model.pk, model_name=model.name,
           added=[o.pk for o in to_add], removed=to_remove, total=len(wanted))
    return {'added': len(to_add), 'removed': len(to_remove), 'total': len(wanted)}


def _variant_rows(model):
    """Every variant of `model` with its option ids, sorted by attribute reading order."""
    links: dict[int, list] = {}
    for link in (ProductVariantOption.objects.filter(product__product_model=model, company_id=model.company_id)
                 .select_related('attribute', 'option')):
        links.setdefault(link.product_id, []).append(link)
    rows = []
    for product in model.variants.order_by('condition_line', 'pk').only('pk', 'name', 'condition_line'):
        own = sorted(links.get(product.pk, []), key=lambda l: attribute_sort_key(l.attribute))
        rows.append({'product_id': product.pk, 'name': product.name, 'condition_line': product.condition_line,
                     'option_ids': [l.option_id for l in own]})
    return rows


def payload(model) -> dict:
    """
    What «Opciones de este modelo» shows: every attribute of the company with
    each option and whether it is in the palette; the combinations of the
    palette with their state and the products that are them; every variant of
    the model with its state; and a summary. Admin only, never public.
    """
    from .device_catalog import variant_key

    palette = palette_of(model)
    attributes = list(VariantAttribute.objects.filter(company_id=model.company_id).prefetch_related('options'))
    attributes.sort(key=attribute_sort_key)
    labels = {}
    attribute_rows = []
    for attribute in attributes:
        chosen = palette.get(attribute.pk, set())
        options = []
        for option in attribute.options.all():
            labels[option.pk] = option.label
            options.append({'id': option.pk, 'label': option.label, 'swatch_hex': option.swatch_hex,
                            'sort_order': option.sort_order, 'in_palette': option.pk in chosen})
        attribute_rows.append({'id': attribute.pk, 'name': attribute.name, 'kind': attribute.kind,
                               'role': attribute.role, 'sort_order': attribute.sort_order, 'options': options})

    variants = _variant_rows(model)
    by_key: dict[str, list] = {}
    for row in variants:
        row['status'] = variant_status(model, row['option_ids'], palette=palette)
        by_key.setdefault(variant_key(row['option_ids']), []).append(row)

    combinations, total = [], 0
    if palette:
        # One column per attribute, in reading order; options in the company's own order within each.
        columns = []
        for attribute in attributes:
            if attribute.pk in palette:
                columns.append([(attribute.pk, option.pk) for option in attribute.options.all()
                                if option.pk in palette[attribute.pk]])
        total = 1
        for column in columns:
            total *= len(column)
        for combo in cartesian(*columns):
            if len(combinations) >= MAX_COMBINATIONS:
                break
            ids = [option_id for _attribute_id, option_id in combo]
            key = variant_key(ids)
            products = [{'product_id': r['product_id'], 'name': r['name'], 'condition_line': r['condition_line']}
                        for r in by_key.get(key, [])]
            combinations.append({
                'key': key, 'option_ids': ids,
                'labels': {str(attribute_id): labels[option_id] for attribute_id, option_id in combo},
                'status': STATUS_EXISTING if products else STATUS_VALID,
                'products': products,
            })

    existing_keys = {c['key'] for c in combinations if c['status'] == STATUS_EXISTING}
    return {
        'model_id': model.pk, 'model_name': model.name,
        'has_palette': bool(palette),
        'attributes': attribute_rows,
        'combinations': combinations,
        'variants': variants,
        'summary': {
            'combinations': total,
            'existing': len(existing_keys),
            'valid_without_variant': max(total - len(existing_keys), 0) if palette else 0,
            'outside': sum(1 for r in variants if r['status'] == STATUS_OUTSIDE),
            'incomplete': sum(1 for r in variants if r['status'] == STATUS_INCOMPLETE),
        },
        'truncated': total > len(combinations),
    }
