"""
USED-READY-01 — test helpers: what a used device needs before the till or the web sells it.

Tests written before USED-READY-01 priced a used device and expected the web (or the till) to sell it. Since then a
used device is sold at the till once its inspection is APPROVED, and online once it is also published and has a real
photo. These helpers take a device through those steps with the real services — never by writing the columns — so
those tests keep testing what they were about. The rule itself is tested in `test_used_ready`.
"""
from __future__ import annotations

PHOTO_URL = '/api/storefront/images/9001'


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
    `update_unit(**fields)`, then — for a used device in the shop's hands — approve it, give it one photo and, when
    it has a usable price, publish it: what the web needs. Idempotent; returns the fresh row.
    """
    from . import stock_unit_services, used_unit_services
    from .models import StockUnit, UnitPhoto

    if fields:
        stock_unit_services.update_unit(unit, **fields)
    fresh = approve(unit, actor)
    if not (used_unit_services.is_used(fresh) and fresh.status in used_unit_services.IN_HAND):
        return fresh
    if not fresh.photos.exists():
        UnitPhoto.objects.create(company_id=fresh.company_id, unit=fresh, image_url=PHOTO_URL, width=10, height=10)
    if stock_unit_services.has_usable_price(fresh.sale_price) and not fresh.published_online:
        used_unit_services.set_publication(fresh, published=True, actor=actor)
    return StockUnit.objects.get(pk=unit.pk)
