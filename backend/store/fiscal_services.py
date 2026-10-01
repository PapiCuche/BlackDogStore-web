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

UN DESCUENTO SE DECLARA CON LO QUE LA VENTA CONGELÓ (ERP-FISCAL-6)
------------------------------------------------------------------
La venta ya rebajada tiene su desglose; lo que el comprobante necesita además es
el estado de ANTES de rebajar, y sale de `subtotal_amount` con la misma
autoridad de cálculo que hizo el resto del snapshot. Cupón y descuento manual se
declaran como un descuento GLOBAL; una promoción automática, en las líneas que
la recibieron, con la atribución que `promotion_services` congeló en la venta.
Nada de esto cambia un céntimo de lo cobrado: representa; no recalcula.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone
from lxml import etree

from .fiscal import builder, packaging, rules, schema, signing
from .fiscal.data import (
    ALLOWANCE_GLOBAL_TAXABLE, ALLOWANCE_LINE_TAXABLE, Allowance, InvoiceData, Line,
    Party,
)
from .fiscal.provider import ProviderOutcome
from .models import (
    FISCAL_NOTE_TYPES, FISCAL_ORIGINAL_TYPES, AppliedPromotion, DiscountSource,
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

#: Catálogo N.º 06, código `0`: «DOC.TRIB.NO.DOM.SIN.RUC». Es lo que lleva una
#: boleta a consumidor final sin identificación.
DOC_SIN_DOCUMENTO = '0'

#: Qué tipo de comprobante del Catálogo N.º 01 pide cada venta. Se RESUELVE por
#: tipo (ERP-FISCAL-4 §21): no se abre a «todo receipt_type», ni se copia el flujo
#: de factura a ciegas. Lo que no esté aquí falla cerrado.
RECEIPT_TYPE_TO_DOCUMENT_TYPE = {
    Order.ReceiptType.FACTURA: FiscalDocumentType.INVOICE,
    Order.ReceiptType.BOLETA: FiscalDocumentType.RECEIPT,
}

#: Reglamento de Comprobantes de Pago, Art. 8 num. 3.10: una boleta cuyo importe
#: total SUPERE S/ 700 debe identificar al adquirente (tipo y número de
#: documento). «Supere» es estrictamente mayor: 700,00 exacto no lo exige.
BOLETA_ID_THRESHOLD = Decimal('700.00')


def _customer_party(order: Order, document_type: str) -> Party:
    """
    El adquirente, según el tipo de comprobante (ERP-FISCAL-4 §24).

    La regla del receptor NO es la misma para factura y boleta:

    - FACTURA: exige RUC. `rules.validate` lo comprueba; aquí se traslada la
      identidad tal cual, sin cambiar el comportamiento previo.
    - BOLETA: admite DNI/CE/RUC, y admite consumidor final SIN documento
      (Catálogo N.º 06 código `0`, número `0`) cuando el total no supera el
      umbral. Si el total SUPERA S/ 700 y no hay documento, se FALLA CERRADO: la
      norma exige identificar al adquirente y emitir sin hacerlo sería declarar
      una venta que no cumple el Reglamento (§26). La guarda vive en el backend,
      no en una pantalla.

    El nombre de un consumidor final sin documento es una convención, no un
    literal que fije la norma: se usa el nombre de la venta si lo hay, y «VARIOS»
    sólo como relleno del campo obligatorio.
    """
    doc_type = DOC_TYPE_TO_SUNAT.get(order.document_type or '', '')
    number = (order.document_number or '').strip()
    name = (order.customer_name or '').strip()

    if document_type != FiscalDocumentType.RECEIPT:
        # Factura (y cualquier otro tipo): identidad tal cual, como antes.
        return Party(doc_type=doc_type, doc_number=number, legal_name=name)

    identified = bool(doc_type and number)
    # Sin identificar: sólo se permite si consta que el total NO supera el umbral.
    # Un total desconocido no puede darse por «≤ 700»: se falla cerrado (L2).
    if not identified and (order.total is None
                           or order.total > BOLETA_ID_THRESHOLD):
        raise FiscalError(
            f'Una boleta cuyo total ({order.total}) supera S/ {BOLETA_ID_THRESHOLD} '
            f'debe identificar al adquirente con su tipo y número de documento '
            f'(Reglamento de Comprobantes de Pago, Art. 8). Emitirla sin '
            f'identificación declararía una venta que no cumple la norma.'
        )
    if identified:
        return Party(doc_type=doc_type, doc_number=number,
                     legal_name=name or 'CLIENTE')
    # Consumidor final sin documento (total ≤ umbral).
    return Party(doc_type=DOC_SIN_DOCUMENTO, doc_number='0',
                 legal_name=name or 'VARIOS')


def _order_to_invoice_data(order: Order, series: FiscalSeries,
                           number: int) -> InvoiceData:
    """
    Traduce la venta a datos planos. NO calcula dinero: lo copia.

    Se valida que el snapshot de C2.1 esté completo antes de tocar nada: una
    venta sin desglose congelado no puede producir un comprobante, y decirlo aquí
    es más útil que un `TypeError` a mitad de la generación.
    """
    from .company_settings import order_identity
    from .tax_services import TaxTreatment

    if order.taxable_amount is None or order.tax_amount is None:
        raise FiscalError(
            'La venta no tiene desglose tributario congelado. Un comprobante no '
            'puede declarar importes que nadie calculó en el momento de vender.'
        )

    identity = order_identity(order)
    if not identity.tax_id:
        raise FiscalError('La empresa emisora no tiene RUC configurado.')

    # SÓLO GRAVADO, explícitamente (§18). Esta fase modela únicamente la
    # operación gravada; el generador escribe la afectación `10` en cada línea.
    # Emitir una venta exonerada/inafecta reutilizando ese código declararía ante
    # SUNAT una operación que no es. Se falla cerrado en vez de apoyarse en que
    # «hoy el catálogo es todo gravado» — exonerado/inafecto son fases futuras.
    if (order.tax_treatment or TaxTreatment.TAXED) != TaxTreatment.TAXED:
        raise FiscalError(
            'La factura electrónica todavía sólo sabe declarar operaciones '
            f'gravadas; esta venta es «{order.tax_treatment}». Emitirla '
            'declararía ante SUNAT una afectación que no corresponde.'
        )

    # VEN-02A. La AUTORIDAD es el snapshot (order.taxable_amount/tax_amount): las
    # líneas se reconstruyen para SUMAR ese snapshot, no una segunda cuenta que
    # redondea por su lado. El bruto de línea G_i = precio_con_igv × cantidad (2dp
    # exacto para cantidad entera) suma `total`; `allocate_line_bases` reparte la
    # base declarada entre las líneas de forma determinista, y el impuesto de
    # línea es G_i − base_i (así Σ impuesto = total − taxable = tax por
    # construcción). El valor unitario sin impuesto se deriva de la base
    # reconciliada, a 10 decimales, para que cantidad×unitario devuelva la línea.
    from .fiscal.rounding import (
        ReconciliationError, allocate_line_bases, allocate_proportionally, money,
        unit_value,
    )

    items = list(order.items.select_related('product').order_by('pk'))
    grosses = [money(Decimal(str(item.price)) * item.quantity) for item in items]
    discount = money(order.discount_amount or Decimal('0'))

    # ERP-FISCAL-6. Con descuento hay DOS estados que declarar: el de antes de
    # rebajar y el de después. La venta congeló el de después; el de antes sale
    # de `subtotal_amount` con la misma autoridad de cálculo y la misma tasa.
    #
    #   global (cupón, manual): las líneas van SIN rebajar y suman la base
    #       previa; un `AllowanceCharge` `02` en el documento, con el descuento
    #       NETO (base previa − base de la venta) y la base previa como base.
    #   línea (promoción): cada línea que la promoción rebajó lleva su
    #       `AllowanceCharge` `00`. Las bases se reconcilian una sola vez contra
    #       la base de la venta, sobre los brutos ya rebajados; el descuento
    #       neto del documento se reparte entre esas líneas en proporción a su
    #       rebaja bruta, y la base «de antes» de cada línea es base + reparto.
    #       Dos reconciliaciones independientes (antes y después) no valdrían:
    #       el desempate por mayor residuo puede mover un céntimo a una línea
    #       sin descuento y declararle una rebaja de 0,01 —o una negativa—.
    global_allowances: tuple[Allowance, ...] = ()
    line_gross_discounts = [Decimal('0.00')] * len(items)
    line_net_allowances = [Decimal('0.00')] * len(items)
    target_taxable = order.taxable_amount
    if discount > 0:
        subtotal = _pre_discount_subtotal(order, discount)
        if sum(grosses, Decimal('0.00')) != subtotal:
            raise FiscalError(
                f'Las líneas congeladas suman {sum(grosses, Decimal("0.00"))} y el '
                f'subtotal previo al descuento es {subtotal}. El snapshot de la '
                f'venta es incoherente y no se corrige aquí.'
            )
        pre_taxable = _pre_discount_taxable(order, subtotal)
        net = pre_taxable - order.taxable_amount
        if net <= 0:
            raise FiscalError(
                f'El descuento de {discount} no llega a rebajar la base imponible '
                f'({pre_taxable} antes, {order.taxable_amount} después): no hay '
                f'importe que declarar y un descuento de 0,00 no es admisible '
                f'(regla 2968).'
            )
        source = order.discount_source
        if source in (DiscountSource.COUPON, DiscountSource.MANUAL):
            global_allowances = (Allowance(
                amount=net, base_amount=pre_taxable,
                reason_code=ALLOWANCE_GLOBAL_TAXABLE,
            ),)
            target_taxable = pre_taxable
        elif source == DiscountSource.PROMOTION:
            line_gross_discounts = _promotion_line_discounts(order, items, grosses)
            try:
                line_net_allowances = allocate_proportionally(net, line_gross_discounts)
            except ReconciliationError as exc:
                raise FiscalError(str(exc)) from exc
        else:
            raise FiscalError(
                f'Esta venta tiene un descuento de {discount} con origen '
                f'«{source}», que esta versión no sabe declarar.'
            )

    gross_after = [g - d for g, d in zip(grosses, line_gross_discounts)]
    try:
        bases = allocate_line_bases(
            gross_after, taxable=target_taxable, rate=order.tax_rate,
        )
    except ReconciliationError as exc:
        # Un descuadre que no es redondeo (caso no gravado, snapshot
        # incoherente) falla cerrado como error de dominio, nunca como 500.
        raise FiscalError(str(exc)) from exc

    percent = (order.tax_rate * 100).quantize(Decimal('0.01'))
    lines = []
    for item, gross_after_i, base, net_i, gross_discount_i in zip(
            items, gross_after, bases, line_net_allowances, line_gross_discounts):
        if gross_discount_i > 0 and base <= 0:
            raise FiscalError(
                f'La línea de «{item.product.name}» queda con valor de venta cero '
                f'tras la promoción. Una entrega sin valor es una operación '
                f'gratuita (Catálogo N.º 07, códigos 11-16), que esta fase no '
                f'sabe declarar; SUNAT exige un valor de venta distinto de cero '
                f'(regla 2370).'
            )
        before = base + net_i
        allowances = ()
        if net_i > 0:
            allowances = (Allowance(
                amount=net_i, base_amount=before, reason_code=ALLOWANCE_LINE_TAXABLE,
            ),)
        # Sin descuento en la línea, el precio que pagó el cliente ES el de
        # catálogo, y el texto del XML no cambia. Con descuento, es lo que pagó
        # por unidad: (valor de venta + impuesto) / cantidad (regla 3270).
        price_with_tax = (
            unit_value(gross_after_i, item.quantity) if gross_discount_i > 0
            else Decimal(str(item.price))
        )
        lines.append(Line(
            description=(item.product.name if item.product else 'PRODUCTO')[:250],
            quantity=Decimal(item.quantity),
            unit_code='NIU',
            unit_price=unit_value(before, item.quantity),
            unit_price_with_tax=price_with_tax,
            line_amount=base,
            tax_amount=gross_after_i - base,
            tax_percent=percent,
            allowances=allowances,
        ))

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
        customer=_customer_party(order, series.document_type),
        lines=tuple(lines),
        taxable_amount=order.taxable_amount,
        tax_amount=order.tax_amount,
        total=order.total,
        amount_in_words=_amount_in_words(order.total, order.currency or 'PEN'),
        allowances=global_allowances,
    )


def _pre_discount_subtotal(order: Order, discount: Decimal) -> Decimal:
    """
    El subtotal previo al descuento, o por qué la venta no se puede declarar.

    SE FALLA CERRADO ante lo que no cuadra, y no se «repara»: un descuento sin
    origen, un subtotal que la venta no conserva o una resta que no da el total
    son defectos del snapshot, y un comprobante legal no es el sitio donde
    adivinarlos. En particular NO se reconstruye `subtotal = total + descuento`:
    sería inventar el dato sobre el que se rebajó.
    """
    from .fiscal.rounding import money

    if (order.discount_source or DiscountSource.NONE) == DiscountSource.NONE:
        raise FiscalError(
            f'Esta venta tiene un descuento de {discount} sin origen declarado '
            f'(`discount_source` = «none»). Un descuento que nadie explica no se '
            f'puede declarar ante SUNAT; se corrige en la venta, no en el '
            f'comprobante.'
        )
    if order.subtotal_amount is None:
        raise FiscalError(
            f'Esta venta tiene un descuento de {discount} y no conserva el '
            f'subtotal previo al descuento. Sin él no se puede declarar sobre qué '
            f'valor se rebajó, y reconstruirlo sumando total y descuento sería '
            f'inventar un dato para un documento legal.'
        )
    subtotal = money(order.subtotal_amount)
    if discount > subtotal:
        raise FiscalError(
            f'El descuento ({discount}) supera el subtotal de la venta ({subtotal}).')
    if subtotal - discount != money(order.total):
        raise FiscalError(
            f'El snapshot de la venta no cuadra: subtotal {subtotal} − descuento '
            f'{discount} != total {order.total}.'
        )
    return subtotal


def _pre_discount_taxable(order: Order, subtotal: Decimal) -> Decimal:
    """
    La base imponible que la venta habría tenido SIN el descuento.

    Se obtiene con LA MISMA autoridad de cálculo que congeló el resto del
    snapshot —`tax_services.breakdown_from_total`—, aplicada al subtotal previo
    al descuento y a la tasa congelada. No es una segunda contabilidad: es la
    misma cuenta, hecha sobre el importe de antes de rebajar. Nunca
    `descuento / 1,18` por su cuenta.
    """
    from .tax_services import TaxTreatment, breakdown_from_total

    return breakdown_from_total(
        total=subtotal, tax_rate=order.tax_rate, currency=order.currency or 'PEN',
        treatment=TaxTreatment.TAXED,
    ).taxable_amount


def _promotion_line_discounts(order: Order, items, grosses) -> list[Decimal]:
    """
    Cuánto rebajó la promoción a cada línea de la venta, en BRUTO (con IGV).

    Sale del snapshot `AppliedPromotion` de ESTA venta y de ESTA empresa, y de
    la atribución por componente que `promotion_services` congeló —o, para una
    venta anterior a la atribución, reconstruye en memoria con la misma regla—.
    Nunca de la `Promotion` viva: la promoción de marzo se editó en abril.

    NO SE CONFÍA EN LOS IDS DEL JSON. Cada producto atribuido tiene que estar
    entre las líneas de la venta, pertenecer a su empresa, no consumir más
    unidades de las vendidas y haberse congelado al mismo precio que la línea.
    Cualquier otra cosa es un snapshot que no describe esta venta, y se falla
    cerrado sin corregir nada.
    """
    from .fiscal.rounding import money
    from .promotion_services import PromotionAllocationError, frozen_component_discounts

    applied = list(
        AppliedPromotion.objects
        .filter(company_id=order.company_id, order=order)
        .select_related('promotion').order_by('pk')
    )
    if not applied:
        raise FiscalError(
            'La venta declara un descuento por promoción automática y no conserva '
            'ninguna promoción aplicada. Sin ese snapshot no hay a qué atribuir '
            'la rebaja.'
        )
    declared = sum((row.discount_amount for row in applied), Decimal('0.00'))
    if declared != money(order.discount_amount):
        raise FiscalError(
            f'Las promociones aplicadas suman {declared} de descuento y la venta '
            f'declara {money(order.discount_amount)}.'
        )

    by_product: dict[int, Decimal] = {}
    units: dict[int, int] = {}
    unit_prices: dict[int, set] = {}
    for row in applied:
        if row.promotion.company_id != order.company_id:
            raise FiscalError(
                f'La promoción «{row.promotion_name_snapshot}» no pertenece a la '
                f'empresa de la venta.'
            )
        try:
            components = frozen_component_discounts(
                row.regular_amount, row.discount_amount,
                (row.metadata or {}).get('components'),
            )
        except PromotionAllocationError as exc:
            raise FiscalError(
                f'La promoción «{row.promotion_name_snapshot}» de esta venta no se '
                f'puede explicar componente a componente: {exc}'
            ) from exc
        for component in components:
            pid = component['product_id']
            by_product[pid] = by_product.get(pid, Decimal('0.00')) + component['discount_amount']
            units[pid] = units.get(pid, 0) + component['quantity_used']
            unit_prices.setdefault(pid, set()).add(Decimal(str(component['unit_price'])))

    # Una línea por producto: lo garantiza `unique_order_line_per_product` en la
    # base, así que el mapa no puede perder una posición.
    positions = {item.product_id: index for index, item in enumerate(items)}

    discounts = [Decimal('0.00')] * len(items)
    for pid, amount in by_product.items():
        index = positions.get(pid)
        if index is None:
            raise FiscalError(
                f'La promoción rebaja el producto {pid}, que no está entre las '
                f'líneas de esta venta.'
            )
        item = items[index]
        if item.product is None or item.product.company_id != order.company_id:
            raise FiscalError(
                f'El producto {pid} de la promoción no pertenece a la empresa de '
                f'la venta.'
            )
        if units[pid] > item.quantity:
            raise FiscalError(
                f'La promoción consume {units[pid]} unidad(es) del producto {pid} '
                f'y la venta sólo tiene {item.quantity}.'
            )
        if unit_prices[pid] != {Decimal(str(item.price))}:
            frozen = ', '.join(str(p) for p in sorted(unit_prices[pid]))
            raise FiscalError(
                f'La promoción congeló el producto {pid} a {frozen} y la línea de '
                f'la venta lo vendió a {item.price}.'
            )
        if amount > grosses[index]:
            raise FiscalError(
                f'La promoción rebaja {amount} del producto {pid} y la línea sólo '
                f'vale {grosses[index]}.'
            )
        discounts[index] = amount
    return discounts


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


def original_fiscal_documents(order: Order):
    """Original receipts for this sale; correcting notes have their own routes."""
    return FiscalDocument.objects.filter(
        company_id=order.company_id, order=order, document_type__in=FISCAL_ORIGINAL_TYPES,
    )


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
    # SE RESUELVE EL TIPO POR EL receipt_type de la venta (§21). No se abre a
    # «cualquier tipo»: lo que no esté en el mapa falla cerrado.
    document_type = RECEIPT_TYPE_TO_DOCUMENT_TYPE.get(order.receipt_type)
    if document_type is None:
        raise FiscalError(
            f'Esta venta pide «{order.receipt_type}», que no es un comprobante '
            f'electrónico que esta versión sepa emitir.'
        )

    # UN DESCUENTO SE DECLARA, NO SE RECHAZA (ERP-FISCAL-6). Hasta esta fase
    # cualquier venta con descuento fallaba cerrado aquí, porque el generador no
    # sabía expresarlo en UBL. Ahora `_order_to_invoice_data` lo declara con
    # `cac:AllowanceCharge` —global o de línea según su origen— y sigue fallando
    # cerrado, con la causa, ante un descuento que el snapshot no explica.

    existing = original_fiscal_documents(order).order_by('-pk').first()
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
            document_type=document_type,
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
                # REVIEW A (H2). `issued_at` es la fecha LEGAL de emisión —la que
                # va en `cbc:IssueDate` del XML, derivada de `paid_at`—, no la del
                # reloj al crear la fila. Así la agrupación del Resumen Diario por
                # fecha coincide con la fecha del comprobante y su `ReferenceDate`
                # no contradice a las boletas que informa.
                number=number, issued_at=(order.paid_at or timezone.now()),
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
    except IntegrityError:
        # ERP-FISCAL-08. Dos emisiones simultáneas de la MISMA venta corren la
        # carrera: una crea el documento y la otra viola la única
        # `fiscal_document_one_live_per_order`. Como get_or_create es
        # idempotente, la perdedora devuelve el documento que ya existe en vez de
        # estrellarse con 500. Se DISCRIMINA el conflicto conocido: si tras la
        # violación hay un documento vivo para esta venta, es esa carrera; si no,
        # la IntegrityError es otra cosa y se re-lanza (sigue siendo 500).
        existing = (
            original_fiscal_documents(order)
            .exclude(status=FiscalDocumentStatus.REJECTED)
            .order_by('-pk').first()
        )
        if existing is not None:
            return existing, False
        raise
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
    # ERP-FISCAL-1E. Un incumplimiento de regla fiscal aquí es un error de
    # DOMINIO (400), no un fallo inesperado (500). Sólo se traduce ESA excepción
    # conocida; cualquier otra sube y sigue siendo 500 (§31).
    try:
        rules.validate(data)
    except rules.FiscalRuleError as exc:
        raise FiscalError(str(exc)) from exc
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
    # REVIEW A (H1). Una BOLETA no se envía por `sendBill`: SUNAT no la acepta por
    # ese canal y la dejaría RECHAZADA, y una boleta rechazada queda excluida de
    # todo resumen futuro —una venta entregada que nunca se informa—. La boleta se
    # informa por el Resumen Diario. Se falla cerrado aquí, en el backend.
    if document.document_type == FiscalDocumentType.RECEIPT:
        raise FiscalError(
            'Una boleta se informa a SUNAT mediante el Resumen Diario, no por '
            'envío individual. Inclúyala en un resumen.')
    # ERP-FISCAL-5A. Una nota de BOLETA (07/08 con serie B) tampoco se envía por
    # `sendBill`: comparte canal con la boleta que corrige —el Resumen Diario—. El
    # prefijo lo fija `resolve_note_series` según el original, así que una serie B
    # en un 07/08 es, sin ambigüedad, una nota de boleta. Se falla cerrado aquí
    # como defensa en profundidad: la emisión ya lo impide antes (NC/ND de boleta
    # es PENDIENTE), y este canal no debe ser una segunda puerta a ese error.
    if document.document_type in FISCAL_NOTE_TYPES and document.series.startswith('B'):
        raise FiscalError(
            'Una nota de boleta se informa por el Resumen Diario, no por envío '
            'individual.')
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

    # FINALIZAR bajo bloqueo, releyendo el estado. Entre el claim y aquí (el envío
    # a SUNAT puede tardar decenas de segundos), una RECONCILIACIÓN concurrente
    # pudo dejar el comprobante en un estado TERMINAL a partir del CDR de una
    # transmisión anterior que sí llegó. No se pisa: el veredicto terminal manda y
    # el intento ya quedó registrado. Sin esto, el `save` a ciegas sobre el objeto
    # en memoria revertía un ACEPTADO correcto (hallazgo REVIEW A, ERP-FISCAL-3).
    with transaction.atomic():
        locked = FiscalDocument.objects.select_for_update().get(pk=document.pk)
        if locked.is_accepted or locked.status == FiscalDocumentStatus.REJECTED:
            for campo in ('status', 'sunat_response_code', 'sunat_response_message',
                          'cdr_xml', 'cdr_sha256'):
                setattr(document, campo, getattr(locked, campo))
            return document

        document.status = OUTCOME_TO_STATUS[result.outcome]
        document.sunat_response_code = result.response_code
        document.sunat_response_message = result.safe_message
        campos = ['status', 'sunat_response_code', 'sunat_response_message',
                  'updated_at']
        # LA FECHA DE RECEPCIÓN DE LA CDR ACEPTADA, UNA SOLA VEZ (ERP-FISCAL-5B).
        #
        # De aquí sale el plazo de la Comunicación de Baja: el artículo 14.1.b
        # cuenta desde el día siguiente de haber RECIBIDO la CDR aceptada. Si ya
        # estaba puesta no se toca — reescribirla correría el plazo solo, y el
        # plazo es lo que separa una baja válida de una rechazada.
        if document.is_accepted and document.cdr_accepted_at is None:
            document.cdr_accepted_at = timezone.now()
            campos.append('cdr_accepted_at')
        if result.cdr_xml:
            document.cdr_xml = result.cdr_xml.decode('utf-8', 'replace')
            document.cdr_sha256 = hashlib.sha256(result.cdr_xml).hexdigest()
            campos += ['cdr_xml', 'cdr_sha256']
        document.save(update_fields=campos)
    return document


# ---------------------------------------------------------------------------
# ERP-FISCAL-3 — reconciliación: cerrar un envío cuyo desenlace no conocemos
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReconciliationResult:
    """
    El desenlace de una reconciliación, para la vista y la bitácora.

    `document` es el comprobante ya releído; `action` dice qué pasó, con un
    vocabulario cerrado para que la auditoría y la respuesta HTTP no lo
    interpreten cada una por su cuenta.
    """

    document: FiscalDocument
    action: str
    #: El estado terminal que el CDR determinó, si se aplicó alguno.
    new_status: str = ''
    #: El código CRUDO de SUNAT que trajo la consulta (§20). Nunca se pierde.
    sunat_code: str = ''
    safe_message: str = ''


#: Desde estos estados reconciliar TIENE sentido: se intentó enviar y el desenlace
#: quedó incierto o pendiente. Los terminales no se reconcilian: se informan.
_RECONCILABLE_STATES = (
    FiscalDocumentStatus.SUBMISSION_ERROR,
    FiscalDocumentStatus.SUBMITTED,
)

#: Los ÚNICOS veredictos de CDR que resuelven a un estado terminal. Un CDR con
#: cualquier otro código (desconocido, o de un rango reintentable) llegó pero no
#: zanja nada: el comprobante se queda no terminal, sin reenviar (§24/§25).
_TERMINAL_CDR_OUTCOMES = frozenset({
    ProviderOutcome.ACCEPTED,
    ProviderOutcome.ACCEPTED_WITH_OBSERVATION,
    ProviderOutcome.REJECTED,
})


def reconcile_fiscal_document(document: FiscalDocument, consult_provider
                              ) -> ReconciliationResult:
    """
    Reconcilia un comprobante cuyo envío quedó incierto, consultando su CDR por
    IDENTIFICADOR (`getStatusCdr`). El identificador es el YA EMITIDO: la consulta
    no inventa nada nuevo.

    LO QUE NUNCA HACE: reenviar, reservar otro correlativo, crear otro documento,
    ni resucitar un rechazo con una consulta accidental. La red va FUERA de
    transacción; el estado terminal se fija bajo un bloqueo breve que NO envuelve
    ninguna llamada externa (§46/§47), releyendo el estado para que dos
    reconciliaciones simultáneas no se pisen (§26).
    """
    if not document.signed_xml:
        raise FiscalError(
            'El documento no está firmado: no hay envío que reconciliar.')

    # Terminal de entrada: no se consulta para no arriesgar sobrescribirlo.
    # Aceptado es idempotente (§27); un rechazo no se «resucita» (§28).
    if document.is_accepted:
        return ReconciliationResult(document, 'already_accepted',
                                    sunat_code=document.sunat_response_code)
    if document.status == FiscalDocumentStatus.REJECTED:
        return ReconciliationResult(document, 'already_rejected',
                                    sunat_code=document.sunat_response_code)
    if document.status not in _RECONCILABLE_STATES:
        raise FiscalError(
            f'Un comprobante en estado «{document.get_status_display()}» no se '
            f'reconcilia: todavía no se ha intentado enviarlo.')

    # Lectura remota. Idempotente en SUNAT: no cambia nada allá.
    result = consult_provider.get_document_cdr(
        issuer_ruc=document.issuer_tax_id,
        document_type=document.document_type,
        series=document.series,
        number=document.number,
    )
    return _apply_reconciliation(document, result)


def _apply_reconciliation(document: FiscalDocument, result) -> ReconciliationResult:
    from .fiscal.cdr import CdrParseError, cdr_matches_document, parse_cdr
    from .fiscal.provider import ReconcileOutcome

    if result.outcome != ReconcileOutcome.RESOLVED:
        # No llegó un CDR terminal. El comprobante se queda como estaba y no se
        # reenvía; el código crudo se conserva para que un humano lo mire.
        return ReconciliationResult(
            document, result.outcome.value,
            sunat_code=result.status_code, safe_message=result.safe_message)

    cdr_result = result.cdr
    if cdr_result is None or not cdr_result.cdr_xml:
        return ReconciliationResult(
            document, 'cdr_unreadable', sunat_code=result.status_code)

    # BINDING (§64/§65): el CDR debe corresponder a ESTE comprobante. Uno de otro
    # comprobante no se aplica jamás — llevaría el estado de una venta a lo que
    # resolvió otra.
    try:
        rep = parse_cdr(cdr_result.cdr_xml)
    except CdrParseError:
        return ReconciliationResult(document, 'cdr_unreadable',
                                    sunat_code=result.status_code)
    if not cdr_matches_document(
            rep, document_id=document.document_id,
            issuer_tax_id=document.issuer_tax_id,
            document_type=document.document_type):
        return ReconciliationResult(document, 'cdr_mismatch',
                                    sunat_code=cdr_result.response_code)

    # Defensa en profundidad: el veredicto que se APLICA lo lee el intérprete
    # común (`_read_cdr`, laxo), mientras que el binding lo ancló la ruta ESTRICTA
    # de `parse_cdr`. Si el `ResponseCode` difiere entre ambas lecturas, el CDR
    # está malformado o manipulado (un código plantado fuera del nodo que ancló el
    # binding): no se aplica nada.
    if rep.response_code != cdr_result.response_code:
        return ReconciliationResult(document, 'cdr_inconclusive',
                                    sunat_code=cdr_result.response_code)

    # Llegó un CDR, pero su código no resuelve a un estado terminal (código
    # desconocido, o de un rango reintentable dentro de una constancia legible):
    # NO es «reconciliado». Queda no terminal, sin tocar el estado.
    if cdr_result.outcome not in _TERMINAL_CDR_OUTCOMES:
        return ReconciliationResult(document, 'cdr_inconclusive',
                                    sunat_code=cdr_result.response_code)

    new_status = OUTCOME_TO_STATUS[cdr_result.outcome]
    cdr_bytes = cdr_result.cdr_xml

    # FINALIZAR bajo bloqueo, releyendo el estado. La red YA ocurrió; el bloqueo
    # es breve y no envuelve ninguna llamada externa.
    with transaction.atomic():
        fresh = FiscalDocument.objects.select_for_update().get(pk=document.pk)
        if fresh.status not in _RECONCILABLE_STATES:
            # Otra reconciliación (o el envío) llegó antes y ya lo dejó terminal.
            if fresh.status == new_status:
                return ReconciliationResult(fresh, 'already_terminal',
                                            new_status=fresh.status,
                                            sunat_code=cdr_result.response_code)
            # SUNAT dice algo distinto de lo ya guardado: NO se sobrescribe la
            # historia; se marca conflicto para auditarlo (§28).
            return ReconciliationResult(
                fresh, 'reconciliation_conflict', new_status=new_status,
                sunat_code=cdr_result.response_code,
                safe_message=cdr_result.safe_message)

        fresh.status = new_status
        fresh.sunat_response_code = cdr_result.response_code
        fresh.sunat_response_message = cdr_result.safe_message
        fresh.cdr_xml = cdr_bytes.decode('utf-8', 'replace')
        fresh.cdr_sha256 = hashlib.sha256(cdr_bytes).hexdigest()
        campos = ['status', 'sunat_response_code', 'sunat_response_message',
                  'cdr_xml', 'cdr_sha256', 'updated_at']
        # LA MISMA FECHA, POR EL OTRO CAMINO (ERP-FISCAL-5B).
        #
        # Una aceptación que llega por reconciliación cuenta igual que una que
        # llega por el envío, así que estampa la fecha —y también sólo la primera
        # vez—. Sin esto, precisamente los comprobantes aceptados de forma
        # asíncrona se quedarían sin plazo calculable: esta vía no crea fila de
        # intento, y `updated_at` es `auto_now` (cualquier guardado lo mueve).
        if fresh.is_accepted and fresh.cdr_accepted_at is None:
            fresh.cdr_accepted_at = timezone.now()
            campos.append('cdr_accepted_at')
        fresh.save(update_fields=campos)

    return ReconciliationResult(
        fresh, 'reconciled', new_status=new_status,
        sunat_code=cdr_result.response_code, safe_message=cdr_result.safe_message)
