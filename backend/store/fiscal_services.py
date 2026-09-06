"""
Emitir un comprobante electrónico a partir de una venta.

DÓNDE ESTÁ LA FRONTERA
----------------------
`store.fiscal` no conoce Django. Este módulo sí: es el que traduce un `Order` a
los datos planos que aquel espera, reserva el correlativo y guarda el resultado.
La separación permite probar el XML contra el esquema de SUNAT sin base de
datos, y evita que el generador pueda «arreglar» un importe consultando algo.

LA REGLA QUE ORDENA EL FLUJO: NADA DE RED DENTRO DE LA TRANSACCIÓN
------------------------------------------------------------------
    transacción:  crear documento, reservar correlativo, congelar snapshot
    commit
    después:      generar, firmar, enviar, registrar el resultado

Mantener abierta una transacción esperando a SUNAT bloquearía la fila del
contador durante segundos de red, y un fallo de SUNAT podría deshacer una venta
que ya se cobró. Lo que SUNAT diga se registra en un segundo paso, sobre un
documento que ya existe.

EL DINERO VIENE DE C2.1
-----------------------
`Order.taxable_amount`, `tax_amount`, `tax_rate` y `total` son el snapshot que la
venta congeló. Aquí se COPIAN. No se recalcula nada, y menos aún se vuelve a
dividir entre 1,18.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from lxml import etree

from .fiscal import builder, packaging, rules, schema, signing
from .fiscal.data import InvoiceData, Line, Party
from .fiscal.provider import ProviderOutcome
from .models import (
    FiscalDocument, FiscalDocumentStatus, FiscalDocumentType, FiscalSeries,
    FiscalSubmissionAttempt, Order,
)

#: Los estados de `ProviderOutcome` traducidos al dominio. Se escribe el mapa en
#: vez de encadenar `if`: así se ve de un vistazo que TRANSPORT_ERROR y
#: UNKNOWN_RESPONSE NO caen en `REJECTED`, que es la distinción que importa.
OUTCOME_TO_STATUS = {
    ProviderOutcome.ACCEPTED: FiscalDocumentStatus.ACCEPTED,
    ProviderOutcome.ACCEPTED_WITH_OBSERVATION:
        FiscalDocumentStatus.ACCEPTED_WITH_OBSERVATION,
    ProviderOutcome.REJECTED: FiscalDocumentStatus.REJECTED,
    ProviderOutcome.TRANSPORT_ERROR: FiscalDocumentStatus.SUBMISSION_ERROR,
    ProviderOutcome.UNKNOWN_RESPONSE: FiscalDocumentStatus.SUBMISSION_ERROR,
}


class FiscalError(Exception):
    """Una venta que no puede convertirse en comprobante. Las vistas dan 400."""


def _amount_in_words(total: Decimal, currency: str) -> str:
    """
    El importe en letras, que es un dato obligatorio del comprobante.

    Se implementa aquí y no con una dependencia porque son treinta líneas y
    porque el castellano de Perú tiene sus reglas —«veintiuno», «cien» frente a
    «ciento»— que una librería genérica de i18n no acierta sin configurarla.
    """
    unidades = ('', 'UNO', 'DOS', 'TRES', 'CUATRO', 'CINCO', 'SEIS', 'SIETE',
                'OCHO', 'NUEVE', 'DIEZ', 'ONCE', 'DOCE', 'TRECE', 'CATORCE',
                'QUINCE', 'DIECISEIS', 'DIECISIETE', 'DIECIOCHO', 'DIECINUEVE',
                'VEINTE')
    decenas = ('', '', 'VEINTI', 'TREINTA', 'CUARENTA', 'CINCUENTA', 'SESENTA',
               'SETENTA', 'OCHENTA', 'NOVENTA')
    centenas = ('', 'CIENTO', 'DOSCIENTOS', 'TRESCIENTOS', 'CUATROCIENTOS',
                'QUINIENTOS', 'SEISCIENTOS', 'SETECIENTOS', 'OCHOCIENTOS',
                'NOVECIENTOS')

    def hasta_999(n: int) -> str:
        if n == 0:
            return ''
        if n == 100:
            return 'CIEN'
        c, resto = divmod(n, 100)
        d, u = divmod(resto, 10)
        partes = [centenas[c]] if c else []
        if resto <= 20:
            if resto:
                partes.append(unidades[resto])
        elif d == 2:
            partes.append(f'VEINTI{unidades[u].lower().upper()}' if u else 'VEINTE')
        else:
            partes.append(decenas[d] + (f' Y {unidades[u]}' if u else ''))
        return ' '.join(p for p in partes if p)

    entero = int(total)
    centimos = int((total - entero) * 100)

    if entero == 0:
        letras = 'CERO'
    else:
        millones, resto = divmod(entero, 1_000_000)
        miles, unidad = divmod(resto, 1000)
        trozos = []
        if millones:
            trozos.append('UN MILLON' if millones == 1
                          else f'{hasta_999(millones)} MILLONES')
        if miles:
            trozos.append('MIL' if miles == 1 else f'{hasta_999(miles)} MIL')
        if unidad:
            trozos.append(hasta_999(unidad))
        letras = ' '.join(trozos)

    moneda = 'SOLES' if currency == 'PEN' else currency
    return f'{letras} CON {centimos:02d}/100 {moneda}'


#: `Order.DocumentType` → Catálogo N.º 06 de SUNAT.
#:
#: Se traduce en vez de fijar `'6'` a mano. Escribirlo a mano hacía que el XML
#: afirmara «esto es un RUC» aunque la venta dijera DNI: el número viajaba tal
#: cual con la etiqueta equivocada, y SUNAT recibía una declaración falsa sobre
#: qué documento identifica al adquirente.
DOC_TYPE_TO_SUNAT = {
    'ruc': '6',
    'dni': '1',
    'ce': '4',   # carné de extranjería
}


def _order_to_invoice_data(order: Order, series: FiscalSeries,
                           number: int) -> InvoiceData:
    """
    Traduce la venta a datos planos. NO calcula dinero: lo copia.

    Se valida que el snapshot de C2.1 esté completo antes de tocar nada: una
    venta sin desglose congelado no puede producir un comprobante, y decirlo aquí
    es más útil que un `TypeError` a mitad de la generación.
    """
    from .company_settings import order_identity

    if order.taxable_amount is None or order.tax_amount is None:
        raise FiscalError(
            'La venta no tiene desglose tributario congelado. Un comprobante no '
            'puede declarar importes que nadie calculó en el momento de vender.'
        )

    identity = order_identity(order)
    if not identity.tax_id:
        raise FiscalError('La empresa emisora no tiene RUC configurado.')

    lines = []
    for item in order.items.select_related('product').all():
        # El precio del catálogo INCLUYE el impuesto (C2.1). El valor unitario
        # sin impuesto se obtiene del mismo modo que el desglose de la venta:
        # dividiendo, no multiplicando.
        con_impuesto = Decimal(str(item.price))
        proporcion = (Decimal('1') + order.tax_rate)
        sin_impuesto = (con_impuesto / proporcion).quantize(Decimal('0.01'))
        importe = (sin_impuesto * item.quantity).quantize(Decimal('0.01'))
        impuesto = ((con_impuesto * item.quantity).quantize(Decimal('0.01'))
                    - importe)
        lines.append(Line(
            description=(item.product.name if item.product else 'PRODUCTO')[:250],
            quantity=Decimal(item.quantity),
            unit_code='NIU',
            unit_price=sin_impuesto,
            unit_price_with_tax=con_impuesto,
            line_amount=importe,
            tax_amount=impuesto,
            tax_percent=(order.tax_rate * 100).quantize(Decimal('0.01')),
        ))

    # AQUÍ NO SE CUADRA NADA A LA FUERZA.
    #
    # Una versión anterior repartía la diferencia entre la suma de las líneas y
    # la base de la venta empujándola a la última línea. Eso tapaba el síntoma de
    # un descuento no declarado y producía una línea cuya aritmética no cerraba.
    #
    # Sin descuento —lo único que esta fase emite— la suma de las líneas coincide
    # con la base por construcción, y si alguna vez no coincidiera es un defecto
    # que hay que ver, no redondear. `rules.validate` lo comprueba.

    issued = timezone.localtime(order.paid_at or timezone.now())
    return InvoiceData(
        document_type=series.document_type,
        serie=series.series,
        correlativo=number,
        issue_date=issued.date(),
        issue_time=issued.time().replace(microsecond=0),
        currency=order.currency or 'PEN',
        supplier=Party(
            doc_type='6', doc_number=identity.tax_id,
            legal_name=identity.legal_name or identity.name,
            trade_name=identity.name or '',
            address_line=identity.legal_address or '',
        ),
        customer=Party(
            doc_type=DOC_TYPE_TO_SUNAT.get(order.document_type, ''),
            doc_number=order.document_number or '',
            legal_name=order.customer_name or '',
        ),
        lines=tuple(lines),
        taxable_amount=order.taxable_amount,
        tax_amount=order.tax_amount,
        total=order.total,
        amount_in_words=_amount_in_words(order.total, order.currency or 'PEN'),
    )


def _reserve(series: FiscalSeries) -> int:
    """
    Reserva el siguiente correlativo bloqueando SÓLO esa fila.

    `select_for_update` sobre la serie y nada más: bloquear la configuración de
    la empresa haría que dos personas emitiendo en sucursales distintas se
    pusieran en cola sin motivo.

    Un número entregado está GASTADO. Si el documento acaba rechazado, ese número
    no vuelve al contador: reciclarlo produciría dos documentos que alguna vez
    compartieron identificador fiscal.
    """
    locked = FiscalSeries.objects.select_for_update().get(pk=series.pk)
    number = locked.next_number
    locked.next_number = number + 1
    locked.save(update_fields=['next_number', 'updated_at'])
    return number


def get_or_create_fiscal_document(order: Order) -> tuple[FiscalDocument, bool]:
    """
    El comprobante de esta venta, creándolo si no existe. IDEMPOTENTE.

    Dos clics en «Emitir» producen UN documento con UN correlativo. La
    restricción de base de datos es la que lo garantiza de verdad —la
    comprobación previa sólo evita el trabajo—, porque entre mirar y crear cabe
    otra petición.

    SIN RED AQUÍ DENTRO. Esta función deja el documento en `GENERATED`; enviarlo
    es `submit_fiscal_document`, fuera de la transacción.
    """
    if not order.paid or order.status != Order.Status.PAID:
        raise FiscalError(
            'Sólo se emite comprobante de una venta pagada. '
            'Un comprobante no es autoridad del pago.'
        )
    if order.receipt_type != Order.ReceiptType.FACTURA:
        raise FiscalError(
            'Esta venta no solicitó factura. Emitir una boleta corresponde a '
            'otra fase.'
        )

    # SE FALLA CERRADO ANTE UN DESCUENTO, y es deliberado.
    #
    # C2.1 sabe vender con descuento; el generador fiscal todavía no sabe
    # DECLARARLO. Un descuento se expresa en UBL con `cac:AllowanceCharge` y su
    # `cbc:AllowanceTotalAmount`, no rebajando el importe de la línea: hacer eso
    # produce un documento que dice «2 unidades a 100,00» con un total de línea
    # de 184,75, donde la aritmética no cierra y la rebaja es invisible.
    #
    # Enviar eso a SUNAT sería declarar un precio unitario que nadie cobró.
    # Negarse es peor experiencia y mejor comportamiento.
    if order.discount_amount and order.discount_amount > 0:
        raise FiscalError(
            f'Esta venta tiene un descuento de {order.discount_amount} y la '
            f'factura electrónica todavía no sabe declararlo. Emitirla ocultaría '
            f'la rebaja y declararía un precio unitario que no se cobró.'
        )

    existing = FiscalDocument.objects.filter(order=order).order_by('-pk').first()
    if existing is not None and existing.status != FiscalDocumentStatus.REJECTED:
        return existing, False

    if existing is not None:
        # UN RECHAZO ES TERMINAL PARA EL BOTÓN GENÉRICO.
        #
        # El modelo permite otro documento para la misma venta —hará falta el día
        # que exista un flujo explícito de corrección—, pero que «Emitir» lo
        # aproveche por defecto convierte un clic distraído en un correlativo
        # gastado. Y SUNAT considera USADO el número de un documento rechazado:
        # cada reintento ciego quema uno más y deja un hueco que hay que explicar.
        #
        # Reemitir tiene que ser una decisión, no el efecto lateral de un
        # queryset que excluía el estado.
        raise FiscalError(
            f'El comprobante {existing.document_id} fue rechazado por SUNAT '
            f'({existing.sunat_response_code or "sin código"}). Corrija la venta '
            f'y emita explícitamente: volver a pulsar «Emitir» gastaría otro '
            f'correlativo sin resolver la causa del rechazo.'
        )

    # LA SERIE LA ELIGE UN RESOLVER, no `order_by('pk').first()`.
    #
    # Aquello convertía «el id más bajo» en política tributaria: bastaba con que
    # la primera serie creada fuese de producción para que una venta de pruebas
    # la usara. El resolver filtra por ambiente, respeta la sucursal y FALLA ante
    # ambigüedad en vez de desempatar por su cuenta.
    from .fiscal_config import FiscalConfigError, resolve_series

    try:
        series = resolve_series(
            order.company, branch=order.fulfillment_branch,
            document_type=FiscalDocumentType.INVOICE,
        )
    except FiscalConfigError as exc:
        raise FiscalError(str(exc)) from None

    # LA TRANSACCIÓN ENVUELVE LA RESERVA Y LA VALIDACIÓN, a propósito: si el
    # documento no cumple una regla, el `rollback` devuelve el correlativo. Una
    # negativa no puede gastar un número, porque cada intento fallido dejaría un
    # hueco que hay que explicar ante SUNAT.
    try:
        with transaction.atomic():
            number = _reserve(series)
            data = _order_to_invoice_data(order, series, number)
            # Se traduce a `FiscalError` para que quien llame tenga UN tipo de
            # excepción. Dejar escapar `FiscalRuleError` hacía que una vista
            # respondiera 500 a lo que es un 400: una venta que no cumple los
            # requisitos de una factura.
            rules.validate(data)

            document = FiscalDocument.objects.create(
                order=order, company=order.company, series_ref=series,
                document_type=series.document_type, series=series.series,
                number=number, issued_at=timezone.now(),
                environment=series.environment,
                issuer_tax_id=data.supplier.doc_number,
                issuer_legal_name=data.supplier.legal_name,
                issuer_trade_name=data.supplier.trade_name,
                issuer_address=data.supplier.address_line,
                customer_doc_type=data.customer.doc_type,
                customer_doc_number=data.customer.doc_number,
                customer_legal_name=data.customer.legal_name,
                currency=data.currency,
                taxable_amount=data.taxable_amount,
                tax_amount=data.tax_amount,
                total=data.total,
                tax_rate=order.tax_rate,
                status=FiscalDocumentStatus.GENERATED,
            )
    except rules.FiscalRuleError as exc:
        raise FiscalError(str(exc)) from None
    return document, True


def sign_fiscal_document(document: FiscalDocument, *, key_pem: bytes,
                         cert_pem: bytes) -> FiscalDocument:
    """
    Genera el XML, lo firma y lo guarda. Valida contra el esquema antes de nada.

    Si ya está firmado no se vuelve a firmar: el XML firmado ES el documento, y
    regenerarlo cambiaría el `DigestValue` que puede estar ya impreso en un QR.
    """
    if document.signed_xml:
        return document

    data = _order_to_invoice_data(document.order, document.series_ref,
                                  document.number)
    rules.validate(data)
    root = etree.fromstring(builder.build_invoice_xml(data))
    signed = signing.sign_invoice(root, key_pem=key_pem, cert_pem=cert_pem)
    xml = etree.tostring(signed, xml_declaration=True, encoding='UTF-8')

    # El esquema es la última puerta antes de la red. Un XML que no valida no
    # sale: el rechazo llegaría igual, pero minutos después y como un número.
    schema.validate_invoice(xml)

    document.signed_xml = xml.decode('utf-8')
    document.signed_xml_sha256 = hashlib.sha256(xml).hexdigest()
    document.digest_value = signing.digest_value(signed)
    document.status = FiscalDocumentStatus.SIGNED
    document.save(update_fields=[
        'signed_xml', 'signed_xml_sha256', 'digest_value', 'status', 'updated_at',
    ])
    return document


#: Cuánto puede durar un envío antes de darlo por muerto. Un intento con
#: `finished_at` nulo más antiguo que esto se considera abandonado —proceso
#: caído, contenedor reiniciado— y deja de bloquear los reintentos.
STALE_ATTEMPT_MINUTES = 10


class FiscalSubmissionInProgress(FiscalError):
    """Ya hay un envío en curso para este comprobante. No se llama otra vez."""


def _claim_attempt(document: FiscalDocument) -> FiscalSubmissionAttempt:
    """
    Reserva el turno de envío. Devuelve la fila; la red va DESPUÉS.

    Un intento sin `finished_at` significa «alguien está llamando a SUNAT ahora
    mismo». Si es reciente, esta petición se retira: dos transmisiones
    simultáneas del mismo comprobante pueden producir dos registros en SUNAT y
    sólo uno de nuestros lados se entera.

    Si es viejo, se da por abandonado. Sin ese plazo, un proceso caído a mitad de
    envío dejaría el comprobante bloqueado para siempre y sin forma de
    desbloquearlo desde el producto.
    """
    from datetime import timedelta

    limite = timezone.now() - timedelta(minutes=STALE_ATTEMPT_MINUTES)
    with transaction.atomic():
        en_curso = FiscalSubmissionAttempt.objects.select_for_update().filter(
            document=document, finished_at__isnull=True,
            started_at__gte=limite,
        ).first()
        if en_curso is not None:
            raise FiscalSubmissionInProgress(
                f'Ya hay un envío en curso para {document.document_id} '
                f'(intento {en_curso.attempt_number}). Espere a que termine.'
            )

        siguiente = (
            FiscalSubmissionAttempt.objects.filter(document=document)
            .order_by('-attempt_number').values_list('attempt_number', flat=True)
            .first() or 0
        ) + 1
        return FiscalSubmissionAttempt.objects.create(
            document=document, attempt_number=siguiente,
            environment=document.environment, started_at=timezone.now(),
            result='in_progress',
        )


def submit_fiscal_document(document: FiscalDocument, provider) -> FiscalDocument:
    """
    Envía el documento y registra el intento. FUERA de cualquier transacción.

    Un reintento usa EL MISMO documento, la misma serie, el mismo correlativo y
    el mismo XML firmado. Lo único nuevo es la fila de intento.

    Un documento ya aceptado no se reenvía: SUNAT lo rechazaría por duplicado y
    el reenvío no aportaría nada.
    """
    if not document.signed_xml:
        raise FiscalError('El documento no está firmado.')
    if document.is_accepted:
        return document

    name = packaging.document_name(
        document.issuer_tax_id, document.document_type,
        document.series, document.number,
    )
    zip_bytes = packaging.build_zip(name, document.signed_xml.encode('utf-8'))

    # SE RESERVA EL INTENTO ANTES DE LA RED, y se cierra la transacción.
    #
    # `attempts.count() + 1` calculado justo antes de llamar dejaba una ventana:
    # dos peticiones simultáneas obtenían el mismo número y, peor, AMBAS llamaban
    # a SUNAT antes de que la base de datos detectara el choque. Dos
    # transmisiones del mismo comprobante por un doble clic.
    #
    # Ahora la fila se crea primero, con `finished_at` nulo. La restricción única
    # sobre (documento, número) hace que sólo una petición gane; la otra ve que
    # hay un envío en curso y no llama.
    #
    # La red queda FUERA de la transacción: mantenerla abierta bloquearía la fila
    # durante segundos de espera.
    attempt = _claim_attempt(document)

    result = provider.submit_invoice(filename=f'{name}.ZIP', zip_bytes=zip_bytes)

    attempt.finished_at = timezone.now()
    attempt.result = result.outcome.value
    attempt.response_code = result.response_code
    attempt.safe_message = result.safe_message
    attempt.request_sha256 = result.request_sha256
    attempt.response_sha256 = result.response_sha256
    attempt.save(update_fields=[
        'finished_at', 'result', 'response_code', 'safe_message',
        'request_sha256', 'response_sha256',
    ])

    document.status = OUTCOME_TO_STATUS[result.outcome]
    document.sunat_response_code = result.response_code
    document.sunat_response_message = result.safe_message
    campos = ['status', 'sunat_response_code', 'sunat_response_message', 'updated_at']
    if result.cdr_xml:
        document.cdr_xml = result.cdr_xml.decode('utf-8', 'replace')
        document.cdr_sha256 = hashlib.sha256(result.cdr_xml).hexdigest()
        campos += ['cdr_xml', 'cdr_sha256']
    document.save(update_fields=campos)
    return document
