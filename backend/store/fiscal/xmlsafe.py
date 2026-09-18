"""
Parseo de XML EXTERNO NO CONFIABLE (respuesta SOAP de SUNAT, CDR).

UNA sola política de endurecimiento, en un solo sitio. Todo lo que venga de la
red se parsea aquí; el XML que generamos nosotros NO usa esto (es confiable y su
parser vive donde se construye).

QUÉ SE DEFIENDE
---------------
- XXE / lectura de ficheros / SSRF: `resolve_entities=False`, `no_network=True`,
  `load_dtd=False`. Las entidades externas no se resuelven y no se toca la red.
- Bombas de entidades (billion laughs): `resolve_entities=False` impide la
  expansión; `huge_tree=False` mantiene los topes internos de libxml2.
- DOCTYPE: se RECHAZA. SUNAT no necesita DTD en una respuesta SOAP/CDR, y un
  DOCTYPE en input externo no tiene un motivo legítimo (§23).
- Tamaño: se rechaza por encima de un tope explícito antes de construir el árbol
  (§26). No usamos una dependencia nueva; `lxml` configurado basta (§1.4).
"""

from __future__ import annotations

from lxml import etree

#: Un CDR o una respuesta SOAP de SUNAT pesan kilobytes. Megabytes es patológico;
#: 5 MiB deja margen de sobra para un CDR legítimo y corta un intento de agotar
#: memoria con una respuesta enorme.
MAX_UNTRUSTED_XML_BYTES = 5 * 1024 * 1024


class UntrustedXmlError(ValueError):
    """El XML externo se rechazó antes de poder confiar en él."""


def _hardened_parser() -> etree.XMLParser:
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        recover=False,
        huge_tree=False,
    )


def parse_untrusted(data: bytes) -> etree._Element:
    """Parsea input externo con la política endurecida. Levanta UntrustedXmlError."""
    if not isinstance(data, (bytes, bytearray)):
        raise UntrustedXmlError('Se esperaban bytes de XML externo.')
    if len(data) > MAX_UNTRUSTED_XML_BYTES:
        raise UntrustedXmlError(
            f'XML externo demasiado grande ({len(data)} bytes; '
            f'máximo {MAX_UNTRUSTED_XML_BYTES}).'
        )
    try:
        root = etree.fromstring(bytes(data), parser=_hardened_parser())
    except etree.XMLSyntaxError as exc:
        raise UntrustedXmlError(f'XML externo no válido: {type(exc).__name__}') from None
    if root.getroottree().docinfo.doctype:
        raise UntrustedXmlError('XML externo con DOCTYPE: se rechaza.')
    return root
