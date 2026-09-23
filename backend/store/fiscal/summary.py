"""
El Resumen Diario de Boletas (RC): datos planos y generación del XML.

ES OTRO DOCUMENTO, NO UNA FACTURA (ERP-FISCAL-4 §36)
---------------------------------------------------
El resumen es UBL 2.0 `SummaryDocuments`, con espacios de nombres y estructura
propios (`sac:SummaryDocumentsLine`, importes por `sac:BillingPayment`). No se
construye con el generador de facturas: comparten la idea de «un XML de SUNAT»,
no la forma. Por eso vive en su propio módulo, con su propio contrato de datos.

QUÉ NO HACE
-----------
No consulta ORM, no reserva correlativos, no firma. Recibe datos ya resueltos
—incluido el `identifier` RC-yyyyMMdd-NNN— y transcribe. Reservar el correlativo
es un acto con consecuencias y no puede ocurrir dentro de algo que se llama para
«ver cómo queda el XML».

XSD
---
El paquete XSD de SUNAT trae el esquema `SummaryDocuments-1` en su árbol 2.0. No
está incluido en el repositorio (ver la nota de ERP-FISCAL-4): la estructura de
este generador se apoya en la Guía del Resumen Diario y en ejemplos oficiales, y
se comprueba con pruebas de estructura y —al final— contra BETA.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from lxml import etree

#: `SummaryDocuments-1` es la raíz; `sac:` son los componentes de SUNAT (UBL 2.0).
SD_NS = 'urn:sunat:names:specification:ubl:peru:schema:xsd:SummaryDocuments-1'
SAC_NS = 'urn:sunat:names:specification:ubl:peru:schema:xsd:SunatAggregateComponents-1'
CAC_NS = 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2'
CBC_NS = 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2'
EXT_NS = 'urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2'
DS_NS = 'http://www.w3.org/2000/09/xmldsig#'

#: Catálogo N.º 06, código 6 = RUC. El emisor siempre.
DOC_RUC = '6'


@dataclass(frozen=True)
class SummaryLine:
    """Una boleta dentro del resumen. Los importes vienen del comprobante."""

    line_id: int
    document_type: str          # Catálogo N.º 01: '03' boleta
    document_id: str            # 'B001-1'
    customer_doc_type: str      # Catálogo N.º 06 (o '0' sin documento)
    customer_doc_number: str    # o '0'
    condition_code: str         # Catálogo N.º 19: '1' Adicionar
    total: Decimal              # importe total (con impuesto)
    taxable_amount: Decimal     # operaciones gravadas (base)
    exempt_amount: Decimal      # exoneradas
    unaffected_amount: Decimal  # inafectas
    tax_amount: Decimal         # IGV


@dataclass(frozen=True)
class SummaryData:
    """Un Resumen Diario completo, listo para convertirse en XML."""

    identifier: str             # RC-yyyyMMdd-NNN
    issue_date: date            # fecha de GENERACIÓN del resumen
    reference_date: date        # fecha de EMISIÓN de las boletas
    supplier_ruc: str
    supplier_name: str
    lines: tuple[SummaryLine, ...]
    currency: str = 'PEN'

    def check(self) -> None:
        if not self.lines:
            raise ValueError('Un resumen sin líneas no es un resumen.')
        # SUNAT: máximo 500 líneas por resumen (Anexo N.º 6, 6.1.3.b.3).
        if len(self.lines) > 500:
            raise ValueError(
                f'Un resumen admite 500 líneas como máximo; llegaron '
                f'{len(self.lines)}. Deben repartirse en varios RC.'
            )
        if self.issue_date < self.reference_date:
            raise ValueError(
                'La fecha de generación del resumen no puede ser anterior a la '
                'fecha de emisión de las boletas.'
            )


def _q(ns: str, tag: str) -> str:
    return f'{{{ns}}}{tag}'


def _el(parent, ns: str, tag: str, text=None, **attrs):
    node = etree.SubElement(parent, _q(ns, tag))
    if text is not None:
        node.text = str(text)
    for key, value in attrs.items():
        node.set(key, str(value))
    return node


def _money(value: Decimal) -> str:
    return f'{Decimal(value):.2f}'


def _billing_payment(parent, amount: Decimal, instruction: str, currency: str):
    """`sac:BillingPayment`: importe por tipo de operación (Cat. instrucción)."""
    node = _el(parent, SAC_NS, 'BillingPayment')
    _el(node, CBC_NS, 'PaidAmount', _money(amount), currencyID=currency)
    _el(node, CBC_NS, 'InstructionID', instruction)


def _line(parent, line: SummaryLine, currency: str):
    node = _el(parent, SAC_NS, 'SummaryDocumentsLine')
    _el(node, CBC_NS, 'LineID', line.line_id)
    _el(node, CBC_NS, 'DocumentTypeCode', line.document_type)
    _el(node, CBC_NS, 'ID', line.document_id)

    customer = _el(node, CAC_NS, 'AccountingCustomerParty')
    _el(customer, CBC_NS, 'CustomerAssignedAccountID', line.customer_doc_number)
    _el(customer, CBC_NS, 'AdditionalAccountID', line.customer_doc_type)

    status = _el(node, CAC_NS, 'Status')
    _el(status, CBC_NS, 'ConditionCode', line.condition_code)

    _el(node, SAC_NS, 'TotalAmount', _money(line.total), currencyID=currency)

    # SUNAT quiere las tres, aunque valgan 0: 01 gravadas, 02 exoneradas, 03
    # inafectas. Su ausencia es un rechazo, no una omisión permitida.
    _billing_payment(node, line.taxable_amount, '01', currency)
    _billing_payment(node, line.exempt_amount, '02', currency)
    _billing_payment(node, line.unaffected_amount, '03', currency)

    tax_total = _el(node, CAC_NS, 'TaxTotal')
    _el(tax_total, CBC_NS, 'TaxAmount', _money(line.tax_amount), currencyID=currency)
    subtotal = _el(tax_total, CAC_NS, 'TaxSubtotal')
    _el(subtotal, CBC_NS, 'TaxAmount', _money(line.tax_amount), currencyID=currency)
    category = _el(subtotal, CAC_NS, 'TaxCategory')
    scheme = _el(category, CAC_NS, 'TaxScheme')
    _el(scheme, CBC_NS, 'ID', '1000')
    _el(scheme, CBC_NS, 'Name', 'IGV')
    _el(scheme, CBC_NS, 'TaxTypeCode', 'VAT')


class SummaryStructureError(ValueError):
    """El XML del resumen no cumple la estructura esperada. Señala el nodo."""


#: Orden de la cabecera del `SummaryDocuments` (tras `UBLExtensions`), según la
#: Guía del Resumen Diario. El orden importa: UBL es una secuencia.
_HEADER_ORDER = [
    (CBC_NS, 'UBLVersionID'), (CBC_NS, 'CustomizationID'), (CBC_NS, 'ID'),
    (CBC_NS, 'ReferenceDate'), (CBC_NS, 'IssueDate'),
    (CAC_NS, 'Signature'), (CAC_NS, 'AccountingSupplierParty'),
]

_ID_RE = re.compile(r'^RC-\d{8}-\d{1,5}$')


def validate_summary_structure(xml: bytes) -> None:
    """
    Validación ESTRUCTURAL local del Resumen Diario antes de firmar/enviar.

    NO es el XSD oficial `SummaryDocuments-1` (el paquete UBL 2.0 de SUNAT no pudo
    incorporarse en este entorno; ver RC-XSD-01). Comprueba lo comprobable sin él:
    raíz y espacio de nombres correctos, ORDEN de la cabecera, `cbc:ID` con el
    formato `RC-YYYYMMDD-correlativo`, y que cada línea traiga sus campos
    obligatorios. Es una red de regresión, no la autoridad normativa: la estructura
    se confirma además contra el XSD oficial —cuando se pueda incorporar— y contra
    SUNAT BETA.
    """
    if isinstance(xml, str):
        xml = xml.encode('utf-8')
    try:
        root = etree.fromstring(xml)
    except etree.XMLSyntaxError as exc:
        raise SummaryStructureError(f'XML mal formado: {exc}') from None

    if root.tag != _q(SD_NS, 'SummaryDocuments'):
        raise SummaryStructureError(
            f'La raíz debe ser SummaryDocuments; llegó {root.tag!r}.')

    children = [c for c in root if isinstance(c.tag, str)]
    if not children or children[0].tag != _q(EXT_NS, 'UBLExtensions'):
        raise SummaryStructureError('Falta ext:UBLExtensions al inicio.')

    # Orden de la cabecera.
    after_ext = [c.tag for c in children[1:]]
    for i, (ns, tag) in enumerate(_HEADER_ORDER):
        if i >= len(after_ext) or after_ext[i] != _q(ns, tag):
            got = after_ext[i] if i < len(after_ext) else '(nada)'
            raise SummaryStructureError(
                f'Cabecera fuera de orden: se esperaba {tag} en la posición {i}, '
                f'llegó {got!r}.')

    doc_id = root.findtext(_q(CBC_NS, 'ID'))
    if not doc_id or not _ID_RE.fullmatch(doc_id):
        raise SummaryStructureError(
            f'cbc:ID debe cumplir RC-YYYYMMDD-correlativo; llegó {doc_id!r}.')

    lines = root.findall(_q(SAC_NS, 'SummaryDocumentsLine'))
    if not lines:
        raise SummaryStructureError('El resumen no tiene ninguna línea.')
    if len(lines) > 500:
        raise SummaryStructureError(
            f'El resumen excede 500 líneas ({len(lines)}).')
    required_line = [
        (CBC_NS, 'LineID'), (CBC_NS, 'DocumentTypeCode'), (CBC_NS, 'ID'),
        (CAC_NS, 'AccountingCustomerParty'), (CAC_NS, 'Status'),
        (SAC_NS, 'TotalAmount'),
    ]
    for n, line in enumerate(lines, 1):
        for ns, tag in required_line:
            if line.find(_q(ns, tag)) is None:
                raise SummaryStructureError(
                    f'Línea {n}: falta {tag}.')
        if len(line.findall(_q(SAC_NS, 'BillingPayment'))) != 3:
            raise SummaryStructureError(
                f'Línea {n}: se esperan 3 sac:BillingPayment (gravadas, '
                f'exoneradas, inafectas).')


def build_summary_xml(data: SummaryData) -> bytes:
    """
    El XML del Resumen Diario, sin firmar, en el orden que exige el esquema.

    La firma va en el `ext:ExtensionContent` (vacío aquí); `signing.sign_invoice`
    la coloca en el ÚLTIMO, igual que en la factura. `cbc:ReferenceDate` va ANTES
    de `cbc:IssueDate`, según los ejemplos oficiales del Resumen Diario.
    """
    data.check()

    root = etree.Element(_q(SD_NS, 'SummaryDocuments'), nsmap={
        None: SD_NS, 'cac': CAC_NS, 'cbc': CBC_NS,
        'ext': EXT_NS, 'sac': SAC_NS, 'ds': DS_NS,
    })

    extensions = _el(root, EXT_NS, 'UBLExtensions')
    _el(_el(extensions, EXT_NS, 'UBLExtension'), EXT_NS, 'ExtensionContent')

    _el(root, CBC_NS, 'UBLVersionID', '2.0')
    _el(root, CBC_NS, 'CustomizationID', '1.1')
    _el(root, CBC_NS, 'ID', data.identifier)
    _el(root, CBC_NS, 'ReferenceDate', data.reference_date.isoformat())
    _el(root, CBC_NS, 'IssueDate', data.issue_date.isoformat())

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
        _line(root, line, data.currency)

    return etree.tostring(root, xml_declaration=True, encoding='UTF-8')
