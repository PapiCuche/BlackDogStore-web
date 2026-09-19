"""
Auditoría de la firma del CDR: integridad SÍ, autenticidad TODAVÍA NO.

El Manual del programador (§2.6) afirma que TODAS las constancias van firmadas
digitalmente por SUNAT (XMLDSig). Este módulo comprueba lo que se PUEDE comprobar
con lo que hoy tenemos, y clasifica con honestidad lo que no.

INTEGRIDAD ≠ AUTENTICIDAD (ERP-FISCAL-3 §32/§33)
------------------------------------------------
- INTEGRIDAD: la firma valida matemáticamente contra el certificado que el propio
  CDR trae dentro, y lo firmado no se ha alterado. Se comprueba con `signxml`
  —biblioteca madura, ya usada para firmar—; NO se implementa XMLDSig a mano ni
  se busca «cualquier» `ds:Signature` para confiar en él (eso invita al signature
  wrapping): `signxml` verifica QUÉ elemento se firmó.
- AUTENTICIDAD: que ese certificado sea de verdad de SUNAT. Exige una ANCLA DE
  CONFIANZA (el certificado raíz de SUNAT), y SUNAT no publica una que permita
  validar la cadena de forma programática. Sin ancla, un atacante puede
  autofirmar su propio CDR: la firma «valida» contra SU certificado embebido.

Por eso este módulo NUNCA devuelve `TRUSTED`. Reporta la integridad y deja la
autenticidad como `unverified`. La aceptación fiscal de un comprobante NO se
apoya hoy en esta comprobación: **CDR-TRUST-01 = PROPUESTA**. Sirve para auditar,
y para tener la evidencia y el mecanismo listos el día que exista un ancla oficial.

Sólo lectura, parser endurecido, sin ORM.
"""

from __future__ import annotations

import base64
import textwrap
from dataclasses import dataclass

from .xmlsafe import parse_untrusted

DS_NS = 'http://www.w3.org/2000/09/xmldsig#'

#: Valores posibles de autenticidad. `TRUSTED` existe en el vocabulario pero NO
#: se emite en esta fase: sin ancla de confianza oficial no puede afirmarse.
AUTH_UNVERIFIED = 'unverified'
AUTH_TRUSTED = 'trusted'


@dataclass(frozen=True)
class CdrSignatureReport:
    """El veredicto de la auditoría de firma de un CDR."""

    has_signature: bool
    #: La firma valida matemáticamente contra el certificado EMBEBIDO en el CDR.
    #: Prueba no-alteración + firma con esa clave; NO prueba que sea SUNAT.
    integrity_valid: bool
    #: Siempre `unverified` en esta fase (§34): falta el ancla de confianza.
    authenticity: str
    detail: str = ''


def _embedded_certificate_pem(root) -> str | None:
    """Saca el certificado embebido (`ds:X509Certificate`) como PEM, si lo hay."""
    node = root.find(f'.//{{{DS_NS}}}X509Certificate')
    if node is None or not (node.text or '').strip():
        return None
    body = ''.join((node.text or '').split())
    try:
        base64.b64decode(body, validate=True)
    except Exception:  # noqa: BLE001 — base64 malformado: no hay certificado usable
        return None
    wrapped = '\n'.join(textwrap.wrap(body, 64))
    return f'-----BEGIN CERTIFICATE-----\n{wrapped}\n-----END CERTIFICATE-----\n'


def inspect_cdr_signature(xml) -> CdrSignatureReport:
    """
    Audita la firma del CDR. Sólo lectura; los bytes se verifican tal cual llegan.

    Verifica la INTEGRIDAD contra el certificado embebido (fijándolo, para no
    exigir una cadena de confianza que no tenemos). La AUTENTICIDAD queda
    `unverified` a propósito: comprobarla exigiría el certificado raíz de SUNAT.
    """
    if isinstance(xml, str):
        raw = xml.encode('utf-8')
    else:
        raw = bytes(xml)

    try:
        root = parse_untrusted(raw)
    except Exception as exc:  # noqa: BLE001 — CDR externo no confiable
        return CdrSignatureReport(
            has_signature=False, integrity_valid=False,
            authenticity=AUTH_UNVERIFIED, detail=f'CDR ilegible: {type(exc).__name__}')

    if root.find(f'.//{{{DS_NS}}}Signature') is None:
        return CdrSignatureReport(
            has_signature=False, integrity_valid=False,
            authenticity=AUTH_UNVERIFIED, detail='El CDR no trae ds:Signature.')

    cert_pem = _embedded_certificate_pem(root)
    if cert_pem is None:
        return CdrSignatureReport(
            has_signature=True, integrity_valid=False,
            authenticity=AUTH_UNVERIFIED,
            detail='La firma no trae certificado embebido verificable.')

    from signxml import XMLVerifier

    try:
        # Se FIJA el certificado embebido: verifica la firma matemáticamente
        # contra esa clave, sin exigir cadena de confianza. Es integridad, no
        # autenticidad — la distinción es justamente el punto (§33).
        XMLVerifier().verify(raw, x509_cert=cert_pem)
        integrity_valid = True
        detail = ('Integridad verificada contra el certificado embebido. '
                  'Autenticidad NO comprobada: falta el ancla de confianza de SUNAT.')
    except Exception as exc:  # noqa: BLE001 — cualquier fallo es «no integra»
        integrity_valid = False
        detail = f'La firma no valida: {type(exc).__name__}'

    return CdrSignatureReport(
        has_signature=True, integrity_valid=integrity_valid,
        authenticity=AUTH_UNVERIFIED, detail=detail)
