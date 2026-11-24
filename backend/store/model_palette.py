"""
CATALOG-COMMERCIAL-01 · A1 — the PALETTE of a model (ADR §5.7, §8, decisions 1 and 2).

    A MODEL SAYS WHICH OPTIONS IT ADMITS. A VARIANT COMBINES ONLY THOSE.

`ModelOption(model, option)` rows are the palette: the capacities, colours, SIM
types and regions (whatever the company calls them, `VariantAttribute.role`)
that this model's variants may combine. The only writers of those rows are
`set_palette` / `add_to_palette` (and, for their display order only,
`set_option_order`, PR E), inside ONE company: an option of another company
answers like one that does not exist.

Rules, and where each is held:

  * every option of a variant is in the palette          `check_variant` (service, before writing)
  * a variant carries ONE option of EVERY palette         `check_variant` — all variants of a model
    attribute (same roles, ADR §5.7)                      with a palette have the same roles
  * a model with no palette accepts any company option    nothing changes for it (as before A1)
  * what already exists is never rewritten               `set_palette` only touches `ModelOption`;
                                                          `variant_status` REPORTS 'fuera_de_paleta'
  * catalogue changes never create stock                  nothing here touches `BranchStock`/units
  * two options of an EXCLUDED pair never go together     `check_variant` — `ModelOptionExclusion`
    (CATALOG-REFERENCE-01 · PR B); a model without        (written only by `add_exclusions`); the
    exclusions behaves exactly as before                  matrix leaves those combinations out

What the admin screen reads (`payload`) is the cartesian product of the
palette, each combination with its state (ADR §8.3): `variante_existente`,
`combinacion_valida_sin_variante`; and, for the variants that exist,
`fuera_de_paleta` or `incompleta`. Nothing of this reaches the PUBLIC payload
(`device_catalog.public_model`), which A1 leaves byte for byte as it was.
"""
from __future__ import annotations

from itertools import product as cartesian

from django.db import IntegrityError, transaction

from django.db.models import Exists, OuterRef

from .models import (
    AdminAuditLog, ModelOption, ModelOptionExclusion, Product, ProductModel, ProductVariantOption, VariantAttribute,
    VariantOption,
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


class Palette(dict):
    """
    {attribute_id: {option ids}} — a plain dict for every reader — that also knows whether the model has
    EXCLUDED pairs (PR B). The palette query says it (an EXISTS beside each row), so a model without exclusions
    costs no query more than before; the pairs themselves are read once, on first use, only when there are some.
    """

    def __init__(self, *args, model=None, has_exclusions=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.model = model
        self.has_exclusions = has_exclusions
        self._excluded = None

    @property
    def excluded(self) -> set[frozenset]:
        if not self.has_exclusions:
            return set()
        if self._excluded is None:
            self._excluded = exclusions_of(self.model)
        return self._excluded


def palette_of(model) -> dict[int, set[int]]:
    """{attribute_id: {option ids}} of `model`'s palette (a `Palette`). Empty: the model has no palette."""
    result = Palette(model=model)
    excluded = ModelOptionExclusion.objects.filter(model_id=OuterRef('model_id'))
    for attribute_id, option_id, has_exclusions in (
            ModelOption.objects.filter(model=model, company_id=model.company_id)
            .annotate(has_exclusions=Exists(excluded))
            .values_list('option__attribute_id', 'option_id', 'has_exclusions')):
        result.setdefault(attribute_id, set()).add(option_id)
        result.has_exclusions = result.has_exclusions or has_exclusions
    return result


def exclusions_of(model) -> set[frozenset]:
    """The excluded pairs of `model`, each as a frozenset of two option ids. Empty: no exclusions."""
    return {frozenset(pair) for pair in ModelOptionExclusion.objects.filter(
        model=model, company_id=model.company_id).values_list('option_a_id', 'option_b_id')}


def _excluded_of(model, palette) -> set[frozenset]:
    return palette.excluded if isinstance(palette, Palette) else exclusions_of(model)


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
    excluded = _excluded_of(model, palette) if len(options) > 1 else set()
    if excluded:
        by_pk = {o.pk: o for o in options}
        for pair in excluded:
            if pair <= by_pk.keys():
                first, second = sorted((by_pk[pk] for pk in pair), key=lambda o: attribute_sort_key(o.attribute))
                raise CatalogError(
                    f'«{model.name}» no existe con «{first.label}» ({first.attribute.name}) y «{second.label}» '
                    f'({second.attribute.name}) a la vez: esa combinación está excluida en este modelo.',
                    field='options', code='combination_excluded')
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
    # FASE 1.2 · Y: a row taken out may hold this model's own swatch; the audit keeps it so it can be read back.
    removed_swatches = {}
    if to_remove:
        leaving = ModelOption.objects.filter(model=model, company=company, option_id__in=to_remove)
        removed_swatches = {
            str(option_id): {'swatch_hex': hex_value, 'swatch_is_approximate': approximate}
            for option_id, hex_value, approximate in
            leaving.exclude(swatch_hex='').values_list('option_id', 'swatch_hex', 'swatch_is_approximate')
        }
        leaving.delete()
    try:
        for option in to_add:
            ModelOption.objects.create(company=company, model=model, option=option)
    except IntegrityError:
        raise CatalogError('Otra persona acaba de cambiar la paleta de este modelo. Vuelve a cargarla.',
                           field='option_ids', code='stale') from None
    _audit('product_model_palette_set', company=company, actor=actor, request=request,
           target_type='product_model', target_id=model.pk, model_name=model.name,
           added=[o.pk for o in to_add], removed=to_remove, removed_swatches=removed_swatches, total=len(wanted))
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


#: The rank of an option nobody placed: after every placed one (PR E).
UNPLACED = 1 << 30


def placed_rank(rank) -> int:
    """`ModelOption.sort_order` as a sort key: 1, 2, 3… first; 0 (never placed) after them."""
    return rank if rank else UNPLACED


def option_order(model) -> dict:
    """
    PR E — the options of `model`'s palette per attribute, in the order the model shows them: the ones placed by
    `set_option_order` first (1, 2, 3…), then the rest in the company's own order (`VariantOption.sort_order`,
    label). Attributes in reading order (capacity, colour, SIM, region, other). Admin only.
    """
    from .device_catalog import resolved_swatch

    rows = list(
        ModelOption.objects.filter(model=model, company_id=model.company_id)
        .select_related('option__attribute')
    )
    by_attribute: dict[int, dict] = {}
    for row in rows:
        attribute = row.option.attribute
        entry = by_attribute.setdefault(attribute.pk, {'attribute': attribute, 'rows': []})
        entry['rows'].append(row)
    attributes = []
    for entry in sorted(by_attribute.values(), key=lambda e: attribute_sort_key(e['attribute'])):
        attribute = entry['attribute']
        ordered = sorted(entry['rows'], key=lambda r: (placed_rank(r.sort_order), r.option.sort_order,
                                                       r.option.label.lower(), r.option.pk))
        attributes.append({
            'id': attribute.pk, 'name': attribute.name, 'kind': attribute.kind, 'role': attribute.role,
            # FASE 1.2 · Y: the swatch this model draws (its own, else the company's, else none).
            'options': [{'id': r.option.pk, 'label': r.option.label,
                         'swatch_hex': resolved_swatch(r.option, r.swatch_hex, r.swatch_is_approximate)[0],
                         'placed': bool(r.sort_order)} for r in ordered],
        })
    return {'model_id': model.pk, 'has_palette': bool(rows), 'attributes': attributes}


@transaction.atomic
def set_option_order(model, *, attribute_id, option_ids, actor=None, request=None) -> dict:
    """
    PR E — the order in which `model` shows the options of ONE of its attributes (its colours, say). `option_ids`
    names every palette option of that attribute exactly once; they are placed 1, 2, 3… (`ModelOption.sort_order`).
    Only this model changes: the company's own order (`VariantOption.sort_order`) and every other model stay.

    An attribute of another company answers like a missing one (`not_found`); a model without a palette has nothing
    to order (`no_palette`); anything else that is not that exact set is refused whole (`invalid`). Under the model's
    lock, like every palette write, and audited with its company.
    """
    from .device_catalog import CatalogError, _audit

    if isinstance(attribute_id, bool) or not isinstance(attribute_id, int) or attribute_id <= 0:
        raise CatalogError('Elige el atributo que vas a ordenar.', field='attribute_id')
    if (not isinstance(option_ids, list) or len(option_ids) > MAX_OPTION_IDS
            or any(isinstance(i, bool) or not isinstance(i, int) or i <= 0 for i in option_ids)):
        raise CatalogError('El orden es una lista de opciones.', field='option_ids')
    company = model.company
    model = type(model).objects.select_for_update().get(pk=model.pk)
    attribute = VariantAttribute.objects.filter(company=company, pk=attribute_id).first()
    if attribute is None:
        raise CatalogError('Ese atributo no existe.', field='attribute_id', code='not_found')
    rows = {row.option_id: row for row in ModelOption.objects.filter(model=model, company=company)
            .select_related('option')}
    if not rows:
        raise CatalogError('Este modelo no tiene paleta: elige primero sus opciones.', field='option_ids',
                           code='no_palette')
    current = {pk for pk, row in rows.items() if row.option.attribute_id == attribute.pk}
    if not current:
        # Review of #374: nothing of that attribute is in this palette, so there is nothing to order (not a no-op 200).
        raise CatalogError(f'Este modelo no tiene opciones de ese atributo («{attribute.name}») en su paleta.',
                           field='attribute_id')
    if len(option_ids) != len(set(option_ids)) or set(option_ids) != current:
        raise CatalogError(
            f'El orden debe nombrar, una vez, cada opción de «{attribute.name}» de este modelo.',
            field='option_ids')
    changed = []
    for position, option_id in enumerate(option_ids, start=1):
        row = rows[option_id]
        if row.sort_order != position:
            row.sort_order = position
            row.save(update_fields=['sort_order'])
            changed.append(option_id)
    _audit('product_model_option_order_set', company=company, actor=actor, request=request,
           target_type='product_model', target_id=model.pk, model_name=model.name, attribute_id=attribute.pk,
           option_ids=list(option_ids), changed=changed)
    return option_order(model)


def model_swatches(model) -> dict[int, tuple[str, bool]]:
    """FASE 1.2 · Y: option id → (this model's own swatch, approximate) for the colours that have one. One query."""
    return {option_id: (hex_value, approximate) for option_id, hex_value, approximate in
            ModelOption.objects.filter(model=model, company_id=model.company_id).exclude(swatch_hex='')
            .values_list('option_id', 'swatch_hex', 'swatch_is_approximate')}


def swatch_fields(option, own=None, *, editable=None) -> dict:
    """
    FASE 1.2 · Y — what the PANEL says of a colour's swatch on one model (admin only; the shop gets only the resolved
    hex): the company's flag, the model's own swatch ('' = inherits) and flag, and what is drawn
    (`device_catalog.resolved_swatch`). `editable`: whether the model may have its own (the colour is in its palette).
    """
    from .device_catalog import resolved_swatch

    model_hex, model_approximate = own or ('', False)
    shown, approximate = resolved_swatch(option, model_hex, model_approximate)
    fields = {'swatch_is_approximate': option.swatch_is_approximate, 'model_swatch_hex': model_hex,
              'model_swatch_is_approximate': bool(model_hex and model_approximate),
              'shown_swatch_hex': shown, 'shown_swatch_is_approximate': approximate}
    if editable is not None:
        fields['model_swatch_editable'] = editable
    return fields


@transaction.atomic
def set_model_swatch(model, *, option_id, swatch_hex=None, swatch_is_approximate=None, actor=None,
                     request=None) -> dict:
    """
    FASE 1.2 · Y — `model`'s OWN swatch for one colour of its palette, without touching the company's colour (which
    every other model shares). `swatch_hex` '' goes back to the company's; `swatch_is_approximate` says the value is
    an approximation pending commercial validation (never an official code) and needs a swatch of the model's own.
    Setting a value without the flag is allowed (an owner-approved value); the flag changes only when it is sent.

    An option of another company answers like a missing one (`not_found`); a model without a palette has no row to
    hold it (`no_palette`); the option must be a colour (`not_a_colour`) of this palette (`not_in_palette`). Under the
    model's lock, like every palette write, and audited with its company and the previous values.
    """
    from .device_catalog import CatalogError, _audit
    from .finish_media import colour_attribute_ids
    from .models import validate_swatch_hex
    from django.core.exceptions import ValidationError

    if isinstance(option_id, bool) or not isinstance(option_id, int) or option_id <= 0:
        raise CatalogError('Elige el color.', field='option_id')
    if swatch_hex is None:
        if swatch_is_approximate is None:
            raise CatalogError('Escribe la muestra (#RRGGBB) o déjala vacía para usar la de la empresa.',
                               field='swatch_hex')
    elif not isinstance(swatch_hex, str):
        raise CatalogError('La muestra se escribe como #RRGGBB.', field='swatch_hex')
    if swatch_is_approximate is not None and not isinstance(swatch_is_approximate, bool):
        raise CatalogError('«Muestra aproximada» es verdadero o falso.', field='swatch_is_approximate')
    company = model.company
    model = type(model).objects.select_for_update().get(pk=model.pk)
    option = VariantOption.objects.filter(company=company, pk=option_id).select_related('attribute').first()
    if option is None:
        raise CatalogError('Ese color no existe.', field='option_id', code='not_found')
    if (option.attribute.kind != VariantAttribute.KIND_COLOR
            and option.attribute_id not in colour_attribute_ids(company.pk)):
        raise CatalogError('Esa opción no es un color.', field='option_id', code='not_a_colour')
    rows = ModelOption.objects.filter(model=model, company=company)
    row = rows.select_for_update().filter(option=option).first()
    if row is None:
        if not rows.exists():
            raise CatalogError('Este modelo no tiene paleta: elige primero sus opciones.', field='option_id',
                               code='no_palette')
        raise CatalogError('Ese color no está entre las opciones de este modelo.', field='option_id',
                           code='not_in_palette')

    value = row.swatch_hex
    if swatch_hex is not None:
        value = swatch_hex.strip().upper()
        try:
            validate_swatch_hex(value)
        except ValidationError as exc:
            raise CatalogError(exc.messages[0], field='swatch_hex') from None
    # Like the company's flag, it changes only when sent; going back to the company's swatch clears it.
    approximate = row.swatch_is_approximate if swatch_is_approximate is None else swatch_is_approximate
    if swatch_hex is not None and not value and swatch_is_approximate is None:
        approximate = False
    if approximate and not value:
        raise CatalogError('Escribe primero la muestra de este modelo: sin ella se usa la de la empresa.',
                           field='swatch_is_approximate')

    previous = {'swatch_hex': row.swatch_hex, 'swatch_is_approximate': row.swatch_is_approximate}
    row.swatch_hex, row.swatch_is_approximate = value, approximate
    row.save(update_fields=['swatch_hex', 'swatch_is_approximate'])
    _audit('product_model_swatch_set', company=company, actor=actor, request=request,
           target_type='product_model', target_id=model.pk, model_name=model.name, option_id=option.pk,
           label=option.label, swatch_hex=value, swatch_is_approximate=approximate, previous=previous)
    return {'model_id': model.pk, 'option_id': option.pk, 'label': option.label, 'swatch_hex': value,
            'swatch_is_approximate': approximate,
            **{key: val for key, val in swatch_fields(option, (value, approximate)).items()
               if key.startswith('shown_')}}


def _clean_pairs(pairs) -> set[tuple[int, int]]:
    from .device_catalog import CatalogError

    if not isinstance(pairs, (list, tuple, set)) or len(pairs) > MAX_OPTION_IDS:
        raise CatalogError('Pares no válidos.', field='pairs')
    out = set()
    for pair in pairs:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise CatalogError('Cada par son dos opciones.', field='pairs')
        ids = clean_option_ids(list(pair), field='pairs')
        if len(ids) != 2:
            raise CatalogError('Un par son dos opciones distintas.', field='pairs')
        out.add((ids[0], ids[1]))
    return out


@transaction.atomic
def add_exclusions(model, *, pairs, actor=None, request=None) -> dict:
    """
    CATALOG-REFERENCE-01 · PR B — ADD impossible pairs to `model` (nothing is ever taken out here). Each pair is two
    options of the model's PALETTE, of two different attributes, of the model's company (another company's option
    answers `not_found`, nothing written). Under the model's lock, like the palette. A pair already there is not
    written again; audited only when something was added. A variant that already is such a combination is left as
    it is (history is kept); new ones are refused by `check_variant`.
    """
    from .device_catalog import CatalogError, _audit

    wanted = _clean_pairs(pairs)
    company = model.company
    model = type(model).objects.select_for_update().get(pk=model.pk)
    ids = {pk for pair in wanted for pk in pair}
    options = {o.pk: o for o in VariantOption.objects.filter(company=company, pk__in=ids).select_related('attribute')}
    if len(options) != len(ids):
        raise CatalogError('Alguna opción no existe.', field='pairs', code='not_found')
    palette = set(ModelOption.objects.filter(model=model, company=company).values_list('option_id', flat=True))
    outside = sorted(ids - palette)
    if outside:
        raise CatalogError(f'«{options[outside[0]].label}» no está en la paleta de «{model.name}».',
                           field='pairs', code='option_not_in_palette')
    for first, second in wanted:
        if options[first].attribute_id == options[second].attribute_id:
            raise CatalogError('Un par son opciones de dos atributos distintos.', field='pairs')
    current = exclusions_of(model)
    to_add = sorted(pair for pair in wanted if frozenset(pair) not in current)
    for first, second in to_add:
        ModelOptionExclusion.objects.create(company=company, model=model, option_a=options[first],
                                            option_b=options[second])
    if to_add:
        _audit('product_model_exclusions_added', company=company, actor=actor, request=request,
               target_type='product_model', target_id=model.pk, model_name=model.name,
               added=[list(pair) for pair in to_add], total=len(current) + len(to_add))
    return {'added': len(to_add), 'total': len(current) + len(to_add)}


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
                          'image_url', 'is_published_online', 'is_active', 'is_serialized', 'archived_at')):
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
            # VARIANT-SAFE-01: kept in the list (it holds its combination), out of the «existing» set.
            'archived': product.archived_at is not None,
        })
    return rows


def _needs_review(row) -> bool:
    """
    A1b-2: a variant the cockpit lists under «Revisar» — a pending draft, a new-line variant without a
    price (a used one is priced device by device), or one standing outside / short of the palette.
    """
    if row.get('archived'):
        return False    # VARIANT-SAFE-01: nobody prices or publishes an archived variant
    return bool(
        row['draft_pending']
        or (row['condition_line'] == Product.CONDITION_NEW and not row['price_set'])
        or row['status'] in (STATUS_OUTSIDE, STATUS_INCOMPLETE)
    )


def _combination(key, ids, combo, labels, by_key) -> dict:
    """
    One configuration of the matrix. VARIANT-SAFE-01: a configuration only ARCHIVED variants hold says so
    (`archived`): it is not «existing» (nothing sells it) and it is not offered to be created (the archived row
    keeps the combination), and the panel reads it «Archivada».
    """
    rows = by_key.get(key, [])
    products = [{'product_id': r['product_id'], 'name': r['name'], 'condition_line': r['condition_line'],
                 'archived': r['archived']} for r in rows]
    return {
        'key': key, 'option_ids': ids,
        'labels': {str(attribute_id): labels[option_id] for attribute_id, option_id in combo},
        'status': STATUS_EXISTING if products else STATUS_VALID,
        'archived': bool(rows) and all(r['archived'] for r in rows),
        'products': products,
    }


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
    pictures = None
    for attribute in attributes:
        chosen = palette.get(attribute.pk, set())
        colour = attribute.kind == VariantAttribute.KIND_COLOR or attribute.role == VariantAttribute.ROLE_COLOR
        if colour and pictures is None:
            from .finish_media import chip_pictures

            pictures = chip_pictures(model)
            own_swatches = model_swatches(model)
        options = []
        for option in attribute.options.all():
            if option.company_id == model.company_id:
                labels[option.pk] = option.label
            row = {'id': option.pk, 'label': option.label, 'swatch_hex': option.swatch_hex,
                   'sort_order': option.sort_order, 'in_palette': option.pk in chosen}
            if colour:
                # G (owner's visual order 4): the chip's picture, and whether the swatch is the company's.
                from .device_catalog import swatch_is_set

                row.update(swatch_set=swatch_is_set(option), thumbnail=pictures.get(option.pk),
                           # FASE 1.2 · Y: the model's own swatch and what is drawn; approximations (panel only).
                           **swatch_fields(option, own_swatches.get(option.pk)))
            options.append(row)
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
    excluded = palette.excluded if palette else set()
    if palette and excluded:
        # PR B: only a model WITH exclusions takes this path; the combinations that hold an excluded pair are not
        # offered, and the total counts what is left (the whole product is walked; palettes are small).
        columns = [[(attribute.pk, option.pk) for option in attribute.options.all()
                    if option.pk in palette[attribute.pk]] for attribute in attributes if attribute.pk in palette]
        for combo in cartesian(*columns):
            ids = [option_id for _attribute_id, option_id in combo]
            chosen = set(ids)
            if any(pair <= chosen for pair in excluded):
                continue
            total += 1
            if len(combinations) >= MAX_COMBINATIONS:
                continue
            key = variant_key(ids)
            combinations.append(_combination(key, ids, combo, labels, by_key))
    elif palette:
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
            combinations.append(_combination(key, ids, combo, labels, by_key))

    # Counted over the variants, not over the (possibly cut) list of combinations.
    existing_keys = {variant_key(r['option_ids']) for r in variants
                     if r['status'] == STATUS_EXISTING and not r['archived']}
    # VARIANT-SAFE-01: configurations only archived variants hold: neither existing nor creatable.
    archived_keys = {variant_key(r['option_ids']) for r in variants
                     if r['status'] == STATUS_EXISTING and r['archived']} - existing_keys
    # Review of #371: a variant that already is an EXCLUDED combination is not one of the offered ones; it is counted
    # apart (only for a model with exclusions), so `existing` + `valid_without_variant` = `combinations`.
    excluded_keys = {variant_key(r['option_ids']) for r in variants if r['status'] == STATUS_EXISTING
                     and any(pair <= set(r['option_ids']) for pair in excluded)} if excluded else set()
    existing_keys -= excluded_keys
    archived_keys -= excluded_keys
    # A1b-2: the roles the company has attributes for that the palette does not cover (empty without a
    # palette: nothing is «missing» from a model that admits anything). In reading order.
    roles_missing = []
    if palette:
        for attribute in attributes:
            if attribute.role != VariantAttribute.ROLE_OTHER and attribute.pk not in palette \
                    and attribute.role not in roles_missing:
                roles_missing.append(attribute.role)
    extra = {'excluded_pairs': sorted(sorted(pair) for pair in excluded)} if excluded else {}
    excluded_count = {'excluded_existing': len(excluded_keys)} if excluded else {}
    return {
        **extra,
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
            'valid_without_variant': max(total - len(existing_keys) - len(archived_keys), 0) if palette else 0,
            'archived': len(archived_keys),
            'outside': sum(1 for r in variants if r['status'] == STATUS_OUTSIDE),
            'incomplete': sum(1 for r in variants if r['status'] == STATUS_INCOMPLETE),
            # A1b-2: what the cockpit's «Revisar» holds, and the roles the palette lacks.
            'pending_drafts': sum(1 for r in variants if r['draft_pending']),
            'needs_review': sum(1 for r in variants if _needs_review(r)),
            'roles_missing': roles_missing,
            **excluded_count,
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
