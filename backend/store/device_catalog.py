"""
DEVICE-COMMERCE-01 · DC2 — models, options and variants of the catalogue.

    A VARIANT IS A PRODUCT. A MODEL ONLY GROUPS THEM.

The only writer of `ProductModel`, `VariantAttribute`, `VariantOption`,
`ProductVariantOption` and of `Product.product_model` / `condition_line` /
`variant_key`. Everything is inside ONE company: a model, an option or a
product of another company answers exactly like one that does not exist.
(The palette of a model, `ModelOption`, is written by `model_palette`.)

The rules a variant obeys, and where each is held:

  * one option per attribute per product   UNIQUE (product, attribute)
  * no two variants of one model with the
    same options in the same line          UNIQUE (model, line, variant_key)
  * everything of one company               checked here, before writing
  * a colour swatch is #RRGGBB              `validate_swatch_hex`
  * only options of the model's palette,    `model_palette.check_variant` (A1); a model
    one per palette attribute               without a palette admits anything, as before
  * one attribute per role per company      UNIQUE (company, role) WHERE role <> 'other'

This module also builds what the PUBLIC reads of a model (`public_model`):
an allowlist written by hand, so a field added to a unit tomorrow does not
reach the shop by default. Never a full IMEI, a serial, a cost or a supplier.
"""
from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from . import storefront_media
from .description_text import DESCRIPTION_MAX_LENGTH, normalize_description, too_long_message
from django.utils.text import slugify

from .models import (
    AdminAuditLog, BranchStock, Category, CosmeticGrade, Product, ProductModel, ProductVariantOption,
    StockUnit, VariantAttribute, VariantOption, validate_swatch_hex,
)

DEFAULT_NEW_LABEL = 'Nuevos'
DEFAULT_USED_LABEL = 'Seminuevos'

#: The default scale offered to a company (`seed_cosmetic_grades`). Words of
#: the platform, not of any shop; the company edits them as it likes.
DEFAULT_COSMETIC_GRADES = (
    ('como-nuevo', 'Como nuevo', 'Sin marcas visibles.'),
    ('excelente', 'Excelente', 'Marcas mínimas, sólo visibles de cerca.'),
    ('muy-bueno', 'Muy bueno', 'Marcas leves de uso en el marco o la parte trasera.'),
    ('bueno', 'Bueno', 'Marcas de uso visibles; funciona perfectamente.'),
)


class CatalogError(Exception):
    """A rule of the catalogue was broken. `field` names the input, when there is one."""

    def __init__(self, message: str, *, field: str | None = None, code: str = 'invalid'):
        super().__init__(message)
        self.field = field
        self.code = code

    def body(self) -> dict:
        payload = {'detail': str(self), 'code': self.code}
        if self.field:
            payload['field'] = self.field
        return payload


def _text(value, label: str, field: str, *, max_length: int, required: bool = True) -> str:
    text = ' '.join(str(value or '').split())
    if required and not text:
        raise CatalogError(f'Indica {label}.', field=field)
    if len(text) > max_length:
        raise CatalogError(f'{label[0].upper()}{label[1:]} admite hasta {max_length} caracteres.', field=field)
    return text


def _description(value) -> str:
    """CATALOG-RICH-DESC-01: a description that is too long is refused, never cut without a word."""
    text = normalize_description(value)
    if len(text) > DESCRIPTION_MAX_LENGTH:
        raise CatalogError(too_long_message(len(text)), field='description', code='too_long')
    return text


def _audit(action, *, company, actor, request, target_type, target_id, **metadata):
    AdminAuditLog.log(
        actor=actor, action=action, target_type=target_type, target_id=target_id,
        metadata=metadata, request=request, company=company,
    )


# ---------------------------------------------------------------------------
# Attributes and options
# ---------------------------------------------------------------------------

def _clean_role(role) -> str:
    role = VariantAttribute.ROLE_OTHER if role is None else str(role).strip().lower()
    if role not in dict(VariantAttribute.ROLE_CHOICES):
        raise CatalogError('Rol desconocido: capacidad, color, SIM, región u otro.', field='role')
    return role


def _name_free(company, name, *, exclude=None) -> None:
    """No other attribute of `company` is called `name` (case-insensitive). Named before the database refuses it."""
    taken = VariantAttribute.objects.filter(company=company, name__iexact=name)
    if exclude is not None:
        taken = taken.exclude(pk=exclude.pk)
    if taken.exists():
        raise CatalogError(f'Ya existe el atributo «{name}».', field='name', code='duplicate')


def _role_free(company, role, *, exclude=None) -> None:
    """A1: one attribute per role per company, except «otro». Named before the database refuses it."""
    if role == VariantAttribute.ROLE_OTHER:
        return
    taken = VariantAttribute.objects.filter(company=company, role=role)
    if exclude is not None:
        taken = taken.exclude(pk=exclude.pk)
    holder = taken.first()
    if holder is not None:
        raise CatalogError(
            f'«{holder.name}» ya tiene el rol «{dict(VariantAttribute.ROLE_CHOICES)[role]}» en esta empresa: '
            'cada rol lo lleva un solo atributo.', field='role', code='role_taken')


def create_attribute(*, company, name, kind=VariantAttribute.KIND_TEXT, sort_order=0,
                     role=VariantAttribute.ROLE_OTHER, actor=None, request=None) -> VariantAttribute:
    name = _text(name, 'el nombre del atributo', 'name', max_length=60)
    if kind not in dict(VariantAttribute.KIND_CHOICES):
        raise CatalogError('Tipo de atributo desconocido.', field='kind')
    role = _clean_role(role)
    if VariantAttribute.objects.filter(company=company, name__iexact=name).exists():
        raise CatalogError(f'Ya existe el atributo «{name}».', field='name', code='duplicate')
    _role_free(company, role)
    try:
        with transaction.atomic():
            attribute = VariantAttribute.objects.create(
                company=company, name=name, kind=kind, role=role, sort_order=int(sort_order or 0),
            )
    except IntegrityError:
        raise CatalogError(f'Ya existe el atributo «{name}» o ya hay uno con ese rol.', field='name',
                           code='duplicate') from None
    _audit('variant_attribute_created', company=company, actor=actor, request=request,
           target_type='variant_attribute', target_id=attribute.pk, name=name, kind=kind, role=role)
    return attribute


def update_attribute(attribute, *, name=None, role=None, sort_order=None, actor=None, request=None) -> VariantAttribute:
    """A1: the name, the role or the order of an attribute. Its kind and its options do not change here."""
    previous = {'name': attribute.name, 'role': attribute.role, 'sort_order': attribute.sort_order}
    changed = []
    if name is not None:
        attribute.name = _text(name, 'el nombre del atributo', 'name', max_length=60)
        _name_free(attribute.company, attribute.name, exclude=attribute)
        changed.append('name')
    if role is not None:
        attribute.role = _clean_role(role)
        _role_free(attribute.company, attribute.role, exclude=attribute)
        changed.append('role')
    if sort_order is not None:
        if isinstance(sort_order, bool) or not str(sort_order).strip().lstrip('-').isdigit() or int(sort_order) < 0:
            raise CatalogError('El orden es un número entero desde 0.', field='sort_order')
        attribute.sort_order = int(sort_order)
        changed.append('sort_order')
    try:
        with transaction.atomic():
            attribute.save()
    except IntegrityError:
        # A1b (LOW 3 of the A1 audit): the pre-checks saw nothing and the database refused the row — somebody
        # wrote in between. Ask again WHICH rule it was, so the answer names the right field.
        others = VariantAttribute.objects.filter(company=attribute.company).exclude(pk=attribute.pk)
        if others.filter(name__iexact=attribute.name).exists():
            raise CatalogError(f'Ya existe el atributo «{attribute.name}».', field='name', code='duplicate') from None
        holder = others.filter(role=attribute.role).first() if attribute.role != VariantAttribute.ROLE_OTHER else None
        if holder is not None:
            raise CatalogError(
                f'«{holder.name}» ya tiene el rol «{dict(VariantAttribute.ROLE_CHOICES)[attribute.role]}» en esta '
                'empresa: cada rol lo lleva un solo atributo.', field='role', code='role_taken') from None
        raise CatalogError('Otro atributo ya tiene ese nombre o ese rol.', field='role', code='role_taken') from None
    _audit('variant_attribute_updated', company=attribute.company, actor=actor, request=request,
           target_type='variant_attribute', target_id=attribute.pk, changed=changed, name=attribute.name,
           role=attribute.role, previous=previous)
    return attribute


def _clean_swatch(attribute, swatch_hex) -> str:
    value = str(swatch_hex or '').strip()
    if attribute.kind != VariantAttribute.KIND_COLOR:
        if value:
            raise CatalogError('Sólo las opciones de un atributo de color llevan muestra.', field='swatch_hex')
        return ''
    if not value:
        raise CatalogError('Una opción de color lleva su muestra (#RRGGBB).', field='swatch_hex')
    try:
        validate_swatch_hex(value)
    except ValidationError as exc:
        raise CatalogError(exc.messages[0], field='swatch_hex') from None
    return value.upper()


def create_option(*, company, attribute_id, label, swatch_hex='', sort_order=0,
                  actor=None, request=None) -> VariantOption:
    attribute = VariantAttribute.objects.filter(company=company, pk=attribute_id).first()
    if attribute is None:
        raise CatalogError('Atributo no encontrado.', field='attribute', code='not_found')
    label = _text(label, 'el valor', 'label', max_length=60)
    swatch = _clean_swatch(attribute, swatch_hex)
    order = _clean_sort_order(sort_order)
    if attribute.options.filter(label__iexact=label).exists() or _reads_like_a_sibling(attribute, label):
        raise CatalogError(f'«{attribute.name}» ya tiene la opción «{label}».', field='label', code='duplicate')
    _not_an_option_alias(company, label)
    try:
        with transaction.atomic():
            option = VariantOption.objects.create(
                company=company, attribute=attribute, label=label, swatch_hex=swatch,
                sort_order=order,
            )
    except IntegrityError:
        raise CatalogError(f'«{attribute.name}» ya tiene la opción «{label}».', field='label', code='duplicate') from None
    _audit('variant_option_created', company=company, actor=actor, request=request,
           target_type='variant_option', target_id=option.pk, attribute_id=attribute.pk, label=label)
    return option


def _reads_like_a_sibling(attribute, label, *, exclude=None) -> bool:
    """A2: «Azul-Marino» next to «Azul Marino» (same words once accents, case and joiners are folded) would be
    one text for the supplier reader: the same option, refused like a duplicate."""
    from .configuration_resolver import colour_key, phrase_key

    key = colour_key(phrase_key(label))
    siblings = attribute.options.all() if exclude is None else attribute.options.exclude(pk=exclude.pk)
    return any(colour_key(phrase_key(other)) == key for other in siblings.values_list('label', flat=True))


def _not_an_option_alias(company, label) -> None:
    """A2 (DEC-CATALOG-ALIAS-01): a label never takes the text of a company alias — it would change in silence
    what that alias reads. The alias is deleted or changed first."""
    from .catalog_aliases import option_alias_holding

    holder = option_alias_holding(company, label)
    if holder is not None:
        raise CatalogError(
            f'«{label}» ya es un alias de «{holder.option.label}» en el vocabulario de proveedores: bórralo o '
            'cámbialo antes de usarlo como opción.', field='label', code='conflict_with_alias')


def _clean_sort_order(value) -> int:
    """An order is a whole number from 0 (a blank one is 0, as before); the field is unsigned."""
    if value in (None, ''):
        return 0
    if isinstance(value, bool):
        raise CatalogError('El orden es un número entero desde 0.', field='sort_order')
    text = str(value).strip()
    if not (text.isascii() and text.isdigit()) or int(text) > 2_147_483_647:
        raise CatalogError('El orden es un número entero desde 0.', field='sort_order')
    return int(text)


def update_option(option, *, label=None, swatch_hex=None, sort_order=None, swatch_is_approximate=None, actor=None,
                  request=None) -> VariantOption:
    # CATALOG-F11: the audit says what the option was, so a rename or a reorder can be read back (and undone).
    previous = {'label': option.label, 'sort_order': option.sort_order, 'swatch_hex': option.swatch_hex,
                'swatch_is_approximate': option.swatch_is_approximate}
    if label is not None:
        option.label = _text(label, 'el valor', 'label', max_length=60)
        if option.attribute.options.exclude(pk=option.pk).filter(label__iexact=option.label).exists() \
                or _reads_like_a_sibling(option.attribute, option.label, exclude=option):
            raise CatalogError(f'«{option.attribute.name}» ya tiene la opción «{option.label}».',
                               field='label', code='duplicate')
        _not_an_option_alias(option.company, option.label)
    if swatch_hex is not None:
        option.swatch_hex = _clean_swatch(option.attribute, swatch_hex)
    if sort_order is not None:
        option.sort_order = _clean_sort_order(sort_order)
    if swatch_is_approximate is not None:
        # FASE 1.2 · Y: a swatch a person chose as an APPROXIMATION, pending commercial validation. Changed only when
        # sent: a new value without it is the company's own (owner-approved) choice and keeps whatever flag it had.
        if not isinstance(swatch_is_approximate, bool):
            raise CatalogError('«Muestra aproximada» es verdadero o falso.', field='swatch_is_approximate')
        if swatch_is_approximate and option.attribute.kind != VariantAttribute.KIND_COLOR:
            raise CatalogError('Sólo una opción de color lleva muestra.', field='swatch_is_approximate')
        option.swatch_is_approximate = swatch_is_approximate
    option.save()
    _audit('variant_option_updated', company=option.company, actor=actor, request=request,
           target_type='variant_option', target_id=option.pk, label=option.label, sort_order=option.sort_order,
           swatch_hex=option.swatch_hex, swatch_is_approximate=option.swatch_is_approximate, previous=previous)
    return option


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

def _category(company, category_id):
    if category_id in (None, ''):
        return None
    category = Category.objects.filter(company=company, pk=category_id).first()
    if category is None:
        raise CatalogError('Categoría no encontrada.', field='category', code='not_found')
    return category


def _free_slug(company, base: str, *, exclude=None) -> str:
    base = slugify(base)[:100] or 'modelo'
    slug, n = base, 2
    taken = ProductModel.objects.filter(company=company)
    if exclude is not None:
        taken = taken.exclude(pk=exclude.pk)
    while taken.filter(slug=slug).exists():
        slug = f'{base}-{n}'
        n += 1
    return slug


def _claim_cover(address: str, *, company) -> None:
    """
    CATALOG-VARIANT-IMAGES: a cover uploaded here must exist and be `company`'s own.

    Another company's image answers exactly like a missing one. An external or site address is not ours to
    follow and passes, as it always did. Called INSIDE the transaction that saves the model.
    """
    try:
        storefront_media.claim(address, company=company, field='image_url')
    except ValidationError as exc:
        messages = exc.message_dict.get('image_url') or ['Esa imagen ya no existe. Súbela de nuevo.']
        raise CatalogError(messages[0], field='image_url') from None


def normalize_model_name(name, *, limit=160) -> str:
    """
    DEC-CATALOG-NAME-01 (A1b, approved by the owner 2026-11-12): the key two spellings of one model name
    share — lower case, accents folded (NFKD, so ligatures and full-width letters fold too), whitespace
    collapsed. NOTHING else is removed: a SIM type, a region, a capacity or a colour written inside the name
    stays in it (reading those out needs the aliases of A2). The same rule, inlined, fills the column in
    migration 0166. A fold longer than 160 is refused by every writer (`save()`), never truncated silently.
    """
    folded = _fold(name)
    return folded if limit is None else folded[:limit]


def _folds_like(company_id, name, *, exclude_pk=None):
    """
    The models of the company whose NAME folds like `name`, by pk. Prefiltered by the stored key, by a blank
    key (a row the previous release inserted) and by the name itself (a row the previous release RENAMED,
    whose key is stale); decided by folding the name in Python, never by the stored key alone.
    """
    key = normalize_model_name(name)
    if not key:
        return []
    rows = ProductModel.objects.filter(company_id=company_id).filter(
        Q(normalized_name=key) | Q(normalized_name='') | Q(name__iexact=' '.join(str(name or '').split())))
    if exclude_pk is not None:
        rows = rows.exclude(pk=exclude_pk)
    return [row for row in rows.order_by('pk') if normalize_model_name(row.name) == key]


def model_name_holder(company_id, name, *, exclude_pk=None):
    """
    The OTHER model of the company whose name folds like `name`, or None. A collision born in the window in
    which the previous release wrote on this schema is seen the first time either row is saved — and refused,
    never resolved by renaming or merging.
    """
    return next(iter(_folds_like(company_id, name, exclude_pk=exclude_pk)), None)


def stale_key_holder(company_id, name, *, exclude_pk=None):
    """
    The model that still HOLDS the key of `name` under another name — a row the previous release renamed
    with a full UPDATE that never wrote the key. The database refuses the key; this names why.
    """
    rows = ProductModel.objects.filter(company_id=company_id, normalized_name=normalize_model_name(name))
    if exclude_pk is not None:
        rows = rows.exclude(pk=exclude_pk)
    return rows.order_by('pk').first()


def _duplicate_after_integrity_error(company, name, *, exclude=None) -> None:
    """After the database refused a model row: say WHICH rule it was, naming the model."""
    _model_name_free(company, name, exclude=exclude)
    stale = stale_key_holder(company.pk, name, exclude_pk=exclude.pk if exclude is not None else None)
    if stale is not None:
        raise CatalogError(
            f'«{stale.name}» conserva la clave de un nombre anterior («{normalize_model_name(name)}»), escrita '
            'por una versión anterior. Guarda ese modelo desde el panel o ejecuta '
            '«python manage.py check_model_names --fill», y vuelve a intentarlo.', field='name', code='duplicate')
    raise CatalogError('Ya hay un modelo con esa dirección.', field='slug', code='duplicate')


def _model_name_free(company, name, *, exclude=None) -> None:
    """No other model of `company` folds to the same name. Named before the database refuses it."""
    if len(_fold(name)) > 160:
        raise CatalogError('El nombre del modelo admite hasta 160 caracteres.', field='name')
    holder = model_name_holder(company.pk, name, exclude_pk=exclude.pk if exclude is not None else None)
    if holder is not None:
        raise CatalogError(
            f'Ya existe el modelo «{holder.name}»: los nombres se comparan sin mayúsculas, acentos ni espacios '
            'de más. Usa ese modelo o elige otro nombre.', field='name', code='duplicate')

    from .catalog_aliases import model_alias_holding

    alias = model_alias_holding(company, name)
    if alias is not None:
        raise CatalogError(
            f'«{name[:60]}» ya es un alias del modelo «{alias.model.name}» en el vocabulario de proveedores: bórralo '
            'o cámbialo antes de usarlo como nombre.', field='name', code='conflict_with_alias')


def create_model(*, company, name, category_id=None, slug='', description='', image_url='',
                 is_published_online=True, sort_order=0, actor=None, request=None) -> ProductModel:
    name = _text(name, 'el nombre del modelo', 'name', max_length=160)
    _model_name_free(company, name)
    wanted = slugify(slug or '')[:120]
    if wanted and ProductModel.objects.filter(company=company, slug=wanted).exists():
        raise CatalogError('Ya hay un modelo con esa dirección.', field='slug', code='duplicate')
    model = ProductModel(
        company=company, name=name, category=_category(company, category_id),
        slug=wanted or _free_slug(company, name),
        description=_description(description), image_url=str(image_url or '').strip(),
        is_published_online=bool(is_published_online), sort_order=int(sort_order or 0),
    )
    try:
        model.full_clean(exclude=['company', 'category'])
    except ValidationError as exc:
        field, messages = next(iter(exc.message_dict.items()))
        raise CatalogError(messages[0], field=field) from None
    try:
        with transaction.atomic():
            _claim_cover(model.image_url, company=company)
            model.save()
    except IntegrityError:
        # Somebody wrote in between the checks and the row, or a stale key: say which rule it was.
        _duplicate_after_integrity_error(company, name)
    _audit('product_model_created', company=company, actor=actor, request=request,
           target_type='product_model', target_id=model.pk, name=name)
    return model


def update_model(model, *, actor=None, request=None, **fields) -> ProductModel:
    if 'name' in fields:
        model.name = _text(fields['name'], 'el nombre del modelo', 'name', max_length=160)
    # Always, not only on a rename: a row the previous release wrote may collide the first time it is saved,
    # and that is said here, before any storefront work (`save()` holds the rule again as the last line).
    _model_name_free(model.company, model.name, exclude=model)
    if 'category_id' in fields:
        model.category = _category(model.company, fields['category_id'])
    if 'slug' in fields and fields['slug']:
        slug = slugify(fields['slug'])[:120]
        if ProductModel.objects.filter(company=model.company, slug=slug).exclude(pk=model.pk).exists():
            raise CatalogError('Ya hay un modelo con esa dirección.', field='slug', code='duplicate')
        model.slug = slug
    if 'description' in fields:
        model.description = _description(fields['description'])
    if 'image_url' in fields:
        model.image_url = str(fields['image_url'] or '').strip()
    if 'is_published_online' in fields:
        model.is_published_online = bool(fields['is_published_online'])
    if 'sort_order' in fields:
        model.sort_order = int(fields['sort_order'] or 0)
    if 'is_featured' in fields:
        # PR E: only a JSON boolean (bool('false') would feature it).
        if not isinstance(fields['is_featured'], bool):
            raise CatalogError('«Destacado» es verdadero o falso.', field='is_featured')
        model.is_featured = fields['is_featured']
    if 'is_active' in fields:
        # VARIANT-SAFE-01: only a JSON boolean, like «Destacado».
        if not isinstance(fields['is_active'], bool):
            raise CatalogError('«Activo» es verdadero o falso.', field='is_active')
        model.is_active = fields['is_active']
    try:
        model.full_clean(exclude=['company', 'category'])
    except ValidationError as exc:
        field, messages = next(iter(exc.message_dict.items()))
        raise CatalogError(messages[0], field=field) from None
    # One transaction, like every storefront slot: what is placed is checked, the row is saved, and what the
    # slot lets go of is released (deleted only if nothing else shows it). If anything fails, nothing moves.
    with transaction.atomic():
        before = (ProductModel.objects.select_for_update().filter(pk=model.pk)
                  .values_list('image_url', flat=True).first()) or ''
        if 'image_url' not in fields:
            # Not sent: the cover stays what the row holds now, never what a stale instance remembers.
            model.image_url = before
        cover_changed = 'image_url' in fields and model.image_url != before
        if cover_changed:
            _claim_cover(model.image_url, company=model.company)
        try:
            with transaction.atomic():
                model.save()
        except IntegrityError:
            _duplicate_after_integrity_error(model.company, model.name, exclude=model)
        _audit('product_model_updated', company=model.company, actor=actor, request=request,
               target_type='product_model', target_id=model.pk, changed=sorted(fields))
        if cover_changed and before:
            storefront_media.release([before], company=model.company, actor=actor, request=request)
    return model


#: Ids in one «publish / hide models» batch (COMMERCE-ADMIN-UX-01).
MAX_BULK_MODELS = 500


@transaction.atomic
def set_models_visibility(*, company, ids, publish: bool, actor=None, request=None) -> dict:
    """
    Publish or hide many models of `company` at once. All of the batch or none.

    COMMERCE-ADMIN-UX-01 — «Publicación e-commerce». The same fact the model
    form writes (`is_published_online`), for several models: a hidden model
    takes its page and its devices off the shop; its variants, stock and
    history do not move. Models have no readiness rule of their own, so
    nothing is refused for being «not ready» — what a variant needs is still
    `product_publication`'s, on each product.

    An id of another company refuses the batch exactly like an id nobody used.
    Locked in id order, written with one UPDATE naming the field.
    """
    wanted = sorted(set(ids))
    if not wanted:
        raise CatalogError('Elige al menos un modelo.', field='ids')
    if len(wanted) > MAX_BULK_MODELS:
        raise CatalogError(f'Hasta {MAX_BULK_MODELS} modelos por vez.', field='ids')
    locked = list(
        ProductModel.objects.select_for_update().filter(company=company, pk__in=wanted).order_by('pk')
    )
    if len(locked) != len(wanted):
        raise CatalogError('Algún modelo no existe.', field='ids', code='not_found')
    to_change = [m.pk for m in locked if m.is_published_online != publish]
    if to_change:
        ProductModel.objects.filter(pk__in=to_change).update(is_published_online=publish,
                                                            updated_at=timezone.now())
        _audit('product_models_visibility_changed', company=company, actor=actor, request=request,
               target_type='product_model', target_id='', visibility='publish' if publish else 'hide',
               changed=len(to_change), unchanged=len(locked) - len(to_change), changed_ids=to_change[:200])
    return {'changed': len(to_change), 'unchanged': len(locked) - len(to_change), 'total': len(locked)}


# ---------------------------------------------------------------------------
# Variants
# ---------------------------------------------------------------------------

def variant_key(option_ids) -> str:
    return '-'.join(str(pk) for pk in sorted(set(int(pk) for pk in option_ids)))


HISTORY_REASON_MIN, HISTORY_REASON_MAX = 5, 500


def variant_history(product, *, lock=False) -> dict:
    """
    CATALOG-VARIANTS-UX — what a variant already carries: units of it sold
    (order lines of any order), devices of it received (not written off),
    devices on hold or set aside, stock of it on the shelves (a product
    counted by quantity), and orders with it not yet handed over.

    `lock`: take the rows of its devices first (review P2), so a hold or a
    sale that locks them queues behind the change instead of racing it.
    """
    from django.db.models import Sum

    from .models import BranchStock, Order, OrderItem, StockMovement

    # INV-UNIT-CORRECT-01: a voided device never was one of the variant's devices.
    units = StockUnit.objects.filter(product_id=product.pk).exclude(status=StockUnit.Status.VOIDED)
    if lock:
        list(units.select_for_update().values_list('pk', flat=True))
    items = OrderItem.objects.filter(product_id=product.pk)
    return {
        'sold': items.count(),
        'units': units.exclude(status=StockUnit.Status.WRITTEN_OFF).count(),
        'reserved': units.filter(status=StockUnit.Status.RESERVED).count(),
        'stock': int(BranchStock.objects.filter(product_id=product.pk).aggregate(n=Sum('quantity'))['n'] or 0),
        'moved': StockMovement.objects.filter(product_id=product.pk).exists(),
        # Paid and not handed over yet. An unpaid checkout is not counted: if it
        # still holds a device, that device is RESERVED (review P2: abandoned
        # checkouts never expire and must not freeze a variant).
        'open_orders': items.filter(order__status=Order.Status.PAID).exclude(
            order__fulfillment_status__in=[Order.FulfillmentStatus.DELIVERED, Order.FulfillmentStatus.CANCELLED],
        ).values('order_id').distinct().count(),
        # The physical condition of its devices: what a line must agree with.
        'conditions': sorted(set(units.exclude(status=StockUnit.Status.WRITTEN_OFF).values_list('condition', flat=True))),
    }


def edit_rules(history) -> dict:
    """
    How the identity (model, options) and the line of a variant with this
    history may change: 'free', 'superior' (with a reason, audited) or 'blocked'.
    Taking it out of its model follows the identity, and is blocked while it
    holds devices or stock (they would lose their line).
    """
    if history['reserved']:
        identity = 'blocked'
    elif history['sold'] or history['units'] or history['stock'] or history['moved'] or history['open_orders']:
        identity = 'superior'
    else:
        identity = 'free'
    holds_goods = bool(history['units'] or history['reserved'] or history['stock'] > 0)
    line = 'blocked' if holds_goods else identity
    removal = 'blocked' if (holds_goods or identity == 'blocked') else identity
    return {'identity_change': identity, 'line_change': line, 'removal': removal}


def _may_override(user, company) -> bool:
    """A superior of the company: products AND the company (legacy: admin/superadmin)."""
    from .models import UserProfile
    from .permissions import get_user_role
    from .tenancy import has_capability, uses_legacy_bridge

    if company is None or user is None or not getattr(user, 'is_authenticated', False):
        return False
    if uses_legacy_bridge(user, company):
        return get_user_role(user) in (UserProfile.ROLE_ADMIN, UserProfile.ROLE_SUPERADMIN)
    return has_capability(user, company, 'products.manage') and has_capability(user, company, 'company.manage')


@transaction.atomic
def set_variant(product, *, model_id, condition_line=Product.CONDITION_NEW, option_ids=(),
                actor=None, request=None, reason='') -> Product:
    """
    Make `product` the variant of a model with these options, or (model_id None)
    a product of its own again. Its stock, price and history do not move.

    CATALOG-VARIANTS-UX: a variant that already sold or holds devices keeps its
    commercial identity unless a superior changes it with a reason (audited);
    one with a device on hold does not change at all; its line (new / used)
    never changes while it holds devices. Leaving the identity as it is, is
    always allowed.
    """
    product = Product.objects.select_for_update().get(pk=product.pk)
    company = product.company
    if product.archived_at is not None:
        # VARIANT-SAFE-01: an archived variant keeps its model, line and options; unarchive it first.
        raise CatalogError(f'«{product.name}» está archivada: desarchívala antes de cambiarla.', field='product',
                           code='variant_archived')
    history = variant_history(product, lock=True)
    rules = edit_rules(history)
    text = ''

    def authorise(kind):
        """`kind` is 'free', 'superior' or 'blocked' for this change."""
        nonlocal text
        if kind == 'free':
            return
        if kind == 'blocked':
            raise CatalogError(
                'Hay equipos de esta variante apartados, en un carrito o en pedidos sin entregar: '
                'termina o libera esas operaciones antes de cambiarla.', field='options', code='variant_reserved')
        if not _may_override(actor, company):
            raise CatalogError(
                f'Esta variante ya tiene historial ({history["sold"]} venta(s), {history["units"]} equipo(s), '
                f'{history["stock"]} en stock): cambiarla corresponde a un superior (administrador de la empresa), '
                'con un motivo.', field='options', code='history_needs_superior')
        text = ' '.join(str(reason or '').split())
        if not HISTORY_REASON_MIN <= len(text) <= HISTORY_REASON_MAX or any(ord(c) < 32 for c in text):
            raise CatalogError(
                f'Escribe el motivo del cambio ({HISTORY_REASON_MIN} a {HISTORY_REASON_MAX} caracteres): '
                'quedará en la auditoría.', field='reason', code='history_reason_required')

    if model_id in (None, ''):
        if product.product_model_id is None:
            return product
        if rules['removal'] == 'blocked' and rules['identity_change'] != 'blocked':
            raise CatalogError(
                'Esta variante tiene equipos o stock propios: sacarla del modelo cambiaría la línea de esos equipos. '
                'Cámbiala de modelo u opciones en su lugar.', field='product_model', code='variant_in_use')
        authorise(rules['identity_change'])
        ProductVariantOption.objects.filter(product=product).delete()
        product.product_model = None
        product.condition_line = Product.CONDITION_NEW
        product.variant_key = ''
        product.save(update_fields=['product_model', 'condition_line', 'variant_key', 'updated_at'])
        _audit('product_variant_cleared', company=company, actor=actor, request=request,
               target_type='product', target_id=product.pk, history=history, reason=text)
        return product

    # A1: the model's row is taken too, after the product's (never before: `set_palette` takes only the
    # model and no product, so the two cannot wait on each other), and a palette being saved at this
    # instant is seen whole, before or after, never half.
    model = ProductModel.objects.select_for_update(of=('self',)).filter(company=company, pk=model_id).first()
    if model is None:
        raise CatalogError('Modelo no encontrado.', field='product_model', code='not_found')
    if condition_line not in dict(Product.CONDITION_LINE_CHOICES):
        raise CatalogError('Línea desconocida: nuevo o seminuevo.', field='condition_line')

    try:
        wanted = sorted({int(pk) for pk in (option_ids or [])})
    except (TypeError, ValueError):
        raise CatalogError('Opciones no válidas.', field='options') from None
    options = list(VariantOption.objects.filter(company=company, pk__in=wanted).select_related('attribute'))
    if len(options) != len(wanted):
        raise CatalogError('Alguna opción no existe.', field='options', code='not_found')
    seen = set()
    for option in options:
        if option.attribute_id in seen:
            raise CatalogError(
                f'Una variante lleva una sola opción de «{option.attribute.name}».', field='options',
            )
        seen.add(option.attribute_id)

    key = variant_key(wanted)
    # The line of a product outside any model is «new» by default: placing it
    # as «used» relabels its devices too (review P1).
    changes_line = product.condition_line != condition_line
    # A line is the line of the devices: it does not change while the variant
    # holds devices, stock or a hold. Review P2: a product outside every model
    # (line «new» only by default) whose devices are all used may be placed in
    # the used line — that relabels nothing.
    conditions = set(history['conditions'])
    loose_used = (product.product_model_id is None and condition_line == Product.CONDITION_USED
                  and bool(conditions) and StockUnit.Condition.NEW not in conditions
                  and not (history['stock'] and not product.is_serialized) and not history['reserved'])
    if changes_line and rules['line_change'] == 'blocked' and not loose_used:
        raise CatalogError(
            'Esta variante tiene equipos o stock propios: su línea (nuevo o seminuevo) es la de esos equipos y no '
            'cambia. Registra los equipos en la variante que corresponde.',
            field='condition_line', code='line_change_with_units')
    # Placing a product that sits outside every model is classifying it (the
    # «Agregar variante» flow), not changing an identity. Taking a variant OUT
    # of its model is what the rules guard (above), so «out, then back in
    # differently» cannot skip them; the line still never relabels devices.
    changes_identity = product.product_model_id is not None and (
        product.product_model_id != model.pk or product.variant_key != key or changes_line)
    if changes_identity:
        authorise(rules['identity_change'])
    # A1 — the palette of the model decides what may be combined. Leaving a variant exactly as it is
    # (same model, same options; only its line, price or publication move) is always allowed: what
    # exists is never invalidated backwards, it is reported (`model_palette.variant_status`).
    if not (product.product_model_id == model.pk and product.variant_key == key):
        from .model_palette import check_variant

        check_variant(model, options)
    clash = (
        Product.objects.filter(product_model=model, condition_line=condition_line, variant_key=key)
        .exclude(pk=product.pk).first()
    )
    if clash is not None and clash.archived_at is not None:
        # VARIANT-SAFE-01: the archived row keeps its combination; it is never created a second time.
        raise CatalogError(
            f'«{clash.name}» ya es esa variante de «{model.name}» y está archivada: desarchívala si vuelve a usarse.',
            field='options', code='duplicate',
        )
    if clash is not None:
        raise CatalogError(
            f'«{clash.name}» ya es esa variante de «{model.name}».', field='options', code='duplicate',
        )

    ProductVariantOption.objects.filter(product=product).delete()
    for option in options:
        ProductVariantOption.objects.create(
            company=company, product=product, attribute=option.attribute, option=option,
        )
    product.product_model = model
    product.condition_line = condition_line
    product.variant_key = key
    try:
        with transaction.atomic():
            product.save(update_fields=['product_model', 'condition_line', 'variant_key', 'updated_at'])
    except IntegrityError:
        raise CatalogError('Otra persona acaba de crear esa misma variante.', field='options', code='duplicate') from None
    extra = {'history': history, 'reason': text} if changes_identity and rules['identity_change'] != 'free' else {}
    _audit('product_variant_set', company=company, actor=actor, request=request,
           target_type='product', target_id=product.pk, model_id=model.pk,
           condition_line=condition_line, option_ids=wanted, **extra)
    return product


# ---------------------------------------------------------------------------
# A1b-2 — a variant created from the palette: a pending draft, and its one transition
# (DEC-CATALOG-VARIANT-DRAFT-01, owner's resolutions of 2026-11-12)
# ---------------------------------------------------------------------------

#: The word the generated name of a used-line variant ends with. Neutral Spanish, not the pilot's
#: label (`line_labels` is what the SHOP shows; a product name is catalogue text the operator may edit).
#: `catalog_proposal` has always used it; `variant_name` is now the one place.
USED_SUFFIX = 'Seminuevo'

#: Palette statuses that stop a draft from being enabled (`model_palette`). Named here so the
#: `not_ready` answer lists them beside `product_publication`'s codes.
PALETTE_NOT_READY = ('fuera_de_paleta', 'incompleta')

#: The codes only `enable_variant_for_sale` reports, with what the panel says (the publication rule's
#: own codes are labelled in `product_publication.PROBLEM_LABELS`; `NotReady.body()` joins both).
DRAFT_PROBLEM_LABELS = {
    'no_model': 'La variante ya no pertenece a un modelo.',
    'no_unit': 'Es seminuevo: recibe al menos un equipo con su precio de venta antes de habilitarla.',
    'fuera_de_paleta': 'Lleva una opción que la paleta del modelo ya no admite.',
    'incompleta': 'Le falta una opción de la paleta del modelo.',
}


def variant_name(model, options, *, condition_line=Product.CONDITION_NEW) -> str:
    """
    The generated name of a variant: the model's name, then the option labels in ROLE order
    (capacity → colour → SIM → region → other; `model_palette.attribute_sort_key`), then
    `USED_SUFFIX` for the used line. `options` are `VariantOption` rows with their attribute
    loaded, or `(attribute, label)` pairs (the catalogue proposal names options it has not
    created yet). Cut to the column's 255 characters without a half word being an error.
    """
    from .model_palette import attribute_sort_key

    labelled = []
    for item in options:
        attribute, label = (item.attribute, item.label) if hasattr(item, 'attribute') else item
        labelled.append((attribute_sort_key(attribute), label))
    labelled.sort(key=lambda pair: pair[0])
    parts = [model.name if hasattr(model, 'name') else str(model), *(label for _key, label in labelled)]
    if condition_line == Product.CONDITION_USED:
        parts.append(USED_SUFFIX)
    return ' '.join(' '.join(str(part).split()) for part in parts)[:255]


@transaction.atomic
def create_variant_product(company, model, *, condition_line, option_ids, name=None, actor, request) -> Product:
    """
    Create the `Product` that IS one combination of the palette of `model` (A1b-2, owner's P1–P5).

    What is born: hidden (`is_published_online=False`), `price=0.00` (the `no_price` placeholder every
    other draft path writes), NOT serialized (S5: counting by unit is a stock decision, taken by the
    first receipt with `inventory.adjust`), `sale_mode='stock'`, the model's category, a generated code
    (`product_codes.ensure_code`), `draft_origin='palette'` and no transition. It creates NO stock, NO
    unit, NO movement, and never publishes anything — not this product, not the model.

    The combination goes through `set_variant`, so the palette rules (`option_not_in_palette`,
    `variant_incomplete`), the tenancy (`not_found` for a foreign option) and the `duplicate` guard are
    the same as for every other variant, and the lock order is product → units → model
    (DEC-CATALOG-PALETTE-LOCK-01). Nothing is written when any of them refuses.

    `name`: the operator's, or generated by `variant_name` (the audit says which).
    """
    from . import product_codes

    if condition_line not in dict(Product.CONDITION_LINE_CHOICES):
        raise CatalogError('Línea desconocida: nuevo o seminuevo.', field='condition_line')
    from .model_palette import clean_option_ids

    wanted = clean_option_ids(option_ids)      # the palette's own rule: ints > 0, no bools, no floats
    if not wanted:
        raise CatalogError('Elige las opciones de la variante.', field='option_ids')
    # Tenancy first: a model of another company is never touched (the view already answered 404; the
    # service holds it too), and so is an option of another company.
    if model.company_id != company.pk:
        raise CatalogError('Modelo no encontrado.', field='product_model', code='not_found')
    options = list(VariantOption.objects.filter(company=company, pk__in=wanted).select_related('attribute'))
    if len(options) != len(wanted):
        raise CatalogError('Alguna opción no existe.', field='option_ids', code='not_found')
    generated = variant_name(model, options, condition_line=condition_line)
    given = _text(name, 'el nombre', 'name', max_length=255, required=False)
    final_name = given or generated

    from .import_services import unique_slug

    product = Product(
        company=company, name=final_name, slug=unique_slug(company, final_name, taken=set()),
        price=Decimal('0.00'), category=model.category if model.category_id else None, is_active=True,
        is_published_online=False, is_serialized=False, requires_imei=False, sale_mode=Product.SALE_MODE_STOCK,
        draft_origin=Product.DRAFT_ORIGIN_PALETTE,
    )
    product.full_clean(exclude=['slug'])
    product.save()
    try:
        product = set_variant(product, model_id=model.pk, condition_line=condition_line, option_ids=wanted,
                              actor=actor, request=request)
    except CatalogError as exc:
        # `set_variant` names its input «options»; this endpoint's is `option_ids`.
        if exc.field == 'options':
            raise CatalogError(str(exc), field='option_ids', code=exc.code) from None
        raise
    product_codes.ensure_code(product, actor=actor, request=request)
    _audit('product_variant_created_from_palette', company=company, actor=actor, request=request,
           target_type='product', target_id=product.pk, model_id=model.pk, model_name=model.name,
           condition_line=condition_line, option_ids=wanted, product_name=product.name,
           name_generated=not given, price='0.00', is_published_online=False, is_serialized=False,
           draft_origin=Product.DRAFT_ORIGIN_PALETTE)
    return product


def _draft_problems(product, model) -> list[str]:
    """
    What keeps a pending draft from being enabled: `product_publication`'s codes (without
    `draft_pending`, which is the state this step ends) plus the palette's. For the USED line the
    product has no price of its own, so `no_price` means «no sellable device with a real unit price»
    (`stock_unit_services.has_usable_price`): the exception is never an authorisation to sell at zero.
    """
    from .model_palette import variant_status
    from .product_publication import publication_problems
    from .stock_unit_services import has_usable_price

    problems = publication_problems(product, draft_pending=False)
    if product.condition_line == Product.CONDITION_USED:
        problems = [code for code in problems if code != 'no_price']
        prices = list(StockUnit.objects.filter(product=product, status=StockUnit.Status.AVAILABLE)
                      .values_list('sale_price', flat=True))
        if not prices:
            # Nothing sellable yet: receiving a device (serialized, `inventory.adjust`) is the step before.
            problems.insert(0, 'no_unit')
        elif not all(has_usable_price(price) for price in prices):
            problems.insert(0, 'no_price')
    status = variant_status(model, [o.pk for o in variant_options_of(product)])
    if status in PALETTE_NOT_READY:
        problems.append(status)
    return problems


class NotReady(CatalogError):
    """`enable_variant_for_sale` refused: `problems` lists every code (publication rule + palette)."""

    def __init__(self, problems):
        super().__init__('La variante no está lista para la venta.', field='product', code='not_ready')
        self.problems = list(problems)

    def body(self) -> dict:
        from .product_publication import PROBLEM_LABELS

        labels = {**PROBLEM_LABELS, **DRAFT_PROBLEM_LABELS}
        return {**super().body(), 'problems': self.problems,
                'labels': {code: labels[code] for code in self.problems if code in labels}}


@transaction.atomic
def enable_variant_for_sale(product, *, publish=False, actor, request) -> Product:
    """
    The ONE commercial transition of a variant created from the palette (A1b-2, T2).

    Locks the product (`of=('self',)`), then its model (product → units → model, as `set_variant`),
    and asks everything again under the lock: the row must still be pending (`already_enabled` 409 if
    somebody got there first; `not_a_draft` for a historical row, which needs no transition), the
    model present, the configuration `variante_existente` in the palette when the model has one, and
    `product_publication`'s rule met — active, price > 0 (used line: a real price on every sellable
    device), category, picture if the company asks — or `NotReady` lists the codes and nothing is
    written.

    Writes `sale_enabled_at=now`, `sale_enabled_by=actor`; `draft_origin` stays (provenance); the
    sale mode and the price do not change. `publish=True` publishes THIS product in the same
    transaction through the product's own write (`write_product_fields`): never another variant, never
    the model. Audited as `product_variant_enabled` with the company.
    """
    product = Product.objects.select_for_update(of=('self',)).select_related('category').get(pk=product.pk)
    company = product.company
    if not product.draft_origin:
        raise CatalogError('Este producto no es un borrador creado desde la paleta: no necesita habilitación.',
                           field='product', code='not_a_draft')
    if product.sale_enabled_at is not None:
        raise CatalogError('Esta variante ya está habilitada para la venta.', field='product', code='already_enabled')
    if product.archived_at is not None:
        raise CatalogError(f'«{product.name}» está archivada: desarchívala antes de habilitarla.', field='product',
                           code='variant_archived')
    if product.product_model_id is None:
        raise NotReady(['no_model'])
    model = ProductModel.objects.select_for_update(of=('self',)).filter(company=company, pk=product.product_model_id).first()
    if model is None:
        raise NotReady(['no_model'])
    # The publication rule is asked whether or not `publish` was: enabled means «may be published».
    problems = _draft_problems(product, model)
    if problems:
        raise NotReady(problems)

    from .model_palette import palette_of
    from .product_publication import requires_image

    now = timezone.now()
    product.sale_enabled_at = now
    product.sale_enabled_by = actor if getattr(actor, 'pk', None) else None
    product.save(update_fields=['sale_enabled_at', 'sale_enabled_by', 'updated_at'])
    checks = [name for name, applied in (
        ('palette', bool(palette_of(model))), ('price', True), ('category', True),
        ('image', requires_image(company)), ('active', True)) if applied]
    _audit('product_variant_enabled', company=company, actor=actor, request=request,
           target_type='product', target_id=product.pk, model_id=model.pk, condition_line=product.condition_line,
           option_ids=[o.pk for o in variant_options_of(product)], price=str(product.price), published=bool(publish),
           was_published=product.is_published_online, is_serialized=product.is_serialized,
           draft_origin=product.draft_origin, checks=checks)
    if publish and not product.is_published_online:
        from .admin_views import write_product_fields

        product, changed = write_product_fields(company, product, {'is_published_online': True})
        if changed:
            AdminAuditLog.log(
                actor=actor, action='product_updated', target_type='product', target_id=product.pk,
                request=request, company=company,
                metadata={'product_name': product.name, 'product_id': product.pk, 'changed_fields': changed,
                          'via': 'enable_sale'},
            )
    return product


#: What a spreadsheet may write in «Línea». Folded: lower case, no accents.
LINE_WORDS = {
    'nuevo': Product.CONDITION_NEW, 'nuevos': Product.CONDITION_NEW, 'new': Product.CONDITION_NEW,
    'seminuevo': Product.CONDITION_USED, 'seminuevos': Product.CONDITION_USED, 'usado': Product.CONDITION_USED,
    'usados': Product.CONDITION_USED, 'used': Product.CONDITION_USED,
}


def _fold(text) -> str:
    import unicodedata

    text = unicodedata.normalize('NFKD', str(text or ''))
    return ' '.join(''.join(ch for ch in text if not unicodedata.combining(ch)).lower().split())


def clean_line(text, *, default=Product.CONDITION_NEW) -> str:
    folded = _fold(text)
    if not folded:
        return default
    if folded not in LINE_WORDS:
        raise CatalogError('«Línea» admite «Nuevo» o «Seminuevo».', field='line')
    return LINE_WORDS[folded]


def find_model(company, name, *, cache=None):
    """
    The model of `company` called `name` — by the normalized key (DEC-CATALOG-NAME-01) or, for a row the
    previous release wrote without one, by its name case-insensitively. None when there is none.
    """
    name = ' '.join(str(name or '').split())
    if not name:
        return None
    key = normalize_model_name(name)
    slot = ('model', company.pk, key)
    if cache is not None and slot in cache:
        return cache[slot]
    # Decided by folding the NAME (`_folds_like`): a stale key left by the previous release is never followed.
    model = next(iter(_folds_like(company.pk, name)), None)
    if cache is not None:
        cache[slot] = model
        if model is not None:
            cache[('row', model.pk)] = model
    return model


def resolve_variant_spec(company, *, model_name, line_text='', labels=(), cache=None, strict=False,
                         notes=None) -> dict:
    """
    A spreadsheet's «Modelo» / «Capacidad» / «Color» / «SIM» / «Región» / «Línea», as THIS
    company's model and options. Raises `CatalogError` naming what is unknown.
    Attribute-agnostic: each label is looked up among the company's options;
    a label two attributes share is ambiguous and refused.

    `cache` (A1b, LOW 2): a dict one PASS over a file hands to every row, so the
    model and its palette are read once per model, not once per row. Never
    shared across requests: a palette saved meanwhile must be seen by the next pass.

    `strict` (A1b): a WRITER's reading. A description no variant is, yet, is
    held to the palette whole — the history bypass below is for readers that
    offer existing variants, never for a row that would create one.

    A2 (DEC-CATALOG-ALIAS-01, owner's P8): what the exact lookup does not find is read again through the
    company's vocabulary (`configuration_resolver`): a model through its aliases and word aliases, an option
    by its label without accents or case, a capacity written without its space («256gb»), or a company
    alias. What resolved before resolves the same; nothing is created. `notes` (a list) receives one
    sentence per such reading, for the preview to say it. A name with options written inside it
    (`name_with_options`) or one that names two models (`ambiguous`) is refused, never a new model.
    """
    name = ' '.join(str(model_name or '').split())
    model = find_model(company, name, cache=cache)
    if model is None:
        model = _model_through_vocabulary(company, name, cache=cache, notes=notes)
    line = clean_line(line_text)
    option_ids, options, seen = [], [], set()
    for label in labels:
        label = ' '.join(str(label or '').split())
        if not label:
            continue
        found = list(VariantOption.objects.filter(company=company, label__iexact=label).select_related('attribute'))
        if not found:
            found = _options_through_vocabulary(company, label, cache=cache, notes=notes)
        if not found:
            raise CatalogError(f'No existe la opción «{label[:40]}». Créala en Catálogo › Modelos.', field='options')
        if len(found) > 1:
            raise CatalogError(f'«{label[:40]}» es opción de varios atributos: no se puede decidir.', field='options')
        if found[0].attribute_id in seen:
            raise CatalogError(f'Dos valores de «{found[0].attribute.name}» en la misma fila.', field='options')
        seen.add(found[0].attribute_id)
        option_ids.append(found[0].pk)
        options.append(found[0])
    spec = {'model_id': model.pk, 'model_name': model.name, 'condition_line': line,
            'option_ids': sorted(option_ids), 'variant_key': variant_key(option_ids)}
    # A1 — a row that names a variant which does not exist yet must fit the model's palette, so the
    # preview refuses it instead of the apply. One that already exists is found whatever the palette
    # says today: a supplier offer or a device load on it keeps working (history is kept).
    if find_variant(company, spec) is None and (strict or not _held_by_a_variant(company, spec)):
        from .model_palette import check_variant

        # Only what the palette does not admit. A PARTIAL description (capacity alone) is still a spec that
        # readers match against the variants; the writer (`set_variant`) is the one that demands every role.
        check_variant(model, options, complete=False, palette=palette_cached(model, cache))
    return spec


#: How a reading through the vocabulary is said in a preview (A2).
_VIA_TEXT = {
    'alias': 'alias de la empresa', 'model_word': 'palabra de modelo de la empresa', 'spacing': 'mismas palabras',
    'label': 'sin tildes ni mayúsculas', 'capacity': 'capacidad', 'number': 'capacidad',
}


def vocabulary_cached(company, cache=None):
    """`configuration_resolver.Vocabulary(company)`, built once per pass when one hands its `cache`."""
    from .configuration_resolver import Vocabulary

    if cache is None:
        return Vocabulary(company)
    key = ('vocabulary', company.pk)
    if key not in cache:
        cache[key] = Vocabulary(company)
    return cache[key]


def _model_through_vocabulary(company, name, *, cache=None, notes=None):
    from .configuration_resolver import resolve_model

    vocabulary = vocabulary_cached(company, cache)
    found = resolve_model(vocabulary, name)
    if found['status'] in ('exact', 'alias', 'model_word'):
        model = vocabulary.models[found['model_id']]
        if notes is not None:
            notes.append(f'«{name[:60]}» se leyó como «{model.name}» ({_VIA_TEXT.get(found["via"], found["via"])}).')
        return model
    if found['status'] == 'name_with_options':
        options = ', '.join(f'«{item["label"]}»' for item in found['extracted'])
        raise CatalogError(
            f'«{name[:60]}» parece «{found["model_name"]}» con {options} dentro del nombre: escribe el modelo solo '
            'y esas opciones en sus columnas.', field='model', code='name_with_options')
    if found['status'] == 'ambiguous':
        names = ' o '.join(f'«{c["name"]}»' for c in found['candidates'])
        raise CatalogError(f'«{name[:60]}» puede ser {names}: escribe el nombre exacto del modelo.', field='model',
                           code='ambiguous')
    raise CatalogError(f'No existe el modelo «{name[:60]}». Créalo en Catálogo › Modelos.', field='model')


def _options_through_vocabulary(company, label, *, cache=None, notes=None) -> list:
    """The option(s) `label` is through the vocabulary (folded label, capacity, alias); [] when none."""
    vocabulary = vocabulary_cached(company, cache)
    hits = vocabulary.lookup_any(label)
    distinct = {}
    for option, via, alias in hits:
        distinct.setdefault(option.pk, (option, via, alias))
    found = []
    for option, via, alias in distinct.values():
        option.attribute = vocabulary.attributes[option.attribute_id]
        found.append(option)
    if len(found) == 1 and notes is not None:
        option, via, _alias = next(iter(distinct.values()))
        notes.append(f'«{label[:40]}» se leyó como «{option.label}» ({_VIA_TEXT.get(via, via)}).')
    return found


def palette_cached(model, cache=None) -> dict:
    """`model_palette.palette_of(model)`, read once per model when a pass hands its `cache`."""
    from .model_palette import palette_of

    if cache is None:
        return palette_of(model)
    key = ('palette', model.pk)
    if key not in cache:
        cache[key] = palette_of(model)
    return cache[key]


def _held_by_a_variant(company, spec) -> bool:
    """
    A1b (LOW 1 of the A1 audit): some variant of the model, in that line, carries EVERY option the spec
    names. Such a description is history — the readers (`match_device`, `_by_model`) offer those variants —
    even when today's palette no longer admits one of its options. A combination no variant holds is new,
    and the palette decides it.
    """
    from django.db.models import Count

    if not spec['option_ids']:
        return False
    return (ProductVariantOption.objects.filter(
        company=company, product__product_model_id=spec['model_id'],
        product__condition_line=spec['condition_line'], option_id__in=spec['option_ids'])
        .values('product_id').annotate(n=Count('option_id', distinct=True))
        .filter(n=len(spec['option_ids'])).exists())


def find_variant(company, spec):
    """The product that IS that variant, or None."""
    return Product.objects.filter(
        company=company, product_model_id=spec['model_id'], condition_line=spec['condition_line'],
        variant_key=spec['variant_key'],
    ).first()


def variant_options_of(product) -> list[VariantOption]:
    return [
        link.option for link in
        ProductVariantOption.objects.filter(product=product, company_id=product.company_id)
        .select_related('option', 'attribute').order_by('attribute__sort_order', 'attribute__name', 'attribute_id')
    ]


# ---------------------------------------------------------------------------
# Cosmetic grades
# ---------------------------------------------------------------------------

def user_may_manage_products(user, company) -> bool:
    """
    DC2 review — whether `user` may set what things sell for in `company`
    (`products.manage`; the legacy bridge like the catalogue screens). The
    service-side twin of the catalogue views' check, for code that holds a
    user and a company (the device load).
    """
    from .models import UserProfile
    from .permissions import get_user_role
    from .tenancy import has_capability, uses_legacy_bridge

    if company is None or user is None or not getattr(user, 'is_authenticated', False):
        return False
    if uses_legacy_bridge(user, company):
        return get_user_role(user) in (UserProfile.ROLE_ADMIN, UserProfile.ROLE_SUPERADMIN)
    return has_capability(user, company, 'products.manage')


def seed_cosmetic_grades(company) -> int:
    """
    The default scale, for ONE company that asks for it. Idempotent: a code
    the company already has is left exactly as the company wrote it.
    """
    created = 0
    for position, (code, label, description) in enumerate(DEFAULT_COSMETIC_GRADES):
        _, made = CosmeticGrade.objects.get_or_create(
            company=company, code=code,
            defaults={'label': label, 'description': description, 'sort_order': position},
        )
        created += int(made)
    return created


# ---------------------------------------------------------------------------
# What the public reads
# ---------------------------------------------------------------------------

def mask_imei(value) -> str:
    """The last four digits and nothing else. Never the whole IMEI, never the serial."""
    digits = ''.join(ch for ch in str(value or '') if ch.isdigit())
    return f'•••• {digits[-4:]}' if len(digits) >= 4 else ''


def line_labels(company) -> dict:
    """How this shop names its two lines. Empty settings fall back to the platform's words."""
    page = getattr(company, 'storefront_page', None) if company is not None else None
    try:
        new = (page.new_line_label if page else '') or DEFAULT_NEW_LABEL
        used = (page.used_line_label if page else '') or DEFAULT_USED_LABEL
    except Exception:  # noqa: BLE001 - a missing row is «use the defaults»
        new, used = DEFAULT_NEW_LABEL, DEFAULT_USED_LABEL
    return {Product.CONDITION_NEW: new, Product.CONDITION_USED: used}


def _unit_price(unit, product) -> Decimal | None:
    """
    What one unit sells for on the web: its own price when used, the variant's when new.

    INV-RECEIVE-PRICE-03 — a used device without a USABLE price (none, or zero from a row written before the rule)
    has no web price: None, never the product's price. The till applies the same test (`pos_services.unit_prices`).
    """
    if product.condition_line == Product.CONDITION_USED:
        from .stock_unit_services import has_usable_price
        return unit.sale_price if has_usable_price(unit.sale_price) else None
    return product.price


def offered_units(products, branch, *, now=None):
    """
    The units the shop offers RIGHT NOW from `branch`: available, of a product
    sold online. A used unit only when it is ready for the web
    (USED-READY-01, `used_unit_services.web_ready_q`): its own price greater
    than zero (INV-RECEIVE-PRICE-03), its inspection approved, itself
    published and at least one real photo of it.
    """
    from .used_unit_services import offerable_q

    if branch is None:
        return StockUnit.objects.none()
    return StockUnit.objects.filter(
        company_id=branch.company_id, branch=branch, product__in=products,
        status=StockUnit.Status.AVAILABLE,
    ).filter(offerable_q())


def models_with_new_for_sale(products, branch) -> set[int]:
    """
    The models (ids) of which some NEW variant in `products` can be bought now:
    a device on `branch`'s shelf or, DC3, an offer that sells it on order.
    The same two reasons `public_model` gives a variant `available: true` — a
    published variant with neither is sold out, shown but not sold.
    """
    from .supply_services import on_order_payload

    for_sale: set[int] = set()
    if branch is not None:
        for_sale = set(
            BranchStock.objects.filter(branch=branch, product__in=products, quantity__gt=0)
            .values_list('product__product_model', flat=True)
        )
    # Only products the shop may sell on order can be saved by an offer.
    for product in (products.exclude(product_model__in=for_sale)
                    .filter(sale_mode=Product.SALE_MODE_STOCK_OR_ORDER)
                    .select_related('company')):
        if product.product_model_id not in for_sale and on_order_payload(product):
            for_sale.add(product.product_model_id)
    return for_sale


def new_for_sale_prices(products, branch) -> dict[int, Decimal]:
    """
    Per model, the lowest price at which a NEW variant in `products` can be bought now: a device on `branch`'s shelf
    at the product's price, or (DC3) a valid on-order offer at the offer's price. The same two reasons as
    `models_with_new_for_sale`. A model with nothing to buy is absent; its card falls back to the last configured price
    (review of P2-F2: «Desde S/» named a sold-out variant's price while a dearer one was the only one on sale).
    """
    from .supply_services import on_order_payload

    prices: dict[int, Decimal] = {}

    def keep(model_id, price):
        if price is not None and (model_id not in prices or price < prices[model_id]):
            prices[model_id] = price

    shelf: set[int] = set()
    if branch is not None:
        for model_id, product_id, price in (
            BranchStock.objects.filter(branch=branch, product__in=products, quantity__gt=0)
            .values_list('product__product_model', 'product_id', 'product__price')
        ):
            shelf.add(product_id)
            keep(model_id, price)
    for product in (products.exclude(pk__in=shelf)
                    .filter(sale_mode=Product.SALE_MODE_STOCK_OR_ORDER)
                    .select_related('company')):
        payload = on_order_payload(product)
        if payload:
            keep(product.product_model_id, Decimal(payload['price']))
    return prices


def company_used_warranty_days(company_id) -> int | None:
    """USED-READY-01 — the company's default warranty days for a used device (None: only its policy text)."""
    from .models import CompanySettings

    return CompanySettings.objects.filter(company_id=company_id).values_list('used_warranty_days', flat=True).first()


def public_unit(unit, product, option_labels: dict, *, default_warranty_days=None) -> dict:
    """ONE used device as the shop shows it. An allowlist: nothing else leaves."""
    from . import unit_media

    # UNIT-PHOTO-02 — only the PUBLIC photos of THIS device (attested, own and verified), the primary first; each
    # says which side it shows («Frontal», «Defecto: …»). Never a generic picture.
    photos = [unit_media.public_payload(photo) for photo in unit_media.public_photos(unit)]
    grade = unit.cosmetic_grade
    return {
        'id': str(unit.public_id),
        'product_id': product.pk,
        'options': option_labels,
        'battery_health': unit.battery_health,
        'cosmetic_grade': (
            {'code': grade.code, 'label': grade.label, 'description': grade.description} if grade else None
        ),
        'price': str(_unit_price(unit, product)),
        'photos': photos,
        'notes': unit.public_notes,
        'imei_hint': mask_imei(unit.imei),
        # USED-READY-01 — what the shop states about THIS device. `warranty_days`: its own, else the company's
        # default (`warranty_source` says which); null: only the company's warranty policy text applies (the page
        # shows it); 0: sold without warranty.
        'warranty_days': unit.warranty_days if unit.warranty_days is not None else default_warranty_days,
        'warranty_source': ('unit' if unit.warranty_days is not None
                            else 'company' if default_warranty_days is not None else 'policy'),
        'functional_state': unit.functional_state or None,
        'functional_state_label': unit.get_functional_state_display() if unit.functional_state else '',
        'defects': unit.defects,
        'accessories': unit.accessories,
    }


def _public_options(options: dict) -> list[dict]:
    """
    The options of one attribute of a public model page. As always — by the company's `sort_order`, then label — unless
    the MODEL placed some of them (PR E, `model_palette.set_option_order`): then the placed ones go first in that
    order, the rest after them as before, and `sort_order` says each one's place on this page (0, 1, 2…), which is
    what the shop sorts by. An attribute nobody placed answers exactly what it did before PR E.
    """
    from .model_palette import placed_rank

    rows = list(options.values())
    if any(row['_rank'] for row in rows):
        rows.sort(key=lambda o: (placed_rank(o['_rank']), o['sort_order'], o['label']))
        return [{**{k: v for k, v in row.items() if k != '_rank'}, 'sort_order': index}
                for index, row in enumerate(rows)]
    rows.sort(key=lambda o: (o['sort_order'], o['label']))
    return [{k: v for k, v in row.items() if k != '_rank'} for row in rows]


#: The neutral swatch the reference import (and a supplier list) gives a NEW colour, because a colour option must
#: carry one (`_clean_swatch`). It is the importer's placeholder, not a colour of the product.
NEUTRAL_SWATCH = '#9CA3AF'


def swatch_is_set(option) -> bool:
    """
    G (owner's visual order 4): whether a colour's swatch is the COMPANY's. Derived, without a column: a colour holding
    the neutral placeholder (what the reference import and a supplier list give a new colour) is «Muestra sin
    configurar», WHATEVER created it (decision of the lead, review of #377). Any other swatch is configured. Limit,
    documented in ADR §4.6 D-PRG-6: a company that deliberately picks exactly that grey reads as unset.
    """
    value = (option.swatch_hex or '').upper()
    return bool(value) and value != NEUTRAL_SWATCH


def shown_swatch(option) -> str:
    """The swatch a chip may draw: the company's, or '' (a name-only chip, never the neutral grey dot)."""
    return option.swatch_hex if swatch_is_set(option) else ''


def resolved_swatch(option, model_swatch_hex='', model_swatch_is_approximate=False) -> tuple[str, bool]:
    """
    CATALOG FASE 1.2 · Y — the swatch a colour is DRAWN with on one model, and whether it is an approximation: the
    model's own (`ModelOption.swatch_hex`) when it has one, else the company's (`shown_swatch`), else '' (a name-only
    chip). A company colour is shared by every model that has it (one «Azul» for several generations), so a model
    that needs its own shade says so on its palette row, without touching the others. The flag is for the panel only.
    """
    if model_swatch_hex:
        return model_swatch_hex.upper(), bool(model_swatch_is_approximate)
    shown = shown_swatch(option)
    return shown, bool(shown and option.swatch_is_approximate)


#: The keys of one picture of a product's public gallery (`product_media.public_payload`): what `images` carries.
PUBLIC_IMAGE_KEYS = ('url', 'alt_text', 'is_primary', 'width', 'height')


def variant_galleries(products) -> dict[int, dict]:
    """
    IMG-GATE-01 — the PUBLIC gallery of each DEVICE VARIANT (a product with a model), for every surface of the shop:
    the model page (`public_model`), the product API, the cart and the checkout that reads it, the buyer's orders
    and the mobile API. Product id → `{'gallery', 'image_url', 'image_pending', 'image_is_demo'}`. A product without
    a model has no entry: its pictures are unchanged (it has no finish to fall back on; IMG-HEALTH reports them).

    Owner, 2026-11-23: «Las nuevas fotografías requieren procedencia y autorización comprobables antes de publicarse.»
    A picture is public only when its file is a managed upload of the product's company whose licence was verified
    (`finish_media.shop_states`) — a variant's own pictures too, since IMG-GATE-01; an external (hotlinked) address
    has no verifiable licence and is never shown. In order:

      1. its own gallery's verified pictures (gallery order; the primary is the one marked, else the first);
      2. else its own single address (`Product.image_url`, from before galleries) when it is a verified file;
      3. else the verified pictures of its colour's finish (model + its option of the company's colour dimension,
         D-PRC-8), described by the model and the colour when they have no text of their own;
      4. else the model's cover, only on a COLOURLESS model (no variant of it has a colour option at all), and only
         when it too is a verified file of the company (review of #408);
      5. else nothing: `image_pending`, which the shop draws as the neutral «Imagen pendiente».

    `source` says which of them ('own', 'finish', 'cover'; '' when pending).

    Never another colour's photo, never the cover of a model with colours. `is_demo` (a verified demo resource) is
    shown and said («Imagen referencial»). Entries have the shapes `public_model` always gave: own pictures
    `product_media.public_payload` + `is_demo`; finish pictures `finish_media.public_payload`. A bounded number of
    queries whatever the number of products (review of #377).
    """
    from .finish_media import colour_attribute_ids, public_payload as finish_public_payload, shop_states
    from .models import ModelFinishImage, ProductImage

    products = [product for product in products if product is not None and product.product_model_id]
    if not products:
        return {}
    ids = [product.pk for product in products]
    own: dict[int, list[dict]] = {}
    for row in (ProductImage.objects.filter(product_id__in=ids).order_by('product_id', 'sort_order', 'id')
                .values('product_id', 'image_url', 'alt_text', 'is_primary', 'width', 'height')):
        own.setdefault(row['product_id'], []).append(row)
    dimensions = {company_id: colour_attribute_ids(company_id) for company_id in {p.company_id for p in products}}
    colour_of: dict[int, tuple[int, str]] = {}
    attribute_ids = set().union(*dimensions.values())
    if attribute_ids:
        for product_id, option_id, label in (ProductVariantOption.objects.filter(
                product_id__in=ids, attribute_id__in=attribute_ids)
                .order_by('product_id', 'pk').values_list('product_id', 'option_id', 'option__label')):
            colour_of.setdefault(product_id, (option_id, label))
    models = {pk: (name, cover) for pk, name, cover in ProductModel.objects.filter(
        pk__in={p.product_model_id for p in products}).values_list('pk', 'name', 'image_url')}
    #: (model id, colour option id) -> its pictures, in the gallery's order.
    finishes: dict[tuple[int, int], list] = {}
    coloured = [p for p in products if p.pk in colour_of]
    if coloured:
        for row in (ModelFinishImage.objects.filter(
                model_id__in={p.product_model_id for p in coloured},
                color_option_id__in={colour_of[p.pk][0] for p in coloured})
                .order_by('model_id', 'color_option_id', 'sort_order', 'pk')):
            finishes.setdefault((row.model_id, row.color_option_id), []).append(row)
    # Licence and demo flag of every candidate file — own, own address and finish — in one query.
    states = shop_states(
        [row['image_url'] for rows in own.values() for row in rows] + [p.image_url for p in products]
        + [row.image_url for rows in finishes.values() for row in rows]
        + [cover for _name, cover in models.values() if cover],
        {p.company_id for p in products})

    def public(product, address):
        """(verified, is a demo) of a managed file of the product's company; None when the shop may not show it."""
        found = states.get((product.company_id, storefront_media.managed_public_id(address)))
        return found if found is not None and found[0] else None

    out: dict[int, tuple[str, list]] = {}
    plain = []
    for product in products:
        gallery, source = [], 'own'
        for row in own.get(product.pk, ()):
            found = public(product, row['image_url'])
            if found:
                gallery.append({'url': row['image_url'], 'alt_text': row['alt_text'], 'is_primary': row['is_primary'],
                                'width': row['width'], 'height': row['height'], 'is_demo': found[1]})
        if not gallery and product.image_url:
            found = public(product, product.image_url)
            if found:
                gallery = [{'url': product.image_url, 'alt_text': '', 'is_primary': True, 'width': 0, 'height': 0,
                            'is_demo': found[1]}]
        if not gallery and product.pk in colour_of:
            source = 'finish'
            option_id, label = colour_of[product.pk]
            model_name = models.get(product.product_model_id, ('', ''))[0]
            for row in finishes.get((product.product_model_id, option_id), ()):
                found = public(product, row.image_url)
                if found:
                    gallery.append(finish_public_payload(row, is_demo=found[1], alt=f'{model_name} {label}'))
        if not gallery and product.pk not in colour_of:
            plain.append(product)
        out[product.pk] = (source if gallery else '', gallery)
    if plain:
        with_colour = set(ProductVariantOption.objects.filter(
            product__product_model_id__in={p.product_model_id for p in plain}).filter(
            Q(attribute__role=VariantAttribute.ROLE_COLOR) | Q(attribute__kind=VariantAttribute.KIND_COLOR))
            .values_list('product__product_model_id', flat=True).distinct())
        for product in plain:
            cover = models.get(product.product_model_id, ('', ''))[1]
            # D-PRC-8: a COLOURLESS model: its cover is not another colour's photo. Review of #408: and it passes the
            # same licence gate (a managed file of the company, verified; its real demo flag).
            found = public(product, cover) if cover and product.product_model_id not in with_colour else None
            if found:
                out[product.pk] = ('cover', [{'url': cover, 'alt_text': '', 'is_primary': True, 'width': 0,
                                              'height': 0, 'is_demo': found[1]}])
    result = {}
    for product_id, (source, gallery) in out.items():
        primary = next((image for image in gallery if image['is_primary']), gallery[0] if gallery else None)
        result[product_id] = {
            'gallery': gallery,
            # Where it comes from: 'own', 'finish', 'cover' or '' (pending).
            'source': source,
            'image_url': primary['url'] if primary else '',
            'image_pending': primary is None,
            'image_is_demo': bool(primary and primary['is_demo']),
        }
    return result


def variant_pictures(products) -> dict[int, tuple[str, bool, bool]]:
    """
    The picture each device variant opens on (`variant_galleries`'s primary, the one the model page opens on: the
    configurator's `gallery.findIndex(image => image.is_primary)`, else the first): product id → `(address, pending,
    is_demo)`; `('', True, False)` is the pending marker. `test_finish_images.CartPictureTest` keeps it equal to the
    picture the page opens on. A product without a model answers its own address, unchanged.
    """
    products = [product for product in products if product is not None]
    galleries = variant_galleries(products)
    out = {}
    for product in products:
        found = galleries.get(product.pk)
        out[product.pk] = ((found['image_url'], found['image_pending'], found['image_is_demo']) if found is not None
                           else (product.image_url, not product.image_url, False))
    return out


def variant_picture(product) -> tuple[str, bool, bool]:
    """`variant_pictures` for one product: `(address, pending, is_demo)`."""
    return variant_pictures([product])[product.pk]


def public_model(model, branch) -> dict:
    """
    A model as the shop shows it: its options, its NEW variants with price,
    images and availability, and its USED units one by one.

    `available` is own stock in the storefront's fulfilment branch (as every
    other public product) or, DC3, a sale on order: `availability` says which
    ('own_stock', 'supplier' or 'none') and `on_order` its lead time and price.

    A published variant with neither is NOT left out (owner, 2026-10-20:
    published does not mean available): it comes with its price and photos
    and `availability: 'none'`, and the page sells nothing of it.

    CATALOG-REFERENCE-01 · PR C (D-C-1) — what a variant SHOWS is `gallery`, by
    `variant_galleries` (the rule every surface of the shop shares): its own
    VERIFIED pictures (IMG-GATE-01: its gallery or, before galleries, its own
    address — only a managed file of the company whose licence was verified)
    → else the PUBLIC pictures of its finish (model + its colour,
    `finish_images`; only those whose licence was verified, owner
    2026-11-23) → else nothing and `image_pending: true`, which the shop draws as the
    neutral «Imagen pendiente». Never another colour's photo and never the
    model's cover (`image_url` stays for lists and a model without variants)
    — except on a model none of whose variants (any) has an option of a
    `color` role or `color` kind attribute (D-PRC-8). A variant's `image_url`
    and `images` are that same gallery's (never an unverified own picture).
    Of a picture's provenance only `is_demo` is public.
    """
    from django.db.models import OuterRef, Prefetch, Subquery, Value
    from django.db.models.functions import Coalesce

    from .accessory_compat import for_model_public
    from .finish_media import colour_attribute_ids, public_payload as finish_public_payload, shop_states
    from .models import ModelFinishImage, ModelOption
    from .storefront_media import managed_public_id
    from .supply_services import on_order_payload

    variants = list(
        model.variants.filter(is_active=True, is_published_online=True)
        # A1b-2: the same belt as `company_storefront_products` — a pending draft is not on the page
        # even if its flag was written past the services.
        .exclude(Product.pending_draft_q())
        .prefetch_related(
            # PR E: each link carries the place this MODEL gives its option (0 = not placed), read in the same
            # query as the links: no query more than before.
            Prefetch('variant_options', queryset=ProductVariantOption.objects.annotate(model_rank=Coalesce(
                Subquery(ModelOption.objects.filter(model_id=model.pk, option_id=OuterRef('option_id'))
                         .values('sort_order')[:1]), 0),
                # FASE 1.2 · Y: this model's own swatch for the option ('' = the company's), in the same query.
                model_swatch=Coalesce(
                    Subquery(ModelOption.objects.filter(model_id=model.pk, option_id=OuterRef('option_id'))
                             .values('swatch_hex')[:1]), Value('')))),
            'variant_options__option__attribute',
        )
        .order_by('pk')
    )
    stock = {}
    if branch is not None:
        stock = {
            row.product_id: row.quantity
            for row in BranchStock.objects.filter(branch=branch, product__in=variants)
        }
    attributes: dict[int, dict] = {}
    variant_rows, used_products = [], []
    #: product id -> the option of its colour (the company's colour dimension, D-PRC-8).
    colour_of: dict[int, int] = {}
    dimension = colour_attribute_ids(model.company_id) if variants else set()
    for product in variants:
        links = sorted(
            product.variant_options.all(),
            key=lambda link: (link.attribute.sort_order, link.attribute.name, link.attribute_id),
        )
        selection = {}
        for link in links:
            attribute = link.attribute
            entry = attributes.setdefault(attribute.pk, {
                # D1 (2026-11-16): the closed role vocabulary is public so the configurator can order capacity,
                # colour, SIM and region; the palette and the normalized name stay private.
                'id': attribute.pk, 'name': attribute.name, 'kind': attribute.kind, 'role': attribute.role,
                'sort_order': attribute.sort_order, 'options': {},
            })
            entry['options'][link.option.pk] = {
                # FASE 1.2 · Y: the model's own swatch, else the company's; never whether it is approximate.
                'id': link.option.pk, 'label': link.option.label,
                'swatch_hex': resolved_swatch(link.option, link.model_swatch)[0],
                'sort_order': link.option.sort_order, '_rank': link.model_rank,
            }
            selection[str(attribute.pk)] = link.option.pk
            if attribute.pk in dimension:
                colour_of[product.pk] = link.option.pk
        if product.condition_line == Product.CONDITION_USED:
            used_products.append((product, selection, {
                link.attribute.name: link.option.label for link in links
            }))
            continue
        quantity = stock.get(product.pk, 0) if branch is not None else 0
        # DC3 — no device of its own on the shelf: on order, when the variant
        # may be sold so and an offer covers it (lead time and price only).
        on_order = on_order_payload(product)
        availability = 'own_stock' if quantity > 0 else ('supplier' if on_order else 'none')
        variant_rows.append({
            'product_id': product.pk,
            'slug': product.slug,
            'name': product.name,
            'options': selection,
            'price': str(product.price),
            # Resolved below (`variant_galleries`, one batch for every variant): never an unverified own picture.
            'image_url': '',
            'images': [],
            'gallery': None,
            'image_pending': None,
            'available': availability != 'none',
            'availability': availability,
            'on_order': on_order,
            # Bought by unit: the cart holds one concrete device of this variant.
            'sells_by_unit': product.sells_by_unit,
        })

    # PR C — the finishes of the colours a public variant has (never one only a hidden variant or the palette
    # has), and which pictures are public and which demo resources: two queries whatever the number of pictures.
    finishes: dict[int, list] = {}
    finish_rows = list(
        ModelFinishImage.objects.filter(model=model, company_id=model.company_id,
                                        color_option_id__in=set(colour_of.values()))
        .select_related('color_option').order_by('sort_order', 'pk')
    ) if colour_of else []
    states = shop_states([row.image_url for row in finish_rows], {model.company_id})

    def state(address) -> tuple[bool, bool]:
        """(licence verified, is a demo) of a managed file of this company; (False, False) for any other address."""
        return states.get((model.company_id, managed_public_id(address)), (False, False))

    # Owner, 2026-11-23: a finish picture is on the shop only once its licence is verified (`finish_media.is_public`);
    # until then the finish is `image_pending` here and the picture stays in the panel.
    finish_rows = [row for row in finish_rows if state(row.image_url)[0]]
    for row in finish_rows:
        finishes.setdefault(row.color_option_id, []).append(finish_public_payload(
            row, is_demo=state(row.image_url)[1],
            alt=f'{model.name} {row.color_option.label}'))
    # IMG-GATE-01: one rule for every surface — the variant's own VERIFIED pictures, else its colour's finish, else
    # the cover of a colourless model, else nothing (`variant_galleries`).
    galleries = variant_galleries([product for product in variants
                                   if product.condition_line != Product.CONDITION_USED]) if variant_rows else {}
    for entry in variant_rows:
        found = galleries.get(entry['product_id'], {'gallery': [], 'image_url': '', 'image_pending': True,
                                                     'source': ''})
        entry['gallery'] = found['gallery']
        entry['image_pending'] = found['image_pending']
        if found['source'] == 'own':
            # `image_url`/`images` stay what they always were — the variant's OWN pictures — but only verified ones.
            entry['image_url'] = found['image_url']
            entry['images'] = [{key: image[key] for key in PUBLIC_IMAGE_KEYS} for image in found['gallery']]

    used_units = []
    if used_products and branch is not None:
        by_product = {product.pk: (product, labels) for product, _sel, labels in used_products}
        default_warranty = company_used_warranty_days(model.company_id)
        from . import unit_media
        for unit in (
            offered_units([p for p, _s, _l in used_products], branch)
            .select_related('cosmetic_grade').prefetch_related(unit_media.public_photos_prefetch())
            .order_by('sale_price', 'received_at', 'pk')
        ):
            product, labels = by_product[unit.product_id]
            used_units.append(public_unit(unit, product, labels, default_warranty_days=default_warranty))

    labels = line_labels(model.company)
    return {
        'slug': model.slug,
        'name': model.name,
        'description': model.description,
        'image_url': model.image_url,
        'category': (
            {'slug': model.category.slug, 'name': model.category.name} if model.category_id else None
        ),
        'attributes': [
            {**{k: v for k, v in entry.items() if k != 'options'}, 'options': _public_options(entry['options'])}
            for entry in sorted(attributes.values(), key=lambda a: (a['sort_order'], a['name']))
        ],
        'lines': {
            'new': {'label': labels[Product.CONDITION_NEW], 'count': len(variant_rows)},
            'used': {'label': labels[Product.CONDITION_USED], 'count': len(used_units)},
        },
        'variants': variant_rows,
        # PR C — colour option id -> the pictures of that finish, for the colours public variants have.
        'finish_images': {str(option_id): images for option_id, images in finishes.items()},
        'used_units': used_units,
        # P2 · F3 — accessories that fit it, only those the shop sells, in the company's order. Empty: none.
        'accessories': for_model_public(model, branch),
        # SUPPLIER-DEVICE-FLOW-02 — published, but nothing of it can be bought
        # now: the page shows it (photos, options, last prices) as «Agotado».
        'sold_out': not used_units and not any(row['available'] for row in variant_rows),
        'as_of': timezone.now().isoformat(),
    }
