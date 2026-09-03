"""
M12D — dónde vive el objeto, y por qué el dominio no lo sabe.

    R2 ES INFRAESTRUCTURA. EL DOMINIO NO DEPENDE DE R2.

El dominio necesita cuatro verbos: guardar, abrir, dar acceso temporal y borrar
un huérfano. No necesita saber que detrás hay Cloudflare, ni un endpoint, ni una
clave, ni un bucket. Cuando mañana el proveedor cambie —y en algún momento
cambia— lo que se reescribe es este archivo y nada más.

    EL STORAGE KEY NO ES AUTORIZACIÓN.

Saber que existe `companies/4/service/32/evidence/ab…webp` no permite
descargarlo. La ruta es una dirección, no una llave: el bucket es privado y cada
acceso se autoriza antes de tocar el objeto. Un esquema donde adivinar la ruta
bastara sería seguridad por oscuridad, y la oscuridad se agota en cuanto alguien
comparte un enlace.

IMPORTACIÓN PEREZOSA, Y NO ES UN DETALLE. `boto3` y `django-storages` sólo se
cargan cuando el backend elegido es S3. Importarlos arriba haría que cualquier
`manage.py test` en una máquina sin esas dependencias —que hoy es esta misma—
fallara al importar los modelos, mucho antes de llegar a una sola prueba. Un
desarrollo local nunca debería necesitar credenciales de producción para correr.
"""

from __future__ import annotations

import logging
import posixpath
import uuid
from dataclasses import dataclass

from django.conf import settings
from django.core.files.base import ContentFile

logger = logging.getLogger(__name__)

BACKEND_FILESYSTEM = 'filesystem'
BACKEND_S3 = 's3'


class EvidenceStorageError(Exception):
    """El storage no pudo cumplir. Nunca lleva credenciales dentro."""


@dataclass(frozen=True)
class StoredObject:
    key: str
    byte_size: int


def build_key(*, company_id: int, repair_order_id: int, extension: str) -> str:
    """
    La ruta, generada SIEMPRE en el servidor.

    Un UUID y nada más. Sin nombre de cliente, sin DNI, sin correo, sin IMEI,
    sin serie y sin el nombre del archivo que subió el técnico: cualquiera de
    esos convertiría el listado de un bucket en una filtración de datos
    personales, y el nombre original es además lo que un atacante controla.

    El prefijo por empresa y por reparación existe para operar —borrar, auditar,
    migrar—, no para separar permisos. Los permisos los decide el endpoint.
    """
    name = f'{uuid.uuid4().hex}.{extension.lstrip(".")}'
    return posixpath.join(
        'companies', str(int(company_id)),
        'service', str(int(repair_order_id)),
        'evidence', name,
    )


def _backend_name() -> str:
    return str(getattr(settings, 'EVIDENCE_STORAGE_BACKEND', BACKEND_FILESYSTEM)).lower()


def _s3_storage():
    """
    Construye el backend S3, y falla RUIDOSAMENTE si no puede.

    Una configuración que dice "usa R2" y silenciosamente escribe en disco local
    es la peor de las dos opciones: en producción parecería funcionar hasta que
    alguien buscara una evidencia que nunca salió del contenedor.
    """
    try:
        from storages.backends.s3boto3 import S3Boto3Storage
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise EvidenceStorageError(
            'EVIDENCE_STORAGE_BACKEND=s3 requiere django-storages y boto3 '
            'instalados.'
        ) from exc

    bucket = getattr(settings, 'EVIDENCE_STORAGE_BUCKET', '')
    if not bucket:
        raise EvidenceStorageError('Falta EVIDENCE_STORAGE_BUCKET.')

    return S3Boto3Storage(
        bucket_name=bucket,
        endpoint_url=getattr(settings, 'EVIDENCE_STORAGE_ENDPOINT_URL', '') or None,
        access_key=getattr(settings, 'EVIDENCE_STORAGE_ACCESS_KEY_ID', '') or None,
        secret_key=getattr(settings, 'EVIDENCE_STORAGE_SECRET_ACCESS_KEY', '') or None,
        region_name=getattr(settings, 'EVIDENCE_STORAGE_REGION', '') or None,
        # PRIVADO, y escrito aquí en vez de confiado a la consola del proveedor.
        # Un bucket que alguien dejó público por error deja de ser un problema
        # de configuración remota y pasa a ser una línea de código revisable.
        default_acl='private',
        querystring_auth=True,
        file_overwrite=False,
        # Contenido privado: ningún CDN intermedio debe quedarse una evidencia.
        object_parameters={'CacheControl': 'private, max-age=0, no-store'},
    )


def _filesystem_storage():
    from django.core.files.storage import FileSystemStorage

    location = getattr(settings, 'EVIDENCE_STORAGE_ROOT', None)
    if not location:
        location = posixpath.join(str(settings.BASE_DIR), 'private-media', 'evidence')
    # Sin `base_url`: no existe una URL pública para estos objetos, y no tenerla
    # es lo que impide que alguien la construya por descuido.
    return FileSystemStorage(location=location, base_url=None)


def get_storage():
    """
    El backend activo. Se construye al pedirlo, nunca al importar el módulo.

    Importar Django no debe abrir un cliente remoto ni validar un bucket por
    red: una suite de tests hace cientos de importaciones y ninguna tiene por
    qué salir a internet.
    """
    name = _backend_name()
    if name == BACKEND_S3:
        return _s3_storage()
    if name == BACKEND_FILESYSTEM:
        return _filesystem_storage()
    raise EvidenceStorageError(f'EVIDENCE_STORAGE_BACKEND desconocido: {name}.')


def save(key: str, content: bytes) -> StoredObject:
    storage = get_storage()
    try:
        written = storage.save(key, ContentFile(content))
    except Exception as exc:
        # El mensaje del proveedor puede traer el endpoint o parte de una firma.
        # Se registra el tipo, no el texto.
        logger.error('fallo al guardar evidencia: %s', type(exc).__name__)
        raise EvidenceStorageError('No se pudo guardar la imagen.') from None
    return StoredObject(key=written, byte_size=len(content))


def open_stream(key: str):
    storage = get_storage()
    try:
        return storage.open(key, 'rb')
    except Exception as exc:
        logger.error('fallo al abrir evidencia: %s', type(exc).__name__)
        raise EvidenceStorageError('No se pudo leer la imagen.') from None


def temporary_url(key: str):
    """
    Un enlace firmado y corto, o `None` si este backend no sabe firmar.

    Devolver `None` es deliberado: el llamador entonces sirve los bytes él
    mismo, autenticando la petición. Lo que NO se hace nunca es inventar una URL
    pública para tapar el hueco.

    La URL no se persiste. Una firma guardada en la base de datos sobrevive a la
    revocación del acceso que la justificaba.
    """
    if _backend_name() != BACKEND_S3:
        return None
    ttl = int(getattr(settings, 'EVIDENCE_STORAGE_URL_TTL_SECONDS', 300))
    storage = get_storage()
    try:
        return storage.url(key, expire=ttl)
    except Exception as exc:  # pragma: no cover - depende del proveedor
        logger.error('fallo al firmar evidencia: %s', type(exc).__name__)
        return None


def delete_quietly(key: str) -> bool:
    """
    Borrar un objeto que quedó huérfano de una transacción que no cuajó.

    Compensación, no una operación de usuario: NO existe borrado físico desde la
    API. Si falla se registra y se sigue, porque el fallo del limpiado no puede
    convertirse en el fallo de la petición — y un objeto huérfano en un bucket
    privado es un desperdicio, no una fuga.
    """
    if not key:
        return False
    try:
        get_storage().delete(key)
        return True
    except Exception as exc:
        logger.warning(
            'objeto de evidencia huérfano, no se pudo borrar (%s)',
            type(exc).__name__,
        )
        return False
