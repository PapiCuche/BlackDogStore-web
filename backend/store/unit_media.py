"""
DC2 · UNIT-PHOTO-02 — the PUBLIC photos of one used device (a seminuevo shown as it is).

The same pipeline as a product gallery: the pixels are a `StorefrontImage` (re-encoded from pixels, owned by one
company, swept when nothing shows it), and `UnitPhoto` says which device shows it, which side it is (`view`, and what
a `defect` photo shows), in what order and which one is the primary. NEVER the private evidence store: these photos
are for anybody to see — once somebody confirmed they are of THIS device.

THE OWNER'S RULE. A photo of a used device is a photo of the EXACT device; never a generic picture presented as the
device. So a photo counts (for the web, for publishing, for a cart or an order line) only when it is PUBLIC:

    attested    `attested_at` set — a person with `inventory.units.inspect` said «Confirmo que esta foto es de este
                equipo exacto» (the inspector, who has the device in hand);
    own photo   its file is a managed `StorefrontImage` of the SAME company, `source_kind = own_photo`, licence
                verified. Every upload here is stored as an own photo, unverified; the ATTESTATION is the verification
                (it sets `own_photo` + verified by the same person, audited, through `finish_media.apply_provenance`,
                the provenance flow every slot shares). Withdrawing the attestation withdraws the verification.

`public_photos_q` is the ONE definition of that rule as a filter; `is_public` reads it for one row. A replaced file is
not the one somebody confirmed: its attestation and its verification start empty.

ONE WRITE AT A TIME PER DEVICE. Every write locks the device's row before it reads its photos, so two people working
at once neither break the order nor leave two primaries (the partial unique index would refuse it anyway). Only the
device is locked, as before (DC2): nothing here takes a shelf row.

The company always comes from the caller's resolved device; a photo of another device or company answers like a
missing one. Background removal is NOT here (the owner approves a tool first).
"""
from __future__ import annotations

from django.conf import settings
from django.db import transaction
from django.db.models import Exists, Max, OuterRef, Prefetch, Q
from django.db.models.functions import Right
from django.utils import timezone

from . import storefront_media
from .models import AdminAuditLog, Product, StockUnit, StorefrontImage, UnitPhoto
from .product_media import clean_alt_text

#: Every managed address starts so (`StorefrontImage.url`); the 32 characters after it are the file's public id.
ADDRESS_PREFIX = '/api/storefront/images/'
PUBLIC_ID_LENGTH = 32
VIEWS = tuple(value for value, _label in UnitPhoto.VIEW_CHOICES)
VIEW_LABELS = dict(UnitPhoto.VIEW_CHOICES)
DEFECT_NOTE_MAX = 160
#: What every upload says about its file: the shop's own photo. Verified only by the attestation.
OWN_PHOTO = {'source_kind': StorefrontImage.SOURCE_OWN_PHOTO, 'license': '', 'source_url': '', 'attribution': ''}


class UnitMediaError(Exception):
    """A refusal the person editing the device can read and fix."""

    def __init__(self, message, *, field=None, code='invalid', status=400):
        super().__init__(message)
        self.field = field
        self.code = code
        self.status = status

    def body(self) -> dict:
        data = {'detail': str(self), 'code': self.code}
        if self.field:
            data['field'] = self.field
        return data


class UnitPhotoGone(UnitMediaError):
    """The photo stopped existing (or never was this device's): answered like a missing one."""

    def __init__(self):
        super().__init__('Esa foto ya no está entre las de este equipo.', code='not_found', status=404)


def max_per_unit() -> int:
    return int(getattr(settings, 'UNIT_PHOTO_MAX_PER_UNIT', 12))


# ---------------------------------------------------------------------------
# What is public
# ---------------------------------------------------------------------------

def _verified_file() -> Exists:
    """On `UnitPhoto` rows: its file is an own photo of the photo's company, with its licence verified."""
    return Exists(StorefrontImage.objects.filter(
        company_id=OuterRef('company_id'), public_id=Right(OuterRef('image_url'), PUBLIC_ID_LENGTH),
        source_kind=StorefrontImage.SOURCE_OWN_PHOTO, license_verified_at__isnull=False,
    ))


def public_photos_q() -> Q:
    """THE rule, as a filter on `UnitPhoto`: attested, a managed address, and its file an own verified photo."""
    return Q(attested_at__isnull=False, image_url__startswith=ADDRESS_PREFIX) & Q(_verified_file())


def with_states(rows):
    """`rows` with `file_verified` read in the same query (no query per photo when the panel or a card reads it)."""
    return rows.annotate(file_verified=_verified_file())


def photos_prefetch() -> Prefetch:
    """Every photo of each device, in the panel's order, with its file's state: the staff's reading."""
    return Prefetch('photos', queryset=with_states(UnitPhoto.objects.select_related('attested_by')))


def public_order(rows):
    """The order the shop shows: the primary first, then the order the shop chose."""
    return rows.order_by('-is_primary', 'sort_order', 'pk')


def public_photos_prefetch() -> Prefetch:
    """Only the PUBLIC photos of each device, primary first: what a card or a model page reads."""
    return Prefetch('photos', queryset=public_order(with_states(UnitPhoto.objects.filter(public_photos_q()))))


def file_verified(photo: UnitPhoto) -> bool:
    found = getattr(photo, 'file_verified', None)
    if found is not None:
        return bool(found)
    return UnitPhoto.objects.filter(pk=photo.pk).filter(Q(_verified_file())).exists()


def is_public(photo: UnitPhoto) -> bool:
    """The rule of `public_photos_q`, for one row (its annotation when it was read with `with_states`)."""
    return (photo.attested_at is not None and str(photo.image_url or '').startswith(ADDRESS_PREFIX)
            and file_verified(photo))


def public_photos(unit) -> list[UnitPhoto]:
    """The public photos of THIS device, primary first. From the prefetch when there is one."""
    cache = getattr(unit, '_prefetched_objects_cache', {})
    if 'photos' in cache:
        rows = [photo for photo in cache['photos'] if is_public(photo)]
        return sorted(rows, key=lambda photo: (not photo.is_primary, photo.sort_order, photo.pk))
    return list(public_order(with_states(unit.photos.filter(public_photos_q()))))


def view_label(photo: UnitPhoto) -> str:
    """«Frontal», «Posterior», «Defecto: rayón en la esquina»…"""
    label = VIEW_LABELS.get(photo.view, VIEW_LABELS[UnitPhoto.VIEW_OTHER])
    if photo.view == UnitPhoto.VIEW_DEFECT and photo.defect_note:
        return f'{label}: {photo.defect_note}'
    return label


def public_payload(photo: UnitPhoto) -> dict:
    """What the shop sees of one photo: an allowlist. Nothing of who attested it or where the file came from."""
    return {
        'url': photo.image_url,
        'alt_text': photo.alt_text,
        'width': photo.width,
        'height': photo.height,
        'view': photo.view,
        'view_label': view_label(photo),
        'defect_note': photo.defect_note,
        'is_primary': photo.is_primary,
    }


def primary_photos(unit_ids) -> dict[int, dict]:
    """
    Device id → the public payload of its first public photo (the primary when it is public), for a cart or an order
    line. A device without a public photo has no entry: the line says «Sin fotos de este equipo», never anything else.
    One query whatever the number of devices.
    """
    ids = {pk for pk in unit_ids if pk}
    if not ids:
        return {}
    found: dict[int, dict] = {}
    rows = UnitPhoto.objects.filter(unit_id__in=ids).filter(public_photos_q()).order_by(
        'unit_id', '-is_primary', 'sort_order', 'pk')
    for photo in rows:
        found.setdefault(photo.unit_id, public_payload(photo))
    return found


def _person(user) -> str | None:
    if user is None:
        return None
    return (user.get_full_name() or '').strip() or user.get_username()


def payload(photo: UnitPhoto) -> dict:
    """What the panel sees: the public fields, the attestation and whether the shop shows it. Never a storage key."""
    return {
        'id': photo.pk,
        'url': photo.image_url,
        'alt_text': photo.alt_text,
        'width': photo.width,
        'height': photo.height,
        'view': photo.view,
        'view_label': view_label(photo),
        'defect_note': photo.defect_note,
        'sort_order': photo.sort_order,
        'is_primary': photo.is_primary,
        'attested': photo.attested_at is not None,
        'attested_at': photo.attested_at.isoformat() if photo.attested_at else None,
        'attested_by': _person(photo.attested_by) if photo.attested_at else None,
        'public': is_public(photo),
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def clean_view(value) -> str:
    """One of `VIEWS`. Absent: `other` (what a photo uploaded before UNIT-PHOTO-02 is)."""
    if value is None or value == '':
        return UnitPhoto.VIEW_OTHER
    if not isinstance(value, str) or value not in VIEWS:
        raise UnitMediaError('Elige qué lado del equipo muestra la foto.', field='view')
    return value


def clean_defect_note(value, view: str) -> str:
    """A `defect` photo says what it shows; any other view carries none (it is cleared)."""
    if value is not None and not isinstance(value, str):
        raise UnitMediaError('Describe el defecto con un texto.', field='defect_note')
    note = ' '.join((value or '').split())
    if view != UnitPhoto.VIEW_DEFECT:
        return ''
    if not note:
        raise UnitMediaError('Una foto de un defecto dice qué defecto muestra.', field='defect_note')
    if len(note) > DEFECT_NOTE_MAX:
        raise UnitMediaError(f'La descripción del defecto admite hasta {DEFECT_NOTE_MAX} caracteres.',
                             field='defect_note')
    return note


def _require_used(unit) -> None:
    if Product.objects.filter(pk=unit.product_id, condition_line=Product.CONDITION_USED).exists():
        return
    from .stock_unit_services import NOT_USED_LINE

    raise UnitMediaError('Sólo los equipos seminuevos llevan fotos propias del equipo.', code=NOT_USED_LINE,
                         status=409)


def _too_many() -> UnitMediaError:
    return UnitMediaError(f'Un equipo admite hasta {max_per_unit()} fotos. Quita una antes de añadir otra.',
                          field='file', code='too_many')


def _photo_id(value) -> int:
    if isinstance(value, bool):
        raise UnitPhotoGone()
    text = str(value if value is not None else '').strip()
    if not (text.isascii() and text.isdigit()) or not 0 < int(text) <= 2_147_483_647:
        raise UnitPhotoGone()
    return int(text)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _locked(unit) -> StockUnit:
    return StockUnit.objects.select_for_update().get(pk=unit.pk, company_id=unit.company_id)


def _current(locked, photo_id) -> UnitPhoto:
    found = UnitPhoto.objects.select_for_update().filter(
        unit=locked, company_id=locked.company_id, pk=_photo_id(photo_id)).first()
    if found is None:
        raise UnitPhotoGone()
    return found


def _audit(*, action, unit, actor, request, **metadata):
    AdminAuditLog.log(
        actor=actor, action=action, target_type='stock_unit', target_id=unit.pk,
        metadata={'product_id': unit.product_id, **metadata}, request=request, company=unit.company,
    )


def _stored(photo) -> StorefrontImage | None:
    public_id = storefront_media.managed_public_id(photo.image_url)
    if public_id is None:
        return None
    return (StorefrontImage.objects.select_for_update()
            .filter(public_id=public_id, company_id=photo.company_id).first())


def add_photo(*, unit, actor, uploaded, alt_text='', view=None, defect_note='', request=None) -> UnitPhoto:
    """
    Validates everything it can BEFORE storing the file, stores it as the shop's own photo (unverified) and appends it
    to the device's photos. The first photo of a device is its primary. Never attested here.
    """
    _require_used(unit)
    alt = clean_alt_text(alt_text)
    view = clean_view(view)
    note = clean_defect_note(defect_note, view)
    if UnitPhoto.objects.filter(unit_id=unit.pk).count() >= max_per_unit():
        raise _too_many()          # checked again under the lock
    raw = storefront_media.read_upload(uploaded)
    stored = storefront_media.store(company=unit.company, actor=actor, raw=raw, request=request,
                                    provenance=dict(OWN_PHOTO))
    try:
        with transaction.atomic():
            locked = _locked(unit)
            existing = list(UnitPhoto.objects.filter(unit=locked))
            if len(existing) >= max_per_unit():
                raise _too_many()
            storefront_media.claim(stored.url, company=locked.company, field='file')
            last = UnitPhoto.objects.filter(unit=locked).aggregate(last=Max('sort_order'))['last']
            photo = UnitPhoto.objects.create(
                company=locked.company, unit=locked, image_url=stored.url, alt_text=alt,
                width=stored.width, height=stored.height, uploaded_by=actor, view=view, defect_note=note,
                sort_order=0 if last is None else last + 1,
                is_primary=not any(item.is_primary for item in existing),
            )
            _audit(action='unit_photo_added', unit=locked, actor=actor, request=request, photo_id=photo.pk,
                   address=stored.url, view=view, is_primary=photo.is_primary)
            return photo
    except Exception:
        storefront_media.release([stored.url], company=unit.company, actor=actor, request=request,
                                 reason='never_placed')
        raise


@transaction.atomic
def update_photo(*, unit, photo_id, actor, view=None, defect_note=None, alt_text=None, is_primary=None,
                 request=None) -> UnitPhoto:
    """Its side (and what a defect photo shows), its alternative text, or making it the primary. Audited."""
    _require_used(unit)
    locked = _locked(unit)
    photo = _current(locked, photo_id)
    if is_primary is not None and not isinstance(is_primary, bool):
        raise UnitMediaError('Indica si la foto es la principal.', field='is_primary')
    if is_primary is False and photo.is_primary:
        raise UnitMediaError('Para cambiar la foto principal, elige otra como principal.', field='is_primary')

    changed = {}
    if view is not None or defect_note is not None:
        chosen = clean_view(view) if view is not None else photo.view
        note = clean_defect_note(defect_note if defect_note is not None else photo.defect_note, chosen)
        if chosen != photo.view:
            changed['view'] = {'old': photo.view, 'new': chosen}
        if note != photo.defect_note:
            changed['defect_note'] = {'old': photo.defect_note, 'new': note}
        photo.view, photo.defect_note = chosen, note
    if alt_text is not None:
        alt = clean_alt_text(alt_text)
        if alt != photo.alt_text:
            changed['alt_text'] = {'old': photo.alt_text, 'new': alt}
            photo.alt_text = alt
    if changed:
        photo.save(update_fields=['view', 'defect_note', 'alt_text'])
        _audit(action='unit_photo_updated', unit=locked, actor=actor, request=request, photo_id=photo.pk, **changed)

    if is_primary is True and not photo.is_primary:
        previous = UnitPhoto.objects.filter(unit=locked, is_primary=True).values_list('pk', flat=True).first()
        UnitPhoto.objects.filter(unit=locked, is_primary=True).update(is_primary=False)
        photo.is_primary = True
        photo.save(update_fields=['is_primary'])
        _audit(action='unit_photo_primary_changed', unit=locked, actor=actor, request=request, photo_id=photo.pk,
               previous_photo_id=previous)
    return photo


@transaction.atomic
def reorder(*, unit, order, actor, request=None) -> list[UnitPhoto]:
    """`order` names every photo of the device once: their new order. The primary stays the primary."""
    _require_used(unit)
    locked = _locked(unit)
    current = {photo.pk: photo for photo in UnitPhoto.objects.filter(unit=locked).select_for_update()}
    if (
        not current
        or not isinstance(order, list)
        or any(isinstance(item, bool) or not isinstance(item, int) for item in order)
        or len(order) != len(set(order))
        or set(order) != set(current)
    ):
        raise UnitMediaError('El orden debe nombrar, una vez, cada foto de este equipo.', field='order')
    for position, photo_id in enumerate(order):
        photo = current[photo_id]
        if photo.sort_order != position:
            photo.sort_order = position
            photo.save(update_fields=['sort_order'])
    _audit(action='unit_photos_reordered', unit=locked, actor=actor, request=request, order=order)
    return [current[photo_id] for photo_id in order]


def replace_photo(*, unit, photo_id, actor, uploaded, request=None) -> UnitPhoto:
    """
    A new FILE for a photo that keeps its PLACE: the same row (id, order, primary, side, defect note, alternative
    text). The new file is not the one somebody confirmed: its attestation starts empty (and so it is not public
    until attested again). The old file goes only if nothing else shows it. A full device may still replace.
    """
    _require_used(unit)
    number = _photo_id(photo_id)
    if not UnitPhoto.objects.filter(unit_id=unit.pk, company_id=unit.company_id, pk=number).exists():
        raise UnitPhotoGone()
    raw = storefront_media.read_upload(uploaded)
    stored = storefront_media.store(company=unit.company, actor=actor, raw=raw, request=request,
                                    provenance=dict(OWN_PHOTO))
    try:
        with transaction.atomic():
            locked = _locked(unit)
            photo = _current(locked, number)
            storefront_media.claim(stored.url, company=locked.company, field='file')
            previous_address, was_attested = photo.image_url, photo.attested_at is not None
            photo.image_url, photo.width, photo.height = stored.url, stored.width, stored.height
            photo.uploaded_by = actor
            photo.attested_by, photo.attested_at = None, None
            photo.save(update_fields=['image_url', 'width', 'height', 'uploaded_by', 'attested_by', 'attested_at'])
            _audit(action='unit_photo_replaced', unit=locked, actor=actor, request=request, photo_id=photo.pk,
                   previous_address=previous_address, address=stored.url, was_attested=was_attested,
                   is_primary=photo.is_primary, sort_order=photo.sort_order)
            storefront_media.release([previous_address], company=locked.company, actor=actor, request=request,
                                     reason='replaced')
            return photo
    except Exception:
        storefront_media.release([stored.url], company=unit.company, actor=actor, request=request,
                                 reason='never_placed')
        raise


@transaction.atomic
def remove_photo(*, actor, unit=None, photo=None, photo_id=None, request=None) -> None:
    """Removes one photo; when it was the primary, the next by order takes its place. Its file goes if unused."""
    unit = unit if unit is not None else photo.unit
    locked = _locked(unit)
    photo = _current(locked, photo_id if photo_id is not None else photo.pk)
    address, was_primary, number = photo.image_url, photo.is_primary, photo.pk
    photo.delete()
    successor = None
    if was_primary:
        successor = UnitPhoto.objects.filter(unit=locked).order_by('sort_order', 'pk').first()
        if successor is not None:
            successor.is_primary = True
            successor.save(update_fields=['is_primary'])
    _audit(action='unit_photo_removed', unit=locked, actor=actor, request=request, photo_id=number,
           address=address, was_primary=was_primary, new_primary_id=successor.pk if successor else None)
    storefront_media.release([address], company=locked.company, actor=actor, request=request)


@transaction.atomic
def attest(*, unit, photo_id, attested, actor, request=None) -> UnitPhoto:
    """
    «Confirmo que esta foto es de este equipo exacto» — or the withdrawal of it.

    Attesting records who and when on the photo AND verifies its file as the shop's own photo of the same company
    (`finish_media.apply_provenance`: `own_photo`, licence verified by this person), both audited with the company.
    Withdrawing clears both: the photo leaves the shop at once. Saying what already is changes nothing.
    """
    from .finish_media import apply_provenance

    if not isinstance(attested, bool):
        raise UnitMediaError('Indica si confirmas que la foto es de este equipo exacto.', field='attested')
    _require_used(unit)
    locked = _locked(unit)
    photo = _current(locked, photo_id)
    stored = _stored(photo)
    if attested and stored is None:
        raise UnitMediaError('Esta foto no tiene un archivo subido por la tienda: súbela de nuevo para confirmarla.',
                             field='file', code='no_file', status=409)

    def audit(action, **metadata):
        _audit(action=action, unit=locked, actor=actor, request=request, photo_id=photo.pk, **metadata)

    if attested:
        apply_provenance(stored, actor=actor, provenance=dict(OWN_PHOTO), license_verified=True, audit=audit)
        if photo.attested_at is None:
            photo.attested_by = actor if getattr(actor, 'pk', None) else None
            photo.attested_at = timezone.now()
            photo.save(update_fields=['attested_by', 'attested_at'])
            audit('unit_photo_attested', public_id=stored.public_id, view=photo.view)
        return photo
    if stored is not None:
        apply_provenance(stored, actor=actor, license_verified=False, audit=audit)
    if photo.attested_at is not None:
        photo.attested_by, photo.attested_at = None, None
        photo.save(update_fields=['attested_by', 'attested_at'])
        audit('unit_photo_attestation_withdrawn', view=photo.view)
    return photo


# ---------------------------------------------------------------------------
# The deploy rehearsal's reading
# ---------------------------------------------------------------------------

def report(company=None) -> dict:
    """
    Read-only counts for the release rehearsal (`manage.py unit_photo_report`), of one company or of every company
    together: how many published used devices IN THE SHOP'S HANDS the web stops offering because none of their photos
    is public yet, and how many photos wait for their attestation. Counts only: no identifier, no amount.
    """
    from .used_unit_services import IN_HAND

    used = StockUnit.objects.filter(product__condition_line=Product.CONDITION_USED)
    photos = UnitPhoto.objects.filter(unit__product__condition_line=Product.CONDITION_USED)
    if company is not None:
        used, photos = used.filter(company=company), photos.filter(company=company)
    published = used.filter(published_online=True, status__in=IN_HAND)
    public = Exists(UnitPhoto.objects.filter(unit_id=OuterRef('pk')).filter(public_photos_q()))
    any_photo = Exists(UnitPhoto.objects.filter(unit_id=OuterRef('pk')))
    return {
        'used_units': used.count(),
        'published_units': published.count(),
        'published_with_public_photo': published.filter(public).count(),
        'published_leaving_the_web': published.filter(any_photo).exclude(public).count(),
        'published_without_any_photo': published.exclude(any_photo).count(),
        'photos': photos.count(),
        'photos_public': photos.filter(public_photos_q()).count(),
        'photos_waiting_attestation': photos.filter(attested_at__isnull=True).count(),
    }
