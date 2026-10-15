"""
DC2 — the PUBLIC photos of one device (a used phone shown as it is).

The same pipeline as a product gallery: the pixels are a `StorefrontImage`
(re-encoded from pixels, owned by one company, swept when nothing shows it),
and `UnitPhoto` only says which device shows it and in what order. NEVER the
private evidence store: these photos are for anybody to see.
"""
from __future__ import annotations

from django.conf import settings
from django.db import transaction
from django.db.models import Max

from . import storefront_media
from .models import AdminAuditLog, StockUnit, UnitPhoto
from .product_media import clean_alt_text


class UnitMediaError(Exception):
    """A refusal the person editing the device can read and fix."""


def max_per_unit() -> int:
    return int(getattr(settings, 'UNIT_PHOTO_MAX_PER_UNIT', 8))


def payload(photo: UnitPhoto) -> dict:
    return {'id': photo.pk, 'url': photo.image_url, 'alt_text': photo.alt_text,
            'width': photo.width, 'height': photo.height}


def add_photo(*, unit, actor, uploaded, alt_text='', request=None) -> UnitPhoto:
    alt = clean_alt_text(alt_text)
    if unit.photos.count() >= max_per_unit():
        raise UnitMediaError(f'Un equipo admite hasta {max_per_unit()} fotos. Quita una antes de añadir otra.')
    raw = storefront_media.read_upload(uploaded)
    stored = storefront_media.store(company=unit.company, actor=actor, raw=raw, request=request)
    try:
        with transaction.atomic():
            locked = StockUnit.objects.select_for_update().get(pk=unit.pk, company_id=unit.company_id)
            if UnitPhoto.objects.filter(unit=locked).count() >= max_per_unit():
                raise UnitMediaError(f'Un equipo admite hasta {max_per_unit()} fotos.')
            storefront_media.claim(stored.url, company=locked.company, field='file')
            last = UnitPhoto.objects.filter(unit=locked).aggregate(last=Max('sort_order'))['last']
            photo = UnitPhoto.objects.create(
                company=locked.company, unit=locked, image_url=stored.url, alt_text=alt,
                width=stored.width, height=stored.height, uploaded_by=actor,
                sort_order=0 if last is None else last + 1,
            )
            AdminAuditLog.log(
                actor=actor, action='unit_photo_added', target_type='stock_unit', target_id=locked.pk,
                metadata={'photo_id': photo.pk, 'address': stored.url}, request=request, company=locked.company,
            )
            return photo
    except Exception:
        storefront_media.release([stored.url], company=unit.company, actor=actor, request=request,
                                 reason='never_placed')
        raise


@transaction.atomic
def remove_photo(*, photo, actor, request=None) -> None:
    address, unit_id, company = photo.image_url, photo.unit_id, photo.company
    photo.delete()
    AdminAuditLog.log(
        actor=actor, action='unit_photo_removed', target_type='stock_unit', target_id=unit_id,
        metadata={'address': address}, request=request, company=company,
    )
    storefront_media.release([address], company=company, actor=actor, request=request)
