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


#: Catálogo N.º 53 de SUNAT (Anexo N.º 8) en la versión que aplican las reglas de
#: validación vigentes («Reglas de validación de CPE», hoja `Catálogos`, edición
#: 21.04.2025). El código dice DOS cosas: a qué nivel va el descuento y si TOCA
#: la base imponible. Esta fase sólo declara descuentos que la reducen, porque
#: así los congela la venta: `taxable_amount` sale del total ya rebajado.
#:
#:   `00` Descuentos que afectan la base imponible del IGV/IVAP — nivel ÍTEM.
#:   `02` Descuentos globales que afectan la base imponible del IGV/IVAP — GLOBAL.
#:
#: NO SON INTERCAMBIABLES. Un `00` a nivel de documento es la observación 4291 y,
#: peor, las reglas aritméticas de totales (3277, 3278, 3279, 3291) sólo restan
#: los globales `02`/`04`: SUNAT recalcularía el total SIN el descuento y el
#: documento no cuadraría. La versión de 2017 del catálogo (R.S. 117-2017 y
#: siguientes) decía `00 OTROS DESCUENTOS` para todo; ya no es la vigente.
ALLOWANCE_LINE_TAXABLE = '00'
ALLOWANCE_GLOBAL_TAXABLE = '02'


@dataclass(frozen=True)
class Allowance:
    """
    Un descuento declarado: `cac:AllowanceCharge` con `ChargeIndicator=false`.

    `amount` y `base_amount` son VALOR DE VENTA, sin impuesto: el XML compara el
    descuento con las bases de línea, no con lo que el cliente dejó de pagar.
    Quien construye esto ya hizo esa traducción reconciliando contra el snapshot
    de la venta; aquí no se divide nada entre 1,18.

    `multiplier` (`cbc:MultiplierFactorNumeric`) es opcional en el esquema y en
    las reglas (3290/3307 sólo se aplican si existe). Sólo se informa cuando el
    porcentaje quedó congelado como tal en la venta; derivarlo dividiendo dos
    importes inventaría un dato que nadie decidió.
    """

    amount: Decimal
    base_amount: Decimal
    #: Código del Catálogo N.º 53.
    reason_code: str
    multiplier: Decimal | None = None


def _check_allowances(allowances, *, base: Decimal, where: str) -> None:
    """
    Lo que un descuento declarado tiene que cumplir para poder existir.

    El importe es positivo y distinto de cero (reglas 2955/2968: «diferente de
    cero»), no supera la base, y la base declarada ES el valor antes del
    descuento — la línea o el documento sin la rebaja—. Sin esto un `Amount`
    podría decir una cosa y `BaseAmount` otra, y el XSD no se enteraría.
    """
    total = Decimal('0.00')
    for allowance in allowances:
        if not allowance.reason_code:
            raise ValueError(f'{where}: un descuento sin código de motivo (Catálogo N.º 53).')
        if allowance.amount <= 0:
            raise ValueError(
                f'{where}: un descuento declarado tiene que ser mayor que cero; '
                f'llegó {allowance.amount}.')
        if allowance.base_amount <= 0:
            raise ValueError(
                f'{where}: la base del descuento tiene que ser mayor que cero; '
                f'llegó {allowance.base_amount}.')
        if allowance.amount > allowance.base_amount:
            raise ValueError(
                f'{where}: el descuento ({allowance.amount}) supera su base '
                f'({allowance.base_amount}).')
        if allowance.base_amount != base:
            raise ValueError(
                f'{where}: la base declarada del descuento ({allowance.base_amount}) '
                f'no es el valor antes del descuento ({base}).')
        total += allowance.amount
    if total > base:
        raise ValueError(
            f'{where}: los descuentos suman {total} sobre una base de {base}.')


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
    #: Descuentos DE ESTA LÍNEA (`cac:InvoiceLine/cac:AllowanceCharge`). Con
    #: ellos, `line_amount` es el valor de venta DESPUÉS del descuento —así lo
    #: define SUNAT (regla 3271: cantidad × valor unitario − descuentos)— y
    #: `unit_price` sigue siendo el valor unitario SIN rebajar.
    allowances: tuple[Allowance, ...] = field(default_factory=tuple)

    @property
    def value_before_allowances(self) -> Decimal:
        """Cantidad × valor unitario: lo que valía la línea antes de rebajarla."""
        return self.line_amount + sum(
            (a.amount for a in self.allowances), Decimal('0.00'))


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
    #: Descuentos GLOBALES (`/Invoice/cac:AllowanceCharge`). Su base es la suma
    #: de los valores de venta de las líneas; `taxable_amount` ya los tiene
    #: restados. Un descuento que llegó a la venta como cupón o como decisión
    #: del mostrador se declara aquí, no repartido por líneas que no lo pidieron.
    allowances: tuple[Allowance, ...] = field(default_factory=tuple)

    @property
    def document_id(self) -> str:
        """`F001-123`, tal y como va en `cbc:ID` y en el nombre del archivo."""
        return f'{self.serie}-{self.correlativo}'

    @property
    def line_total(self) -> Decimal:
        """Σ valor de venta por ítem, tal y como cada línea lo declara."""
        return sum((ln.line_amount for ln in self.lines), Decimal('0.00'))

    @property
    def line_allowance_total(self) -> Decimal:
        return sum(
            (a.amount for ln in self.lines for a in ln.allowances), Decimal('0.00'))

    @property
    def global_allowance_total(self) -> Decimal:
        return sum((a.amount for a in self.allowances), Decimal('0.00'))

    @property
    def allowance_total(self) -> Decimal:
        """Todo lo rebajado, en valor de venta: líneas más global."""
        return self.line_allowance_total + self.global_allowance_total

    def check(self) -> None:
        """
        Las cuentas tienen que cuadrar ANTES de generar nada.

        No es una comprobación de cortesía: si el XML sale con un total que no
        es la suma de sus partes, SUNAT lo rechaza y el rechazo llega minutos
        después por la red. Aquí falla en microsegundos y señala el dato.

        Con descuentos, las identidades son las que SUNAT valida (reglas 3277 y
        3278): la base imponible es la suma de los valores de venta por ítem
        —que ya descuentan lo suyo— MENOS los descuentos globales que afectan a
        la base. Los descuentos de línea NO se restan otra vez: ya están dentro
        de `line_amount`. Y `taxable + tax == total` sigue siendo exacta, sin
        tolerancias: el snapshot de la venta la garantiza en origen.
        """
        if self.taxable_amount + self.tax_amount != self.total:
            raise ValueError(
                f'El comprobante no cuadra: {self.taxable_amount} + '
                f'{self.tax_amount} != {self.total}'
            )
        if not self.lines:
            raise ValueError('Un comprobante sin líneas no es un comprobante.')
        for i, line in enumerate(self.lines, 1):
            _check_allowances(
                line.allowances, base=line.value_before_allowances, where=f'Línea {i}')
        suma_lineas = self.line_total
        _check_allowances(self.allowances, base=suma_lineas, where='El documento')
        global_total = self.global_allowance_total
        if suma_lineas - global_total != self.taxable_amount:
            if global_total:
                raise ValueError(
                    f'Las líneas suman {suma_lineas}, el descuento global es '
                    f'{global_total} y la base declarada es {self.taxable_amount}: '
                    f'{suma_lineas} − {global_total} != {self.taxable_amount}'
                )
            raise ValueError(
                f'Las líneas suman {suma_lineas} y la base declarada es '
                f'{self.taxable_amount}'
            )


@dataclass(frozen=True)
class NoteData:
    """
    Una Nota de Crédito (07) o de Débito (08), lista para convertirse en XML.

    Es un documento RELACIONADO: apunta al comprobante ORIGINAL que modifica, con
    su motivo (Catálogo 09 para la NC, 10 para la ND). El original es inmutable;
    la nota no lo toca, lo referencia. Los importes vienen dados por la operación
    correctiva (no se recalculan aquí); para una anulación total, son los del
    original.
    """

    #: Catálogo N.º 01: `07` nota de crédito, `08` nota de débito.
    document_type: str
    serie: str
    correlativo: int
    issue_date: date
    issue_time: time
    currency: str

    supplier: Party
    customer: Party
    lines: tuple[Line, ...]

    taxable_amount: Decimal
    tax_amount: Decimal
    total: Decimal
    amount_in_words: str

    #: El comprobante que se modifica: su identificador (`F001-123`) y su tipo
    #: (Catálogo N.º 01: `01` factura, `03` boleta).
    original_id: str
    original_type: str
    #: El motivo: código (Catálogo 09 NC / 10 ND) y su descripción textual.
    reason_code: str
    reason_description: str

    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def document_id(self) -> str:
        return f'{self.serie}-{self.correlativo}'

    def check(self) -> None:
        if self.taxable_amount + self.tax_amount != self.total:
            raise ValueError(
                f'La nota no cuadra: {self.taxable_amount} + {self.tax_amount} '
                f'!= {self.total}')
        suma = sum((ln.line_amount for ln in self.lines), Decimal('0.00'))
        if suma != self.taxable_amount:
            raise ValueError(
                f'Las líneas suman {suma} y la base declarada es {self.taxable_amount}')
        if not self.lines:
            raise ValueError('Una nota sin líneas no es una nota.')
        if not self.reason_code:
            raise ValueError('Una nota necesita un motivo (Catálogo 09/10).')
        if not self.original_id or not self.original_type:
            raise ValueError('Una nota debe referenciar el comprobante original.')
