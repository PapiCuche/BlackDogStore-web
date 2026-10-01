"""
M12D — normalización y compresión de evidencias fotográficas.

    LA EVIDENCIA DEBE SER SUFICIENTEMENTE CLARA PARA VERSE,
    NO INNECESARIAMENTE PESADA.

Estas fotos existen para ver el estado físico de un equipo: un golpe, una
rayadura, una pantalla rota, un puerto sucio, una placa. No son un archivo
fotográfico, no se imprimen y nadie va a hacer zoom sobre el número de serie —
para eso existen los campos estructurados, que no se degradan al comprimir.

EL ORIGINAL DE CÁMARA NO SE CONSERVA. Guardar los 5 MB que salieron del iPhone
*además* de los 200 KB que se van a mirar duplicaría almacenamiento,
transferencia, retención y superficie de privacidad sin aportar nada al
requisito actual. Si algún día hace falta evidencia con valor forense, será una
fase con su propia política de integridad y retención, no un efecto secundario
de esta.

EL HASH DESCRIBE LO QUE REALMENTE GUARDAMOS. Se calcula sobre los bytes finales,
después de decodificar, orientar, limpiar, redimensionar y comprimir. Un hash
del upload original describiría un archivo que este sistema tira a la basura.

UNA ENTRADA .JPG NO ES NECESARIAMENTE UNA IMAGEN. Ni el `Content-Type` ni la
extensión son prueba de nada: los dos los elige quien sube el archivo. Lo único
que decide es si Pillow consigue decodificarlo.
"""

from __future__ import annotations

import hashlib
import io
import logging
from dataclasses import dataclass

from django.conf import settings

from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)

# HEIC/HEIF: un iPhone con "Alta eficiencia" —el ajuste de fábrica— entrega
# esto. Se registra al importar el módulo y NO es fatal si falta: sin el plugin
# el resto de formatos sigue funcionando y un HEIC se rechaza con un mensaje
# claro en vez de tumbar el proceso entero.
try:  # pragma: no cover - depende del entorno
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_SUPPORTED = True
except Exception:  # pragma: no cover
    HEIF_SUPPORTED = False
    logger.warning('pillow-heif no disponible: no se podrán decodificar HEIC/HEIF')

Image.init()

#: Lo que el pipeline sabe decodificar. NO se consulta la extensión para esto:
#: se intenta abrir y se mira lo que Pillow dice que era.
ACCEPTED_INPUT_FORMATS = frozenset({'JPEG', 'PNG', 'WEBP', 'HEIF', 'HEIC', 'MPO'})

#: Lo que se almacena, siempre, venga lo que venga.
OUTPUT_FORMAT = 'WEBP'
OUTPUT_MIME = 'image/webp'
OUTPUT_EXTENSION = 'webp'


class EvidenceImageError(Exception):
    """Un rechazo que quien sube la foto puede entender y corregir."""


@dataclass(frozen=True)
class ProcessedEvidenceImage:
    """
    El resultado del pipeline, y lo único que la capa de storage llega a ver.

    Todos los números describen el objeto FINAL. No hay aquí un solo dato del
    archivo que llegó, porque ese archivo ya no existe en ninguna parte.
    """

    content: bytes
    mime_type: str
    width: int
    height: int
    byte_size: int
    sha256: str
    quality: int
    attempts: int
    source_format: str
    source_width: int
    source_height: int
    source_byte_size: int
    source_sha256: str


def _setting(name, default):
    return getattr(settings, name, default)


def max_upload_bytes() -> int:
    """Lo que se acepta RECIBIR. Protege al servidor antes de decodificar nada."""
    return int(_setting('SERVICE_EVIDENCE_MAX_UPLOAD_BYTES', 25 * 1024 * 1024))


def max_edge() -> int:
    return int(_setting('SERVICE_EVIDENCE_MAX_EDGE', 1600))


def max_pixels() -> int:
    """
    Contra la bomba de descompresión.

    Un PNG de 20 KB puede declarar 30.000 × 30.000 y pedir varios GB de RAM al
    decodificarse. El límite de bytes no lo detiene porque el archivo pesa poco;
    lo que hay que mirar es lo que dice que mide.
    """
    return int(_setting('SERVICE_EVIDENCE_MAX_PIXELS', 60_000_000))


def target_bytes() -> int:
    """Un OBJETIVO, no una garantía. Ver `_compress`."""
    return int(_setting('SERVICE_EVIDENCE_TARGET_BYTES', 1_000_000))


def image_quality() -> int:
    return int(_setting('SERVICE_EVIDENCE_IMAGE_QUALITY', 75))


def min_quality() -> int:
    """
    El piso, y existe por una razón concreta.

    Comprimir hasta llegar a un número de bytes es fácil; lo difícil es no
    destruir la única cosa que la foto tenía que demostrar. Una rayadura fina
    desaparece antes que el peso del archivo, y una evidencia que ya no permite
    ver el daño no es una evidencia más ligera: no es una evidencia.
    """
    return int(_setting('SERVICE_EVIDENCE_MIN_QUALITY', 60))


def max_attempts() -> int:
    return int(_setting('SERVICE_EVIDENCE_MAX_COMPRESSION_ATTEMPTS', 6))


def _open(raw: bytes) -> Image.Image:
    """
    Decodificar, y que la decodificación sea la única autoridad sobre el tipo.

    `Image.open` es perezoso, así que `load()` es lo que de verdad ejerce el
    decodificador — y lo que revienta con un truncado. Sin ese `load()` un
    archivo corrupto pasaría la validación y fallaría más tarde, en un sitio
    donde ya no se puede devolver un 400 útil.
    """
    try:
        image = Image.open(io.BytesIO(raw))
    except UnidentifiedImageError:
        raise EvidenceImageError(
            'Ese archivo no es una imagen que podamos leer.'
        ) from None
    except Exception:
        raise EvidenceImageError('No se pudo leer la imagen.') from None

    fmt = (image.format or '').upper()
    if fmt not in ACCEPTED_INPUT_FORMATS:
        if fmt in ('HEIF', 'HEIC') and not HEIF_SUPPORTED:
            raise EvidenceImageError(
                'No se pueden procesar fotos HEIC en este servidor.'
            )
        raise EvidenceImageError(f'Formato no admitido para evidencias: {fmt or "?"}.')

    width, height = image.size
    if width < 1 or height < 1:
        raise EvidenceImageError('La imagen no tiene dimensiones válidas.')
    if width * height > max_pixels():
        # Antes de `load()`: el tamaño se lee de la cabecera, así que aquí
        # todavía no se ha reservado la memoria que este límite evita.
        raise EvidenceImageError('La imagen tiene dimensiones desproporcionadas.')

    try:
        image.load()
    except Exception:
        raise EvidenceImageError(
            'La imagen está incompleta o dañada.'
        ) from None
    return image


def _flatten(image: Image.Image) -> Image.Image:
    """
    A RGB, con la transparencia resuelta en vez de descartada.

    Una foto no necesita canal alfa, pero un PNG puede traerlo. Convertir a RGB
    sin componer primero pinta el fondo transparente de negro, y una foto de un
    equipo sobre negro es exactamente lo que no se quería ver.
    """
    if image.mode in ('RGBA', 'LA', 'PA') or (
        image.mode == 'P' and 'transparency' in image.info
    ):
        rgba = image.convert('RGBA')
        # Blanco: es el fondo de un mostrador o de una mesa de taller, no un
        # vacío. Cualquier color de marca aquí teñiría la evidencia.
        canvas = Image.new('RGB', rgba.size, (255, 255, 255))
        canvas.paste(rgba, mask=rgba.split()[-1])
        return canvas
    if image.mode != 'RGB':
        return image.convert('RGB')
    return image


def _orient(image: Image.Image) -> Image.Image:
    """
    Aplicar la orientación EXIF ANTES de tirar el EXIF.

    Las fotos de móvil casi siempre salen del sensor en horizontal y dependen de
    una etiqueta para saber qué lado es arriba. Si se limpia la metadata primero,
    la etiqueta se va y la foto queda tumbada para siempre — con el agravante de
    que en el móvil que la tomó se veía bien.
    """
    try:
        return ImageOps.exif_transpose(image) or image
    except Exception:  # pragma: no cover - EXIF corrupto
        return image


def _strip(image: Image.Image) -> Image.Image:
    """
    Quitar TODA la metadata, y hacerlo copiando los píxeles.

    `del image.info` no basta: el objeto conserva cosas que el encoder vuelve a
    escribir. La única forma fiable de garantizar que no sale un GPS es no
    reutilizar el contenedor — se crean píxeles nuevos y no se copia nada más.

    Lo que se va: GPS, marca y modelo del equipo, fecha de captura, software,
    comentarios, miniaturas EXIF. Un cliente que recibe una foto de su reparación
    no debe recibir de propina dónde estaba el técnico cuando la tomó.
    """
    clean = Image.new('RGB', image.size)
    clean.putdata(list(image.getdata()))
    return clean


def _resize(image: Image.Image) -> Image.Image:
    """
    Sólo hacia abajo. NUNCA hacia arriba.

    Ampliar una foto pequeña no añade información: inventa píxeles, engorda el
    archivo y hace parecer que hay más detalle del que se capturó.
    """
    limit = max_edge()
    width, height = image.size
    if max(width, height) <= limit:
        return image
    scale = limit / max(width, height)
    return image.resize(
        (max(1, round(width * scale)), max(1, round(height * scale))),
        Image.LANCZOS,
    )


def _encode(image: Image.Image, quality: int) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, OUTPUT_FORMAT, quality=quality, method=4)
    return buffer.getvalue()


def _compress(image: Image.Image):
    """
    Bajar el peso hasta el objetivo, pero no a cualquier precio.

    Primero se baja la calidad, que degrada de forma gradual; sólo si eso no
    basta se baja la resolución, que quita detalle de golpe. Y hay un suelo en
    las dos: alcanzado el mínimo se DEVUELVE LA IMAGEN MÁS PESADA en vez de
    seguir apretando, porque el objetivo de bytes es una preferencia y ver el
    daño es el requisito.

    El número de intentos está acotado. Un bucle que comprime "hasta que quepa"
    es un bucle que un día no termina.
    """
    limit = target_bytes()
    quality = image_quality()
    floor = min_quality()
    attempts = 1
    data = _encode(image, quality)

    while len(data) > limit and attempts < max_attempts():
        if quality > floor:
            quality = max(floor, quality - 7)
        else:
            width, height = image.size
            if max(width, height) <= 640:
                # Suelo de resolución: por debajo de esto ya no se distingue una
                # rayadura de un reflejo, y el archivo deja de servir para lo
                # único que existía.
                break
            image = image.resize(
                (max(1, round(width * 0.8)), max(1, round(height * 0.8))),
                Image.LANCZOS,
            )
        attempts += 1
        data = _encode(image, quality)

    return image, data, quality, attempts


def process(raw: bytes) -> ProcessedEvidenceImage:
    """
    El pipeline entero, y el único camino por el que una foto llega al storage.

        bytes → validar tamaño → decodificar → validar dimensiones
              → orientar → aplanar → limpiar metadata → reducir
              → comprimir → validar salida → SHA-256

    Devuelve lo que hay que guardar. No toca la base de datos, no habla con el
    storage y no sabe de qué reparación es la foto: eso lo decide quien lo llama.
    """
    if not raw:
        raise EvidenceImageError('El archivo llegó vacío.')
    if len(raw) > max_upload_bytes():
        mb = max_upload_bytes() / 1024 / 1024
        raise EvidenceImageError(f'La imagen supera el máximo de {mb:.0f} MB.')

    source = _open(raw)
    source_format = (source.format or '').upper()
    source_width, source_height = source.size

    image = _strip(_flatten(_orient(source)))
    image = _resize(image)
    image, data, quality, attempts = _compress(image)

    # Releer lo que se va a guardar. Si el encoder produjo algo que no se puede
    # abrir, es mejor descubrirlo aquí que cuando alguien intente mirar la
    # evidencia de una reparación que ya se entregó.
    try:
        check = Image.open(io.BytesIO(data))
        check.load()
    except Exception:  # pragma: no cover - fallo del encoder
        raise EvidenceImageError('No se pudo generar la imagen procesada.') from None

    return ProcessedEvidenceImage(
        content=data,
        mime_type=OUTPUT_MIME,
        width=image.size[0],
        height=image.size[1],
        byte_size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        quality=quality,
        attempts=attempts,
        source_format=source_format,
        source_width=source_width,
        source_height=source_height,
        source_byte_size=len(raw),
        # Del upload ORIGINAL, y sólo para idempotencia: dos reintentos del mismo
        # archivo deben reconocerse como el mismo request aunque el pipeline sea
        # capaz de producir bytes distintos. Nunca para deduplicar entre
        # empresas: que dos talleres suban la misma foto no es asunto de ninguno
        # de los dos.
        source_sha256=hashlib.sha256(raw).hexdigest(),
    )
