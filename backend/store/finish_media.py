"""
CATALOG-REFERENCE-01 · PR C (D-C-1) — the gallery of a real finish: a model in one colour.

WHAT IT IS. Every variant of a colour (any capacity, SIM or region) is the same object in the same colour, so its
pictures are uploaded ONCE, for the finish, and every variant of that colour without pictures of its own shows them.
A finish without pictures shows «Imagen pendiente» on the shop: never another colour's photo, never the model's cover
(`device_catalog.public_model`).

THE PIXELS ARE NOT HERE. Like `product_media`, the file is a `StorefrontImage` from `storefront_media` (re-encoded
from its pixels, owned by one company, swept by reference count). This module decides which finish shows it, in what
order, with what alternative text, which one is primary — and requires to say WHERE IT COMES FROM: the owner's
order of preference is own photo > material authorised by a supplier or distributor > stock with a commercial
licence > a demo resource, which the shop flags. Provenance lives on the `StorefrontImage` (it is the file's, not the
slot's). No picture is ever compiled into the repository (AGENTS §9).

ONE WRITE AT A TIME PER MODEL. Every operation locks the model's row before it looks at the gallery, so two people
uploading at once neither break the order nor leave two primaries. Only the model is locked (the catalogue's lock
order is product → units → model; nothing here takes a product).

The company always comes from the caller's resolved context; an option or a model of another company answers like a
missing one.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from django.conf import settings
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from . import storefront_media
from .models import (
    AdminAuditLog, ModelFinishImage, ModelOption, ProductModel, StorefrontImage, VariantAttribute, VariantOption,
)

ALT_TEXT_MAX = 160
#: What a person may choose when uploading. `unknown` is only what an upload without provenance (before PR C, or
#: another slot) has; a finish picture never starts so.
UPLOAD_SOURCE_KINDS = (
    StorefrontImage.SOURCE_OWN_PHOTO, StorefrontImage.SOURCE_SUPPLIER_AUTHORIZED,
    StorefrontImage.SOURCE_LICENSED_STOCK, StorefrontImage.SOURCE_DEMO,
)
PROVENANCE_FIELDS = ('source_kind', 'license', 'source_url', 'attribution')


class FinishMediaError(Exception):
    """A refusal the person editing the finish can read and correct."""

    def __init__(self, message, *, field=None, code='invalid'):
        super().__init__(message)
        self.field = field
        self.code = code

    def body(self) -> dict:
        data = {'detail': str(self), 'code': self.code}
        if self.field:
            data['field'] = self.field
        return data


class FinishImageGone(FinishMediaError):
    """The picture stopped existing while the lock was awaited: someone removed it."""

    def __init__(self):
        super().__init__('Esa imagen ya no está en la galería.', code='not_found')


def max_per_colour() -> int:
    return int(getattr(settings, 'FINISH_IMAGE_MAX_PER_COLOUR', 12))


def _too_many() -> FinishMediaError:
    return FinishMediaError(
        f'Un acabado admite hasta {max_per_colour()} imágenes. Quita una antes de añadir otra.', field='file',
        code='too_many')


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def colour_attribute_ids(company_id) -> set[int]:
    """
    D-PRC-8 — the company's COLOUR DIMENSION: its attribute with the `color` role; when it has none, its attributes
    of the `color` kind (a company whose colour attribute kept the `other` role, as 0165 left existing ones). One
    query. The same rule on every side: placing a picture, the panel's listing, the public page and the F4 report.
    """
    rows = list(VariantAttribute.objects.filter(company_id=company_id)
                .filter(Q(role=VariantAttribute.ROLE_COLOR) | Q(kind=VariantAttribute.KIND_COLOR))
                .values_list('pk', 'role'))
    by_role = {pk for pk, role in rows if role == VariantAttribute.ROLE_COLOR}
    return by_role or {pk for pk, _role in rows}


def gallery(model, colour=None):
    """The finish pictures of `model` (of one colour, when given), in the order they are shown."""
    rows = ModelFinishImage.objects.filter(model=model, company_id=model.company_id)
    return rows.filter(color_option=colour) if colour is not None else rows


def colours_of(model, dimension=None) -> list[VariantOption]:
    """
    The finishes a model has: the colours (`colour_attribute_ids`) of its palette and those its variants use (a model
    without a palette, or a variant outside it, still has a finish) — and any option that already has pictures, even
    if its attribute stopped being a colour later (the listing flags it; its pictures stay editable and removable).
    """
    from .models import ProductVariantOption

    dimension = colour_attribute_ids(model.company_id) if dimension is None else dimension
    used = ProductVariantOption.objects.filter(
        company_id=model.company_id, product__product_model=model, product__is_active=True,
    ).values('option_id')
    palette = ModelOption.objects.filter(model=model).values('option_id')
    pictured = ModelFinishImage.objects.filter(model=model).values('color_option_id')
    return list(
        VariantOption.objects.filter(company_id=model.company_id)
        .filter((Q(attribute_id__in=dimension) & (Q(pk__in=used) | Q(pk__in=palette))) | Q(pk__in=pictured))
        .order_by('sort_order', 'label', 'pk')
    )


def stored_of(images) -> dict[str, StorefrontImage]:
    """The `StorefrontImage` behind each picture, by public id. One query whatever the number of pictures."""
    ids = {storefront_media.managed_public_id(image.image_url) for image in images} - {None}
    if not ids:
        return {}
    return {row.public_id: row for row in StorefrontImage.objects.filter(public_id__in=ids)}


def _source_payload(stored: StorefrontImage | None) -> dict:
    if stored is None:
        return {'kind': StorefrontImage.SOURCE_UNKNOWN, 'kind_label': 'Sin registrar', 'url': '', 'license': '',
                'attribution': '', 'is_demo': False, 'license_verified': False, 'license_verified_at': None}
    return {
        'kind': stored.source_kind,
        'kind_label': stored.get_source_kind_display(),
        'url': stored.source_url,
        'license': stored.license,
        'attribution': stored.attribution,
        'is_demo': stored.source_kind == StorefrontImage.SOURCE_DEMO,
        'license_verified': stored.license_verified_at is not None,
        'license_verified_at': stored.license_verified_at.isoformat() if stored.license_verified_at else None,
    }


def payload(image: ModelFinishImage, stored: StorefrontImage | None = None) -> dict:
    """What the panel sees, provenance included. Never the storage key."""
    if stored is None:
        stored = stored_of([image]).get(storefront_media.managed_public_id(image.image_url))
    return {
        'id': image.pk,
        'color_option_id': image.color_option_id,
        'url': image.image_url,
        'alt_text': image.alt_text,
        'is_primary': image.is_primary,
        'sort_order': image.sort_order,
        'width': image.width,
        'height': image.height,
        'source': _source_payload(stored),
    }


def listing(model) -> dict:
    """Every finish of the model with its pictures, or `image_pending` when it has none."""
    images = list(gallery(model))
    stored = stored_of(images)
    by_colour: dict[int, list] = {}
    for image in images:
        by_colour.setdefault(image.color_option_id, []).append(
            payload(image, stored.get(storefront_media.managed_public_id(image.image_url))))
    dimension = colour_attribute_ids(model.company_id)
    return {'finishes': [
        {'option': {'id': colour.pk, 'label': colour.label, 'swatch_hex': colour.swatch_hex},
         'images': by_colour.get(colour.pk, []), 'image_pending': not by_colour.get(colour.pk),
         # Its attribute stopped being a colour after the pictures were placed: shown so they can be removed.
         'not_colour': colour.attribute_id not in dimension}
        for colour in colours_of(model, dimension)
    ], 'max_per_colour': max_per_colour(), 'source_kinds': [
        {'value': kind, 'label': label} for kind, label in StorefrontImage.SOURCE_KIND_CHOICES
        if kind in UPLOAD_SOURCE_KINDS
    ]}


def public_payload(image: ModelFinishImage, *, is_demo: bool, alt: str = '') -> dict:
    """What the shop sees: an allowlist. Of the provenance, only whether it is a demo resource."""
    return {
        'url': image.image_url,
        'alt_text': image.alt_text or alt,
        'is_primary': image.is_primary,
        'width': image.width,
        'height': image.height,
        'is_demo': is_demo,
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def clean_alt_text(value) -> str:
    text = ' '.join(str(value or '').split())
    if len(text) > ALT_TEXT_MAX:
        raise FinishMediaError(f'El texto alternativo admite hasta {ALT_TEXT_MAX} caracteres.', field='alt_text')
    return text


def _short_text(value, field, label, *, max_length) -> str:
    text = ' '.join(str(value or '').split())
    if len(text) > max_length:
        raise FinishMediaError(f'{label} admite hasta {max_length} caracteres.', field=field)
    return text


def clean_provenance(data) -> dict:
    """
    Where the picture comes from, validated whole. `source_kind` is required (and never `unknown`); the licence or
    authorisation text is required unless it is the shop's own photo; `source_url` is optional, a web page
    (http/https), and never one of our own image addresses (the reference sweep reads that field).
    """
    kind = str(data.get('source_kind') or '').strip()
    if kind not in UPLOAD_SOURCE_KINDS:
        raise FinishMediaError(
            'Indica de dónde sale la imagen: foto propia, material autorizado por un proveedor o distribuidor, '
            'banco con licencia comercial o recurso demostrativo.', field='source_kind')
    license_text = _short_text(data.get('license'), 'license', 'La licencia', max_length=255)
    if kind != StorefrontImage.SOURCE_OWN_PHOTO and not license_text:
        raise FinishMediaError(
            'Escribe la licencia o la autorización con la que se usa esta imagen.', field='license')
    source_url = str(data.get('source_url') or '').strip()
    if source_url:
        if len(source_url) > 500:
            raise FinishMediaError('La dirección de origen admite hasta 500 caracteres.', field='source_url')
        try:
            parts = urlsplit(source_url)
        except ValueError:
            parts = None
        if parts is None or parts.scheme.lower() not in ('http', 'https') or not parts.netloc:
            raise FinishMediaError('La dirección de origen debe ser una página web (http o https).',
                                   field='source_url')
        if storefront_media.managed_public_id(source_url):
            raise FinishMediaError('La dirección de origen no puede ser una imagen de esta tienda.',
                                   field='source_url')
    attribution = _short_text(data.get('attribution'), 'attribution', 'La atribución', max_length=255)
    return {'source_kind': kind, 'license': license_text, 'source_url': source_url, 'attribution': attribution}


def _option_id(value) -> int:
    if isinstance(value, bool):
        raise FinishMediaError('Elige el color del acabado.', field='color_option_id')
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise FinishMediaError('Elige el color del acabado.', field='color_option_id') from None
    if number <= 0:
        raise FinishMediaError('Elige el color del acabado.', field='color_option_id')
    return number


def colour_for(model, option_id) -> VariantOption:
    """
    The colour option `option_id` of `model`'s company, if it may have finish pictures on `model`: an option of
    another company answers like a missing one (`not_found`); it must be of the colour dimension
    (`colour_attribute_ids`); with a palette, one of its colours.
    """
    number = _option_id(option_id)
    option = (VariantOption.objects.select_related('attribute')
              .filter(company_id=model.company_id, pk=number).first())
    if option is None:
        raise FinishMediaError('Color no encontrado.', field='color_option_id', code='not_found')
    if option.attribute_id not in colour_attribute_ids(model.company_id):
        raise FinishMediaError('Esa opción no es un color.', field='color_option_id', code='not_a_colour')
    palette = ModelOption.objects.filter(model=model)
    if palette.exists() and not palette.filter(option=option).exists():
        raise FinishMediaError('Ese color no está entre las opciones de este modelo.', field='color_option_id',
                               code='not_in_palette')
    return option


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _locked(model) -> ProductModel:
    return ProductModel.objects.select_for_update().get(pk=model.pk, company_id=model.company_id)


def _current(model, image) -> ModelFinishImage:
    found = gallery(model).select_for_update().filter(pk=image.pk).first()
    if found is None:
        raise FinishImageGone()
    return found


def _audit(*, action, model, actor, request, **metadata):
    AdminAuditLog.log(
        actor=actor, action=action, target_type='product_model', target_id=model.pk,
        metadata={'model_id': model.pk, 'model_name': model.name, **metadata},
        request=request, company=model.company,
    )


def place(*, model, colour, address: str, actor, alt_text: str = '', request=None) -> ModelFinishImage:
    """
    Places an image ALREADY UPLOADED by the model's company in the finish gallery. Called inside a transaction:
    locks the model, checks the colour again (the palette may have changed) and that the image exists and is the
    company's (`claim`), and appends it. The first picture of a finish is its primary.
    """
    row = _locked(model)
    colour = colour_for(row, colour.pk)
    existing = list(gallery(row, colour))
    if len(existing) >= max_per_colour():
        raise _too_many()
    storefront_media.claim(address, company=row.company, field='file')
    stored = StorefrontImage.objects.get(
        public_id=storefront_media.managed_public_id(address), company=row.company)
    last = gallery(row, colour).aggregate(last=Max('sort_order'))['last']
    image = ModelFinishImage.objects.create(
        company=row.company, model=row, color_option=colour, image_url=stored.url, alt_text=alt_text,
        width=stored.width, height=stored.height, sort_order=0 if last is None else last + 1,
        is_primary=not any(item.is_primary for item in existing), uploaded_by=actor,
    )
    _audit(action='finish_image_added', model=row, actor=actor, request=request, image_id=image.pk,
           color_option_id=colour.pk, color_label=colour.label, address=stored.url, is_primary=image.is_primary,
           source_kind=stored.source_kind)
    return image


def add_image(*, model, color_option_id, actor, uploaded, provenance, alt_text='', request=None) -> ModelFinishImage:
    """Validates everything it can BEFORE storing the file, uploads it with its provenance and places it."""
    alt = clean_alt_text(alt_text)
    origin = clean_provenance(provenance or {})
    colour = colour_for(model, color_option_id)
    if gallery(model, colour).count() >= max_per_colour():
        raise _too_many()      # checked again under the lock
    raw = storefront_media.read_upload(uploaded)
    stored = storefront_media.store(company=model.company, actor=actor, raw=raw, request=request,
                                    provenance=origin)
    try:
        with transaction.atomic():
            return place(model=model, colour=colour, address=stored.url, actor=actor, alt_text=alt, request=request)
    except Exception:
        # Stored but never placed: it is not left behind.
        storefront_media.release([stored.url], company=model.company, actor=actor, request=request,
                                 reason='never_placed')
        raise


@transaction.atomic
def update_image(*, image, actor, alt_text=None, is_primary=None, license_verified=None, provenance=None,
                 request=None) -> ModelFinishImage:
    """
    Alternative text, primary, provenance and licence check. `provenance` is a partial dict of
    `PROVENANCE_FIELDS`: merged with the current one and validated whole; any change clears the licence check
    (it has to be verified again). `license_verified` True records who and when.
    """
    row = _locked(image.model)
    image = _current(row, image)

    if is_primary is False and image.is_primary:
        raise FinishMediaError('Para cambiar la imagen principal, elige otra como principal.', field='is_primary')

    if alt_text is not None:
        alt = clean_alt_text(alt_text)
        if alt != image.alt_text:
            before, image.alt_text = image.alt_text, alt
            image.save(update_fields=['alt_text'])
            _audit(action='finish_image_updated', model=row, actor=actor, request=request, image_id=image.pk,
                   alt_text={'old': before, 'new': alt})

    if is_primary is True and not image.is_primary:
        previous = gallery(row, image.color_option).filter(is_primary=True).first()
        gallery(row, image.color_option).filter(is_primary=True).update(is_primary=False)
        image.is_primary = True
        image.save(update_fields=['is_primary'])
        _audit(action='finish_image_primary_changed', model=row, actor=actor, request=request, image_id=image.pk,
               color_option_id=image.color_option_id, previous_image_id=previous.pk if previous else None)

    if provenance or license_verified is not None:
        stored = (StorefrontImage.objects.select_for_update()
                  .filter(public_id=storefront_media.managed_public_id(image.image_url), company=row.company).first())
        if stored is None:
            raise FinishImageGone()
        if provenance:
            current = {field: getattr(stored, field) for field in PROVENANCE_FIELDS}
            origin = clean_provenance({**current, **provenance})
            if origin != current:
                for field, value in origin.items():
                    setattr(stored, field, value)
                stored.license_verified_by, stored.license_verified_at = None, None
                stored.save(update_fields=[*PROVENANCE_FIELDS, 'license_verified_by', 'license_verified_at'])
                _audit(action='storefront_image_provenance_changed', model=row, actor=actor, request=request,
                       image_id=image.pk, public_id=stored.public_id, old=current, new=origin)
        if license_verified is True and stored.license_verified_at is None:
            if stored.source_kind not in UPLOAD_SOURCE_KINDS:
                raise FinishMediaError('Registra primero de dónde sale la imagen.', field='source_kind')
            stored.license_verified_by, stored.license_verified_at = actor, timezone.now()
            stored.save(update_fields=['license_verified_by', 'license_verified_at'])
            _audit(action='storefront_image_license_verified', model=row, actor=actor, request=request,
                   image_id=image.pk, public_id=stored.public_id, source_kind=stored.source_kind)
        elif license_verified is False and stored.license_verified_at is not None:
            stored.license_verified_by, stored.license_verified_at = None, None
            stored.save(update_fields=['license_verified_by', 'license_verified_at'])
            _audit(action='storefront_image_license_unverified', model=row, actor=actor, request=request,
                   image_id=image.pk, public_id=stored.public_id)
    return image


@transaction.atomic
def remove_image(*, image, actor, request=None) -> None:
    row = _locked(image.model)
    image = _current(row, image)
    address, was_primary, image_id, colour = image.image_url, image.is_primary, image.pk, image.color_option
    image.delete()
    if was_primary:
        successor = gallery(row, colour).first()
        if successor is not None:
            successor.is_primary = True
            successor.save(update_fields=['is_primary'])
    _audit(action='finish_image_removed', model=row, actor=actor, request=request, image_id=image_id,
           color_option_id=colour.pk, address=address, was_primary=was_primary)
    # After the row is gone the count no longer sees it: the file goes only if nothing else shows it.
    storefront_media.release([address], company=row.company, actor=actor, request=request)


@transaction.atomic
def reorder(*, model, color_option_id, order, actor, request=None) -> list[ModelFinishImage]:
    row = _locked(model)
    number = _option_id(color_option_id)
    current = {image.pk: image for image in gallery(row).filter(color_option_id=number).select_for_update()}
    if (
        not current
        or not isinstance(order, list)
        or any(isinstance(item, bool) or not isinstance(item, int) for item in order)
        or len(order) != len(set(order))
        or set(order) != set(current)
    ):
        raise FinishMediaError('El orden debe nombrar, una vez, cada imagen de ese acabado.', field='order')
    for position, image_id in enumerate(order):
        image = current[image_id]
        if image.sort_order != position:
            image.sort_order = position
            image.save(update_fields=['sort_order'])
    _audit(action='finish_images_reordered', model=row, actor=actor, request=request, color_option_id=number,
           order=order)
    return [current[image_id] for image_id in order]
