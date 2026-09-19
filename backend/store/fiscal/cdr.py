"""
Lectura del CDR (Constancia de Recepción — `ApplicationResponse` de SUNAT).

El CDR es la PRUEBA de lo que SUNAT resolvió sobre un comprobante. Se lee, nunca
se recalcula, y con el parser endurecido: aunque lo emita SUNAT, llega por la red
y se trata como entrada no confiable (misma política que la respuesta SOAP).

QUÉ HACE Y QUÉ NO
-----------------
Este módulo NO decide estados del dominio —eso es del proveedor (`provider.py`) y
del servicio de reconciliación—. Extrae, en sólo lectura, lo justo para dos cosas
que el dominio sí necesita:

1. BINDING (§ FISCAL-3.64/65). Comprobar que un CDR pertenece al comprobante que
   se está reconciliando: su identificador (`F001-1`) y el RUC del emisor tienen
   que concordar. Aceptar un CDR de OTRO comprobante llevaría el estado de una
   venta a lo que resolvió otra.

2. OBSERVACIONES (§ FISCAL-3.36/37). Conservar cada observación con SU CÓDIGO. Un
   CDR aceptado con observaciones trae notas reparables; quedarse sólo con
   «aceptado con observaciones» pierde el código que dice QUÉ reparar.

Módulo puro (sin ORM). El código SUNAT crudo nunca se traduce ni se pierde.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .xmlsafe import parse_untrusted

#: Espacios de nombres del `ApplicationResponse` UBL 2.1. `ar` es la raíz del CDR;
#: `cac`/`cbc` son los componentes comunes, los mismos que en la factura.
NS = {
    'ar': 'urn:oasis:names:specification:ubl:schema:xsd:ApplicationResponse-2',
    'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2',
    'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2',
}

#: Una nota de SUNAT suele venir como «<código> - <texto>» o «<código>: <texto>».
#: El código es de 3 a 5 dígitos (rangos 2000-3999 rechazo, 4000+ observación).
_OBSERVATION_RE = re.compile(r'^\s*(\d{3,5})\s*[-:]\s*(.*)$', re.DOTALL)


class CdrParseError(ValueError):
    """El CDR no pudo leerse. Nunca se confunde con «rechazado por SUNAT»."""


@dataclass(frozen=True)
class SunatObservation:
    """Una observación del CDR, con su código intacto (§37)."""

    #: El código de SUNAT (p. ej. «4267»). Cadena vacía si la nota no traía uno.
    code: str
    #: El texto tal cual, saneado de espacios pero SIN traducir.
    text: str


@dataclass(frozen=True)
class CdrRepresentation:
    """Lo que el CDR afirma, sin interpretar todavía a estado del dominio."""

    #: RUC de quien EMITE el CDR. Debe ser SUNAT; se expone para no confiar en el
    #: remitente a ciegas (§32/§33: integridad ≠ confianza en el emisor).
    sender_ruc: str
    #: RUC del receptor del CDR = el emisor del comprobante.
    receiver_ruc: str
    #: El identificador del comprobante al que responde el CDR: «F001-1».
    reference_id: str
    #: El código del tipo de comprobante (Catálogo 01), si el CDR lo trae.
    document_type_code: str
    #: El `ResponseCode` del CDR. `'0'` es aceptado. Cadena cruda, sin mapear.
    response_code: str
    description: str
    observations: tuple[SunatObservation, ...]


def _text(node, path: str) -> str:
    value = node.findtext(path, namespaces=NS)
    return (value or '').strip()


def parse_cdr(xml) -> CdrRepresentation:
    """
    Lee un `ApplicationResponse` con el parser endurecido. Sólo lectura.

    Levanta `CdrParseError` si no es un CDR legible: quien llama debe distinguir
    «CDR ilegible» de «SUNAT rechazó», que son cosas distintas.
    """
    if isinstance(xml, str):
        xml = xml.encode('utf-8')
    try:
        root = parse_untrusted(xml)
    except Exception as exc:  # noqa: BLE001 — input externo no confiable
        raise CdrParseError(f'CDR ilegible: {type(exc).__name__}') from None

    # El identificador del comprobante vive, según la versión del CDR, en el
    # `DocumentReference` o en el `ReferenceID` de la respuesta. Se miran ambos y
    # NO se toma el primer `cbc:ID` que aparezca (el primero es el del propio CDR).
    reference_id = (
        _text(root, 'cac:DocumentResponse/cac:DocumentReference/cbc:ID')
        or _text(root, 'cac:DocumentResponse/cac:Response/cbc:ReferenceID')
    )
    document_type_code = _text(
        root, 'cac:DocumentResponse/cac:DocumentReference/cbc:DocumentTypeCode')

    observations = tuple(
        _observation(note.strip())
        for note in root.itertext_notes() if note.strip()
    ) if hasattr(root, 'itertext_notes') else _notes(root)

    return CdrRepresentation(
        sender_ruc=_text(root, 'cac:SenderParty/cac:PartyIdentification/cbc:ID'),
        receiver_ruc=_text(root, 'cac:ReceiverParty/cac:PartyIdentification/cbc:ID'),
        reference_id=reference_id,
        document_type_code=document_type_code,
        response_code=_text(root, 'cac:DocumentResponse/cac:Response/cbc:ResponseCode'),
        description=_text(root, 'cac:DocumentResponse/cac:Response/cbc:Description'),
        observations=observations,
    )


def _notes(root) -> tuple[SunatObservation, ...]:
    """Todas las `cbc:Note` del CDR, cada una con su código conservado."""
    notes = root.findall('.//cbc:Note', NS)
    return tuple(
        _observation((n.text or '').strip())
        for n in notes if (n.text or '').strip()
    )


def _observation(text: str) -> SunatObservation:
    match = _OBSERVATION_RE.match(text)
    if match:
        return SunatObservation(code=match.group(1), text=match.group(2).strip())
    return SunatObservation(code='', text=text)


def cdr_matches_document(cdr: CdrRepresentation, *, document_id: str,
                         issuer_tax_id: str, document_type: str = '') -> bool:
    """
    ¿Este CDR responde al comprobante que se reconcilia? (§64/§65).

    Se exige que concuerden el identificador (serie-correlativo) y el RUC del
    emisor. El tipo se compara sólo si el CDR lo trae: su ausencia no debe hacer
    pasar por bueno un CDR que sí discrepa en lo que sí está.

    FALLA CERRADO: cualquier discrepancia devuelve `False`. Un CDR que no
    corresponde no se aplica, se audita.
    """
    if cdr.reference_id != document_id:
        return False
    if cdr.receiver_ruc != issuer_tax_id:
        return False
    if document_type and cdr.document_type_code \
            and cdr.document_type_code != document_type:
        return False
    return True
