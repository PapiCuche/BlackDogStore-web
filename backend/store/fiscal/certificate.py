"""
Material de firma (certificado + clave privada) desde PKCS#12 o PEM, EN MEMORIA.

POR QUÉ AQUÍ, Y POR QUÉ EN MEMORIA
----------------------------------
El CDT del contribuyente llega como contenedor PKCS#12 (`.p12`). La firma
(`signing.py`) trabaja con PEM. La conversión ocurre EN MEMORIA: nunca se
escribe `key.pem`/`cert.pem` a disco ni se invoca `openssl ... -passin pass:`,
porque ambas cosas dejan material sensible donde otro proceso puede leerlo.

NADA DE SECRETOS EN LOS ERRORES
-------------------------------
`CertificateError` describe QUÉ falló (contraseña incorrecta, contenedor dañado,
sin clave, vencido…) pero NUNCA incluye la contraseña ni bytes de la clave. La
clave privada no se serializa a ningún log, repr ni excepción.

Módulo puro: sin Django, sin red, sin disco (el archivo lo lee el llamador y
pasa los bytes). `fiscal_config` es la frontera que lo orquesta.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs12


class CertificateError(Exception):
    """El material de firma no se pudo cargar o no es utilizable."""


@dataclass(frozen=True)
class SigningMaterial:
    """Certificado y clave privada en PEM, listos para `signing.py`."""

    cert_pem: bytes
    key_pem: bytes


@dataclass(frozen=True)
class CertificateMetadata:
    """Metadatos NO secretos de un certificado. Nunca lleva la clave privada."""

    subject: str
    issuer: str
    serial: str
    not_before: datetime
    not_after: datetime
    fingerprint_sha256: str

    def validity(self, *, now: datetime | None = None) -> str:
        now = now or datetime.now(timezone.utc)
        if now < self.not_before:
            return 'not_yet_valid'
        if now > self.not_after:
            return 'expired'
        return 'valid'


def load_pkcs12(data: bytes, password: bytes | None):
    """
    Abre un contenedor PKCS#12 y devuelve (private_key, certificate).

    Levanta CertificateError —sin filtrar la contraseña— ante una contraseña
    incorrecta o un contenedor dañado (la librería no distingue ambos casos: los
    dos son un `ValueError` al descifrar), o si falta la clave o el certificado.
    """
    try:
        key, cert, _chain = pkcs12.load_key_and_certificates(data, password)
    except ValueError:
        # Contraseña incorrecta O contenedor corrupto: cryptography no los
        # separa. No se refleja el valor de la contraseña en el mensaje.
        raise CertificateError(
            'No se pudo abrir el PKCS#12: contraseña incorrecta o archivo dañado.'
        ) from None
    if cert is None:
        raise CertificateError('El PKCS#12 no contiene un certificado.')
    if key is None:
        raise CertificateError('El PKCS#12 no contiene una clave privada.')
    return key, cert


def signing_material_from_pkcs12(data: bytes, password: bytes | None) -> SigningMaterial:
    """Convierte un `.p12` en PEM (cert + clave PKCS#8 sin cifrar) EN MEMORIA."""
    key, cert = load_pkcs12(data, password)
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    return SigningMaterial(cert_pem=cert_pem, key_pem=key_pem)


def metadata_from_certificate(cert: x509.Certificate) -> CertificateMetadata:
    """Extrae metadatos NO secretos. La clave privada no interviene."""
    return CertificateMetadata(
        subject=cert.subject.rfc4514_string(),
        issuer=cert.issuer.rfc4514_string(),
        serial=format(cert.serial_number, 'x'),
        not_before=cert.not_valid_before_utc,
        not_after=cert.not_valid_after_utc,
        fingerprint_sha256=cert.fingerprint(hashes.SHA256()).hex(),
    )


def inspect_pkcs12(data: bytes, password: bytes | None) -> CertificateMetadata:
    """Metadatos del certificado de un `.p12`, sin exponer la clave."""
    _key, cert = load_pkcs12(data, password)
    return metadata_from_certificate(cert)


def certificate_metadata_from_pem(cert_pem: bytes) -> CertificateMetadata:
    """Metadatos NO secretos de un certificado PEM. Levanta si no se lee."""
    try:
        cert = x509.load_pem_x509_certificate(cert_pem)
    except ValueError:
        raise CertificateError('El certificado de firma no se puede leer.') from None
    return metadata_from_certificate(cert)


def subject_values(cert_pem: bytes, oid=None) -> tuple[str, ...]:
    """
    Los valores del sujeto del certificado; sólo los del atributo `oid` si se da.

    SUNAT pide el RUC del titular en el campo OU (Manual del programador, §3.1).
    """
    try:
        cert = x509.load_pem_x509_certificate(cert_pem)
    except ValueError:
        raise CertificateError('El certificado de firma no se puede leer.') from None
    attributes = (cert.subject.get_attributes_for_oid(oid) if oid is not None
                  else list(cert.subject))
    return tuple(str(attribute.value) for attribute in attributes)


def is_self_signed(cert: x509.Certificate) -> bool:
    """
    ¿Es autofirmado? Sí si el emisor se llama como el sujeto (RFC 5280, «self-issued»)
    O si la firma se verifica con la clave pública del propio certificado: cambiarle
    el nombre al emisor no convierte en acreditado un certificado que se firmó a sí
    mismo. Un algoritmo que no se sabe verificar aquí cuenta como NO autofirmado:
    el resto de las reglas (RUC en el sujeto, vigencia) se aplican igual, y SUNAT
    valida la cadena al recibir.
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

    if cert.subject == cert.issuer:
        return True
    public_key = cert.public_key()
    algorithm = cert.signature_hash_algorithm
    try:
        if isinstance(public_key, rsa.RSAPublicKey):
            public_key.verify(cert.signature, cert.tbs_certificate_bytes, padding.PKCS1v15(), algorithm)
        elif isinstance(public_key, ec.EllipticCurvePublicKey):
            public_key.verify(cert.signature, cert.tbs_certificate_bytes, ec.ECDSA(algorithm))
        else:
            return False
    except (InvalidSignature, TypeError, ValueError):
        return False
    return True


def is_self_signed_pem(cert_pem: bytes) -> bool:
    """`is_self_signed` de un PEM. Levanta `CertificateError` si no se lee."""
    try:
        cert = x509.load_pem_x509_certificate(cert_pem)
    except ValueError:
        raise CertificateError('El certificado de firma no se puede leer.') from None
    return is_self_signed(cert)


def check_signing_pair(cert_pem: bytes, key_pem: bytes) -> None:
    """
    El par de firma se puede usar: ambos se leen y el certificado es el de la clave.

    Se comprueba ANTES de reservar un correlativo (FISCAL-PROD-02, revisión de
    #176). Sin esto, un PEM roto pasaba la puerta, el número se gastaba y la firma
    fallaba después. Los mensajes no llevan nada del contenido.
    """
    try:
        key = serialization.load_pem_private_key(key_pem, password=None)
    except (ValueError, TypeError):
        raise CertificateError('La clave privada de firma no se puede leer.') from None
    try:
        cert = x509.load_pem_x509_certificate(cert_pem)
    except ValueError:
        raise CertificateError('El certificado de firma no se puede leer.') from None
    spki = serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    if cert.public_key().public_bytes(*spki) != key.public_key().public_bytes(*spki):
        raise CertificateError('El certificado de firma no corresponde a la clave privada.')
