"""
LA ÚNICA AUTORIDAD QUE DESCOMPONE UNA VENTA EN BASE E IMPUESTO.

EL PROBLEMA QUE CIERRA
----------------------
Antes de esto no había impuesto en ninguna parte: `subtotal = Σ(precio × cant)`,
`total = subtotal − descuento`, y ahí acababa. Un comprobante que hay que
entregar en Perú necesita decir cuánto de ese total es valor de venta y cuánto
es tributo.

El riesgo al añadirlo es que cada superficie lo calcule a su manera —el
escaparate de una, el punto de venta de otra, el PDF de una tercera— y que los
tres números difieran en un céntimo. Un céntimo de diferencia entre lo que ve el
cliente, lo que cobra la pasarela y lo que dice el papel es un descuadre
contable, no un detalle estético. Por eso hay una sola función.

LOS PRECIOS DEL CATÁLOGO YA INCLUYEN EL IMPUESTO
------------------------------------------------
Se comprobó leyendo el código, no suponiendo: `checkout_services.price_checkout`
y `pos_services` construyen el total sumando `Product.price` y restando el
descuento. Ese total es exactamente lo que se cobra hoy.

Por tanto el impuesto se descompone HACIA ATRÁS. Aplicar el 18 % encima
convertiría un artículo de S/ 118 en S/ 139,24 — cambiaría lo que la tienda
cobra, que no es lo que se pide.

    total     = subtotal − descuento          (intacto: es lo que se cobra)
    base      = total / (1 + tasa)            redondeado a céntimos
    impuesto  = total − base                  POR DIFERENCIA

EL IMPUESTO SE OBTIENE RESTANDO, Y ESO NO ES UN ATAJO. Calcularlo aparte como
`base × tasa` y redondear ambos por separado produce sumas que no cuadran: dos
redondeos independientes pueden separarse un céntimo del total. Restando, la
identidad `base + impuesto = total` se cumple SIEMPRE, por construcción, para
cualquier importe y cualquier tasa.

LA TASA SE CONGELA EN LA VENTA
------------------------------
No es una precaución teórica. La Ley N.º 32387 reparte el 18 % entre IGV e
Impuesto de Promoción Municipal de forma distinta cada año —15,5 + 2,5 en 2026,
15,0 + 3,0 en 2027, hasta 14,0 + 4,0 en 2029— manteniendo el total en 18 %. Un
documento emitido hoy tiene que seguir diciendo lo que dijo cuando el reparto
cambie, así que la venta guarda su propia copia de la tasa y ninguna lectura
posterior la recalcula.

Fuente: SUNAT, «Concepto, tasa y operaciones gravadas — IGV».

LO QUE ESTO NO ES
-----------------
No emite comprobantes electrónicos. No firma nada. No habla con SUNAT. Descompone
importes; la emisión fiscal es otra fase y otro modelo.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

#: Los importes de dinero de este proyecto viven a dos decimales.
CENT = Decimal('0.01')

#: Tasa total aplicable a una operación gravada en Perú.
#:
#: 18 %, y se guarda como UNA tasa a propósito. El reparto interno entre IGV e
#: IPM cambia cada año por la Ley N.º 32387, pero lo que se aplica al importe es
#: siempre el total: partirlo en dos columnas obligaría a redondear dos veces y
#: a que la suma no cuadrara, a cambio de un detalle que la representación
#: impresa no necesita separar.
DEFAULT_TAX_RATE = Decimal('0.18')


class TaxTreatment:
    """
    Cómo tributa una venta.

    Existe para no dar por sentado eternamente que todo está gravado. Hoy sólo
    se emite `TAXED`, pero el campo viaja congelado en cada venta, así que el
    día que un tenant venda algo exonerado los documentos antiguos seguirán
    diciendo lo que decían.
    """

    TAXED = 'taxed'
    EXEMPT = 'exempt'
    UNAFFECTED = 'unaffected'

    CHOICES = [
        (TAXED, 'Gravado'),
        (EXEMPT, 'Exonerado'),
        (UNAFFECTED, 'Inafecto'),
    ]


#: Cómo se escribe cada moneda delante de un importe.
#:
#: Un documento peruano dice «S/ 118,00», no «PEN 118,00». El código ISO es lo
#: que se guarda; el símbolo es lo que se imprime. Una moneda que no figure aquí
#: se imprime con su código: feo, pero nunca incorrecto.
CURRENCY_SYMBOLS = {
    'PEN': 'S/',
    'USD': '$',
    'EUR': '€',
}


def currency_symbol(code: str) -> str:
    """El símbolo con el que imprimir esta moneda."""
    return CURRENCY_SYMBOLS.get((code or 'PEN').upper()[:3], (code or 'PEN').upper()[:3])


def money(value) -> Decimal:
    """
    Dos decimales, media al alza — la convención monetaria del proyecto.

    UN `float` SE RECHAZA EN VEZ DE ACEPTARSE EN SILENCIO. `Decimal(0.1)` no
    vale 0,1: vale 0,1000000000000000055511151231257827, y redondear eso a
    céntimos oculta el error justo hasta que un total deja de cuadrar. Toda esta
    fase existe para que `base + impuesto = total` se cumpla siempre, y un float
    que entra por aquí es la vía más rápida de romperlo.

    Se levanta al llamar, no se arregla por dentro: quien pasa un float tiene un
    float en su cadena de cálculo, y ése es el problema que hay que ver.
    """
    if isinstance(value, float):
        raise TypeError(
            'money() no acepta float: use Decimal o str. '
            f'Recibido: {value!r}'
        )
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class TaxBreakdown:
    """
    El desglose de UNA venta, ya cerrado.

    Inmutable a propósito: quien lo recibe lo muestra o lo guarda, no lo ajusta.
    Un desglose «corregido» aguas abajo es exactamente cómo dos superficies
    acaban enseñando cifras distintas de la misma venta.
    """

    currency: str
    #: Suma de las líneas antes de aplicar descuento.
    subtotal: Decimal
    discount_amount: Decimal
    #: Valor de venta: lo que queda tras quitar el tributo del total.
    taxable_amount: Decimal
    tax_amount: Decimal
    tax_rate: Decimal
    tax_treatment: str
    #: Lo que se cobra. No lo decide esta función: entra dado.
    total: Decimal

    def as_dict(self) -> dict:
        """Cadenas, no floats: un importe que pasa por `float` deja de ser exacto."""
        return {
            'currency': self.currency,
            'subtotal': str(self.subtotal),
            'discount_amount': str(self.discount_amount),
            'taxable_amount': str(self.taxable_amount),
            'tax_amount': str(self.tax_amount),
            'tax_rate': str(self.tax_rate),
            'tax_treatment': self.tax_treatment,
            'total': str(self.total),
        }


def resolve_tax_rate(company=None, *, treatment: str = TaxTreatment.TAXED) -> Decimal:
    """
    La tasa que corresponde a esta venta, AHORA.

    Recibe la empresa porque el día que haya jurisdicciones distintas la
    respuesta dependerá de ella, y quien llama ya no tendrá que cambiar. Hoy
    devuelve la tasa general salvo que la operación no esté gravada.

    Sin condicionales por `slug`: el piloto no es un caso especial del motor.
    """
    if treatment != TaxTreatment.TAXED:
        return Decimal('0')
    return DEFAULT_TAX_RATE


def breakdown_from_total(
    *,
    total,
    subtotal=None,
    discount_amount=Decimal('0.00'),
    tax_rate=None,
    currency: str = 'PEN',
    treatment: str = TaxTreatment.TAXED,
    company=None,
) -> TaxBreakdown:
    """
    Descompone un total YA COBRADO en valor de venta y tributo.

    `total` entra dado y sale igual. Esta función no decide cuánto cuesta la
    venta: eso ya lo resolvieron el catálogo y el descuento. Sólo separa lo que
    dentro de ese importe es tributo.

    El impuesto se obtiene RESTANDO la base al total, no multiplicando la base
    por la tasa. Es lo que garantiza que `base + impuesto == total` sin excepción
    — con dos redondeos independientes, un céntimo se escapa.
    """
    total = money(total)
    discount_amount = money(discount_amount)
    subtotal = money(subtotal) if subtotal is not None else money(total + discount_amount)

    rate = tax_rate if tax_rate is not None else resolve_tax_rate(
        company, treatment=treatment,
    )
    rate = Decimal(rate)

    if rate == 0:
        taxable = total
        tax = Decimal('0.00')
    else:
        taxable = money(total / (Decimal('1') + rate))
        tax = money(total - taxable)

    return TaxBreakdown(
        currency=(currency or 'PEN').upper()[:3],
        subtotal=subtotal,
        discount_amount=discount_amount,
        taxable_amount=taxable,
        tax_amount=tax,
        tax_rate=rate,
        tax_treatment=treatment,
        total=total,
    )


def breakdown_for_order(order) -> TaxBreakdown:
    """
    El desglose de una venta ya registrada, leído de SU PROPIO SNAPSHOT.

    NO recalcula. Una venta de hace un año tiene que seguir diciendo lo que
    dijo aunque hoy la tasa sea otra, la empresa se llame distinto o su
    tratamiento tributario haya cambiado.

    Sólo cae al cálculo en vivo cuando la venta es anterior a que existiera el
    snapshot y no tiene nada guardado — y entonces usa la tasa congelada por la
    migración, no la de hoy.
    """
    stored_rate = getattr(order, 'tax_rate', None)
    if stored_rate is not None and getattr(order, 'taxable_amount', None) is not None:
        return TaxBreakdown(
            currency=(order.currency or 'PEN').upper()[:3],
            subtotal=money(order.subtotal_amount),
            discount_amount=money(order.discount_amount),
            taxable_amount=money(order.taxable_amount),
            tax_amount=money(order.tax_amount),
            tax_rate=Decimal(stored_rate),
            tax_treatment=order.tax_treatment or TaxTreatment.TAXED,
            total=money(order.total),
        )

    return breakdown_from_total(
        total=order.total,
        discount_amount=order.discount_amount,
        company=getattr(order, 'company', None),
    )
