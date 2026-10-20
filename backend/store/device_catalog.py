"""
DEVICE-COMMERCE-01 · DC2 — models, options and variants of the catalogue.

    A VARIANT IS A PRODUCT. A MODEL ONLY GROUPS THEM.

The only writer of `ProductModel`, `VariantAttribute`, `VariantOption`,
`ProductVariantOption` and of `Product.product_model` / `condition_line` /
`variant_key`. Everything is inside ONE company: a model, an option or a
product of another company answers exactly like one that does not exist.

The rules a variant obeys, and where each is held:

  * one option per attribute per product   UNIQUE (product, attribute)
  * no two variants of one model with the
    same options in the same line          UNIQUE (model, line, variant_key)
  * everything of one company               checked here, before writing
  * a colour swatch is #RRGGBB              `validate_swatch_hex`

This module also builds what the PUBLIC reads of a model (`public_model`):
an allowlist written by hand, so a field added to a unit tomorrow does not
reach the shop by default. Never a full IMEI, a serial, a cost or a supplier.
"""
from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
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


def _audit(action, *, company, actor, request, target_type, target_id, **metadata):
    AdminAuditLog.log(
        actor=actor, action=action, target_type=target_type, target_id=target_id,
        metadata=metadata, request=request, company=company,
    )


# ---------------------------------------------------------------------------
# Attributes and options
# ---------------------------------------------------------------------------

def create_attribute(*, company, name, kind=VariantAttribute.KIND_TEXT, sort_order=0,
                     actor=None, request=None) -> VariantAttribute:
    name = _text(name, 'el nombre del atributo', 'name', max_length=60)
    if kind not in dict(VariantAttribute.KIND_CHOICES):
        raise CatalogError('Tipo de atributo desconocido.', field='kind')
    if VariantAttribute.objects.filter(company=company, name__iexact=name).exists():
        raise CatalogError(f'Ya existe el atributo «{name}».', field='name', code='duplicate')
    try:
        with transaction.atomic():
            attribute = VariantAttribute.objects.create(
                company=company, name=name, kind=kind, sort_order=int(sort_order or 0),
            )
    except IntegrityError:
        raise CatalogError(f'Ya existe el atributo «{name}».', field='name', code='duplicate') from None
    _audit('variant_attribute_created', company=company, actor=actor, request=request,
           target_type='variant_attribute', target_id=attribute.pk, name=name, kind=kind)
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


def create_model(*, company, name, category_id=None, slug='', description='', image_url='',
                 is_published_online=True, sort_order=0, actor=None, request=None) -> ProductModel:
    name = _text(name, 'el nombre del modelo', 'name', max_length=160)
    wanted = slugify(slug or '')[:120]
    if wanted and ProductModel.objects.filter(company=company, slug=wanted).exists():
        raise CatalogError('Ya hay un modelo con esa dirección.', field='slug', code='duplicate')
    model = ProductModel(
        company=company, name=name, category=_category(company, category_id),
        slug=wanted or _free_slug(company, name),
        description=str(description or '')[:5000], image_url=str(image_url or '').strip(),
        is_published_online=bool(is_published_online), sort_order=int(sort_order or 0),
    )
    try:
        model.full_clean(exclude=['company', 'category'])
    except ValidationError as exc:
        field, messages = next(iter(exc.message_dict.items()))
        raise CatalogError(messages[0], field=field) from None
    try:
        with transaction.atomic():
            model.save()
    except IntegrityError:
        raise CatalogError('Ya hay un modelo con esa dirección.', field='slug', code='duplicate') from None
    _audit('product_model_created', company=company, actor=actor, request=request,
           target_type='product_model', target_id=model.pk, name=name)
    return model


def update_model(model, *, actor=None, request=None, **fields) -> ProductModel:
    if 'name' in fields:
        model.name = _text(fields['name'], 'el nombre del modelo', 'name', max_length=160)
    if 'category_id' in fields:
        model.category = _category(model.company, fields['category_id'])
    if 'slug' in fields and fields['slug']:
        slug = slugify(fields['slug'])[:120]
        if ProductModel.objects.filter(company=model.company, slug=slug).exclude(pk=model.pk).exists():
            raise CatalogError('Ya hay un modelo con esa dirección.', field='slug', code='duplicate')
        model.slug = slug
    if 'description' in fields:
        model.description = str(fields['description'] or '')[:5000]
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
    model.save()
    _audit('product_model_updated', company=model.company, actor=actor, request=request,
           target_type='product_model', target_id=model.pk, changed=sorted(fields))
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


def variant_history(product) -> dict:
    """
    CATALOG-VARIANTS-UX — what a variant already carries: units of it sold
    (order lines of any order), devices of it received (not written off) and
    devices of it on hold or set aside.
    """
    from .models import OrderItem

    units = StockUnit.objects.filter(product_id=product.pk)
    return {
        'sold': OrderItem.objects.filter(product_id=product.pk).count(),
        'units': units.exclude(status=StockUnit.Status.WRITTEN_OFF).count(),
        'reserved': units.filter(status=StockUnit.Status.RESERVED).count(),
    }


def edit_rules(history) -> dict:
    """
    How the identity (model, options) and the line of a variant with this
    history may change: 'free', 'superior' (with a reason, audited) or 'blocked'.
    """
    if history['reserved']:
        identity = 'blocked'
    elif history['sold'] or history['units']:
        identity = 'superior'
    else:
        identity = 'free'
    line = 'blocked' if (history['units'] or history['reserved']) else identity
    return {'identity_change': identity, 'line_change': line}


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
    if model_id in (None, ''):
        ProductVariantOption.objects.filter(product=product).delete()
        product.product_model = None
        product.condition_line = Product.CONDITION_NEW
        product.variant_key = ''
        product.save(update_fields=['product_model', 'condition_line', 'variant_key', 'updated_at'])
        _audit('product_variant_cleared', company=company, actor=actor, request=request,
               target_type='product', target_id=product.pk)
        return product

    model = ProductModel.objects.filter(company=company, pk=model_id).first()
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
    changes_identity = (product.product_model_id != model.pk or product.variant_key != key)
    changes_line = product.product_model_id is not None and product.condition_line != condition_line
    history = rules = None
    text = ''
    if product.product_model_id is not None and (changes_identity or changes_line):
        history = variant_history(product)
        rules = edit_rules(history)
        if changes_line and rules['line_change'] == 'blocked':
            raise CatalogError(
                'Esta variante tiene equipos propios: su línea (nuevo o seminuevo) es la de esos equipos y no '
                'cambia. Registra los equipos en la variante que corresponde.',
                field='condition_line', code='line_change_with_units')
        if rules['identity_change'] == 'blocked':
            raise CatalogError(
                'Hay equipos de esta variante apartados o en un carrito: libera esas reservas antes de cambiarla.',
                field='options', code='variant_reserved')
        if rules['identity_change'] == 'superior':
            if not _may_override(actor, company):
                raise CatalogError(
                    f'Esta variante ya tiene historial ({history["sold"]} venta(s), {history["units"]} equipo(s)): '
                    'cambiar su modelo u opciones corresponde a un superior (administrador de la empresa), '
                    'con un motivo.', field='options', code='history_needs_superior')
            text = ' '.join(str(reason or '').split())
            if not HISTORY_REASON_MIN <= len(text) <= HISTORY_REASON_MAX or any(ord(c) < 32 for c in text):
                raise CatalogError(
                    f'Escribe el motivo del cambio ({HISTORY_REASON_MIN} a {HISTORY_REASON_MAX} caracteres): '
                    'quedará en la auditoría.', field='reason', code='history_reason_required')
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
    extra = {'history': history, 'reason': text} if history is not None else {}
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


def resolve_variant_spec(company, *, model_name, line_text='', labels=()) -> dict:
    """
    A spreadsheet's «Modelo» / «Capacidad» / «Color» / «Línea», as THIS
    company's model and options. Raises `CatalogError` naming what is unknown.
    Attribute-agnostic: each label is looked up among the company's options;
    a label two attributes share is ambiguous and refused.
    """
    name = ' '.join(str(model_name or '').split())
    model = ProductModel.objects.filter(company=company, name__iexact=name).first() if name else None
    if model is None:
        raise CatalogError(f'No existe el modelo «{name[:60]}». Créalo en Catálogo › Modelos.', field='model')
    line = clean_line(line_text)
    option_ids, seen = [], set()
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
    return {'model_id': model.pk, 'model_name': model.name, 'condition_line': line,
            'option_ids': sorted(option_ids), 'variant_key': variant_key(option_ids)}


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
    """What one unit sells for on the web: its own price when used, the variant's when new."""
    if product.condition_line == Product.CONDITION_USED:
        return unit.sale_price
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
        .exclude(product__condition_line=Product.CONDITION_USED, sale_price__isnull=True)
    )


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
        'as_of': timezone.now().isoformat(),
    }
