"""
El ticket de 80 mm de la nota de venta interna.

POR QUÉ ES OTRO MÓDULO Y NO OTRO `if` DENTRO DEL A4
---------------------------------------------------
El A4 y el ticket no son dos estilos del mismo documento: son dos geometrías sin
nada en común. El A4 tiene 17 cm de ancho, tablas de cuatro columnas y una
página de altura conocida. El ticket tiene 7,2 cm útiles, una sola columna, y
altura CONTINUA —el rollo se corta donde termina el contenido—, así que la
altura de la página hay que calcularla antes de poder crearla.

Meter las dos en la misma función habría significado un `if` en cada línea.

LO QUE SÍ COMPARTEN, Y ES LO QUE IMPORTA
----------------------------------------
Los DATOS. Ambos leen `build_sales_note_context`, que a su vez lee el desglose
CONGELADO de la venta. El ticket no vuelve a calcular nada: si lo hiciera,
podría imprimir un céntimo distinto del que dice el A4 de la misma compra.

Y ambos llevan el mismo aviso: esto NO es un comprobante electrónico SUNAT.

QUÉ NO LLEVA
------------
Ningún identificador de pasarela, ningún `payment_error`, ningún token, ningún
dato de tarjeta. El ticket se entrega en mano.
"""

from __future__ import annotations

import io
from decimal import Decimal

from django.utils import timezone

from .models import Order, SalesNote
from .sales_note_services import (
    SALES_NOTE_DISCLAIMER, SalesNoteError, build_sales_note_context,
)

#: Rollo de 80 mm. El área imprimible real de casi toda térmica de 80 mm son
#: 72 mm: los 4 mm de cada lado no los alcanza el cabezal.
TICKET_WIDTH_MM = Decimal('80')
TICKET_MARGIN_MM = Decimal('4')

_FONT = 'Helvetica'
_FONT_BOLD = 'Helvetica-Bold'


def get_sales_note_ticket_filename(sales_note: SalesNote) -> str:
    """Mismo nombre que el A4 pero marcado, para no confundir dos descargas."""
    from .sales_note_services import get_sales_note_filename

    return get_sales_note_filename(sales_note).replace('.pdf', '-ticket80.pdf')


def generate_sales_note_ticket_pdf(sales_note: SalesNote) -> bytes:
    """
    El ticket de 80 mm de esta nota, en memoria. No se escribe nada en disco.

    Se dibuja DOS VECES: la primera para medir cuánto ocupa, la segunda para
    dibujarlo de verdad en una página de exactamente esa altura. Es lo que
    evita que la impresora escupa quince centímetros de papel en blanco después
    de un ticket de dos líneas.

    Levanta `SalesNoteError` si la orden ya no está pagada — la misma regla que
    el A4, porque es el mismo documento.
    """
    order = sales_note.order
    if not order.paid or order.status != Order.Status.PAID:
        raise SalesNoteError(
            f'No se puede generar el ticket de la nota {sales_note.number}: '
            f'la orden no está pagada.'
        )

    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdfcanvas

    ctx = build_sales_note_context(sales_note)

    width = float(TICKET_WIDTH_MM) * mm
    margin = float(TICKET_MARGIN_MM) * mm
    content = width - 2 * margin

    # Pasada 1: medir. Pasada 2: dibujar en una página de esa altura exacta.
    height = _lay_out(None, ctx, content, margin, 0.0)

    buffer = io.BytesIO()
    pdf = pdfcanvas.Canvas(buffer, pagesize=(width, height))
    pdf.setTitle(f"{ctx['number']} — {ctx['store_name']}"
                 if ctx['store_name'] else ctx['number'])
    pdf.setAuthor(ctx['store_name'] or '')
    _lay_out(pdf, ctx, content, margin, height)
    pdf.showPage()
    pdf.save()

    pdf_bytes = buffer.getvalue()
    buffer.close()

    SalesNote.objects.filter(pk=sales_note.pk).update(pdf_generated_at=timezone.now())
    return pdf_bytes


# ---------------------------------------------------------------------------
# Trazado
# ---------------------------------------------------------------------------

def _wrap(text: str, font: str, size: float, width: float) -> list[str]:
    """
    Parte un texto para que quepa en el ancho del rollo.

    Se mide con las métricas reales de la fuente, no contando caracteres: una
    «W» ocupa el triple que una «l», y un nombre de producto largo cortado por
    número de letras se sale del papel o deja media línea vacía.

    UNA PALABRA SIN ESPACIOS TAMBIÉN SE PARTE. Esta función sólo cortaba entre
    palabras, así que un nombre como
    `MacBookProM4Max16Pulgadas1TBNegroEspacialConCargadorMagSafe140W` —o el
    nombre largo de una empresa, que es texto libre de cada inquilino— se
    dibujaba entero: 258 pt de ancho sobre una página de 227 pt. No se recortaba
    con un aviso; se salía del papel y desaparecía. En el A4 no pasaba porque
    `Paragraph` parte palabras largas por su cuenta.
    """
    from reportlab.pdfbase.pdfmetrics import stringWidth

    words = str(text).split()
    if not words:
        return ['']

    lines: list[str] = []
    current = ''
    for word in words:
        candidate = f'{current} {word}' if current else word
        if stringWidth(candidate, font, size) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ''
        # La palabra sola tampoco cabe: se trocea por caracteres, midiendo.
        while stringWidth(word, font, size) > width:
            cut = len(word) - 1
            while cut > 1 and stringWidth(word[:cut], font, size) > width:
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        current = word

    if current:
        lines.append(current)
    return lines


class _Cursor:
    """
    Un lápiz que sabe dibujar o sólo contar.

    Con `pdf=None` mide; con un canvas dibuja. Es la misma secuencia de
    llamadas en las dos pasadas, así que la altura medida no puede separarse de
    la dibujada — que es exactamente el fallo que produce el papel en blanco.
    """

    def __init__(self, pdf, left: float, top: float, width: float):
        self.pdf = pdf
        self.left = left
        self.width = width
        self.y = top
        self.used = 0.0

    def _advance(self, amount: float):
        self.y -= amount
        self.used += amount

    def line(self, text: str, *, size: float = 7.5, bold: bool = False,
             align: str = 'left', leading: float = 1.35):
        font = _FONT_BOLD if bold else _FONT
        step = size * leading
        for chunk in _wrap(text, font, size, self.width):
            if self.pdf is not None:
                self.pdf.setFont(font, size)
                if align == 'center':
                    self.pdf.drawCentredString(self.left + self.width / 2, self.y - size, chunk)
                elif align == 'right':
                    self.pdf.drawRightString(self.left + self.width, self.y - size, chunk)
                else:
                    self.pdf.drawString(self.left, self.y - size, chunk)
            self._advance(step)

    def row(self, label: str, value: str, *, size: float = 7.5, bold: bool = False):
        """Etiqueta a la izquierda, importe a la derecha, en la misma línea."""
        font = _FONT_BOLD if bold else _FONT
        if self.pdf is not None:
            self.pdf.setFont(font, size)
            self.pdf.drawString(self.left, self.y - size, label)
            self.pdf.drawRightString(self.left + self.width, self.y - size, value)
        self._advance(size * 1.35)

    def rule(self, *, gap: float = 3.0, dashed: bool = True):
        self._advance(gap)
        if self.pdf is not None:
            self.pdf.setLineWidth(0.4)
            if dashed:
                self.pdf.setDash(1.5, 1.5)
            self.pdf.line(self.left, self.y, self.left + self.width, self.y)
            self.pdf.setDash()
        self._advance(gap)

    def gap(self, amount: float):
        self._advance(amount)


def _lay_out(pdf, ctx: dict, content: float, margin: float, page_height: float) -> float:
    """
    Escribe el ticket entero y devuelve cuánto alto ocupó.

    Una sola definición del ticket para las dos pasadas.
    """
    cur = _Cursor(pdf, margin, page_height - margin, content)
    tax = ctx['tax']
    money = tax['symbol']

    # --- Quién vende ---
    cur.line(ctx['store_name'] or ctx['store_legal_name'], size=10, bold=True, align='center')
    if ctx['store_legal_name'] and ctx['store_legal_name'] != ctx['store_name']:
        cur.line(ctx['store_legal_name'], size=6.5, align='center')
    if ctx['store_ruc']:
        cur.line(f"RUC {ctx['store_ruc']}", size=6.5, align='center')
    if ctx['store_address']:
        cur.line(ctx['store_address'], size=6.5, align='center')
    if ctx['store_phone']:
        cur.line(f"WhatsApp {ctx['store_phone']}", size=6.5, align='center')

    cur.rule()

    # --- Qué es este papel ---
    cur.line(ctx['title'], size=8.5, bold=True, align='center')
    cur.line(ctx['number'], size=9, bold=True, align='center')
    cur.line('Número interno del sistema. No es una serie fiscal.',
             size=6, align='center')
    cur.gap(2)

    cur.row('Fecha:', ctx['issued_at'], size=6.5)
    cur.row('Pedido:', f"#{ctx['order_id']}", size=6.5)
    # «SOLICITADO», Y LA PALABRA IMPORTA.
    #
    # El ticket decía «Comprobante: Boleta», que se lee como que ESTE papel es
    # una boleta. No lo es: aquí no ha habido emisión ni aceptación de SUNAT,
    # sólo la petición del cliente. El A4 ya lo rotulaba bien y el ticket no,
    # así que el mismo dato afirmaba dos cosas distintas según el formato.
    cur.row('Comprobante solicitado:', ctx['receipt_label'], size=6.5)

    cur.rule()

    # --- Quién compra ---
    cur.line(f"Cliente: {ctx['customer_name']}", size=6.5)
    if ctx['document_number'] and ctx['document_number'] != '—':
        cur.line(f"{ctx['document_label']}: {ctx['document_number']}", size=6.5)
    cur.line(f"Entrega: {ctx['delivery_label']}", size=6.5)
    if ctx['full_address']:
        cur.line(ctx['full_address'], size=6.5)

    cur.rule()

    # --- Qué se lleva ---
    # Nombre en su línea y el importe debajo: en 7,2 cm no caben cuatro
    # columnas sin partir los nombres en pedazos ilegibles.
    for item in ctx['items']:
        cur.line(str(item['name']), size=7)
        cur.row(
            f"  {item['quantity']} x {money} {item['unit_price']:.2f}",
            f"{money} {item['subtotal']:.2f}",
            size=7,
        )

    cur.rule()

    # --- Cuánto es ---
    cur.row('Subtotal', f"{money} {tax['subtotal']:.2f}", size=7)
    if tax['discount_amount'] > 0:
        cur.row('Descuento', f"- {money} {tax['discount_amount']:.2f}", size=7)
    cur.row(tax['base_label'], f"{money} {tax['taxable_amount']:.2f}", size=7)
    if tax['shows_tax']:
        cur.row(tax['tax_label'], f"{money} {tax['tax_amount']:.2f}", size=7)
    cur.gap(1)
    cur.row('TOTAL', f"{money} {tax['total']:.2f}", size=10, bold=True)

    if ctx['notes']:
        cur.rule()
        cur.line('Notas', size=6.5, bold=True)
        cur.line(str(ctx['notes']), size=6.5)

    cur.rule()

    # --- El aviso, que no es opcional ---
    cur.line(SALES_NOTE_DISCLAIMER, size=6.5, bold=True, align='center')
    cur.gap(2)
    if ctx['warranty_note']:
        cur.line(str(ctx['warranty_note']), size=6, align='center')

    # Cola de papel para que el corte no muerda la última línea.
    cur.gap(margin)
    return cur.used + margin
