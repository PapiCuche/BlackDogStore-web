"""
Las reglas que se comprueban ANTES de tocar la red.

POR QUÉ NO BASTA CON EL ESQUEMA
-------------------------------
Un XML puede validar contra el XSD de SUNAT y ser rechazado igualmente: el
esquema dice qué nodos existen y de qué tipo son, no si la serie de una factura
empieza por F ni si el IGV cuadra con la base. Esas son reglas de negocio
tributario y viven aquí.

Comprobarlas en local no duplica al validador de SUNAT: es la diferencia entre
enterarse en microsegundos, con el dato señalado, y enterarse minutos después
por un código numérico devuelto por un servidor.

CADA REGLA CITA SU AUTORIDAD, y es siempre un anexo o una resolución de SUNAT,
nunca un blog. Las URL y las fechas de consulta están en
`docs/sunat-cpe-requisitos.md`.
"""

from __future__ import annotations

import re
from decimal import Decimal

from .data import InvoiceData

#: Catálogo N.º 01. Sólo estos dos entran en el alcance de C2.2A.
INVOICE = '01'
BOLETA = '03'

#: Anexo N.º 1, campo 8: «La serie debe ser alfanumérica de cuatro (4)
#: caracteres, siendo el primer caracter de la izquierda la letra F». Para la
#: boleta, la B (Anexo N.º 2). Reconfirmado por el Anexo N.º 6 vigente desde el
#: 1.8.2026, numeral 6.1.3.b.1.
SERIE_PREFIX = {INVOICE: 'F', BOLETA: 'B'}
SERIE_RE = re.compile(r'^[A-Z0-9]{4}$')

#: Anexo N.º 1, campo 8: «El número correlativo podrá tener hasta ocho (8)
#: caracteres y se iniciará en uno (1)».
CORRELATIVO_MAX = 99_999_999

#: Catálogo N.º 06: `6` es RUC.
DOC_RUC = '6'

#: Catálogo N.º 07: `10` es «Gravado - Operación Onerosa», lo único que esta
#: fase sabe emitir.
AFFECTATION_TAXED = '10'


class FiscalRuleError(ValueError):
    """Una regla tributaria incumplida, con el dato señalado."""


def _fail(message: str) -> None:
    raise FiscalRuleError(message)


def check_ruc(value: str) -> bool:
    """
    Un RUC peruano: 11 dígitos y un prefijo de los que SUNAT asigna.

    NO se implementa el dígito verificador a propósito. Un checksum propio que
    difiera del de SUNAT produciría rechazos nuestros sobre RUC que SUNAT sí
    acepta — y ése es un fallo peor que dejar pasar uno que SUNAT rechazará
    diciendo exactamente qué pasa.
    """
    return bool(re.fullmatch(r'(10|15|16|17|20)\d{9}', value or ''))


def validate(data: InvoiceData) -> None:
    """
    Todo lo comprobable sin red. Levanta al primer incumplimiento.

    De lo estructural a lo aritmético, para que el mensaje señale la causa y no
    una consecuencia: una serie inválida se dice antes de que las sumas «no
    cuadren» por culpa de ella.
    """
    if data.document_type not in SERIE_PREFIX:
        _fail(f'Tipo de documento no soportado: {data.document_type!r}. '
              f'Catálogo N.º 01: {INVOICE} factura, {BOLETA} boleta.')

    prefix = SERIE_PREFIX[data.document_type]
    if not SERIE_RE.fullmatch(data.serie or ''):
        _fail(f'La serie debe ser alfanumérica de 4 caracteres; llegó {data.serie!r}.')
    if not data.serie.startswith(prefix):
        _fail(f'La serie de un documento tipo {data.document_type} debe empezar '
              f'por {prefix!r}; llegó {data.serie!r}. (Anexo N.º 1, campo 8.)')
    if not 1 <= data.correlativo <= CORRELATIVO_MAX:
        _fail(f'El correlativo se inicia en 1 y admite hasta 8 dígitos; '
              f'llegó {data.correlativo}.')

    if data.supplier.doc_type != DOC_RUC:
        _fail('El emisor se identifica siempre con RUC (Catálogo N.º 06, código 6).')
    if not check_ruc(data.supplier.doc_number):
        _fail(f'El emisor debe tener un RUC de 11 dígitos; '
              f'llegó {data.supplier.doc_number!r}.')
    if not data.supplier.legal_name.strip():
        _fail('Falta la razón social del emisor.')

    if data.document_type == INVOICE:
        # Anexo N.º 1, campo 11: «El Tipo de documento será 6 - RUC».
        if data.customer.doc_type != DOC_RUC:
            _fail('Una factura exige RUC del adquirente (Catálogo N.º 06, código 6). '
                  'Para una venta sin RUC corresponde una boleta.')
        if not check_ruc(data.customer.doc_number):
            _fail(f'El RUC del adquirente no es válido: {data.customer.doc_number!r}.')
        if not data.customer.legal_name.strip():
            _fail('La razón social del adquirente es obligatoria en la factura '
                  '(Anexo N.º 1, campo 12).')

    if not re.fullmatch(r'[A-Z]{3}', data.currency or ''):
        _fail(f'La moneda debe ser un código ISO 4217 de 3 letras; '
              f'llegó {data.currency!r}.')

    # SE FALLA CERRADO ante lo que no se sabe emitir. Un XML con un código de
    # afectación inventado sería peor que negarse: presentaría ante SUNAT una
    # declaración que nadie comprobó.
    for i, line in enumerate(data.lines, 1):
        if line.tax_affectation != AFFECTATION_TAXED:
            _fail(f'Línea {i}: esta fase sólo emite operaciones gravadas '
                  f'(Catálogo N.º 07, código {AFFECTATION_TAXED}). '
                  f'Llegó {line.tax_affectation!r}.')
        if line.quantity <= 0:
            _fail(f'Línea {i}: la cantidad debe ser mayor que cero.')
        if not line.description.strip():
            _fail(f'Línea {i}: falta la descripción del ítem.')

    # Las dos identidades que C2.1 garantiza en origen. Repetirlas no es
    # desconfianza: este generador puede recibir datos de otra parte mañana, y
    # un comprobante que no cuadra no debe poder llegar a existir.
    data.check()

    for i, line in enumerate(data.lines, 1):
        esperado = (line.line_amount * line.tax_percent / Decimal('100')).quantize(
            Decimal('0.01')
        )
        if abs(esperado - line.tax_amount) > Decimal('0.01'):
            _fail(f'Línea {i}: el impuesto declarado ({line.tax_amount}) no '
                  f'corresponde al {line.tax_percent}% de {line.line_amount}.')

    suma = sum((ln.tax_amount for ln in data.lines), Decimal('0.00'))
    if abs(suma - data.tax_amount) > Decimal('0.01'):
        _fail(f'Las líneas suman {suma} de impuesto y el documento declara '
              f'{data.tax_amount}.')

    # LA ARITMÉTICA DE CADA LÍNEA TIENE QUE CERRAR.
    #
    # `cantidad × valor unitario` debe dar el importe de la línea. Parece obvio y
    # no lo es: repartir un descuento global reduciendo sólo el importe deja un
    # documento que declara «2 unidades a 100,00» con un total de línea de
    # 184,75. El XSD lo acepta —no comprueba aritmética— y SUNAT lo rechaza, o
    # peor, lo acepta con un precio unitario que nadie cobró.
    #
    # Un descuento se declara con `cac:AllowanceCharge`, no escondiéndolo en el
    # importe. Mientras eso no esté implementado, esta regla impide emitir el
    # documento incoherente.
    for i, line in enumerate(data.lines, 1):
        esperado = (line.quantity * line.unit_price).quantize(Decimal('0.01'))
        if abs(esperado - line.line_amount) > Decimal('0.01'):
            _fail(
                f'Línea {i}: {line.quantity} × {line.unit_price} = {esperado}, '
                f'pero el importe declarado es {line.line_amount}. Un descuento '
                f'no puede esconderse en el importe de la línea: se declara con '
                f'AllowanceCharge, y eso todavía no está implementado.'
            )
