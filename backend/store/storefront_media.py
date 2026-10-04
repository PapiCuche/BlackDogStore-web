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
import re
import uuid
from dataclasses import dataclass

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from PIL import Image, ImageOps, UnidentifiedImageError

from . import evidence_storage as storage
from .models import AdminAuditLog, StorefrontImage, validate_asset_url

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
    # Con canal alfa, o declarando un color como transparente (tRNS), que PNG
    # admite también en RGB y en escala de grises, no sólo con paleta.
    return image.mode in ('RGBA', 'LA', 'PA') or 'transparency' in image.info


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


# ---------------------------------------------------------------------------
# STOREFRONT-IMAGE-CLEANUP — retirar lo que ya nadie muestra
# ---------------------------------------------------------------------------
#
# Una imagen subida ocupa almacenamiento para siempre si nadie la retira. Hay
# dos formas de que una quede sin uso:
#
#   1. un hueco deja de apuntar a ella (se sustituye o se quita);
#   2. se subió y nunca se colocó (se cerró el formulario sin guardar).
#
# LA REGLA ES LA MISMA EN LAS DOS: se borra sólo si NO QUEDA NINGUNA REFERENCIA
# en toda la plataforma. Las referencias no son claves foráneas, son direcciones
# escritas en campos de texto, así que se cuentan buscando el identificador en
# TODOS los campos que pueden llevar una dirección, de todas las empresas. Es
# deliberadamente amplio: una imagen que alguien todavía muestra no se toca.
#
# Y PARA QUE EL RECUENTO VALGA, lo que se coloca en un hueco tiene que existir
# (`claim`). `claim` y `release` bloquean la misma fila, así que no pueden
# cruzarse: o el borrado ve la referencia nueva, o quien coloca ve que la imagen
# ya no existe y se lo dice a quien guarda.

#: Una dirección de esta tubería, relativa o absoluta, con o sin barra final.
_MANAGED_ADDRESS = re.compile(r'/api/storefront/images/([0-9a-f]{32})(?:/|$|[?#])')

_NO_LONGER_EXISTS = 'Esa imagen ya no existe. Súbela de nuevo.'


def managed_public_id(value) -> str | None:
    """El identificador, si `value` es la dirección de una imagen subida aquí."""
    match = _MANAGED_ADDRESS.search(str(value or ''))
    return match.group(1) if match else None


def reference_fields() -> list[tuple[type[models.Model], str]]:
    """
    Todos los campos de la plataforma que pueden llevar una dirección.

    SE CALCULA, NO SE ESCRIBE A MANO. Una lista escrita a mano se queda corta el
    día que alguien añade un hueco nuevo, y un recuento al que le falta un campo
    borra una imagen en uso. Entra cualquier campo de texto que valide una
    dirección de imagen o cuyo nombre diga que lleva una dirección: de más no
    hace daño —sólo se busca un identificador de 32 caracteres—; de menos, sí.
    """
    found = []
    for model in apps.get_app_config('store').get_models():
        for field in model._meta.get_fields():
            if not isinstance(field, (models.CharField, models.TextField)):
                continue
            name = field.name.lower()
            if (
                validate_asset_url in field.validators
                or name.endswith('_url') or 'image' in name or 'logo' in name
            ):
                found.append((model, field.name))
    return found


def reference_count(public_id: str) -> int:
    """Cuántas filas, de cualquier empresa, llevan esta imagen en algún campo."""
    return sum(
        model._default_manager.filter(**{f'{field}__contains': public_id}).count()
        for model, field in reference_fields()
    )


def claim(value, *, company, field: str) -> None:
    """
    Comprueba que lo que se va a colocar en un hueco existe y es de la tienda.

    Se llama DENTRO de la transacción que guarda el hueco y bloquea la fila de
    la imagen hasta que esa transacción termina: un borrado simultáneo espera, y
    al reanudarse ya ve la referencia nueva.

    Una dirección que no es de esta tubería —un recurso del sitio, una URL
    externa— no se comprueba: no es nuestra para seguirle la pista.

    La imagen de OTRA tienda responde igual que una que no existe: confirmar que
    existe ya sería decir algo de otra empresa.
    """
    public_id = managed_public_id(value)
    if public_id is None:
        return
    image = (
        StorefrontImage.objects.select_for_update()
        .filter(public_id=public_id, company=company).first()
    )
    if image is None:
        raise ValidationError({field: [_NO_LONGER_EXISTS]})


def _delete_if_unreferenced(image_id: int, *, actor=None, request=None, reason: str) -> bool:
    """
    Borra la imagen si no queda NINGUNA referencia. Devuelve si la borró.

    El orden importa: primero el bloqueo de la fila, después el recuento. Así el
    recuento se hace cuando ya nadie puede estar colocándola (`claim` espera a
    este mismo bloqueo). El archivo se borra al confirmarse la transacción: si
    ésta se deshace, la fila vuelve y el archivo nunca se fue.
    """
    with transaction.atomic():
        image = StorefrontImage.objects.select_for_update().filter(pk=image_id).first()
        if image is None or reference_count(image.public_id) > 0:
            return False
        key, company, public_id = image.storage_key, image.company, image.public_id
        byte_size, pk = image.byte_size, image.pk
        image.delete()
        AdminAuditLog.log(
            actor=actor, action='storefront_image_deleted',
            target_type='storefront_image', target_id=pk,
            metadata={'public_id': public_id, 'byte_size': byte_size, 'reason': reason},
            request=request, company=company,
        )
        transaction.on_commit(lambda: storage.delete_quietly(key))
    return True


def release(values, *, company, actor=None, request=None) -> int:
    """
    Un hueco de `company` dejó de apuntar a estas direcciones.

    Se llama después de guardar, dentro de la misma transacción: el recuento ya
    ve el hueco con su valor nuevo. Sólo se consideran imágenes de la propia
    empresa; la de otra nunca se borra desde aquí, aunque un dato antiguo
    apuntara a ella.
    """
    deleted = 0
    for public_id in {managed_public_id(value) for value in values} - {None}:
        image = StorefrontImage.objects.filter(public_id=public_id, company=company).first()
        if image is not None and _delete_if_unreferenced(
            image.pk, actor=actor, request=request, reason='replaced',
        ):
            deleted += 1
    return deleted


def unplaced(older_than_hours: int):
    """
    Imágenes que nadie muestra y que llevan subidas más del plazo.

    El plazo existe porque subir no coloca: una imagen recién subida puede estar
    en un formulario abierto, a punto de guardarse. El recuento aquí es sólo una
    primera criba; quien borra vuelve a contar con la fila bloqueada.
    """
    from datetime import timedelta
    from django.utils import timezone

    cutoff = timezone.now() - timedelta(hours=older_than_hours)
    return [
        image for image in StorefrontImage.objects.filter(created_at__lt=cutoff).order_by('pk')
        if reference_count(image.public_id) == 0
    ]


def delete_unplaced(image: StorefrontImage) -> bool:
    return _delete_if_unreferenced(image.pk, reason='never_placed')
