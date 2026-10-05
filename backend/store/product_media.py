"""
PRODUCT-MEDIA — la galería de un producto.

QUÉ ES Y QUÉ NO. Esto no es un segundo almacén de imágenes. Los píxeles los
guarda `storefront_media` —la tubería del hero y de las categorías: recodifica
desde los píxeles, pertenece a una empresa, limpia lo que nadie muestra— y aquí
sólo se decide qué producto muestra cada imagen, en qué orden, con qué texto
alternativo y cuál es la principal.

`Product.image_url` ES LA PRINCIPAL. La tienda, el carrito, las líneas de
pedido y la caja leen ese campo desde la fase 0. En vez de enseñarles a todos
una galería, la galería mantiene ese campo: cada vez que cambia la principal,
se escribe aquí, en la misma transacción. Un producto sin galería conserva la
dirección que tuviera escrita.

UNA ESCRITURA A LA VEZ POR PRODUCTO. Cada operación bloquea la fila del
producto antes de mirar la galería. Así dos personas subiendo a la vez no se
pisan el orden ni dejan dos principales.
"""
from __future__ import annotations

import hashlib

from django.conf import settings
from django.db import transaction
from django.db.models import Max

from . import storefront_media
from .models import AdminAuditLog, Product, ProductImage

ALT_TEXT_MAX = 160


class ProductMediaError(Exception):
    """Un rechazo que quien edita el producto puede leer y corregir."""


def max_per_product() -> int:
    return int(getattr(settings, 'PRODUCT_IMAGE_MAX_PER_PRODUCT', 12))


def gallery(product):
    """Las imágenes de `product`, en el orden en que se muestran."""
    return ProductImage.objects.filter(product=product, company_id=product.company_id)


def payload(image: ProductImage) -> dict:
    """Lo que ve el panel. Nunca la clave del objeto en el almacenamiento."""
    return {
        'id': image.pk,
        'url': image.image_url,
        'alt_text': image.alt_text,
        'is_primary': image.is_primary,
        'sort_order': image.sort_order,
        'width': image.width,
        'height': image.height,
    }


def public_payload(image: ProductImage) -> dict:
    """Lo que ve la tienda: allowlist, sin identificadores internos."""
    return {
        'url': image.image_url,
        'alt_text': image.alt_text,
        'is_primary': image.is_primary,
        'width': image.width,
        'height': image.height,
    }


def clean_alt_text(value) -> str:
    text = ' '.join(str(value or '').split())
    if len(text) > ALT_TEXT_MAX:
        raise ProductMediaError(
            f'El texto alternativo admite hasta {ALT_TEXT_MAX} caracteres.'
        )
    return text


def _locked(product) -> Product:
    return Product.objects.select_for_update().get(pk=product.pk, company_id=product.company_id)


def _audit(*, action, product, actor, request, **metadata):
    AdminAuditLog.log(
        actor=actor, action=action, target_type='product', target_id=product.pk,
        metadata={'product_id': product.pk, 'product_name': product.name, **metadata},
        request=request, company=product.company,
    )


def _show(product: Product, address: str) -> None:
    """La tienda pasa a mostrar `address` como imagen del producto."""
    if product.image_url != address:
        product.image_url = address
        product.save(update_fields=['image_url', 'updated_at'])


def place(*, product, address: str, actor, alt_text: str = '', request=None,
          make_primary: bool = False, source: str = 'panel',
          source_sha256: str = '') -> ProductImage:
    """
    Coloca en la galería una imagen YA SUBIDA por la empresa del producto.

    Se llama dentro de una transacción. Bloquea el producto, comprueba que la
    imagen existe y es de la empresa (`claim`), y la añade al final.
    """
    row = _locked(product)
    existing = list(gallery(row))
    if len(existing) >= max_per_product():
        raise ProductMediaError(
            f'Un producto admite hasta {max_per_product()} imágenes. Quita una antes de añadir otra.'
        )
    storefront_media.claim(address, company=row.company, field='file')
    stored = storefront_media.StorefrontImage.objects.filter(
        public_id=storefront_media.managed_public_id(address), company=row.company,
    ).first()

    has_primary = any(image.is_primary for image in existing)
    primary = make_primary or not has_primary
    if primary and has_primary:
        ProductImage.objects.filter(product=row, is_primary=True).update(is_primary=False)
    last = gallery(row).aggregate(last=Max('sort_order'))['last']
    image = ProductImage.objects.create(
        company=row.company, product=row, image_url=stored.url, alt_text=alt_text,
        sort_order=0 if last is None else last + 1, is_primary=primary,
        width=stored.width, height=stored.height, uploaded_by=actor,
        source_sha256=source_sha256,
    )
    replaced = row.image_url if primary and row.image_url != stored.url else ''
    if primary:
        _show(row, stored.url)
    _audit(
        action='product_image_added', product=row, actor=actor, request=request,
        image_id=image.pk, address=stored.url, is_primary=primary, source=source,
        **({'replaced_address': replaced} if replaced else {}),
    )
    return image


def add_image(*, product, actor, uploaded, alt_text='', request=None) -> ProductImage:
    """Sube un archivo y lo coloca en la galería de `product`."""
    alt = clean_alt_text(alt_text)
    if gallery(product).count() >= max_per_product():
        # Antes de guardar nada: el tope se vuelve a comprobar con el bloqueo.
        raise ProductMediaError(
            f'Un producto admite hasta {max_per_product()} imágenes. Quita una antes de añadir otra.'
        )
    raw = storefront_media.read_upload(uploaded)
    stored = storefront_media.store(
        company=product.company, actor=actor, raw=raw, request=request,
    )
    try:
        with transaction.atomic():
            return place(
                product=product, address=stored.url, actor=actor, alt_text=alt, request=request,
                source_sha256=hashlib.sha256(raw).hexdigest(),
            )
    except Exception:
        # La imagen se guardó pero no llegó a ninguna galería: no se deja.
        storefront_media.release(
            [stored.url], company=product.company, actor=actor, request=request,
            reason='never_placed',
        )
        raise


@transaction.atomic
def update_image(*, image, actor, alt_text=None, is_primary=None, request=None) -> ProductImage:
    row = _locked(image.product)
    image = gallery(row).select_for_update().get(pk=image.pk)

    if is_primary is False and image.is_primary:
        raise ProductMediaError('Para cambiar la imagen principal, elige otra como principal.')

    if alt_text is not None:
        alt = clean_alt_text(alt_text)
        if alt != image.alt_text:
            before, image.alt_text = image.alt_text, alt
            image.save(update_fields=['alt_text', 'updated_at'])
            _audit(
                action='product_image_updated', product=row, actor=actor, request=request,
                image_id=image.pk, alt_text={'old': before, 'new': alt},
            )

    if is_primary is True and not image.is_primary:
        previous = gallery(row).filter(is_primary=True).first()
        # Primero se quita la marca: la restricción admite una sola principal.
        gallery(row).filter(is_primary=True).update(is_primary=False)
        image.is_primary = True
        image.save(update_fields=['is_primary', 'updated_at'])
        _show(row, image.image_url)
        _audit(
            action='product_image_primary_changed', product=row, actor=actor, request=request,
            image_id=image.pk, address=image.image_url,
            previous_image_id=previous.pk if previous else None,
        )
    return image


@transaction.atomic
def remove_image(*, image, actor, request=None) -> None:
    row = _locked(image.product)
    image = gallery(row).select_for_update().get(pk=image.pk)
    address, was_primary, image_id = image.image_url, image.is_primary, image.pk
    image.delete()

    if was_primary:
        successor = gallery(row).first()
        if successor is not None:
            successor.is_primary = True
            successor.save(update_fields=['is_primary', 'updated_at'])
        _show(row, successor.image_url if successor else '')

    _audit(
        action='product_image_removed', product=row, actor=actor, request=request,
        image_id=image_id, address=address, was_primary=was_primary,
    )
    # Después de borrar la fila: el recuento ya no ve esta referencia. El
    # archivo se va sólo si nadie más lo muestra, y al confirmarse todo.
    storefront_media.release([address], company=row.company, actor=actor, request=request)


@transaction.atomic
def reorder(*, product, order, actor, request=None) -> list[ProductImage]:
    row = _locked(product)
    current = {image.pk: image for image in gallery(row).select_for_update()}
    if (
        not isinstance(order, list)
        or any(isinstance(item, bool) or not isinstance(item, int) for item in order)
        or len(order) != len(set(order))
        or set(order) != set(current)
    ):
        raise ProductMediaError('El orden debe nombrar, una vez, cada imagen del producto.')
    for position, image_id in enumerate(order):
        image = current[image_id]
        if image.sort_order != position:
            image.sort_order = position
            image.save(update_fields=['sort_order', 'updated_at'])
    _audit(
        action='product_images_reordered', product=row, actor=actor, request=request,
        order=order,
    )
    return [current[image_id] for image_id in order]
