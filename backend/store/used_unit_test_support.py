"""
USED-READY-01 — test helpers: what a used device needs before the till or the web sells it.

Tests written before USED-READY-01 priced a used device and expected the web (or the till) to sell it. Since then a
used device is sold at the till once its inspection is APPROVED, and online once it is also published and has a real
photo. These helpers take a device through those steps with the real services — never by writing the columns — so
those tests keep testing what they were about. The rule itself is tested in `test_used_ready`.
"""
from __future__ import annotations

PHOTO_URL = '/api/storefront/images/9001'


def real_photo(unit, actor=None, *, view='front', attested=True, verified=True, **fields):
    """
    UNIT-PHOTO-02 — one REAL photo of `unit`: its file an own photo of the unit's company (a `StorefrontImage` row, no
    pixels: nothing here reads them), licence verified, and the photo attested — what `unit_media.public_photos_q`
    counts. `attested`/`verified` False build the cases the rule refuses. The first photo of a device is its primary.
    """
    import uuid

    from django.utils import timezone

    from .models import StorefrontImage, UnitPhoto

    now = timezone.now()
    public_id = uuid.uuid4().hex
    stored = StorefrontImage.objects.create(
        company_id=unit.company_id, public_id=public_id, storage_key=f'test/unit-photo/{public_id}.webp',
        mime_type='image/webp', byte_size=1, width=10, height=10, source_kind=StorefrontImage.SOURCE_OWN_PHOTO,
        license_verified_at=now if verified else None, license_verified_by=actor if verified else None,
    )
    fields.setdefault('is_primary', not UnitPhoto.objects.filter(unit_id=unit.pk, is_primary=True).exists())
    return UnitPhoto.objects.create(
        company_id=unit.company_id, unit_id=unit.pk, image_url=stored.url, width=10, height=10, view=view,
        attested_at=now if attested else None, attested_by=actor if attested else None, **fields,
    )


def approve(unit, actor=None):
    """Approve the inspection of a used device still in the shop's hands (idempotent)."""
    from . import used_unit_services
    from .models import StockUnit

    fresh = StockUnit.objects.select_related('product').get(pk=unit.pk)
    if (used_unit_services.is_used(fresh) and fresh.status in used_unit_services.IN_HAND
            and fresh.inspection_status != StockUnit.Inspection.APPROVED):
        used_unit_services.set_inspection(fresh, decision='approved', actor=actor)
    return StockUnit.objects.get(pk=unit.pk)


def approve_all(units, actor=None):
    return [approve(unit, actor) for unit in units]


def ready_used(unit, actor=None, **fields):
    """
    `update_unit(**fields)`, then — for a used device in the shop's hands — approve it, give it one REAL photo
    (attested and verified, UNIT-PHOTO-02) and, when it has a usable price, publish it: what the web needs.
    Idempotent; returns the fresh row.
    """
    from . import stock_unit_services, used_unit_services
    from .models import StockUnit

    if fields:
        stock_unit_services.update_unit(unit, **fields)
    fresh = approve(unit, actor)
    if not (used_unit_services.is_used(fresh) and fresh.status in used_unit_services.IN_HAND):
        return fresh
    if not used_unit_services.real_photos(fresh):
        real_photo(fresh, actor)
    if stock_unit_services.has_usable_price(fresh.sale_price) and not fresh.published_online:
        used_unit_services.set_publication(fresh, published=True, actor=actor)
    return StockUnit.objects.get(pk=unit.pk)
