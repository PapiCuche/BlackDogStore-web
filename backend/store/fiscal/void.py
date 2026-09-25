"""
El XML de la Comunicación de Baja (`VoidedDocuments`).

QUÉ DA DE BAJA, Y QUÉ NO
------------------------
Da de baja la NUMERACIÓN de comprobantes que NO fueron otorgados (artículo 14 de
la RS 097-2012, sustituido en bloque por la RS 114-2019). No corrige un
comprobante —eso es una nota— y no borra nada: el comprobante original conserva su
XML firmado, su CDR y su historia. La baja es un hecho NUEVO que se añade.

POR QUÉ ESTE MÓDULO SÍ TIENE XSD OFICIAL Y EL RESUMEN NO
--------------------------------------------------------
`UBLPE-VoidedDocuments-1.0.xsd` viene en el paquete oficial de SUNAT y está
versionado en `schemas/2.0/`, así que aquí NO hay validación artesanal: se valida
contra el esquema de verdad (`schema.validate_voided_documents`). El Resumen por
documento, en cambio, sigue sin esquema oficial publicado — ver RC-XSD-01 y
`schemas/PROCEDENCIA.md`, y NO confundir un caso con el otro.

LO QUE ES FÁCIL EQUIVOCAR
-------------------------
- `cbc:ReferenceDate` va ANTES de `cbc:IssueDate`.
- En la línea, sólo los TRES ÚLTIMOS hijos son `sac:`; `LineID` y
  `DocumentTypeCode` son `cbc:`.
- `cbc:CustomizationID` es **1.0** aquí. El Resumen por documento usa 1.1; copiar
  al hermano sin mirar produciría un valor equivocado.
- El identificador `RA-yyyyMMdd-N` NO lleva el RUC. El RUC aparece sólo en el
  nombre del archivo (`RUC-RA-yyyyMMdd-N`).

Las primitivas UBL 2.0 (`_q`, `_el`, los espacios de nombres) se reutilizan del
Resumen: es la misma familia de esquemas y duplicarlas las dejaría derivar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from lxml import etree

from .summary import (
    CAC_NS, CBC_NS, DOC_RUC, DS_NS, EXT_NS, SAC_NS, _el, _q,
)

#: Espacio de nombres propio de la Comunicación de Baja.
VD_NS = 'urn:sunat:names:specification:ubl:peru:schema:xsd:VoidedDocuments-1'

#: `RA-<fecha de generación yyyyMMdd>-<correlativo hasta 5 posiciones>`, an..17.
VOID_ID_RE = re.compile(r'^RA-\d{8}-\d{1,5}$')

#: Longitud máxima del motivo (`sac:VoidReasonDescription`, an..100, Anexo N.º 9).
REASON_MAX = 100

#: Catálogo N.º 01. Lo que esta fase sabe dar de baja por este canal: la factura y
#: las notas vinculadas a una factura. La BOLETA no va por aquí —se anula informando
#: el Resumen— y por eso no está en esta lista.
VOIDABLE_TYPES = ('01', '07', '08')


class VoidStructureError(ValueError):
    """Los datos de la baja no pueden formar un XML válido. Señala el dato."""


@dataclass(frozen=True)
class VoidLine:
    """Un comprobante dentro de la comunicación."""

    line_id: int
    #: Catálogo N.º 01: `01` factura, `07` nota de crédito, `08` nota de débito.
    document_type: str
    #: `sac:DocumentSerialID`: la SERIE (`F001`), sin el número.
    document_serial: str
    #: `sac:DocumentNumberID`: el correlativo, sin la serie.
    document_number: int
    #: `sac:VoidReasonDescription`: el motivo. Texto libre —no hay catálogo de
    #: motivos de baja—, obligatorio y de hasta 100 caracteres.
    reason: str


@dataclass(frozen=True)
class VoidData:
    """
    Una Comunicación de Baja completa, lista para convertirse en XML.

    `reference_date` es UNA sola fecha para toda la comunicación porque el artículo
    14.1.b sólo admite agrupar documentos «generados o emitidos en un mismo día».
    Agrupar está permitido; mezclar días, no.
    """

    #: `RA-yyyyMMdd-N`. Entra YA ASIGNADO: reservar un correlativo es un acto con
    #: consecuencias y no ocurre dentro de un generador de XML.
    identifier: str
    #: `cbc:IssueDate`: la fecha de GENERACIÓN de la comunicación.
    issue_date: date
    #: `cbc:ReferenceDate`: el día común de los comprobantes que se dan de baja.
    reference_date: date
    supplier_ruc: str
    supplier_name: str
    lines: tuple[VoidLine, ...]
    notes: tuple[str, ...] = field(default_factory=tuple)

    def check(self) -> None:
        """Todo lo comprobable antes de generar. Levanta al primer incumplimiento."""
        if not VOID_ID_RE.fullmatch(self.identifier or ''):
            raise VoidStructureError(
                f'El identificador debe cumplir RA-yyyyMMdd-correlativo; llegó '
                f'{self.identifier!r}.')
        if not self.supplier_ruc or len(self.supplier_ruc) != 11:
            raise VoidStructureError(
                f'El emisor se identifica con RUC de 11 dígitos; llegó '
                f'{self.supplier_ruc!r}.')
        if not self.supplier_name.strip():
            raise VoidStructureError('Falta la razón social del emisor.')
        if not self.lines:
            raise VoidStructureError('Una comunicación de baja sin líneas no baja nada.')

        vistos = set()
        for line in self.lines:
            if line.document_type not in VOIDABLE_TYPES:
                raise VoidStructureError(
                    f'Línea {line.line_id}: por este canal sólo se dan de baja '
                    f'{", ".join(VOIDABLE_TYPES)} (la boleta se anula informando el '
                    f'Resumen). Llegó {line.document_type!r}.')
            if not line.document_serial.strip():
                raise VoidStructureError(f'Línea {line.line_id}: falta la serie.')
            if line.document_number <= 0:
                raise VoidStructureError(
                    f'Línea {line.line_id}: el número debe ser mayor que cero.')
            motivo = (line.reason or '').strip()
            if not motivo:
                raise VoidStructureError(
                    f'Línea {line.line_id}: la baja exige un motivo.')
            if len(motivo) > REASON_MAX:
                raise VoidStructureError(
                    f'Línea {line.line_id}: el motivo admite hasta {REASON_MAX} '
                    f'caracteres; llegó {len(motivo)}.')
            if line.line_id in vistos:
                raise VoidStructureError(f'LineID repetido: {line.line_id}.')
            vistos.add(line.line_id)


def _line(parent, line: VoidLine) -> None:
    """
    Una `sac:VoidedDocumentsLine`. Los CINCO hijos son obligatorios y el reparto de
    espacios de nombres es el que el XSD exige: los dos primeros `cbc:`, los tres
    últimos `sac:`.
    """
    node = _el(parent, SAC_NS, 'VoidedDocumentsLine')
    _el(node, CBC_NS, 'LineID', line.line_id)
    _el(node, CBC_NS, 'DocumentTypeCode', line.document_type)
    _el(node, SAC_NS, 'DocumentSerialID', line.document_serial)
    _el(node, SAC_NS, 'DocumentNumberID', line.document_number)
    _el(node, SAC_NS, 'VoidReasonDescription', line.reason.strip())


def build_void_xml(data: VoidData) -> bytes:
    """
    El XML de la Comunicación de Baja, SIN firmar, en el orden que exige el esquema.

    La firma va en el `ext:ExtensionContent` (vacío aquí); `signing.sign_invoice` la
    coloca en el último, igual que en la factura y en el Resumen.
    """
    data.check()

    root = etree.Element(_q(VD_NS, 'VoidedDocuments'), nsmap={
        None: VD_NS, 'cac': CAC_NS, 'cbc': CBC_NS,
        'ext': EXT_NS, 'sac': SAC_NS, 'ds': DS_NS,
    })

    extensions = _el(root, EXT_NS, 'UBLExtensions')
    _el(_el(extensions, EXT_NS, 'UBLExtension'), EXT_NS, 'ExtensionContent')

    _el(root, CBC_NS, 'UBLVersionID', '2.0')
    # 1.0, no 1.1: es la versión de la comunicación de baja.
    _el(root, CBC_NS, 'CustomizationID', '1.0')
    _el(root, CBC_NS, 'ID', data.identifier)
    _el(root, CBC_NS, 'ReferenceDate', data.reference_date.isoformat())
    _el(root, CBC_NS, 'IssueDate', data.issue_date.isoformat())

    for note in data.notes:
        _el(root, CBC_NS, 'Note', note)

    signature = _el(root, CAC_NS, 'Signature')
    _el(signature, CBC_NS, 'ID', data.identifier)
    party = _el(signature, CAC_NS, 'SignatoryParty')
    ident = _el(party, CAC_NS, 'PartyIdentification')
    _el(ident, CBC_NS, 'ID', data.supplier_ruc)
    name = _el(party, CAC_NS, 'PartyName')
    _el(name, CBC_NS, 'Name', data.supplier_name)
    attachment = _el(signature, CAC_NS, 'DigitalSignatureAttachment')
    external = _el(attachment, CAC_NS, 'ExternalReference')
    _el(external, CBC_NS, 'URI', '#SignatureSP')

    supplier = _el(root, CAC_NS, 'AccountingSupplierParty')
    _el(supplier, CBC_NS, 'CustomerAssignedAccountID', data.supplier_ruc)
    _el(supplier, CBC_NS, 'AdditionalAccountID', DOC_RUC)
    sup_party = _el(supplier, CAC_NS, 'Party')
    legal = _el(sup_party, CAC_NS, 'PartyLegalEntity')
    _el(legal, CBC_NS, 'RegistrationName', data.supplier_name)

    for line in data.lines:
        _line(root, line)

    return etree.tostring(root, xml_declaration=True, encoding='UTF-8')
