"""
La prueba de impresión: unas pocas líneas antes de gastar un ticket entero.

PRINT-THERMAL-01. Sirve para dos comprobaciones distintas, con el mismo texto:

  · ESC/POS por el agente (`escpos.test_print_ticket`): ancho del rollo (la
    regla de 48 o 32 columnas), página de códigos (los acentos), avance y corte.
  · El PDF de 80 mm (`test_page_pdf`): que el controlador de la cola del
    sistema imprime un PDF en una térmica, antes de mandarle una nota.

Si en vez de estas líneas sale texto técnico (`%!PS`, `%PDF`), la cola está
mandando PostScript o PDF a una impresora que no lo entiende: se apaga la
impresora y se cancela la cola. La última línea lo dice en el propio papel.
"""
from __future__ import annotations

import io
from datetime import datetime

TITLE = 'PRUEBA DE IMPRESIÓN'
ACCENTS = 'áéíóú ñ Ñ ¿¡ S/ 10.50'
WARNING = 'Si ve texto técnico (%!PS, %PDF) apague la impresora y cancele la cola.'
FILENAME = 'prueba-impresion-ticket80.pdf'


def ruler(columns: int) -> str:
    """`1234567890…` hasta el ancho exacto del rollo: si se parte, el ancho está mal."""
    return ('1234567890' * 10)[:columns]


def stamp(printed_at: datetime) -> str:
    return printed_at.strftime('%d/%m/%Y %H:%M')


def test_page_pdf(*, company_name: str, printed_at: datetime) -> bytes:
    """
    La misma prueba en un PDF de 80 mm, con la altura de su contenido.

    Se imprime con el controlador de la térmica (no con «Generic PostScript
    Printer»): si sale bien, el PDF de 80 mm de una nota también saldrá.
    """
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdfcanvas

    from ..ticket_services import TICKET_MARGIN_MM, TICKET_WIDTH_MM, _Cursor

    width = float(TICKET_WIDTH_MM) * mm
    margin = float(TICKET_MARGIN_MM) * mm
    content = width - 2 * margin

    def lay_out(pdf, page_height: float) -> float:
        cur = _Cursor(pdf, margin, page_height - margin, content)
        cur.line(company_name, size=8.5, bold=True, align='center')
        cur.line(TITLE, size=9, bold=True, align='center')
        cur.line(f'Fecha: {stamp(printed_at)}', size=7)
        cur.line('PDF de 80 mm, para el controlador de la térmica.', size=6.5)
        cur.line(ruler(48), size=6.5)
        cur.line(f'Acentos: {ACCENTS}', size=7)
        cur.line(WARNING, size=6.5, bold=True)
        cur.gap(margin)
        return cur.used + margin

    height = lay_out(None, 0.0)
    buffer = io.BytesIO()
    pdf = pdfcanvas.Canvas(buffer, pagesize=(width, height))
    pdf.setTitle(f'{TITLE} — {company_name}')
    lay_out(pdf, height)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()
