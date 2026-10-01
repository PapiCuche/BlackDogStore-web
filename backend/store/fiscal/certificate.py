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
