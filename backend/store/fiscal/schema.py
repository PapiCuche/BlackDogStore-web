"""
Validación del XML contra el esquema oficial de SUNAT.

POR QUÉ LOS ESQUEMAS ESTÁN EN EL REPOSITORIO Y NO SE DESCARGAN
--------------------------------------------------------------
Un test que descarga un XSD depende de que SUNAT esté disponible, de que la URL
no cambie y de que el contenido de hoy sea el de ayer. Ninguna de las tres cosas
es cierta a largo plazo, y las tres convierten un fallo de red en un test rojo
que no señala ningún defecto del proyecto.

Los esquemas están versionados en `schemas/2.1/`. Su procedencia, su fecha de
publicación y el SHA-256 del paquete original están en `schemas/PROCEDENCIA.md`.

QUÉ VALIDA Y QUÉ NO
-------------------
El XSD comprueba estructura, ORDEN de elementos y tipos de dato. No comprueba
catálogos, ni que el IGV cuadre, ni el correlativo, ni la firma. Pasar el
esquema NO es pasar SUNAT — por eso existe además `rules.py`.

Que el orden importe no es un detalle: UBL es una secuencia XSD, así que un
documento con todos los campos correctos en el orden equivocado es inválido, y
tiene que fallar AQUÍ y no en la red.
"""

from __future__ import annotations

import functools
from pathlib import Path

from lxml import etree

SCHEMA_DIR = Path(__file__).parent / 'schemas' / '2.1'
INVOICE_XSD = SCHEMA_DIR / 'maindoc' / 'UBL-Invoice-2.1.xsd'


class SchemaError(Exception):
    """El XML no cumple el esquema. El mensaje dice qué nodo y por qué."""


@functools.lru_cache(maxsize=4)
def _schema(path: str) -> etree.XMLSchema:
    """
    Compila el esquema una vez por proceso.

    Compilarlo cuesta bastante —resuelve una cadena de imports relativos entre
    catorce ficheros—, y una suite que lo rehiciera en cada test pagaría ese
    precio cientos de veces.
    """
    return etree.XMLSchema(etree.parse(path))


def validate_invoice(xml: bytes) -> None:
    """
    Valida una factura contra `UBL-Invoice-2.1.xsd`. Levanta con el detalle.

    El mensaje de error incluye la línea y el nodo que falla, que es la
    diferencia entre «el XML es inválido» y poder arreglarlo.
    """
    try:
        doc = etree.fromstring(xml)
    except etree.XMLSyntaxError as exc:
        raise SchemaError(f'El XML no está bien formado: {exc}') from None

    schema = _schema(str(INVOICE_XSD))
    if not schema.validate(doc):
        problemas = '; '.join(
            f'línea {e.line}: {e.message}' for e in schema.error_log
        )
        raise SchemaError(f'No valida contra UBL-Invoice-2.1.xsd — {problemas}')
