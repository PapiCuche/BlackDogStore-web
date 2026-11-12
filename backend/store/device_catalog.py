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
           role=attribute.role)
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
    if attribute.options.filter(label__iexact=label).exists():
        raise CatalogError(f'«{attribute.name}» ya tiene la opción «{label}».', field='label', code='duplicate')
    try:
        with transaction.atomic():
            option = VariantOption.objects.create(
                company=company, attribute=attribute, label=label, swatch_hex=swatch,
                sort_order=int(sort_order or 0),
            )
    except IntegrityError:
        raise CatalogError(f'«{attribute.name}» ya tiene la opción «{label}».', field='label', code='duplicate') from None
    _audit('variant_option_created', company=company, actor=actor, request=request,
           target_type='variant_option', target_id=option.pk, attribute_id=attribute.pk, label=label)
    return option


def update_option(option, *, label=None, swatch_hex=None, sort_order=None, actor=None, request=None) -> VariantOption:
    if label is not None:
        option.label = _text(label, 'el valor', 'label', max_length=60)
        if option.attribute.options.exclude(pk=option.pk).filter(label__iexact=option.label).exists():
            raise CatalogError(f'«{option.attribute.name}» ya tiene la opción «{option.label}».',
                               field='label', code='duplicate')
    if swatch_hex is not None:
        option.swatch_hex = _clean_swatch(option.attribute, swatch_hex)
    if sort_order is not None:
        option.sort_order = int(sort_order or 0)
    option.save()
    _audit('variant_option_updated', company=option.company, actor=actor, request=request,
           target_type='variant_option', target_id=option.pk, label=option.label)
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

    units = StockUnit.objects.filter(product_id=product.pk)
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


def resolve_variant_spec(company, *, model_name, line_text='', labels=(), cache=None, strict=False) -> dict:
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
    """
    name = ' '.join(str(model_name or '').split())
    model = find_model(company, name, cache=cache)
    if model is None:
        raise CatalogError(f'No existe el modelo «{name[:60]}». Créalo en Catálogo › Modelos.', field='model')
    line = clean_line(line_text)
    option_ids, options, seen = [], [], set()
    for label in labels:
        label = ' '.join(str(label or '').split())
        if not label:
            continue
        found = list(VariantOption.objects.filter(company=company, label__iexact=label).select_related('attribute'))
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
    sold online. A used unit without its own price is not offered.
    """
    if branch is None:
        return StockUnit.objects.none()
    return (
        StockUnit.objects.filter(
            company_id=branch.company_id, branch=branch, product__in=products,
            status=StockUnit.Status.AVAILABLE,
        )
        # INV-RECEIVE-PRICE-03: «priced» is greater than zero, as at the till — a zero is no price.
        .exclude(Q(product__condition_line=Product.CONDITION_USED)
                 & (Q(sale_price__isnull=True) | Q(sale_price__lte=0)))
    )


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


def public_unit(unit, product, option_labels: dict) -> dict:
    """ONE used device as the shop shows it. An allowlist: nothing else leaves."""
    photos = [
        {'url': photo.image_url, 'alt_text': photo.alt_text, 'width': photo.width, 'height': photo.height}
        for photo in unit.photos.all()
    ]
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
    }


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
    """
    from .product_media import public_payload
    from .supply_services import on_order_payload

    variants = list(
        model.variants.filter(is_active=True, is_published_online=True)
        .prefetch_related('images', 'variant_options__option__attribute')
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
    for product in variants:
        links = sorted(
            product.variant_options.all(),
            key=lambda link: (link.attribute.sort_order, link.attribute.name, link.attribute_id),
        )
        selection = {}
        for link in links:
            attribute = link.attribute
            entry = attributes.setdefault(attribute.pk, {
                'id': attribute.pk, 'name': attribute.name, 'kind': attribute.kind,
                'sort_order': attribute.sort_order, 'options': {},
            })
            entry['options'][link.option.pk] = {
                'id': link.option.pk, 'label': link.option.label, 'swatch_hex': link.option.swatch_hex,
                'sort_order': link.option.sort_order,
            }
            selection[str(attribute.pk)] = link.option.pk
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
            'image_url': product.image_url,
            'images': [public_payload(image) for image in product.images.all()],
            'available': availability != 'none',
            'availability': availability,
            'on_order': on_order,
            # Bought by unit: the cart holds one concrete device of this variant.
            'sells_by_unit': product.sells_by_unit,
        })

    used_units = []
    if used_products and branch is not None:
        by_product = {product.pk: (product, labels) for product, _sel, labels in used_products}
        for unit in (
            offered_units([p for p, _s, _l in used_products], branch)
            .select_related('cosmetic_grade').prefetch_related('photos')
            .order_by('sale_price', 'received_at', 'pk')
        ):
            product, labels = by_product[unit.product_id]
            used_units.append(public_unit(unit, product, labels))

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
            {**{k: v for k, v in entry.items() if k != 'options'},
             'options': sorted(entry['options'].values(), key=lambda o: (o['sort_order'], o['label']))}
            for entry in sorted(attributes.values(), key=lambda a: (a['sort_order'], a['name']))
        ],
        'lines': {
            'new': {'label': labels[Product.CONDITION_NEW], 'count': len(variant_rows)},
            'used': {'label': labels[Product.CONDITION_USED], 'count': len(used_units)},
        },
        'variants': variant_rows,
        'used_units': used_units,
        # SUPPLIER-DEVICE-FLOW-02 — published, but nothing of it can be bought
        # now: the page shows it (photos, options, last prices) as «Agotado».
        'sold_out': not used_units and not any(row['available'] for row in variant_rows),
        'as_of': timezone.now().isoformat(),
    }
