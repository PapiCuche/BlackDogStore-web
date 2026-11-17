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

from .models import (
    AdminAuditLog, ModelOption, Product, ProductModel, ProductVariantOption, VariantAttribute, VariantOption,
)

#: Statuses of a configuration, as the ADR names them (§8.3, training-lines.json).
STATUS_EXISTING = 'variante_existente'
STATUS_VALID = 'combinacion_valida_sin_variante'
STATUS_OUTSIDE = 'fuera_de_paleta'
STATUS_INCOMPLETE = 'incompleta'

#: Combinations the screen receives at most; the summary still counts them all.
MAX_COMBINATIONS = 1000

ROLE_RANK = {role: n for n, role in enumerate(VariantAttribute.ROLE_ORDER)}


def attribute_sort_key(attribute) -> tuple:
    """
    Roles in their reading order (capacity, colour, SIM, region, other), then the company's own order.
    The name tie-break ignores case (A1b-2 review): it is the order the catalogue proposal always used.
    """
    return (ROLE_RANK.get(attribute.role, len(ROLE_RANK)), attribute.sort_order, attribute.name.lower(), attribute.pk)


def palette_of(model) -> dict[int, set[int]]:
    """{attribute_id: {option ids}} of `model`'s palette. Empty: the model has no palette."""
    result: dict[int, set[int]] = {}
    for attribute_id, option_id in (ModelOption.objects.filter(model=model, company_id=model.company_id)
                                    .values_list('option__attribute_id', 'option_id')):
        result.setdefault(attribute_id, set()).add(option_id)
    return result


#: Option ids one call may name (a palette, a variant): far above any real palette, below any abuse.
MAX_OPTION_IDS = 5000


def is_ascii_id(text: str) -> bool:
    """An id written as text: ASCII digits only (after trimming). Superscripts and other scripts are not ids."""
    text = text.strip()
    return bool(text) and text.isascii() and text.isdigit()


def clean_option_ids(value, *, field='option_ids') -> list[int]:
    """A sorted list of distinct positive ints, or `CatalogError`. A bool, a float or a huge list is refused."""
    from .device_catalog import CatalogError

    if value is None:
        return []
    if not isinstance(value, (list, tuple, set)) or len(value) > MAX_OPTION_IDS:
        raise CatalogError('Opciones no válidas.', field=field)
    ids = set()
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            # Ids written as ASCII digits (a spreadsheet, a form) are accepted; anything else is not an id.
            # `isdigit()` alone also says yes to «²» (int() raises) and «١» (int() reads 1): ASCII only.
            if isinstance(item, str) and is_ascii_id(item) and int(item) > 0:
                item = int(item)
            else:
                raise CatalogError('Opciones no válidas.', field=field)
        ids.add(item)
    return sorted(ids)


def check_complete(model, option_ids, *, palette=None) -> None:
    """Refuse (`variant_incomplete`) a combination that lacks an attribute of the palette of `model`."""
    from .device_catalog import CatalogError

    palette = palette_of(model) if palette is None else palette
    if not palette:
        return
    by_option = {option_id: attribute_id for attribute_id, ids in palette.items() for option_id in ids}
    missing = set(palette) - {by_option.get(int(pk)) for pk in option_ids}
    if missing:
        roles = dict(VariantAttribute.ROLE_CHOICES)
        every = sorted(VariantAttribute.objects.filter(pk__in=palette), key=attribute_sort_key)

        def said(attribute):
            # The attribute as the company calls it and, when it has one, its role: «Tipo de SIM» (SIM).
            role = f' ({roles[attribute.role]})' if attribute.role != VariantAttribute.ROLE_OTHER else ''
            return f'«{attribute.name}»{role}'

        carries = ', '.join(said(a) for a in every)
        lacking = [a for a in every if a.pk in missing]
        names = ', '.join(said(a) for a in lacking)
        raise CatalogError(
            f'Una variante de «{model.name}» lleva {carries}: a esta le falta{"n" if len(lacking) > 1 else ""} '
            f'{names}. Elige una opción de cada uno.',
            field='options', code='variant_incomplete')


def check_variant(model, options, *, palette=None, complete=True) -> None:
    """
    Refuse (`CatalogError`) a combination the palette of `model` does not admit:
    an option outside it (`option_not_in_palette`) and, with `complete`, an
    attribute of the palette missing (`variant_incomplete`). `options` are
    `VariantOption` rows with their attribute loaded. A model without a palette
    admits anything, as before. `complete=False` is for READERS that match a
    partial description against variants (the text recogniser, the supplier
    list): a partial spec is still a spec; only the writer demands all roles.
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
    if complete:
        check_complete(model, [o.pk for o in options], palette=palette)


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
def set_palette(model, *, option_ids, actor=None, request=None, expected_option_ids=None) -> dict:
    """
    Make `option_ids` THE palette of `model` (a set: what is missing goes, what
    is new comes, what is equal stays). Only options of the model's company;
    one of another company, or none, refuses the whole call (`not_found`).

    `expected_option_ids` (optional, PALETTE-SUGGEST-01 review) is the palette
    the caller last read, as a set. When the palette under the model's lock is
    not that one, somebody saved in the meantime: `stale` (409), nothing written.
    Without it, the call replaces whatever is there, as before.

    Taking an option out never rewrites a variant that uses it: that variant is
    reported as outside the palette from then on (ADR §5: history is kept).
    """
    from .device_catalog import CatalogError, _audit

    wanted = clean_option_ids(option_ids)
    expected = None
    if expected_option_ids is not None:
        if not isinstance(expected_option_ids, (list, tuple, set)):
            raise CatalogError('Opciones no válidas.', field='expected_option_ids')
        expected = set(clean_option_ids(expected_option_ids, field='expected_option_ids'))
    company = model.company
    # The model's row is the lock: two palette saves of one model queue; a variant save reads the
    # palette inside its own lock of the product, so it sees a whole palette, never half of one.
    model = type(model).objects.select_for_update().get(pk=model.pk)
    options = list(VariantOption.objects.filter(company=company, pk__in=wanted).select_related('attribute'))
    if len(options) != len(wanted):
        raise CatalogError('Alguna opción no existe.', field='option_ids', code='not_found')
    current = set(ModelOption.objects.filter(model=model, company=company).values_list('option_id', flat=True))
    if expected is not None and current != expected:
        raise CatalogError('Otra persona acaba de cambiar la paleta de este modelo. Vuelve a cargarla.',
                           field='option_ids', code='stale')
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


@transaction.atomic
def add_to_palette(model, *, option_ids, actor=None, request=None) -> dict:
    """
    B1-SIMREG — ADD `option_ids` to the palette of `model`; nothing is ever taken out. The set is joined under the
    model's lock, so an option added from another screen between somebody's read and this write stays (a whole-set
    PUT built on a stale read would have removed it). Same rules as `set_palette`: only options of the model's
    company (`not_found` otherwise, nothing written), audited with what was added.
    """
    from .device_catalog import CatalogError, _audit

    wanted = clean_option_ids(option_ids)
    company = model.company
    model = type(model).objects.select_for_update().get(pk=model.pk)
    options = list(VariantOption.objects.filter(company=company, pk__in=wanted).select_related('attribute'))
    if len(options) != len(wanted):
        raise CatalogError('Alguna opción no existe.', field='option_ids', code='not_found')
    current = set(ModelOption.objects.filter(model=model, company=company).values_list('option_id', flat=True))
    if not current:
        # Review of B1-SIMREG: a model WITHOUT a palette is never given one by an addition (it would admit only this
        # option and leave every variant outside). Decided here, under the lock; the screen's read may be stale.
        raise CatalogError('Este modelo no tiene paleta: defínela en Catálogo › Modelos antes de añadirle opciones.',
                           field='option_ids', code='no_palette')
    to_add = [o for o in options if o.pk not in current]
    try:
        for option in to_add:
            ModelOption.objects.create(company=company, model=model, option=option)
    except IntegrityError:
        raise CatalogError('Otra persona acaba de cambiar la paleta de este modelo. Vuelve a cargarla.',
                           field='option_ids', code='stale') from None
    if to_add:
        _audit('product_model_palette_set', company=company, actor=actor, request=request,
               target_type='product_model', target_id=model.pk, model_name=model.name,
               added=[o.pk for o in to_add], removed=[], total=len(current) + len(to_add))
    return {'added': len(to_add), 'removed': 0, 'total': len(current) + len(to_add)}


def _variant_rows(model):
    """
    Every variant of `model` with its option ids, sorted by attribute reading order, and (A1b-2) what
    the cockpit needs to say what each one lacks: provenance, the transition, price, picture, flags.
    """
    from django.db.models import Count

    links: dict[int, list] = {}
    for link in (ProductVariantOption.objects.filter(product__product_model=model, company_id=model.company_id)
                 .select_related('attribute', 'option')):
        links.setdefault(link.product_id, []).append(link)
    rows = []
    for product in (model.variants.annotate(image_count=Count('images')).order_by('condition_line', 'pk')
                    # `product_model` too: the related manager reads it on every row; deferred, it cost a query per row.
                    .only('pk', 'product_model', 'name', 'condition_line', 'draft_origin', 'sale_enabled_at', 'price',
                          'image_url', 'is_published_online', 'is_active', 'is_serialized')):
        own = sorted(links.get(product.pk, []), key=lambda l: attribute_sort_key(l.attribute))
        rows.append({
            'product_id': product.pk, 'name': product.name, 'condition_line': product.condition_line,
            'option_ids': [l.option_id for l in own],
            'draft_origin': product.draft_origin,
            'draft_pending': product.is_pending_draft,
            'sale_enabled_at': product.sale_enabled_at.isoformat() if product.sale_enabled_at else None,
            'has_image': bool(product.image_url) or product.image_count > 0,
            'price_set': product.price is not None and product.price > 0,
            'is_published_online': product.is_published_online,
            'is_active': product.is_active,
            'is_serialized': product.is_serialized,
        })
    return rows


def _needs_review(row) -> bool:
    """
    A1b-2: a variant the cockpit lists under «Revisar» — a pending draft, a new-line variant without a
    price (a used one is priced device by device), or one standing outside / short of the palette.
    """
    return bool(
        row['draft_pending']
        or (row['condition_line'] == Product.CONDITION_NEW and not row['price_set'])
        or row['status'] in (STATUS_OUTSIDE, STATUS_INCOMPLETE)
    )


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
            if option.company_id == model.company_id:
                labels[option.pk] = option.label
            options.append({'id': option.pk, 'label': option.label, 'swatch_hex': option.swatch_hex,
                            'sort_order': option.sort_order, 'in_palette': option.pk in chosen})
        attribute_rows.append({'id': attribute.pk, 'name': attribute.name, 'kind': attribute.kind,
                               'role': attribute.role, 'sort_order': attribute.sort_order, 'options': options})

    variants = _variant_rows(model)
    # PALETTE-SUGGEST-01: what the variants already use, read from the rows above (no query of its own).
    suggested = sorted({pk for row in variants for pk in row['option_ids'] if pk in labels})
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

    # Counted over the variants, not over the (possibly cut) list of combinations.
    existing_keys = {variant_key(r['option_ids']) for r in variants if r['status'] == STATUS_EXISTING}
    # A1b-2: the roles the company has attributes for that the palette does not cover (empty without a
    # palette: nothing is «missing» from a model that admits anything). In reading order.
    roles_missing = []
    if palette:
        for attribute in attributes:
            if attribute.role != VariantAttribute.ROLE_OTHER and attribute.pk not in palette \
                    and attribute.role not in roles_missing:
                roles_missing.append(attribute.role)
    return {
        'model_id': model.pk, 'model_name': model.name,
        'has_palette': bool(palette),
        'attributes': attribute_rows,
        'combinations': combinations,
        'variants': variants,
        # PALETTE-SUGGEST-01: the union of the options its variants use (both lines, active or not). Read-only:
        # the panel pre-checks them and the person saves with the usual PUT.
        'suggested_option_ids': suggested,
        'summary': {
            'combinations': total,
            'existing': len(existing_keys),
            'valid_without_variant': max(total - len(existing_keys), 0) if palette else 0,
            'outside': sum(1 for r in variants if r['status'] == STATUS_OUTSIDE),
            'incomplete': sum(1 for r in variants if r['status'] == STATUS_INCOMPLETE),
            # A1b-2: what the cockpit's «Revisar» holds, and the roles the palette lacks.
            'pending_drafts': sum(1 for r in variants if r['draft_pending']),
            'needs_review': sum(1 for r in variants if _needs_review(r)),
            'roles_missing': roles_missing,
        },
        'truncated': total > len(combinations),
    }


def suggestions(company) -> dict:
    """
    PALETTE-SUGGEST-01 — «Paletas por armar»: every model of `company` WITHOUT a palette, each with the palette its
    variants suggest (the union of the options they use: both lines, active or not), grouped by attribute in role
    order, and how many of its variants would stand «incompleta» if that palette were saved as it is (a variant
    that lacks one of the suggested attributes).

    READ-ONLY: it writes nothing and audits nothing; the person saves a model's palette with the usual PUT
    (`set_palette`, `products.manage`). Every role counts, «Otro» too: `set_palette` accepts any option of the
    company. Nothing of another company is read: an option of another company never enters a suggestion.

    FOUR QUERIES, whatever the size of the catalogue: the attributes, their options, the models without a palette
    (with their number of variants) and the option links of those models' variants.
    """
    from django.db.models import Count, Exists, OuterRef, Q

    attributes = list(VariantAttribute.objects.filter(company=company).prefetch_related('options'))
    attributes.sort(key=attribute_sort_key)
    attribute_of: dict[int, int] = {}
    option_rows: dict[int, dict] = {}
    for attribute in attributes:
        for option in attribute.options.all():
            if option.company_id == company.pk:
                attribute_of[option.pk] = attribute.pk
                option_rows[option.pk] = {'id': option.pk, 'label': option.label, 'swatch_hex': option.swatch_hex}

    with_palette = ModelOption.objects.filter(model=OuterRef('pk'))
    models = list(
        ProductModel.objects.filter(company=company).filter(~Exists(with_palette))
        .annotate(variant_count=Count('variants', filter=Q(variants__company=company)))
        .order_by('sort_order', 'name', 'pk').values('pk', 'name', 'variant_count')
    )

    # model → product → {attribute ids it carries}; only options of the company, only models without a palette.
    carried: dict[int, dict[int, set[int]]] = {}
    links = (ProductVariantOption.objects
             .filter(company=company, product__company=company, product__product_model__company=company)
             .exclude(Exists(ModelOption.objects.filter(model=OuterRef('product__product_model'))))
             .values_list('product__product_model_id', 'product_id', 'option_id'))
    chosen: dict[int, set[int]] = {}
    for model_id, product_id, option_id in links:
        attribute_id = attribute_of.get(option_id)
        if attribute_id is None:
            continue
        carried.setdefault(model_id, {}).setdefault(product_id, set()).add(attribute_id)
        chosen.setdefault(model_id, set()).add(option_id)

    rows = []
    for model in models:
        ids = chosen.get(model['pk'], set())
        # One group per attribute, in role order; its options in the company's own order (`Meta.ordering`).
        groups = []
        for attribute in attributes:
            options = [option_rows[option.pk] for option in attribute.options.all() if option.pk in ids]
            if options:
                groups.append({'attribute_id': attribute.pk, 'name': attribute.name, 'role': attribute.role,
                               'kind': attribute.kind, 'options': options})
        suggested_attributes = {group['attribute_id'] for group in groups}
        products = carried.get(model['pk'], {})
        incomplete = 0
        if suggested_attributes:
            # A variant with no option of the company at all is not in `products`: it would be incomplete too.
            incomplete = max(model['variant_count'] - len(products), 0) + sum(
                1 for attrs in products.values() if suggested_attributes - attrs)
        rows.append({
            'model_id': model['pk'], 'name': model['name'], 'variant_count': model['variant_count'],
            'suggested_option_ids': sorted(ids),
            'groups': groups,
            'incomplete_variants': incomplete,
        })
    return {
        'models': rows,
        'summary': {'without_palette': len(rows), 'with_suggestion': sum(1 for r in rows if r['suggested_option_ids'])},
    }
