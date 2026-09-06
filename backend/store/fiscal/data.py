"""
Lo que hace falta para construir un comprobante, y nada más.

ESTAS ESTRUCTURAS SON LA FRONTERA. A un lado queda Django —empresas, pedidos,
snapshots—; al otro, la generación del XML. El generador recibe esto y no puede
preguntar nada más: no tiene ORM que consultar ni ajuste que aplicar.

EL DINERO ENTRA DADO Y SALE IGUAL
---------------------------------
Todos los importes son `Decimal` y vienen del snapshot que C2.1 congeló en la
venta. El generador NO los recalcula. En particular no vuelve a dividir entre
1,18: esa cuenta ya se hizo una vez, en `tax_services`, en el momento de la
venta, con la tasa de ese momento — y el papel tiene que decir lo que dijo.

Si alguna vez estas cifras y las de `Order` difieren en un céntimo, el defecto
está en quien rellenó esto, no en el generador.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal


@dataclass(frozen=True)
class Party:
    """
    Quien emite o quien recibe.

    `doc_type` es el código del Catálogo N.º 06 de SUNAT (`6` RUC, `1` DNI…), no
    el nombre del tipo: el XML lleva el código y traducirlo aquí evitaría tener
    que traducirlo en el generador.
    """

    doc_type: str
    doc_number: str
    legal_name: str
    trade_name: str = ''
    address_line: str = ''
    #: Código de ubigeo. Vacío cuando el documento no lo exige.
    district_code: str = ''
    country_code: str = 'PE'


@dataclass(frozen=True)
class Line:
    """
    Un renglón del comprobante.

    `unit_price` es el valor unitario SIN impuesto y `unit_price_with_tax` el que
    ve el cliente. Se piden los dos en vez de derivar uno del otro por la misma
    razón de siempre: derivarlo obligaría a redondear aquí, y un redondeo más es
    un céntimo menos de acuerdo con el resto del sistema.
    """

    description: str
    quantity: Decimal
    #: Unidad de medida UN/ECE rec. 20. `NIU` es «unidad (bienes)».
    unit_code: str
    unit_price: Decimal
    unit_price_with_tax: Decimal
    #: Valor de venta de la línea: cantidad × valor unitario, sin impuesto.
    line_amount: Decimal
    tax_amount: Decimal
    #: Tasa como porcentaje —18.00—, no como fracción.
    tax_percent: Decimal
    #: Código del Catálogo N.º 07. `10` es gravado, operación onerosa.
    tax_affectation: str = '10'
    item_code: str = ''


@dataclass(frozen=True)
class InvoiceData:
    """
    Un comprobante completo, listo para convertirse en XML.

    `serie` y `correlativo` entran YA ASIGNADOS. El generador no reserva números:
    reservar un correlativo es un acto con consecuencias —entra en el historial y
    no se recicla— y no puede ocurrir dentro de una función que alguien podría
    llamar dos veces para ver cómo queda el XML.
    """

    #: Código del Catálogo N.º 01: `01` factura, `03` boleta.
    document_type: str
    serie: str
    correlativo: int
    issue_date: date
    issue_time: time
    currency: str

    supplier: Party
    customer: Party
    lines: tuple[Line, ...]

    #: Suma de los valores de venta, sin impuesto.
    taxable_amount: Decimal
    tax_amount: Decimal
    #: Lo que se cobra. Entra dado.
    total: Decimal
    #: El importe en letras. Es un dato del documento, no un adorno.
    amount_in_words: str

    #: Tipo de operación del **Catálogo N.º 17**, que va en la extensión
    #: `sac:SUNATTransaction`. `01` es venta interna.
    operation_type: str = '01'

    #: Tipo de operación del **Catálogo N.º 51**, que va en el atributo `listID`
    #: de `cbc:InvoiceTypeCode`. `0101` es venta interna.
    #:
    #: SON DOS CATÁLOGOS DISTINTOS PARA LA MISMA IDEA, con códigos de longitud
    #: distinta. Usar el del 17 en el sitio del 51 devuelve el error 3206, «El
    #: dato ingresado como tipo de operación no corresponde a un valor esperado
    #: (catálogo nro. 51)» — comprobado contra BETA, no supuesto.
    invoice_type_code: str = '0101'
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def document_id(self) -> str:
        """`F001-123`, tal y como va en `cbc:ID` y en el nombre del archivo."""
        return f'{self.serie}-{self.correlativo}'

    def check(self) -> None:
        """
        Las cuentas tienen que cuadrar ANTES de generar nada.

        No es una comprobación de cortesía: si el XML sale con un total que no
        es la suma de sus partes, SUNAT lo rechaza y el rechazo llega minutos
        después por la red. Aquí falla en microsegundos y señala el dato.
        """
        if self.taxable_amount + self.tax_amount != self.total:
            raise ValueError(
                f'El comprobante no cuadra: {self.taxable_amount} + '
                f'{self.tax_amount} != {self.total}'
            )
        suma_lineas = sum((ln.line_amount for ln in self.lines), Decimal('0.00'))
        if suma_lineas != self.taxable_amount:
            raise ValueError(
                f'Las líneas suman {suma_lineas} y la base declarada es '
                f'{self.taxable_amount}'
            )
        if not self.lines:
            raise ValueError('Un comprobante sin líneas no es un comprobante.')
