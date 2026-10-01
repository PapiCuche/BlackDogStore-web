"""
El código QR de la representación impresa.

LA ESPECIFICACIÓN, LITERAL
--------------------------
Del Anexo A de la R.S. 113-2018, numeral 6.4.3. Diez campos, separados por `|`,
**sin separador final**:

    RUC | TIPO DE DOCUMENTO | SERIE | NUMERO | MTO TOTAL IGV |
    MTO TOTAL DEL COMPROBANTE | FECHA DE EMISION |
    TIPO DE DOCUMENTO ADQUIRENTE | NUMERO DE DOCUMENTO ADQUIRENTE | VALOR RESUMEN

QUE NO HAYA PIPE FINAL NO ES UNA SUPOSICIÓN. El mismo anexo define el código de
barras PDF417 (numeral 6.3.3) con ONCE campos —añade el valor de la firma— y ahí
sí cierra con `|`. El contraste dentro del mismo documento es la evidencia.

EL VALOR RESUMEN NO SE CALCULA AQUÍ
-----------------------------------
Dice el anexo: «El valor resumen es la cadena resumen en base 64 [...] Corresponde
al valor del elemento <ds:DigestValue> del documento». Se lee del XML firmado.
Calcular un hash propio produciría una cadena distinta de la que el documento
declara, y el QR dejaría de corresponder al comprobante que representa.

NO ES UNA URL. No se codifica un enlace ni un identificador interno: se codifica
esta cadena.
"""

from __future__ import annotations

from decimal import Decimal

#: El anexo: «siendo el separador de campo el carácter |».
SEPARATOR = '|'

#: Numeral 6.4.4: tamaño máximo 6 cm x 6 cm incluido el blanco alrededor, zona de
#: silencio mínima de 1 mm, color negro, y va en la parte inferior de la
#: representación impresa.
MAX_SIZE_CM = 6
QUIET_ZONE_MM = 1


def _money(value: Decimal) -> str:
    """
    «Con el mismo formato empleado en el comprobante», dice el anexo. En el XML
    los importes van a dos decimales, así que aquí también.
    """
    return f'{Decimal(value):.2f}'


def build_qr_payload(*, issuer_tax_id: str, document_type: str, series: str,
                     number: int, tax_amount: Decimal, total: Decimal,
                     issue_date, customer_doc_type: str,
                     customer_doc_number: str, digest_value: str) -> str:
    """
    La cadena exacta que va dentro del QR.

    Los argumentos son POR NOMBRE a propósito: diez campos posicionales en el
    orden equivocado producirían un QR sintácticamente perfecto y semánticamente
    falso, y nada lo detectaría hasta que alguien lo escaneara.
    """
    campos = (
        issuer_tax_id,
        document_type,
        series,
        str(number),
        _money(tax_amount),
        _money(total),
        issue_date.isoformat() if hasattr(issue_date, 'isoformat') else str(issue_date),
        customer_doc_type,
        customer_doc_number,
        digest_value,
    )
    return SEPARATOR.join(campos)


def render_qr_png(payload: str, *, box_size: int = 4) -> bytes:
    """
    El QR como PNG. Corrección de errores media: el papel térmico se borra.

    `border=4` son cuatro módulos de zona de silencio, por encima del mínimo de
    1 mm que exige el anexo para cualquier tamaño de impresión razonable.
    """
    import io

    import qrcode

    code = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=4,
    )
    code.add_data(payload)
    code.make(fit=True)
    buffer = io.BytesIO()
    code.make_image(fill_color='black', back_color='white').save(buffer, format='PNG')
    return buffer.getvalue()
