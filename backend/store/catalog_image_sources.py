"""
P2 · F4 «Imágenes propias» — qué imágenes del catálogo NO subió la tienda.

POR QUÉ. Fichas de modelo y de producto enlazan fotos del CDN de un fabricante. El manual de marca pide fotos
propias, y una foto ajena es además un riesgo de licencia. Este informe le dice al dueño qué reemplazar; la tienda
ya las rotula «Imagen referencial» (F1).

QUÉ ES EXTERNA. Lo que no sale de la tubería de la tienda (`/api/storefront/images/<id>`, relativa o absoluta —
la misma regla que `isManagedStorefrontImage` del frontend) ni es una ruta del propio sitio (`/assets/…`). Vacía
no es una imagen.

DÓNDE HAY IMÁGENES. `ProductModel.image_url` (la portada del modelo), `Product.image_url` (lo que muestra la tienda
de un producto o una variante) y su galería, `ProductImage.image_url`. Una variante es un producto con modelo.

SÓLO LECTURA, UNA EMPRESA, TRES CONSULTAS. La empresa la da el contexto del servidor. Cada tabla se filtra en la
base por dirección (un superconjunto de lo externo) y la decisión final es `is_external`, la misma para todo; el
número de consultas no crece con el catálogo. Sale el dominio, nunca la dirección entera, y nunca un precio o un
costo.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from django.db.models import Q

from .models import Product, ProductImage, ProductModel

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

#: Una dirección ABSOLUTA de la tubería de la tienda: su ruta es exactamente la de una imagen subida.
_MANAGED_ABSOLUTE = r'^https?://[^/?#]+/api/storefront/images/[0-9a-f]{32}/?([?#].*)?$'
_MANAGED_ABSOLUTE_RE = re.compile(_MANAGED_ABSOLUTE, re.IGNORECASE)

KIND_MODEL, KIND_VARIANT, KIND_PRODUCT = 'model', 'variant', 'product'
_KIND_ORDER = {KIND_MODEL: 0, KIND_VARIANT: 1, KIND_PRODUCT: 2}


def is_external(value) -> bool:
    """¿`value` es una imagen que la tienda no subió?"""
    text = str(value or '').strip()
    if not text:
        return False
    if text.startswith('/') and not text.startswith('//'):
        return False  # una ruta del propio sitio, subida o no
    return not _MANAGED_ABSOLUTE_RE.match(text)


def host_of(value) -> str:
    """Sólo el dominio, en minúsculas: sin usuario, puerto, ruta ni consulta. Vacío si no tiene."""
    try:
        return (urlsplit(str(value or '').strip()).hostname or '').lower()
    except ValueError:
        return ''


def _candidates(field: str) -> Q:
    """
    En la base, lo que PUEDE ser externo: un superconjunto de `is_external`.

    Sólo se descarta lo que `is_external` también descarta —vacío, una ruta del sitio, la dirección absoluta exacta
    de la tubería—, así que la base nunca deja fuera una externa; lo que sobre lo quita `is_external`.
    """
    relative = Q(**{f'{field}__startswith': '/'}) & ~Q(**{f'{field}__startswith': '//'})
    return ~Q(**{field: ''}) & ~relative & ~Q(**{f'{field}__iregex': _MANAGED_ABSOLUTE})


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

    products = (Product.objects.filter(company=company)
                .filter(_candidates('image_url') | Q(pk__in=list(gallery)))
                .values_list('pk', 'name', 'slug', 'image_url', 'product_model_id', 'is_active', 'is_published_online'))
    for pk, name, slug, url, model_id, active, online in products:
        urls = []
        for candidate in [url, *gallery.get(pk, [])]:
            candidate = (candidate or '').strip()
            if is_external(candidate) and candidate not in urls:
                urls.append(candidate)
        if urls:
            entries.append(_entry(KIND_VARIANT if model_id else KIND_PRODUCT, pk, name, slug, urls,
                                  published=bool(active and online), model_id=model_id))

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
