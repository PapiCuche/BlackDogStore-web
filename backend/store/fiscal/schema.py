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
CREDIT_NOTE_XSD = SCHEMA_DIR / 'maindoc' / 'UBL-CreditNote-2.1.xsd'
DEBIT_NOTE_XSD = SCHEMA_DIR / 'maindoc' / 'UBL-DebitNote-2.1.xsd'

#: El paquete UBL 2.0 de SUNAT, para los documentos que no son UBL 2.1.
SCHEMA_DIR_20 = Path(__file__).parent / 'schemas' / '2.0'
#: Comunicación de Baja. Éste SÍ es el esquema oficial que nos aplica.
VOIDED_DOCUMENTS_XSD = SCHEMA_DIR_20 / 'maindoc' / 'UBLPE-VoidedDocuments-1.0.xsd'

#: OJO — NO existe aquí un `SUMMARY_XSD`, y es deliberado. El paquete trae
#: `UBLPE-SummaryDocuments-1.0.xsd`, pero es el Resumen por RANGOS de 2012 (línea
#: con `Start/EndDocumentNumberID`, sin `cac:Status` ni adquirente), no el Resumen
#: por DOCUMENTO que emite `summary.py`. Validar contra él rechazaría documentos
#: correctos, así que el Resumen conserva su validación estructural propia
#: (RC-XSD-01 sigue PARCIAL). Ver `schemas/PROCEDENCIA.md`.


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


def _validate_against(xml: bytes, xsd_path: Path, label: str) -> None:
    try:
        doc = etree.fromstring(xml)
    except etree.XMLSyntaxError as exc:
        raise SchemaError(f'El XML no está bien formado: {exc}') from None
    schema = _schema(str(xsd_path))
    if not schema.validate(doc):
        problemas = '; '.join(
            f'línea {e.line}: {e.message}' for e in schema.error_log
        )
        raise SchemaError(f'No valida contra {label} — {problemas}')


def validate_credit_note(xml: bytes) -> None:
    """Valida una Nota de Crédito contra `UBL-CreditNote-2.1.xsd`."""
    _validate_against(xml, CREDIT_NOTE_XSD, 'UBL-CreditNote-2.1.xsd')


def validate_debit_note(xml: bytes) -> None:
    """Valida una Nota de Débito contra `UBL-DebitNote-2.1.xsd`."""
    _validate_against(xml, DEBIT_NOTE_XSD, 'UBL-DebitNote-2.1.xsd')


def validate_voided_documents(xml: bytes) -> None:
    """
    Valida una Comunicación de Baja contra `UBLPE-VoidedDocuments-1.0.xsd`.

    Aquí SÍ hay esquema oficial —viene en el paquete de SUNAT y está versionado en
    `schemas/2.0/`—, así que la baja no necesita validación artesanal. Es la
    diferencia con el Resumen por documento, que sigue sin esquema publicado.
    """
    _validate_against(xml, VOIDED_DOCUMENTS_XSD, 'UBLPE-VoidedDocuments-1.0.xsd')
