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

LOS DESCUENTOS SE DECLARAN, NO SE ESCONDEN (ERP-FISCAL-6)
---------------------------------------------------------
Un descuento va en `cac:AllowanceCharge` con `ChargeIndicator=false`: en la
línea que lo recibió (`00`) o en el documento (`02`), según el Catálogo N.º 53
vigente. Lo que NO se emite, y por qué, según la hoja `Factura2_0` (21.04.2025):

- `cbc:AllowanceTotalAmount` NO lleva estos descuentos. SUNAT lo define como
  «Sumatoria otros descuentos (que NO afectan la base imponible)» y lo valida
  contra los códigos `01`/`03` (regla 3300); además lo RESTA del total en la
  regla 3280 (`PayableAmount = TaxInclusiveAmount + cargos − AllowanceTotal…`).
  Ponerlo aquí declararía el descuento dos veces y el importe a pagar saldría
  rebajado por segunda vez. Como esta fase sólo declara descuentos que sí
  afectan a la base, el nodo se omite: la base ya viene rebajada.
- `LegalMonetaryTotal/cbc:LineExtensionAmount` («Total valor de venta») es la
  suma de las líneas MENOS los descuentos globales `02` (regla 3278): es decir,
  la base imponible. Sigue saliendo de `taxable_amount`.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from lxml import etree

from .data import InvoiceData, Line, NoteData

NS = {
    'inv': 'urn:oasis:names:specification:ubl:schema:xsd:Invoice-2',
    'cn': 'urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2',
    'dn': 'urn:oasis:names:specification:ubl:schema:xsd:DebitNote-2',
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
    Un IMPORTE con dos decimales, sin notación científica ni signo redundante.

    `str(Decimal)` puede producir `1E+2`, que es un número válido y un importe
    inválido. Se formatea explícitamente. Se usa para todo monto n(12,2):
    LineExtensionAmount, TaxAmount, TaxableAmount, PricingReference (precio con
    IGV), totales y porcentaje.
    """
    return f'{value:.2f}'


def _unit_value(value: Decimal) -> str:
    """
    El VALOR UNITARIO sin impuesto: `cbc:Price/cbc:PriceAmount`, n(12,10).

    SUNAT admite hasta 10 decimales aquí, y hacen falta: el valor unitario se
    deriva de la base de línea reconciliada (VEN-02A), que a 2 decimales no
    representaría `cantidad × unitario = valor de venta`. Se emite el número a lo
    sumo a 10 decimales, sin ceros de relleno innecesarios pero con un mínimo de
    2 (serialización determinista): 50.765 → «50.765», 100 → «100.00».
    """
    quantized = value.quantize(Decimal('0.0000000001'), rounding=ROUND_HALF_UP)
    text = format(quantized, 'f')
    if '.' in text:
        integer, fraction = text.split('.')
        fraction = fraction.rstrip('0')
        if len(fraction) < 2:
            fraction = (fraction + '00')[:2]
        text = f'{integer}.{fraction}'
    else:
        text = f'{text}.00'
    return text


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


def _factor(value: Decimal) -> str:
    """`cbc:MultiplierFactorNumeric`, n(3,5): 5 % es `0.05`, sin ceros de más."""
    text = format(value.quantize(Decimal('0.00001'), rounding=ROUND_HALF_UP), 'f')
    integer, fraction = text.split('.')
    fraction = fraction.rstrip('0') or '0'
    return f'{integer}.{fraction}'


def _allowance_charge(parent, allowance, currency: str):
    """
    Un descuento declarado, idéntico en el documento y en la línea.

    Siempre `ChargeIndicator=false`: esta fase no declara cargos. El orden de
    los hijos lo fija el XSD (`AllowanceChargeType`): indicador, motivo, factor,
    importe, base. El factor sólo si viene; es opcional en el esquema y en las
    reglas, y derivarlo aquí sería inventar un porcentaje.
    """
    node = _el(parent, 'cac:AllowanceCharge')
    _el(node, 'cbc:ChargeIndicator', 'false')
    _el(node, 'cbc:AllowanceChargeReasonCode', allowance.reason_code)
    if allowance.multiplier is not None:
        _el(node, 'cbc:MultiplierFactorNumeric', _factor(allowance.multiplier))
    _el(node, 'cbc:Amount', _money(allowance.amount), currencyID=currency)
    _el(node, 'cbc:BaseAmount', _money(allowance.base_amount), currencyID=currency)
    return node


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


def _line(parent, index: int, line: Line, currency: str, *,
          line_tag: str = 'cac:InvoiceLine',
          qty_tag: str = 'cbc:InvoicedQuantity'):
    # La misma línea sirve a factura/boleta (`InvoiceLine`/`InvoicedQuantity`) y a
    # las notas (`CreditNoteLine`/`CreditedQuantity`, `DebitNoteLine`/
    # `DebitedQuantity`): sólo cambian los nombres del renglón y de la cantidad;
    # la aritmética y el desglose son idénticos.
    node = _el(parent, line_tag)
    _el(node, 'cbc:ID', index)
    _el(node, qty_tag, _money(line.quantity), unitCode=line.unit_code)
    _el(node, 'cbc:LineExtensionAmount', _money(line.line_amount), currencyID=currency)

    # El precio que ve el cliente, con impuesto incluido. Va aparte del valor
    # unitario porque son dos cifras distintas y SUNAT pide las dos. Es n(12,10)
    # (regla 33): en una línea con descuento es lo que el cliente PAGÓ por unidad
    # —(valor de venta + impuesto) / cantidad, regla 3270—, y eso no siempre
    # cabe en dos decimales. Sin descuento es el precio de catálogo, y el texto
    # que sale es el mismo de siempre.
    pricing = _el(node, 'cac:PricingReference')
    alt = _el(pricing, 'cac:AlternativeConditionPrice')
    _el(alt, 'cbc:PriceAmount', _unit_value(line.unit_price_with_tax), currencyID=currency)
    _el(alt, 'cbc:PriceTypeCode', PRICE_TYPE_WITH_TAX)

    # El descuento de ESTA línea, antes de su impuesto: así lo ordena el XSD
    # (`PricingReference` → `AllowanceCharge` → `TaxTotal`).
    for allowance in line.allowances:
        _allowance_charge(node, allowance, currency)

    _tax_block(node, taxable=line.line_amount, tax=line.tax_amount,
               currency=currency, percent=line.tax_percent,
               affectation=line.tax_affectation)

    item = _el(node, 'cac:Item')
    _el(item, 'cbc:Description', line.description)
    if line.item_code:
        code = _el(item, 'cac:SellersItemIdentification')
        _el(code, 'cbc:ID', line.item_code)

    price = _el(node, 'cac:Price')
    _el(price, 'cbc:PriceAmount', _unit_value(line.unit_price), currencyID=currency)


def _ubl_extensions(root, operation_type: str):
    """
    Las dos `ext:UBLExtension`: la primera con la información adicional de SUNAT
    (tipo de operación, Catálogo 17), la segunda RESERVADA a la firma —
    `signing.sign_invoice` usa la ÚLTIMA `ext:ExtensionContent`—. Compartida por
    la factura/boleta y las notas: es el mismo andamiaje de SUNAT.
    """
    extensions = _el(root, 'ext:UBLExtensions')
    first = _el(_el(extensions, 'ext:UBLExtension'), 'ext:ExtensionContent')
    info = _el(first, 'sac:AdditionalInformation')
    transaction = _el(info, 'sac:SUNATTransaction')
    _el(transaction, 'cbc:ID', operation_type)
    _el(_el(extensions, 'ext:UBLExtension'), 'ext:ExtensionContent')


def _signature_block(root, document_id: str, signatory):
    """El bloque `cac:Signature` (mismo en factura/boleta y notas)."""
    signature = _el(root, 'cac:Signature')
    _el(signature, 'cbc:ID', document_id)
    party = _el(signature, 'cac:SignatoryParty')
    ident = _el(party, 'cac:PartyIdentification')
    _el(ident, 'cbc:ID', signatory.doc_number)
    name = _el(party, 'cac:PartyName')
    _el(name, 'cbc:Name', signatory.legal_name)
    attachment = _el(signature, 'cac:DigitalSignatureAttachment')
    external = _el(attachment, 'cac:ExternalReference')
    _el(external, 'cbc:URI', '#SignatureSP')


def _monetary_total(root, *, taxable: Decimal, total: Decimal, currency: str,
                    tag: str = 'cac:LegalMonetaryTotal'):
    """
    El total monetario. Factura/boleta y Nota de Crédito usan
    `cac:LegalMonetaryTotal`; la Nota de Débito usa `cac:RequestedMonetaryTotal`
    (así lo exige su XSD). Los renglones internos son los mismos.

    `LineExtensionAmount` es la base imponible también cuando hay descuentos:
    SUNAT lo define como Σ líneas − descuentos globales `02` (regla 3278), y las
    líneas ya descuentan lo suyo. `AllowanceTotalAmount` se omite a propósito:
    ver la cabecera del módulo (reglas 3300 y 3280).
    """
    totals = _el(root, tag)
    _el(totals, 'cbc:LineExtensionAmount', _money(taxable), currencyID=currency)
    _el(totals, 'cbc:TaxInclusiveAmount', _money(total), currencyID=currency)
    _el(totals, 'cbc:PayableAmount', _money(total), currencyID=currency)


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

    _ubl_extensions(root, data.operation_type)

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

    _signature_block(root, data.document_id, data.supplier)

    _party(root, 'cac:AccountingSupplierParty', data.supplier, with_address=True)
    _party(root, 'cac:AccountingCustomerParty', data.customer, with_address=False)

    # LA FORMA DE PAGO. Su ausencia es el error 3244. Ver la cabecera del módulo.
    terms = _el(root, 'cac:PaymentTerms')
    _el(terms, 'cbc:ID', PAYMENT_TERMS_ID)
    _el(terms, 'cbc:PaymentMeansID', PAYMENT_CASH)

    # Los descuentos globales van entre la forma de pago y el impuesto: es su
    # sitio en la secuencia del XSD (`…PaymentTerms → PrepaidPayment →
    # AllowanceCharge → … → TaxTotal → LegalMonetaryTotal → InvoiceLine`).
    for allowance in data.allowances:
        _allowance_charge(root, allowance, data.currency)

    _tax_block(root, taxable=data.taxable_amount, tax=data.tax_amount,
               currency=data.currency)
    _monetary_total(root, taxable=data.taxable_amount, total=data.total,
                    currency=data.currency)

    for index, line in enumerate(data.lines, 1):
        _line(root, index, line, data.currency)

    return etree.tostring(root, xml_declaration=True, encoding='UTF-8')


def build_note_xml(data: NoteData) -> bytes:
    """
    El XML sin firmar de una Nota de Crédito (07) o de Débito (08).

    Comparte las primitivas con la factura —partes, desglose de impuestos, líneas,
    firma, totales— pero NO es una factura con otra raíz: lleva su propia raíz
    (`CreditNote`/`DebitNote`), su relación con el original (`DiscrepancyResponse`
    y `BillingReference`) y su renglón propio (`CreditNoteLine`/`DebitNoteLine`).
    El orden lo dicta el XSD; se valida contra él antes de firmar.
    """
    is_credit = data.document_type == '07'
    root_ns = NS['cn'] if is_credit else NS['dn']
    root_local = 'CreditNote' if is_credit else 'DebitNote'
    line_tag = 'cac:CreditNoteLine' if is_credit else 'cac:DebitNoteLine'
    qty_tag = 'cbc:CreditedQuantity' if is_credit else 'cbc:DebitedQuantity'

    root = etree.Element(f'{{{root_ns}}}{root_local}', nsmap={
        None: root_ns, 'cac': NS['cac'], 'cbc': NS['cbc'],
        'ext': NS['ext'], 'ds': NS['ds'], 'sac': NS['sac'],
    })

    _ubl_extensions(root, '01')

    _el(root, 'cbc:UBLVersionID', '2.1')
    _el(root, 'cbc:CustomizationID', '2.0')
    _el(root, 'cbc:ID', data.document_id)
    _el(root, 'cbc:IssueDate', data.issue_date.isoformat())
    _el(root, 'cbc:IssueTime', data.issue_time.isoformat())
    _el(root, 'cbc:Note', data.amount_in_words, languageLocaleID='1000')
    for extra in data.notes:
        _el(root, 'cbc:Note', extra)
    _el(root, 'cbc:DocumentCurrencyCode', data.currency)

    # La relación con el comprobante que se modifica.
    discrepancy = _el(root, 'cac:DiscrepancyResponse')
    _el(discrepancy, 'cbc:ReferenceID', data.original_id)
    _el(discrepancy, 'cbc:ResponseCode', data.reason_code)
    _el(discrepancy, 'cbc:Description', data.reason_description)

    billing = _el(root, 'cac:BillingReference')
    ref = _el(billing, 'cac:InvoiceDocumentReference')
    _el(ref, 'cbc:ID', data.original_id)
    _el(ref, 'cbc:DocumentTypeCode', data.original_type)

    _signature_block(root, data.document_id, data.supplier)

    _party(root, 'cac:AccountingSupplierParty', data.supplier, with_address=True)
    _party(root, 'cac:AccountingCustomerParty', data.customer, with_address=False)

    _tax_block(root, taxable=data.taxable_amount, tax=data.tax_amount,
               currency=data.currency)
    _monetary_total(
        root, taxable=data.taxable_amount, total=data.total, currency=data.currency,
        tag='cac:LegalMonetaryTotal' if is_credit else 'cac:RequestedMonetaryTotal')

    for index, line in enumerate(data.lines, 1):
        _line(root, index, line, data.currency, line_tag=line_tag, qty_tag=qty_tag)

    return etree.tostring(root, xml_declaration=True, encoding='UTF-8')
