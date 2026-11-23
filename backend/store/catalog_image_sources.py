"""
P2 · F4 «Imágenes propias» — qué imágenes del catálogo NO subió la tienda.

POR QUÉ. Fichas de modelo y de producto enlazan fotos del CDN de un fabricante. El manual de marca pide fotos
propias, y una foto ajena es además un riesgo de licencia. Este informe le dice al dueño qué reemplazar; la tienda
ya las rotula «Imagen referencial» (F1).

QUÉ SE INFORMA. Todo lo que la tienda NO rotula como propio, con la misma regla que `isManagedStorefrontImage` del
frontend: sólo es propia una imagen de la tubería de la tienda, `/api/storefront/images/<id>`, relativa o absoluta.
Lo demás la tienda lo muestra como «Imagen referencial» y aquí sale: una URL de otro dominio y también una ruta del
propio sitio que no es una subida (`/assets/…`, `/media/…`), agrupada aparte bajo `SITE_PATH`. Vacía no es una imagen.

DÓNDE HAY IMÁGENES. `ProductModel.image_url` (la portada del modelo), `Product.image_url` (lo que muestra la tienda
de un producto o una variante) y su galería, `ProductImage.image_url`. Una variante es un producto con modelo.

SÓLO LECTURA, UNA EMPRESA, TRES CONSULTAS. La empresa la da el contexto del servidor. Cada tabla se filtra en la
base por dirección (un superconjunto de lo externo) y la decisión final es `is_external`, la misma para todo; el
número de consultas no crece con el catálogo. Sale el dominio, nunca la dirección entera, y nunca un precio o un
costo.

SE CALCULA EN CADA PETICIÓN. El resumen por dominio es del catálogo entero, así que cada página lee las direcciones
candidatas de toda la empresa (pocas columnas, sin N+1) y agrupa en Python: sacar el dominio de una URL en SQL no es
portable. Si un catálogo llega a decenas de miles de fichas con imágenes ajenas, ese es el punto a llevar a la base.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from django.db.models import Exists, OuterRef, Q

from .models import (
    ModelFinishImage, ModelOption, Product, ProductImage, ProductModel, ProductVariantOption, StorefrontImage,
)

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

#: La RUTA de una imagen subida, exactamente (`MANAGED_STOREFRONT_IMAGE` del frontend).
_MANAGED_PATH_RE = re.compile(r'^/api/storefront/images/[0-9a-f]{32}/?$', re.IGNORECASE)
#: En la base: lo mismo, relativa (con su consulta) o absoluta. Sólo para descartar pronto lo que es propio seguro.
_MANAGED_RELATIVE = r'^/api/storefront/images/[0-9a-f]{32}/?(\?.*)?$'
_MANAGED_ABSOLUTE = r'^https?://[^/?#]+/api/storefront/images/[0-9a-f]{32}/?([?#].*)?$'

#: El «dominio» de una ruta del propio sitio que no es una subida: se agrupa aparte de los dominios ajenos.
SITE_PATH = '(ruta del sitio)'

KIND_MODEL, KIND_VARIANT, KIND_PRODUCT = 'model', 'variant', 'product'
_KIND_ORDER = {KIND_MODEL: 0, KIND_VARIANT: 1, KIND_PRODUCT: 2}


def _managed(text: str) -> bool:
    """`isManagedStorefrontImage`: una relativa sin su consulta, o la ruta de una absoluta, es la de una subida."""
    if text.startswith('/') and not text.startswith('//'):
        return bool(_MANAGED_PATH_RE.match(text.split('?')[0]))
    try:
        parts = urlsplit(text)
    except ValueError:
        return False
    if parts.scheme.lower() not in ('http', 'https') or not parts.netloc:
        return False
    return bool(_MANAGED_PATH_RE.match(parts.path))


def is_external(value) -> bool:
    """¿La tienda muestra `value` como «Imagen referencial»? Todo lo que no es una subida suya, salvo vacío."""
    text = str(value or '').strip()
    return bool(text) and not _managed(text)


def host_of(value) -> str:
    """
    Sólo el dominio, en minúsculas: sin usuario, puerto, ruta ni consulta. `SITE_PATH` para una ruta del propio
    sitio; vacío si no tiene dominio.
    """
    text = str(value or '').strip()
    if text.startswith('/') and not text.startswith('//'):
        return SITE_PATH
    try:
        return (urlsplit(text).hostname or '').lower()
    except ValueError:
        return ''


def _candidates(field: str) -> Q:
    """
    En la base, lo que PUEDE ser externo: un superconjunto de `is_external`.

    Sólo se descarta lo que `is_external` también descarta —vacío y la dirección exacta de una subida, relativa o
    absoluta—, así que la base nunca deja fuera una externa; lo que sobre lo quita `is_external`.
    """
    return (~Q(**{field: ''}) & ~Q(**{f'{field}__iregex': _MANAGED_RELATIVE})
            & ~Q(**{f'{field}__iregex': _MANAGED_ABSOLUTE}))


def _entry(kind, pk, name, slug, urls, *, published, model_id=None):
    hosts = [host_of(url) for url in urls]
    admin_url = f'/admin/products/models/{pk}' if kind == KIND_MODEL else f'/admin/products/{pk}'
    return {
        'kind': kind, 'id': pk, 'name': name, 'slug': slug,
        'host': hosts[0], 'hosts': sorted(set(hosts)), 'image_count': len(urls),
        'model_id': model_id, 'is_published': published, 'admin_url': admin_url,
        '_hosts_per_image': hosts,
    }


def _number(value, default, *, low, high):
    try:
        number = int(str(value).strip()) if value is not None else default
    except (TypeError, ValueError):
        return default
    if number < low:
        return default
    return min(number, high)


def report(company, *, page=1, page_size=DEFAULT_PAGE_SIZE) -> dict:
    """El informe de `company`: resumen por dominio y una página de modelos y productos con imágenes externas."""
    page = _number(page, 1, low=1, high=10 ** 6)
    page_size = _number(page_size, DEFAULT_PAGE_SIZE, low=1, high=MAX_PAGE_SIZE)

    entries = []
    models = (ProductModel.objects.filter(company=company).filter(_candidates('image_url'))
              .values_list('pk', 'name', 'slug', 'image_url', 'is_published_online'))
    for pk, name, slug, url, published in models:
        if is_external(url):
            entries.append(_entry(KIND_MODEL, pk, name, slug, [url.strip()], published=published))

    gallery: dict[int, list[str]] = {}
    for product_id, url in (ProductImage.objects.filter(company=company).filter(_candidates('image_url'))
                            .order_by('product_id', 'sort_order', 'pk').values_list('product_id', 'image_url')):
        if is_external(url):
            gallery.setdefault(product_id, []).append(url.strip())

    external_gallery = ProductImage.objects.filter(product=OuterRef('pk'), company=company).filter(
        _candidates('image_url'))
    products = (Product.objects.filter(company=company)
                .filter(_candidates('image_url') | Q(Exists(external_gallery)))
                .values_list('pk', 'name', 'slug', 'image_url', 'product_model_id', 'is_active', 'is_published_online',
                             'product_model__is_published_online'))
    for pk, name, slug, url, model_id, active, online, model_online in products:
        urls = []
        for candidate in [url, *gallery.get(pk, [])]:
            candidate = (candidate or '').strip()
            if is_external(candidate) and candidate not in urls:
                urls.append(candidate)
        if urls:
            entries.append(_entry(KIND_VARIANT if model_id else KIND_PRODUCT, pk, name, slug, urls,
                                  published=bool(active and online and (model_id is None or model_online)),
                                  model_id=model_id))

    entries.sort(key=lambda e: (_KIND_ORDER[e['kind']], e['name'].casefold(), e['id']))

    by_host: dict[str, dict] = {}
    for entry in entries:
        for host in entry['hosts']:
            by_host.setdefault(host, {'host': host, 'items': 0, 'images': 0})['items'] += 1
        for host in entry.pop('_hosts_per_image'):
            by_host[host]['images'] += 1

    count = {kind: sum(1 for e in entries if e['kind'] == kind) for kind in _KIND_ORDER}
    start = (page - 1) * page_size
    return {
        'summary': {
            'models': count[KIND_MODEL], 'variants': count[KIND_VARIANT], 'products': count[KIND_PRODUCT],
            'items': len(entries), 'images': sum(e['image_count'] for e in entries),
            'hosts': sorted(by_host.values(), key=lambda h: (-h['images'], h['host'])),
        },
        'count': len(entries), 'page': page, 'page_size': page_size,
        'results': entries[start:start + page_size],
    }


# ---------------------------------------------------------------------------
# CATALOG-REFERENCE-01 · PR C — finishes without pictures, and the licence of what the shop uploaded
# ---------------------------------------------------------------------------

#: How many finishes without pictures the report names (the count is always the whole catalogue's).
MAX_PENDING_FINISHES = 200


def _managed_ids(rows) -> set[str]:
    from .storefront_media import managed_public_id

    return {managed_public_id(value) for value in rows} - {None}


def catalog_finishes(company) -> dict[tuple[int, int], str]:
    """
    Every finish of `company`'s catalogue, uncapped: {(model id, colour option id): colour label}. A finish is a colour
    (`finish_media.colour_attribute_ids`, D-PRC-8) of the model's palette or of one of its active variants. Two queries.
    """
    from .finish_media import colour_attribute_ids

    dimension = colour_attribute_ids(company.pk)      # D-PRC-8: the same colour dimension as the shop
    finishes: dict[tuple[int, int], str] = {}
    for model_id, option_id, label in (ModelOption.objects.filter(company=company, option__attribute_id__in=dimension)
                                       .values_list('model_id', 'option_id', 'option__label')):
        finishes[(model_id, option_id)] = label
    for model_id, option_id, label in (ProductVariantOption.objects.filter(
            company=company, attribute_id__in=dimension, product__product_model__isnull=False,
            product__is_active=True)
            .values_list('product__product_model_id', 'option_id', 'option__label')):
        finishes[(model_id, option_id)] = label
    return finishes


def licensing(company) -> dict:
    """
    Read-only, one company, a fixed number of queries whatever the size of the catalogue:

    * `finishes_without_images` / `pending_finishes`: each (model, colour) the catalogue has — a colour
      (`finish_media.colour_attribute_ids`) of the model's palette or of one of its active variants — without a finish
      picture. The shop shows those variants
      «Imagen pendiente» unless the variant has pictures of its own.
    * `uploads`: the shop's own uploads shown somewhere in the catalogue (model cover, a product's picture or
      gallery, a finish), each counted once: `unknown_source` (no provenance: uploaded before PR C or through a slot
      that does not ask), `unverified_license` (provenance given, not the shop's own photo, nobody checked it yet),
      `demo` (a demo resource, flagged on the shop).
    """
    finishes = catalog_finishes(company)
    pictured = set(ModelFinishImage.objects.filter(company=company).values_list('model_id', 'color_option_id'))
    pending = sorted(key for key in finishes if key not in pictured)
    names = dict(ProductModel.objects.filter(company=company, pk__in={model_id for model_id, _ in pending})
                 .values_list('pk', 'name')) if pending else {}
    rows = sorted(
        ({'model_id': model_id, 'model_name': names.get(model_id, ''), 'option_id': option_id,
          'option_label': finishes[(model_id, option_id)], 'admin_url': f'/admin/products/models/{model_id}'}
         for model_id, option_id in pending),
        key=lambda row: (row['model_name'].casefold(), row['option_label'].casefold(), row['option_id']),
    )

    ids = (_managed_ids(ProductModel.objects.filter(company=company).exclude(image_url='')
                        .values_list('image_url', flat=True))
           | _managed_ids(Product.objects.filter(company=company).exclude(image_url='')
                          .values_list('image_url', flat=True))
           | _managed_ids(ProductImage.objects.filter(company=company).values_list('image_url', flat=True))
           | _managed_ids(ModelFinishImage.objects.filter(company=company).values_list('image_url', flat=True)))
    uploads = {'total': 0, 'unknown_source': 0, 'unverified_license': 0, 'demo': 0}
    if ids:
        for kind, verified_at in (StorefrontImage.objects.filter(company=company, public_id__in=ids)
                                  .values_list('source_kind', 'license_verified_at')):
            uploads['total'] += 1
            if kind == StorefrontImage.SOURCE_UNKNOWN:
                uploads['unknown_source'] += 1
            elif kind != StorefrontImage.SOURCE_OWN_PHOTO and verified_at is None:
                uploads['unverified_license'] += 1
            if kind == StorefrontImage.SOURCE_DEMO:
                uploads['demo'] += 1
    return {
        'finishes_without_images': len(rows),
        'pending_finishes': rows[:MAX_PENDING_FINISHES],
        'uploads': uploads,
    }


# ---------------------------------------------------------------------------
# FASE 1.2 — photo coverage of every finish (the `report_finish_coverage` command; no cap)
# ---------------------------------------------------------------------------

FINISH_COVERAGE_COLUMNS = (
    'model_id', 'model_name', 'option_id', 'option_label', 'photos', 'has_primary', 'license_verified', 'demo',
    'own', 'supplier', 'licensed', 'unknown', 'covered', 'public',
)
_BY_KIND = {
    StorefrontImage.SOURCE_OWN_PHOTO: 'own', StorefrontImage.SOURCE_SUPPLIER_AUTHORIZED: 'supplier',
    StorefrontImage.SOURCE_LICENSED_STOCK: 'licensed', StorefrontImage.SOURCE_DEMO: 'demo',
}


def finish_coverage(company) -> dict:
    """
    Read-only, one company, a fixed number of queries: one row per finish of the catalogue (`catalog_finishes`), with
    how many pictures it has and where they come from (the `StorefrontImage` of each one; a picture without one, or
    without provenance, is `unknown`). A finish is COVERED when at least one of its pictures is not a demo resource:
    a demo picture fills the slot on the shop, never the catalogue. It is PUBLIC when at least one of its pictures
    is on the shop: its licence was verified (`finish_media.is_public`, owner 2026-11-23; a verified demo too) — a
    covered finish nobody verified still shows «Imagen pendiente». Every finish, no cap (the API's `licensing`
    keeps its own).
    """
    from .storefront_media import managed_public_id

    finishes = catalog_finishes(company)
    images = list(ModelFinishImage.objects.filter(company=company)
                  .values_list('model_id', 'color_option_id', 'image_url', 'is_primary'))
    ids = {managed_public_id(url) for _m, _o, url, _p in images} - {None}
    stored = ({public_id: (kind, verified_at) for public_id, kind, verified_at in StorefrontImage.objects.filter(
        company=company, public_id__in=ids).values_list('public_id', 'source_kind', 'license_verified_at')}
        if ids else {})
    counts: dict[tuple[int, int], dict] = {
        key: {'photos': 0, 'has_primary': False, 'license_verified': 0, 'demo': 0, 'own': 0, 'supplier': 0,
              'licensed': 0, 'unknown': 0} for key in finishes}
    for model_id, option_id, url, primary in images:
        row = counts.get((model_id, option_id))
        if row is None:         # a picture of a colour the catalogue no longer has: not a finish of the catalogue
            continue
        kind, verified_at = stored.get(managed_public_id(url), (StorefrontImage.SOURCE_UNKNOWN, None))
        row['photos'] += 1
        row['has_primary'] = row['has_primary'] or primary
        row['license_verified'] += verified_at is not None
        row[_BY_KIND.get(kind, 'unknown')] += 1
    names = dict(ProductModel.objects.filter(company=company, pk__in={model_id for model_id, _ in finishes})
                 .values_list('pk', 'name')) if finishes else {}
    rows = sorted(
        ({'model_id': model_id, 'model_name': names.get(model_id, ''), 'option_id': option_id,
          'option_label': finishes[(model_id, option_id)], **row, 'covered': row['photos'] > row['demo'],
          # `stored` is this company's files only, so a verified one is exactly a public one.
          'public': row['license_verified'] > 0}
         for (model_id, option_id), row in counts.items()),
        key=lambda row: (row['model_name'].casefold(), row['option_label'].casefold(), row['model_id'],
                         row['option_id']),
    )
    covered = sum(1 for row in rows if row['covered'])
    return {
        'summary': {
            'finishes': len(rows), 'covered': covered,
            'without_photos': sum(1 for row in rows if not row['photos']),
            'demo_only': sum(1 for row in rows if row['photos'] and not row['covered']),
            'public': sum(1 for row in rows if row['public']),
            'coverage_percent': round(100 * covered / len(rows), 1) if rows else 0.0,
        },
        'rows': rows,
    }
