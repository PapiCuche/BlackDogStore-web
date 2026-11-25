"""
USED-READY-01 — a used device is sold as THAT device, and only once somebody checked it.

The owner's rule: each used device (seminuevo) is one unit with its own data, real photos, warranty, inspection and
publication; the shop sells the exact device, never presents a generic photo as that device, and a device pending
inspection (or received as a trade-in) is not stock for sale online before it is inspected.

    WHERE A DEVICE OF THE USED LINE MAY BE SOLD

        online   AVAILABLE · its own price > 0 · inspection APPROVED · published (`published_online`) · ≥ 1 real photo
                 (UNIT-PHOTO-02: attested as THIS device and its file an own photo, verified — `unit_media`)
                 — plus what every device needs already (dispatch branch, variant and model published).
        at the till   inspection APPROVED (and its own price, as before). Publication is not asked: the buyer holds
                 the device in the shop.

`web_ready_q` is the ONE definition of the online rule, used by every query that offers or counts devices for the web
(`device_catalog.offered_units`, the cart's holds and their pools); `web_problems` says the same thing about one
device, with every reason, for the panel. Photos are read at query time: a published device whose last photo is
deleted leaves the web at once, without anybody unpublishing it.

INSPECTION IS NOT A STATUS. It never touches `BranchStock.quantity == count(AVAILABLE)` (docs/adr-trade-in.md §3.2):
a pending device is on the shelf, counted, and simply not sold.

AUTHORITY (checked by the views; these functions trust their caller):
    inspection   `inventory.units.inspect` — not who received the device (`inventory.adjust`).
    publication  `products.manage` — the catalogue's decision, like the device's price.
Every decision is audited with the company, without amounts or identifiers.
"""
from __future__ import annotations

from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from .models import AdminAuditLog, Product, StockUnit, UnitPhoto
from .stock_unit_services import NOT_USED_LINE, StockUnitError, has_usable_price

MAX_NOTE = 300
#: The 409 of a publication refused: `reasons` says every missing piece.
NOT_READY = 'not_ready'
NOT_IN_STOCK = 'not_in_stock'
#: The 409 of every internal path that would sell a used device without an approved inspection.
NOT_INSPECTED = 'unit_not_inspected'
#: USED-READY-01 — the minimum of REAL photos of the device itself. UNIT-PHOTO-02: a photo is real only when it is
#: PUBLIC (`unit_media.public_photos_q`: attested as THIS device, its file an own photo of the company, verified).
#: `real_photos` / `real_photos_q` are the only two places that read photos for this rule.
MIN_REAL_PHOTOS = 1
#: The device is still the shop's: it may be inspected, published or unpublished.
IN_HAND = (StockUnit.Status.AVAILABLE, StockUnit.Status.RESERVED, StockUnit.Status.IN_TRANSIT)
DECISIONS = (StockUnit.Inspection.APPROVED, StockUnit.Inspection.REJECTED)

#: What the till says of a used device that was not approved (`pos_services.unit_prices`).
TILL_PENDING = ('este seminuevo todavía no tiene la inspección aprobada: no se vende hasta que '
                'un usuario autorizado lo inspeccione.')
TILL_REJECTED = 'este seminuevo tiene la inspección rechazada: no se vende.'


class NotReadyToPublish(StockUnitError):
    """`publish` refused: `reasons` lists every code (`no_price`, `not_inspected`, …)."""

    def __init__(self, reasons):
        words = ', '.join(REASON_TEXT.get(code, code) for code in reasons)
        super().__init__(f'No se puede publicar este equipo: {words}.', code=NOT_READY, status=409)
        self.reasons = list(reasons)


REASON_TEXT = {
    'not_in_stock': 'ya no está en la tienda (vendido, dado de baja o anulado)',
    'no_price': 'no tiene precio de venta',
    'not_inspected': 'su inspección está pendiente',
    'inspection_rejected': 'su inspección fue rechazada',
    'no_photo': 'no tiene fotos reales del equipo',
    # UNIT-PHOTO-02 — it has photos, but nobody confirmed that any of them is of THIS device.
    'photo_not_attested': 'ninguna de sus fotos está confirmada como de este equipo exacto',
}


def is_used(unit) -> bool:
    return unit.product.condition_line == Product.CONDITION_USED


def real_photos_q() -> Exists:
    """As a filter: the device has its minimum of real photos — one PUBLIC `UnitPhoto` (`unit_media`)."""
    from .unit_media import public_photos_q

    return Exists(UnitPhoto.objects.filter(unit_id=OuterRef('pk')).filter(public_photos_q()))


def real_photos(unit) -> int:
    """How many real photos of THIS device count for the rule (the same as `real_photos_q`)."""
    from .unit_media import public_photos

    return len(public_photos(unit))


def _has_any_photo(unit) -> bool:
    cache = getattr(unit, '_prefetched_objects_cache', {})
    if 'photos' in cache:
        return bool(cache['photos'])
    return UnitPhoto.objects.filter(unit_id=unit.pk).exists()


def web_ready_q() -> Q:
    """The used devices the web may offer, as a filter on `StockUnit` (status and branch are the caller's)."""
    return Q(sale_price__gt=0, inspection_status=StockUnit.Inspection.APPROVED, published_online=True) & real_photos_q()


def till_sellable_q() -> Q:
    """
    USED-UNIT-OWN-GATE-01 — the used devices a sale may take, as a filter on `StockUnit` (status and branch are the
    caller's): its OWN price > 0 and its inspection APPROVED. Each device answers for itself: a sibling without a
    price is simply not in the set, and blocks nothing else of its variant.
    """
    return Q(sale_price__gt=0, inspection_status=StockUnit.Inspection.APPROVED)


def till_sellable_units(branch, product):
    """The AVAILABLE devices of a used `product` on `branch`'s shelf that a sale may take (`till_sellable_q`)."""
    return StockUnit.objects.filter(company_id=branch.company_id, branch=branch, product=product,
                                    status=StockUnit.Status.AVAILABLE).filter(till_sellable_q())


#: USED-UNIT-OWN-GATE-01 — the 409 of a used device without its own price on a path that would sell or hand it over.
NO_PRICE = 'no_price'


def sale_refusal(unit) -> str:
    """
    THE ONE PREDICATE of «may this used device leave in a sale?» — '' when it may, else the code: `NOT_INSPECTED`
    (inspection not approved) or `NO_PRICE` (no price of its own > 0). The same as `till_sellable_q`, for one device in
    hand: the till, the payment and the hand-over of an order ask it, so the three agree. Publication and photos are
    the shop window's, not the sale's. A new device: always ''.
    """
    if not is_used(unit):
        return ''
    if unit.inspection_status != StockUnit.Inspection.APPROVED:
        return NOT_INSPECTED
    if not has_usable_price(unit.sale_price):
        return NO_PRICE
    return ''


def payment_refusal(unit) -> str:
    """
    '' when a web payment may still sell this used device (`sale_refusal`); otherwise why not, as the end of a
    sentence and without amounts. A device already bound to a checked-out order is left to its payment
    (`release_withdrawn`), which asks this.
    """
    return {NOT_INSPECTED: 'ya no tiene la inspección aprobada',
            NO_PRICE: 'ya no tiene precio de venta'}.get(sale_refusal(unit), '')


def offerable_q() -> Q:
    """Devices of either line the web may offer: a new one as always, a used one only when `web_ready_q`."""
    return Q(product__condition_line=Product.CONDITION_NEW) | (
        Q(product__condition_line=Product.CONDITION_USED) & web_ready_q())


def _inspection_problem(unit) -> list[str]:
    if unit.inspection_status == StockUnit.Inspection.REJECTED:
        return ['inspection_rejected']
    if unit.inspection_status != StockUnit.Inspection.APPROVED:
        return ['not_inspected']
    return []


def web_problems(unit) -> list[str]:
    """
    Why the web does not offer THIS used device, by its own data (status, branch and catalogue are the caller's).
    Empty for a new device and for a used one that is ready. Codes, never amounts.
    """
    if not is_used(unit):
        return []
    problems = []
    if not has_usable_price(unit.sale_price):
        problems.append('no_price')
    problems += _inspection_problem(unit)
    if not unit.published_online:
        problems.append('unit_not_published')
    if real_photos(unit) < MIN_REAL_PHOTOS:
        # UNIT-PHOTO-02: a clear reason — none at all, or none confirmed as THIS device.
        problems.append('photo_not_attested' if _has_any_photo(unit) else 'no_photo')
    return problems


def used_unit_publish_blockers(unit) -> list[str]:
    """
    THE mandatory controls before a used device is published: in the shop's hands, its own price > 0, an approved
    inspection and its minimum of real photos — UNIT-PHOTO-02: attested and verified (`no_photo` without any photo,
    `photo_not_attested` with photos nobody confirmed). Every code, in that order.
    """
    problems = [] if unit.status in IN_HAND else [NOT_IN_STOCK]
    return problems + [code for code in web_problems(unit) if code != 'unit_not_published']


publish_problems = used_unit_publish_blockers


def till_refusal(unit) -> str:
    """'' when the till may sell this device; otherwise the sentence (without the product's name)."""
    if not is_used(unit) or unit.inspection_status == StockUnit.Inspection.APPROVED:
        return ''
    return TILL_REJECTED if unit.inspection_status == StockUnit.Inspection.REJECTED else TILL_PENDING


def _locked_used(unit) -> StockUnit:
    # THE MODULE'S ONE LOCK ORDER (stock_unit_services): the shelf row first, the device second — a decision here may
    # release a web hold, which writes a Kardex line on that shelf.
    from .inventory_services import _locked_branch_stocks

    plain = StockUnit.objects.select_related('branch', 'product').get(pk=unit.pk)
    _locked_branch_stocks(plain.branch, [plain.product])
    locked = StockUnit.objects.select_for_update().get(pk=unit.pk)
    locked = StockUnit.objects.select_related('product', 'company').get(pk=locked.pk)
    if not is_used(locked):
        raise StockUnitError('Sólo los equipos seminuevos se inspeccionan y se publican uno por uno.',
                             code=NOT_USED_LINE)
    return locked


def _clean_note(value) -> str:
    if value is not None and not isinstance(value, str):
        raise StockUnitError('La nota de la inspección debe ser un texto.', field='note')
    note = ' '.join((value or '').split())
    if len(note) > MAX_NOTE:
        raise StockUnitError(f'La nota de la inspección admite hasta {MAX_NOTE} caracteres.', field='note')
    return note


@transaction.atomic
def set_inspection(unit, *, decision, note='', actor=None, request=None) -> StockUnit:
    """
    Approve or reject the inspection of ONE used device. A rejection needs a note (why) and takes the device off the
    web. Approving again after a rejection is allowed (it was repaired, or re-checked). Moves no stock.
    """
    if decision not in DECISIONS:
        raise StockUnitError('Indica si la inspección se aprueba o se rechaza.', field='decision')
    note = _clean_note(note)
    if decision == StockUnit.Inspection.REJECTED and not note:
        raise StockUnitError('Indica por qué se rechaza la inspección.', field='note')
    locked = _locked_used(unit)
    if locked.status not in IN_HAND:
        raise StockUnitError(f'El equipo {locked.identifier} ya no está en la tienda: no se inspecciona.',
                             code=NOT_IN_STOCK, status=409)
    previous = locked.inspection_status
    was_published = locked.published_online
    now = timezone.now()
    locked.inspection_status = decision
    locked.inspection_note = note
    locked.inspected_at = now
    locked.inspected_by = actor if getattr(actor, 'pk', None) else None
    fields = ['inspection_status', 'inspection_note', 'inspected_at', 'inspected_by']
    if decision == StockUnit.Inspection.REJECTED and was_published:
        locked.published_online, locked.published_at, locked.published_by = False, None, None
        fields += ['published_online', 'published_at', 'published_by']
    locked.save(update_fields=[*fields, 'updated_at'])
    released = decision == StockUnit.Inspection.REJECTED and _release_web_hold(locked)
    AdminAuditLog.log(
        actor=actor, action='stock_unit_inspection', target_type='stock_unit', target_id=locked.pk,
        metadata={'product_id': locked.product_id, 'branch_id': locked.branch_id, 'decision': decision,
                  'previous': previous, 'unpublished': bool(was_published and not locked.published_online),
                  'released_web_hold': bool(released), **({'note': note[:120]} if note else {})},
        request=request, company=locked.company,
    )
    return StockUnit.objects.select_related('product', 'company').get(pk=locked.pk)


def _release_web_hold(unit) -> bool:
    """A cart's hold of a device just withdrawn goes back to the shelf (`unit_hold_services.release_withdrawn`)."""
    from . import unit_hold_services

    return unit_hold_services.release_withdrawn(unit)


@transaction.atomic
def set_publication(unit, *, published: bool, actor=None, request=None) -> StockUnit:
    """
    Put ONE used device on the web, or take it off. Publishing is refused (409, every reason) without a price, an
    approved inspection or a real photo, or once the device left the shop. Unpublishing is always possible.
    Saying what already is changes nothing and audits nothing.
    """
    if not isinstance(published, bool):
        raise StockUnitError('Indica si el equipo se publica o se retira de la tienda en línea.', field='published')
    locked = _locked_used(unit)
    if published:
        problems = used_unit_publish_blockers(locked)
        if problems:
            raise NotReadyToPublish(problems)
    if locked.published_online == published:
        return locked
    locked.published_online = published
    locked.published_at = timezone.now() if published else None
    locked.published_by = (actor if getattr(actor, 'pk', None) else None) if published else None
    locked.save(update_fields=['published_online', 'published_at', 'published_by', 'updated_at'])
    released = not published and _release_web_hold(locked)
    AdminAuditLog.log(
        actor=actor, action='stock_unit_published' if published else 'stock_unit_unpublished',
        target_type='stock_unit', target_id=locked.pk,
        metadata={'product_id': locked.product_id, 'branch_id': locked.branch_id,
                  **({} if published else {'released_web_hold': bool(released)})},
        request=request, company=locked.company,
    )
    return StockUnit.objects.select_related('product', 'company').get(pk=locked.pk)


def require_reinspection(unit_id, *, why: str, actor=None, request=None) -> bool:
    """
    A used device that comes BACK after it left (a return): it is checked again before it is sold again. Pending,
    unpublished. Called inside the caller's transaction, after the device is back on a shelf. True when it changed.
    """
    unit = StockUnit.objects.select_related('product', 'company').get(pk=unit_id)
    if not is_used(unit):
        return False
    if unit.inspection_status == StockUnit.Inspection.PENDING and not unit.published_online:
        return False
    previous = unit.inspection_status
    StockUnit.objects.filter(pk=unit.pk).update(
        inspection_status=StockUnit.Inspection.PENDING, inspection_note='', inspected_at=None, inspected_by=None,
        published_online=False, published_at=None, published_by=None, updated_at=timezone.now())
    AdminAuditLog.log(
        actor=actor, action='stock_unit_inspection_reset', target_type='stock_unit', target_id=unit.pk,
        metadata={'product_id': unit.product_id, 'branch_id': unit.branch_id, 'previous': previous, 'why': why},
        request=request, company=unit.company,
    )
    return True
