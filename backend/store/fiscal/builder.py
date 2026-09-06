"""
El XML UBL 2.1 de una factura electrónica peruana.

FUNCIÓN PURA. Recibe `InvoiceData` y devuelve bytes. No consulta la base de
datos, no habla por red, no lee `settings` y no conoce ninguna empresa concreta.
Eso permite validarlo contra el esquema de SUNAT sin levantar Django, y —más
importante— impide que el generador «arregle» un importe consultando algo. El
dinero ya lo decidió C2.1.

EL ORDEN NO ES ESTÉTICO
-----------------------
UBL es una secuencia XSD. Un documento con todos los campos correctos en el orden
equivocado es inválido, y el esquema local lo dice al instante. Por eso los
nodos se escriben en el orden del esquema y hay un test que lo vigila.

LA FORMA DE PAGO NO ES OPCIONAL
-------------------------------
`cac:PaymentTerms` con `cbc:ID = 'FormaPago'` es obligatorio desde el 01/01/2022.
Su ausencia produce el error 3244, «Debe consignar la informacion del tipo de
transaccion del comprobante» — un mensaje que suena a «tipo de operación» y NO
lo es: «tipo de transacción» es la etiqueta que SUNAT usa para el bloque
Contado/Crédito.

Esto costó tres envíos a BETA moviendo un nodo que no intervenía en la regla.
Está escrito aquí para que a nadie le vuelva a costar: la fuente es la hoja
`Factura2_0` del archivo oficial «Reglas de validación», líneas 174-177.
"""

from __future__ import annotations

from decimal import Decimal

from lxml import etree

from .data import InvoiceData, Line

NS = {
    'inv': 'urn:oasis:names:specification:ubl:schema:xsd:Invoice-2',
    'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2',
    'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2',
    'ext': 'urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2',
    'ds': 'http://www.w3.org/2000/09/xmldsig#',
    'sac': 'urn:sunat:names:specification:ubl:peru:schema:xsd:SunatAggregateComponents-1',
}

#: Catálogo N.º 05: el IGV. `VAT` es su código UN/ECE 5153; `S` el 5305, marcado
#: en el Anexo N.º 8 como «vigente para la versión UBL 2.1».
IGV_ID, IGV_NAME, IGV_UNECE, IGV_CATEGORY = '1000', 'IGV', 'VAT', 'S'

#: Catálogo N.º 16: precio unitario que incluye el IGV.
PRICE_TYPE_WITH_TAX = '01'

#: El valor literal que exige la regla 3244.
PAYMENT_TERMS_ID = 'FormaPago'
#: Regla 3245, encadenada: hay que decir si es al contado o al crédito.
PAYMENT_CASH = 'Contado'


def _q(tag: str) -> str:
    prefix, local = tag.split(':')
    return f'{{{NS[prefix]}}}{local}'


def _el(parent, tag: str, text=None, **attrs):
    node = etree.SubElement(parent, _q(tag), **attrs)
    if text is not None:
        node.text = str(text)
    return node


def _money(value: Decimal) -> str:
    """
    Un importe con dos decimales, sin notación científica ni signo redundante.

    `str(Decimal)` puede producir `1E+2`, que es un número válido y un importe
    inválido. Se formatea explícitamente.
    """
    return f'{value:.2f}'


def _tax_block(parent, *, taxable: Decimal, tax: Decimal, currency: str,
               percent: Decimal | None = None, affectation: str | None = None):
    """
    El bloque de impuesto, idéntico en el documento y en cada línea.

    Se comparte porque son el mismo bloque: escribirlo dos veces es cómo dos
    ramas del mismo XML acaban divergiendo en un atributo.
    """
    total = _el(parent, 'cac:TaxTotal')
    _el(total, 'cbc:TaxAmount', _money(tax), currencyID=currency)
    subtotal = _el(total, 'cac:TaxSubtotal')
    _el(subtotal, 'cbc:TaxableAmount', _money(taxable), currencyID=currency)
    _el(subtotal, 'cbc:TaxAmount', _money(tax), currencyID=currency)
    category = _el(subtotal, 'cac:TaxCategory')
    if percent is not None:
        _el(category, 'cbc:Percent', _money(percent))
    if affectation is not None:
        _el(category, 'cbc:TaxExemptionReasonCode', affectation)
    scheme = _el(category, 'cac:TaxScheme')
    _el(scheme, 'cbc:ID', IGV_ID)
    _el(scheme, 'cbc:Name', IGV_NAME)
    _el(scheme, 'cbc:TaxTypeCode', IGV_UNECE)
    return total


def _party(parent, tag: str, party, *, with_address: bool):
    """Emisor o receptor. La dirección sólo la lleva el emisor."""
    holder = _el(parent, tag)
    node = _el(holder, 'cac:Party')

    ident = _el(node, 'cac:PartyIdentification')
    _el(ident, 'cbc:ID', party.doc_number, schemeID=party.doc_type)

    if party.trade_name:
        name = _el(node, 'cac:PartyName')
        _el(name, 'cbc:Name', party.trade_name)

    legal = _el(node, 'cac:PartyLegalEntity')
    _el(legal, 'cbc:RegistrationName', party.legal_name)
    if with_address:
        address = _el(legal, 'cac:RegistrationAddress')
        if party.district_code:
            _el(address, 'cbc:ID', party.district_code)
        # `0000` significa establecimiento anexo no declarado: el domicilio
        # fiscal principal. Es el valor correcto mientras no se emita desde una
        # sucursal declarada ante SUNAT.
        _el(address, 'cbc:AddressTypeCode', '0000')
        if party.address_line:
            line = _el(address, 'cac:AddressLine')
            _el(line, 'cbc:Line', party.address_line)
        country = _el(address, 'cac:Country')
        _el(country, 'cbc:IdentificationCode', party.country_code)
    return holder


def _line(parent, index: int, line: Line, currency: str):
    node = _el(parent, 'cac:InvoiceLine')
    _el(node, 'cbc:ID', index)
    _el(node, 'cbc:InvoicedQuantity', _money(line.quantity), unitCode=line.unit_code)
    _el(node, 'cbc:LineExtensionAmount', _money(line.line_amount), currencyID=currency)

    # El precio que ve el cliente, con impuesto incluido. Va aparte del valor
    # unitario porque son dos cifras distintas y SUNAT pide las dos.
    pricing = _el(node, 'cac:PricingReference')
    alt = _el(pricing, 'cac:AlternativeConditionPrice')
    _el(alt, 'cbc:PriceAmount', _money(line.unit_price_with_tax), currencyID=currency)
    _el(alt, 'cbc:PriceTypeCode', PRICE_TYPE_WITH_TAX)

    _tax_block(node, taxable=line.line_amount, tax=line.tax_amount,
               currency=currency, percent=line.tax_percent,
               affectation=line.tax_affectation)

    item = _el(node, 'cac:Item')
    _el(item, 'cbc:Description', line.description)
    if line.item_code:
        code = _el(item, 'cac:SellersItemIdentification')
        _el(code, 'cbc:ID', line.item_code)

    price = _el(node, 'cac:Price')
    _el(price, 'cbc:PriceAmount', _money(line.unit_price), currencyID=currency)


def build_invoice_xml(data: InvoiceData) -> bytes:
    """
    El XML sin firmar, en el orden que exige el esquema.

    No valida reglas de negocio: eso es `rules.validate`, y se llama antes. Aquí
    sólo se transcribe. Separarlo permite que un test genere un XML deliberadamente
    incorrecto para comprobar que el esquema lo rechaza.
    """
    root = etree.Element(_q('inv:Invoice'), nsmap={
        None: NS['inv'], 'cac': NS['cac'], 'cbc': NS['cbc'],
        'ext': NS['ext'], 'ds': NS['ds'], 'sac': NS['sac'],
    })

    # Dos extensiones: la primera para la información adicional de SUNAT, la
    # segunda reservada a la firma. `signing.sign_invoice` usa la ÚLTIMA.
    extensions = _el(root, 'ext:UBLExtensions')
    first = _el(_el(extensions, 'ext:UBLExtension'), 'ext:ExtensionContent')
    info = _el(first, 'sac:AdditionalInformation')
    transaction = _el(info, 'sac:SUNATTransaction')
    _el(transaction, 'cbc:ID', data.operation_type)
    _el(_el(extensions, 'ext:UBLExtension'), 'ext:ExtensionContent')

    _el(root, 'cbc:UBLVersionID', '2.1')
    _el(root, 'cbc:CustomizationID', '2.0')
    _el(root, 'cbc:ID', data.document_id)
    _el(root, 'cbc:IssueDate', data.issue_date.isoformat())
    _el(root, 'cbc:IssueTime', data.issue_time.isoformat())
    # `listID` lleva el Catálogo N.º 51 (`0101`), NO el 17 (`01`). Son dos
    # catálogos distintos para la misma idea y con longitudes distintas;
    # confundirlos devuelve el error 3206.
    _el(root, 'cbc:InvoiceTypeCode', data.document_type,
        listID=data.invoice_type_code)
    # El importe en letras es un dato del comprobante, no decoración: va con
    # `languageLocaleID="1000"` (Catálogo N.º 52, «Monto en Letras»).
    _el(root, 'cbc:Note', data.amount_in_words, languageLocaleID='1000')
    for extra in data.notes:
        _el(root, 'cbc:Note', extra)
    _el(root, 'cbc:DocumentCurrencyCode', data.currency)

    signature = _el(root, 'cac:Signature')
    _el(signature, 'cbc:ID', data.document_id)
    party = _el(signature, 'cac:SignatoryParty')
    ident = _el(party, 'cac:PartyIdentification')
    _el(ident, 'cbc:ID', data.supplier.doc_number)
    name = _el(party, 'cac:PartyName')
    _el(name, 'cbc:Name', data.supplier.legal_name)
    attachment = _el(signature, 'cac:DigitalSignatureAttachment')
    external = _el(attachment, 'cac:ExternalReference')
    _el(external, 'cbc:URI', '#SignatureSP')

    _party(root, 'cac:AccountingSupplierParty', data.supplier, with_address=True)
    _party(root, 'cac:AccountingCustomerParty', data.customer, with_address=False)

    # LA FORMA DE PAGO. Su ausencia es el error 3244. Ver la cabecera del módulo.
    terms = _el(root, 'cac:PaymentTerms')
    _el(terms, 'cbc:ID', PAYMENT_TERMS_ID)
    _el(terms, 'cbc:PaymentMeansID', PAYMENT_CASH)

    _tax_block(root, taxable=data.taxable_amount, tax=data.tax_amount,
               currency=data.currency)

    totals = _el(root, 'cac:LegalMonetaryTotal')
    _el(totals, 'cbc:LineExtensionAmount', _money(data.taxable_amount),
        currencyID=data.currency)
    _el(totals, 'cbc:TaxInclusiveAmount', _money(data.total), currencyID=data.currency)
    _el(totals, 'cbc:PayableAmount', _money(data.total), currencyID=data.currency)

    for index, line in enumerate(data.lines, 1):
        _line(root, index, line, data.currency)

    return etree.tostring(root, xml_declaration=True, encoding='UTF-8')
