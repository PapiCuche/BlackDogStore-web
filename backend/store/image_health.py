"""
IMG-HEALTH (IMG-GATE-01, read-only part) — what is wrong with the pictures of ONE company's catalogue.

    python manage.py report_image_health --company <slug> [--format csv|json] [--skip-storage]
    GET /api/admin/catalog/image-health/                                       products.view

READ-ONLY. It writes nothing, audits nothing and NEVER deletes: the owner decides each fix (owner: «no borrar fotos
comerciales en masa sin sustitutos preparados»). One row per problem, five classes:

  * `broken_file` — a managed address (`/api/storefront/images/<id>`) whose file row is not this company's (or does
    not exist), or whose object is gone from the storage (`evidence_storage.exists`; `storage_error` when the
    storage could not say). The shop shows a broken picture there.
  * `external_hotlink` — an address that is not an upload of the shop (another host, or a site path): its licence
    cannot be verified. The host, never the address (as `catalog_image_sources.report`).
  * `unverified_public` — a picture the shop STILL shows without a verified licence: a published model's cover and
    the pictures of a product WITHOUT a model (an accessory), which IMG-GATE-01 left as they were (decision of the
    owner). A device variant's or a finish's are not here: the shop no longer shows them unverified.
  * `shared_across_colours` — the same file shown for two colours of one model (finish pictures and the variants'
    own pictures of each colour): one of them presents another colour's photo.
  * `variant_without_photo` — a NEW device variant on sale online that shows «Imagen pendiente»
    (`device_catalog.variant_galleries`); `own_hidden` when it has pictures of its own that the gate hides (what the
    release rehearsal must count: they had a photo before IMG-GATE-01).

The catalogue's slots: a model's cover, a product's address (unless it is its gallery's primary, the same file), a
product's gallery and a finish. A fixed number of queries whatever the size of the catalogue, plus one storage
check per distinct managed file (skippable).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from . import evidence_storage, storefront_media
from .catalog_image_sources import host_of, is_external
from .models import (
    ModelFinishImage, Product, ProductImage, ProductModel, ProductVariantOption, StorefrontImage, VariantOption,
)

BROKEN, EXTERNAL, UNVERIFIED, SHARED, NO_PHOTO = (
    'broken_file', 'external_hotlink', 'unverified_public', 'shared_across_colours', 'variant_without_photo')
ISSUES = (BROKEN, EXTERNAL, UNVERIFIED, SHARED, NO_PHOTO)
COLUMNS = ('issue', 'slot', 'object_id', 'object_name', 'model_id', 'model_name', 'colour', 'host', 'public_id',
           'detail', 'admin_url')
#: How many rows the admin API returns (the counts are always the whole catalogue's; the command has no cap).
MAX_ROWS = 500

SLOT_COVER, SLOT_PRODUCT, SLOT_GALLERY, SLOT_FINISH, SLOT_MODEL = (
    'model_cover', 'product_image', 'product_gallery', 'finish', 'model')


@dataclass(frozen=True)
class _Ref:
    slot: str
    object_id: int
    object_name: str
    model_id: int | None
    address: str
    colour_id: int | None
    #: Shown by the shop as it is, without the licence gate (a published model's cover, a product without a model).
    ungated_public: bool


def _admin_url(slot, object_id) -> str:
    return (f'/admin/products/models/{object_id}' if slot in (SLOT_COVER, SLOT_FINISH, SLOT_MODEL)
            else f'/admin/products/{object_id}')


def _references(company) -> tuple[list[_Ref], dict, dict]:
    """Every picture address of the catalogue, by slot; and the models' names and the colours' labels."""
    from .finish_media import colour_attribute_ids
    from .tenancy import company_storefront_products

    models = {pk: (name, cover, published) for pk, name, cover, published in
              ProductModel.objects.filter(company=company)
              .values_list('pk', 'name', 'image_url', 'is_published_online')}
    on_sale = set(company_storefront_products(company).values_list('pk', flat=True))
    dimension = colour_attribute_ids(company.pk)
    colour_of = {}
    for product_id, option_id in (ProductVariantOption.objects.filter(company=company, attribute_id__in=dimension)
                                  .order_by('product_id', 'pk').values_list('product_id', 'option_id')):
        colour_of.setdefault(product_id, option_id)
    refs = []
    for pk, (name, cover, published) in models.items():
        if cover.strip():
            refs.append(_Ref(SLOT_COVER, pk, name, pk, cover.strip(), None, bool(published)))
    gallery: dict[int, set[str]] = defaultdict(set)
    products = {pk: (name, model_id) for pk, name, model_id in
                Product.objects.filter(company=company).values_list('pk', 'name', 'product_model_id')}
    for product_id, address in (ProductImage.objects.filter(company=company).order_by('product_id', 'sort_order', 'pk')
                                .values_list('product_id', 'image_url')):
        name, model_id = products.get(product_id, ('', None))
        gallery[product_id].add(address.strip())
        refs.append(_Ref(SLOT_GALLERY, product_id, name, model_id, address.strip(), colour_of.get(product_id),
                         model_id is None and product_id in on_sale))
    for pk, address in Product.objects.filter(company=company).exclude(image_url='').values_list('pk', 'image_url'):
        address = address.strip()
        if not address or address in gallery.get(pk, ()):
            continue    # its gallery's primary: the same file, already counted
        name, model_id = products[pk]
        refs.append(_Ref(SLOT_PRODUCT, pk, name, model_id, address, colour_of.get(pk),
                         model_id is None and pk in on_sale))
    for model_id, option_id, address in (ModelFinishImage.objects.filter(company=company)
                                         .order_by('model_id', 'color_option_id', 'sort_order', 'pk')
                                         .values_list('model_id', 'color_option_id', 'image_url')):
        refs.append(_Ref(SLOT_FINISH, model_id, models.get(model_id, ('',))[0], model_id, address.strip(), option_id,
                         False))
    colours = dict(VariantOption.objects.filter(company=company, pk__in={r.colour_id for r in refs} - {None})
                   .values_list('pk', 'label'))
    return refs, {pk: values[0] for pk, values in models.items()}, colours


def report(company, *, check_storage: bool = True) -> dict:
    """The rows and the summary of `company`'s catalogue. Read-only (see the module)."""
    from .device_catalog import variant_galleries

    refs, model_names, colours = _references(company)
    ids = {storefront_media.managed_public_id(ref.address) for ref in refs} - {None}
    files = {public_id: (key, kind, verified_at) for public_id, key, kind, verified_at in
             StorefrontImage.objects.filter(company=company, public_id__in=ids)
             .values_list('public_id', 'storage_key', 'source_kind', 'license_verified_at')} if ids else {}
    present: dict[str, bool | None] = {}

    def row(issue, ref_or_slot, *, object_id=None, object_name='', model_id=None, colour='', host='', public_id='',
            detail=''):
        if isinstance(ref_or_slot, _Ref):
            ref = ref_or_slot
            slot, object_id, object_name, model_id = ref.slot, ref.object_id, ref.object_name, ref.model_id
            colour = colour or colours.get(ref.colour_id, '')
        else:
            slot = ref_or_slot
        return {'issue': issue, 'slot': slot, 'object_id': object_id, 'object_name': object_name,
                'model_id': model_id, 'model_name': model_names.get(model_id, '') if model_id else '',
                'colour': colour, 'host': host, 'public_id': public_id, 'detail': detail,
                'admin_url': _admin_url(slot, object_id)}

    rows = []
    hosts: dict[str, int] = defaultdict(int)
    for ref in refs:
        public_id = storefront_media.managed_public_id(ref.address)
        if public_id is None:
            if is_external(ref.address):
                host = host_of(ref.address)
                hosts[host] += 1
                rows.append(row(EXTERNAL, ref, host=host))
                if ref.ungated_public:
                    rows.append(row(UNVERIFIED, ref, host=host, detail='external'))
            continue
        found = files.get(public_id)
        if found is None:
            rows.append(row(BROKEN, ref, public_id=public_id, detail='no_file_row'))
            continue
        key, kind, verified_at = found
        if check_storage:
            if public_id not in present:
                present[public_id] = evidence_storage.exists(key)
            if present[public_id] is not True:
                rows.append(row(BROKEN, ref, public_id=public_id,
                                detail='missing_object' if present[public_id] is False else 'storage_error'))
        if ref.ungated_public and verified_at is None:
            rows.append(row(UNVERIFIED, ref, public_id=public_id, detail=kind))

    # The same file for two colours of one model: finishes and the variants' own pictures, by file.
    by_file: dict[tuple[int, str, str], set[int]] = defaultdict(set)     # (model, public id, external address)
    for ref in refs:
        if ref.model_id is None or ref.colour_id is None or ref.slot == SLOT_COVER:
            continue
        public_id = storefront_media.managed_public_id(ref.address)
        by_file[(ref.model_id, public_id or '', '' if public_id else ref.address)].add(ref.colour_id)
    for (model_id, public_id, address), colour_ids in sorted(by_file.items()):
        if len(colour_ids) < 2:
            continue
        rows.append(row(SHARED, SLOT_MODEL, object_id=model_id, object_name=model_names.get(model_id, ''),
                        model_id=model_id, colour=', '.join(sorted(colours.get(c, str(c)) for c in colour_ids)),
                        public_id=public_id, host=host_of(address) if address else ''))

    # NEW device variants on sale that show «Imagen pendiente».
    from .tenancy import company_storefront_products

    variants = list(company_storefront_products(company).filter(
        product_model__isnull=False, condition_line=Product.CONDITION_NEW).order_by('product_model_id', 'name', 'pk'))
    galleries = variant_galleries(variants)
    with_own = {ref.object_id for ref in refs if ref.slot in (SLOT_GALLERY, SLOT_PRODUCT)}
    hidden_by_gate = 0
    for product in variants:
        if not galleries.get(product.pk, {'image_pending': True})['image_pending']:
            continue
        own_hidden = product.pk in with_own
        hidden_by_gate += own_hidden
        rows.append(row(NO_PHOTO, SLOT_PRODUCT, object_id=product.pk, object_name=product.name,
                        model_id=product.product_model_id, detail='own_hidden' if own_hidden else 'no_photo'))

    summary = {issue: sum(1 for r in rows if r['issue'] == issue) for issue in ISSUES}
    summary['external_hosts'] = [{'host': host, 'images': count}
                                 for host, count in sorted(hosts.items(), key=lambda item: (-item[1], item[0]))]
    summary['variants_hidden_by_gate'] = hidden_by_gate
    summary['storage_checked'] = check_storage
    return {'summary': summary, 'rows': rows}
