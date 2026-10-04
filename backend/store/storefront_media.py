"""
Imágenes públicas de la tienda: lo que se sube desde el panel.

EN QUÉ SE DIFERENCIA DE UNA EVIDENCIA. Las evidencias del servicio técnico son
privadas, se aplanan sobre blanco y se convierten a WebP: son fotos de un
mostrador. Una imagen de la tienda es lo contrario — pública, y a menudo un
RECORTE: un producto en PNG sin fondo para que se apoye sobre el color de la
página. Aplanarla o pasarla a JPEG le pondría un rectángulo detrás. Aquí el
formato y la transparencia de lo que se sube se conservan.

LO QUE SÍ SE HACE CON CADA ARCHIVO:
  - decide el tipo el decodificador, nunca el nombre ni el Content-Type;
  - sólo PNG, JPEG y WebP, de un solo fotograma: nada que el navegador pueda
    ejecutar (SVG) ni animar;
  - se vuelve a codificar desde los píxeles, así no sale ningún metadato (GPS,
    cámara, miniaturas) ni nada escondido tras el final de la imagen;
  - se reduce si es más grande de lo que una portada necesita, nunca se amplía.
"""

from __future__ import annotations

import io
import uuid
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction
from PIL import Image, ImageOps, UnidentifiedImageError

from . import evidence_storage as storage
from .models import AdminAuditLog, StorefrontImage

#: formato del decodificador -> (formato de salida, tipo, extensión)
_FORMATS = {
    'PNG': ('PNG', 'image/png', 'png'),
    'JPEG': ('JPEG', 'image/jpeg', 'jpg'),
    'MPO': ('JPEG', 'image/jpeg', 'jpg'),
    'WEBP': ('WEBP', 'image/webp', 'webp'),
}


class StorefrontImageError(Exception):
    """El archivo no sirve como imagen de la tienda. El mensaje es para quien lo subió."""


@dataclass(frozen=True)
class ProcessedImage:
    content: bytes
    mime_type: str
    extension: str
    width: int
    height: int
    has_alpha: bool


def _setting(name: str, default: int) -> int:
    return int(getattr(settings, name, default))


def max_upload_bytes() -> int:
    return _setting('STOREFRONT_IMAGE_MAX_UPLOAD_BYTES', 8 * 1024 * 1024)


def max_pixels() -> int:
    return _setting('STOREFRONT_IMAGE_MAX_PIXELS', 40_000_000)


def max_edge() -> int:
    return _setting('STOREFRONT_IMAGE_MAX_EDGE', 2400)


def read_upload(uploaded) -> bytes:
    """
    Los bytes del archivo, con el tope aplicado ANTES de tenerlos en memoria.

    El tamaño declarado rechaza pronto; la lectura se corta un byte después del
    tope, porque el tamaño declarado es lo que dice quien envía.
    """
    limit = max_upload_bytes()
    megabytes = max(1, limit // (1024 * 1024))
    too_large = StorefrontImageError(f'La imagen pesa más de {megabytes} MB.')
    declared = getattr(uploaded, 'size', None)
    if isinstance(declared, int) and declared > limit:
        raise too_large
    raw = uploaded.read(limit + 1)
    if len(raw) > limit:
        raise too_large
    if not raw:
        raise StorefrontImageError('El archivo está vacío.')
    return raw


def _open(raw: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(raw))
    except UnidentifiedImageError:
        raise StorefrontImageError('Ese archivo no es una imagen que podamos leer.') from None
    except Exception:
        raise StorefrontImageError('No se pudo leer la imagen.') from None

    if (image.format or '').upper() not in _FORMATS:
        raise StorefrontImageError('Sube la imagen en PNG, JPEG o WebP.')
    if getattr(image, 'n_frames', 1) > 1 and (image.format or '').upper() != 'MPO':
        raise StorefrontImageError('No se admiten imágenes animadas.')

    width, height = image.size
    if width < 1 or height < 1:
        raise StorefrontImageError('La imagen no tiene dimensiones válidas.')
    if width * height > max_pixels():
        # De la cabecera: todavía no se ha reservado la memoria que esto evita.
        raise StorefrontImageError('La imagen tiene dimensiones desproporcionadas.')
    try:
        image.load()
    except Exception:
        raise StorefrontImageError('La imagen está incompleta o dañada.') from None
    return image


def _carries_transparency(image: Image.Image) -> bool:
    return image.mode in ('RGBA', 'LA', 'PA') or (
        image.mode == 'P' and 'transparency' in image.info
    )


def process(raw: bytes) -> ProcessedImage:
    image = _open(raw)
    out_format, mime_type, extension = _FORMATS[(image.format or '').upper()]

    try:
        image = ImageOps.exif_transpose(image) or image
    except Exception:  # pragma: no cover - EXIF corrupto
        pass

    # JPEG no tiene canal alfa; en PNG y WebP se conserva si de verdad se usa.
    has_alpha = False
    if out_format != 'JPEG' and _carries_transparency(image):
        image = image.convert('RGBA')
        has_alpha = image.getchannel('A').getextrema()[0] < 255
    mode = 'RGBA' if has_alpha else 'RGB'
    if image.mode != mode:
        image = image.convert(mode)

    limit = max_edge()
    width, height = image.size
    if max(width, height) > limit:
        scale = limit / max(width, height)
        image = image.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))), Image.LANCZOS,
        )

    # Píxeles nuevos y nada más: ningún metadato sobrevive a esto.
    clean = Image.frombytes(mode, image.size, image.tobytes())

    buffer = io.BytesIO()
    if out_format == 'PNG':
        clean.save(buffer, 'PNG', optimize=True)
    elif out_format == 'JPEG':
        clean.save(buffer, 'JPEG', quality=88, optimize=True)
    else:
        clean.save(buffer, 'WEBP', quality=90, method=4)
    return ProcessedImage(
        content=buffer.getvalue(), mime_type=mime_type, extension=extension,
        width=clean.size[0], height=clean.size[1], has_alpha=has_alpha,
    )


def upload(*, company, actor, uploaded, request=None) -> StorefrontImage:
    """Procesa, guarda y registra una imagen de la tienda de `company`."""
    processed = process(read_upload(uploaded))

    public_id = uuid.uuid4().hex
    # La ruta la genera el servidor: un identificador aleatorio y nada del
    # nombre original, que es lo que controla quien sube el archivo.
    key = f'companies/{company.pk}/storefront/{public_id}.{processed.extension}'
    try:
        stored = storage.save(key, processed.content)
    except storage.EvidenceStorageError:
        raise StorefrontImageError('No se pudo guardar la imagen.') from None

    try:
        with transaction.atomic():
            image = StorefrontImage.objects.create(
                company=company, public_id=public_id, storage_key=stored.key,
                mime_type=processed.mime_type, byte_size=stored.byte_size,
                width=processed.width, height=processed.height,
                has_alpha=processed.has_alpha, uploaded_by=actor,
            )
            AdminAuditLog.log(
                actor=actor, action='storefront_image_uploaded',
                target_type='storefront_image', target_id=image.pk,
                metadata={
                    'public_id': public_id, 'mime_type': processed.mime_type,
                    'byte_size': stored.byte_size,
                    'width': processed.width, 'height': processed.height,
                },
                request=request, company=company,
            )
    except Exception:
        # Sin fila no hay forma de llegar al objeto: no se deja huérfano.
        storage.delete_quietly(stored.key)
        raise
    return image


def payload(image: StorefrontImage) -> dict:
    return {
        'id': image.public_id,
        'url': image.url,
        'mime_type': image.mime_type,
        'byte_size': image.byte_size,
        'width': image.width,
        'height': image.height,
        'has_alpha': image.has_alpha,
    }
