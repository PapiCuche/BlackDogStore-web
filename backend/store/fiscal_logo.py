"""
El logotipo de la tienda en sus comprobantes, CONGELADO con cada uno.

DE DÓNDE SALE. La tienda sube una imagen desde el panel —la misma tubería que
las de la portada— y la elige en `CompanySettings.document_logo_url`.

POR QUÉ SE COPIA. Un comprobante reimpreso tiene que decir lo que dijo. El
nombre, el RUC y la dirección del emisor ya se congelan en `FiscalDocument`; el
logotipo es parte de esa misma identidad. Si el papel leyera el logotipo vigente,
una factura de hace dos años saldría con una marca que entonces no existía, y
una tienda que borra su logotipo dejaría sin él a todos sus comprobantes.

Por eso, al emitir, se guarda una copia PROPIA de la imagen bajo una clave que
depende de su contenido. Esa copia no está en ninguna tabla de imágenes de la
tienda, no se cuenta entre las que la limpieza puede borrar y no cambia nunca:
mil comprobantes con el mismo logotipo comparten un archivo, y cambiarlo crea
otro sin tocar el anterior.

SIN ADORNO. La copia se aplana sobre blanco, que es el color del papel: sin
canal alfa no hay máscara, y sin máscara no hay nada con lo que fabricar una
sombra. Se dibuja una vez, tal cual.

NUNCA DETIENE UNA EMISIÓN NI UNA REIMPRESIÓN. Si la imagen no se puede leer, el
comprobante se emite —o se reimprime— sin logotipo. Un adorno no vale un
comprobante.
"""
from __future__ import annotations

import hashlib
import io
import logging

from . import evidence_storage as storage
from .models import CompanySettings, StorefrontImage

logger = logging.getLogger(__name__)

#: Lado mayor de la copia. Un logotipo impreso a 5 cm no necesita más.
MAX_EDGE = 800


def _normalised(raw: bytes) -> bytes:
    """PNG en RGB sobre blanco, sin metadatos y de tamaño acotado."""
    from PIL import Image

    image = Image.open(io.BytesIO(raw))
    image.load()
    image = image.convert('RGBA')
    if max(image.size) > MAX_EDGE:
        scale = MAX_EDGE / max(image.size)
        image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.LANCZOS)
    paper = Image.new('RGB', image.size, (255, 255, 255))
    paper.paste(image, mask=image.getchannel('A'))
    out = io.BytesIO()
    paper.save(out, 'PNG', optimize=True)
    return out.getvalue()


def _exists(key: str) -> bool:
    # Se pregunta al almacén, no se intenta abrir: abrir lo que todavía no
    # existe es lo normal la primera vez y dejaría un error en el registro.
    try:
        return bool(storage.get_storage().exists(key))
    except Exception:  # noqa: BLE001 - sin respuesta, se intenta guardar
        return False


def snapshot(company) -> dict:
    """
    Los campos de logotipo para un comprobante que se emite AHORA.

    `{}` si la tienda no tiene logotipo o no se pudo leer: el llamador lo
    despliega en la creación del comprobante y, vacío, no cambia nada.
    """
    from . import storefront_media

    try:
        row = CompanySettings.objects.filter(company=company).only('document_logo_url').first()
        public_id = storefront_media.managed_public_id(row.document_logo_url) if row else None
        if not public_id:
            return {}
        image = StorefrontImage.objects.filter(public_id=public_id, company=company).first()
        if image is None:
            return {}
        with storage.open_stream(image.storage_key) as stream:
            content = _normalised(stream.read())
        digest = hashlib.sha256(content).hexdigest()
        key = f'companies/{company.pk}/fiscal/logos/{digest}.png'
        if not _exists(key):
            key = storage.save(key, content).key
        return {'logo_storage_key': key, 'logo_sha256': digest}
    except Exception:  # noqa: BLE001 - un logotipo no puede impedir una emisión
        logger.warning(
            'No se pudo congelar el logotipo de la empresa %s; el comprobante se emite sin él.',
            getattr(company, 'pk', None), exc_info=True)
        return {}


def load(document) -> bytes | None:
    """La imagen congelada de `document`, o None si no tiene o no se puede leer."""
    key = getattr(document, 'logo_storage_key', '') or ''
    if not key:
        return None
    try:
        with storage.open_stream(key) as stream:
            return stream.read()
    except storage.EvidenceStorageError:
        logger.warning('Logotipo congelado ilegible (%s); se imprime sin él.', key)
        return None
