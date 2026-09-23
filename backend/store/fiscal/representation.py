"""
Representación (PDF/QR) leída del XML FIRMADO, no de tablas vivas.

FISCAL-03/04. Una vez firmado, el comprobante es INMUTABLE: su representación
impresa y su QR deben decir lo que dice el XML que SUNAT recibió, no lo que digan
hoy `OrderItem`/`Product` (que pueden cambiar tras la firma) ni el timestamp
técnico de creación de la fila. Este parser extrae, EN SÓLO LECTURA, lo que la
representación necesita del XML firmado.

Autoridad de representación = XML firmado + CDR. El PDF es una derivación; no
recalcula importes ni relee la venta.

Usa el parser endurecido (`xmlsafe`) y NO modifica el XML. Módulo puro (sin ORM).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal

from .xmlsafe import parse_untrusted

NS = {
    'inv': 'urn:oasis:names:specification:ubl:schema:xsd:Invoice-2',
    'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2',
    'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2',
    'ds': 'http://www.w3.org/2000/09/xmldsig#',
}


@dataclass(frozen=True)
class RepLine:
    description: str
    quantity: Decimal
    unit_price: Decimal          # valor unitario SIN impuesto (n(12,10))
    unit_price_with_tax: Decimal  # precio unitario CON impuesto
    line_amount: Decimal          # valor de venta de la línea
    tax_amount: Decimal
    #: La TASA declarada en la línea (cbc:Percent), tal cual la firmó el original.
    #: Se conserva —no se deriva de impuesto/base— para que una nota de anulación
    #: pueda reflejar el original al pie de la letra, incluida su tasa (18.00), sin
    #: reintroducir un porcentaje inventado por el redondeo (REVIEW ERP-FISCAL-5A).
    tax_percent: Decimal = Decimal('0')


@dataclass(frozen=True)
class SignedInvoiceRepresentation:
    document_id: str
    issue_date: date
    issue_time: time | None
    currency: str
    issuer_tax_id: str
    issuer_legal_name: str
    customer_doc_number: str
    customer_legal_name: str
    lines: tuple[RepLine, ...]
    taxable_amount: Decimal
    tax_amount: Decimal
    payable_amount: Decimal
    digest_value: str


def _text(node, path: str, default: str = '') -> str:
    found = node.findtext(path, namespaces=NS)
    return found if found is not None else default


def _dec(node, path: str) -> Decimal:
    return Decimal(_text(node, path, '0') or '0')


#: Lo único que cambia entre una factura/boleta y sus notas: el nombre del
#: renglón, el de la cantidad y el del total. Los componentes comunes (`cac:`,
#: `cbc:`) son idénticos —de ahí que el resto del parser no distinga el tipo—.
#: La ND usa `RequestedMonetaryTotal`; la factura, la boleta y la NC comparten
#: `LegalMonetaryTotal`.
_LAYOUT_BY_ROOT = {
    'Invoice': ('cac:InvoiceLine', 'cbc:InvoicedQuantity', 'cac:LegalMonetaryTotal'),
    'CreditNote': ('cac:CreditNoteLine', 'cbc:CreditedQuantity',
                   'cac:LegalMonetaryTotal'),
    'DebitNote': ('cac:DebitNoteLine', 'cbc:DebitedQuantity',
                  'cac:RequestedMonetaryTotal'),
}


def parse_signed_invoice_for_representation(xml) -> SignedInvoiceRepresentation:
    """
    Extrae del XML firmado lo necesario para PDF/QR. Sólo lectura.

    Sirve a la factura, la boleta y sus notas (07/08): el nombre local de la raíz
    decide los tres nombres que cambian entre ellos —renglón, cantidad y total—;
    todo lo demás son componentes comunes que no dependen del tipo.
    """
    if isinstance(xml, str):
        xml = xml.encode('utf-8')
    root = parse_untrusted(xml)

    local = root.tag.rsplit('}', 1)[-1]  # «Invoice», «CreditNote», «DebitNote»
    line_tag, qty_tag, total_tag = _LAYOUT_BY_ROOT.get(local, _LAYOUT_BY_ROOT['Invoice'])

    lines = tuple(
        RepLine(
            description=_text(node, 'cac:Item/cbc:Description'),
            quantity=_dec(node, qty_tag),
            unit_price=_dec(node, 'cac:Price/cbc:PriceAmount'),
            unit_price_with_tax=_dec(
                node,
                'cac:PricingReference/cac:AlternativeConditionPrice/cbc:PriceAmount'),
            line_amount=_dec(node, 'cbc:LineExtensionAmount'),
            tax_amount=_dec(node, 'cac:TaxTotal/cbc:TaxAmount'),
            tax_percent=_dec(
                node, 'cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cbc:Percent'),
        )
        for node in root.findall(line_tag, NS)
    )

    issue_time_text = _text(root, 'cbc:IssueTime')
    return SignedInvoiceRepresentation(
        document_id=_text(root, 'cbc:ID'),
        issue_date=date.fromisoformat(_text(root, 'cbc:IssueDate')),
        issue_time=time.fromisoformat(issue_time_text) if issue_time_text else None,
        currency=_text(root, 'cbc:DocumentCurrencyCode'),
        issuer_tax_id=_text(
            root, 'cac:AccountingSupplierParty/cac:Party/'
            'cac:PartyIdentification/cbc:ID'),
        issuer_legal_name=_text(
            root, 'cac:AccountingSupplierParty/cac:Party/'
            'cac:PartyLegalEntity/cbc:RegistrationName'),
        customer_doc_number=_text(
            root, 'cac:AccountingCustomerParty/cac:Party/'
            'cac:PartyIdentification/cbc:ID'),
        customer_legal_name=_text(
            root, 'cac:AccountingCustomerParty/cac:Party/'
            'cac:PartyLegalEntity/cbc:RegistrationName'),
        lines=lines,
        taxable_amount=_dec(root, 'cac:TaxTotal/cac:TaxSubtotal/cbc:TaxableAmount'),
        tax_amount=_dec(root, 'cac:TaxTotal/cbc:TaxAmount'),
        payable_amount=_dec(root, f'{total_tag}/cbc:PayableAmount'),
        digest_value=_text(root, './/ds:DigestValue'),
    )
